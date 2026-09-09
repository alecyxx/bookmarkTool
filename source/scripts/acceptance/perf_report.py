"""性能与规模验收探针（BM-V1-904）——生成基线数据并归档原始结果。

用法：
    python -m scripts.acceptance.perf_report [--root DIR]

基线：50,000 书签、平均 3 标签、最深 8 层分类样本、50 并发以下（单写者模型）。
输出：<root>/perf-<ts>.json（计数/各场景 p50/p95 ms/内存峰值/导入总耗时）+ console 摘要。

说明：本探针不修改任何生产库；全部在独立临时目录完成。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import load_settings
from app.database import create_engine_from_settings, create_session_factory
from app.services.bookmark_service import BookmarkService
from app.services.import_parser import parse_csv
from app.services.import_service import ImportExecutor, create_previewed_job
from sqlalchemy import insert, select


def _upgrade_to_head(database_url: str) -> None:
    """本地迁移辅助（与 tests/conftest 一致，保证脚本独立可运行）。"""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    config = AlembicConfig(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


BOOKMARK_COUNT = 50_000
TAG_POOL = 200
TAGS_PER_BOOKMARK = 3
TARGET_WORD = "目标词验收"


def _percentile(samples: list[float], percentile: float) -> float:
    """Nearest-rank percentile；样本由调用方提供且至少包含一项。"""
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _timed(fn, repeats: int = 20):
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "samples": repeats,
        "p50_ms": round(_percentile(samples, 0.50), 1),
        "p95_ms": round(_percentile(samples, 0.95), 1),
        "max_ms": round(max(samples), 1),
    }


def _insert_chunked(session, table, rows, chunk_size: int = 100) -> None:
    """分批插入：单条语句参数不超过 SQLite 变量上限（999）。"""
    for start in range(0, len(rows), chunk_size):
        session.execute(insert(table).values(rows[start : start + chunk_size]))
    session.commit()


def build_dataset(db_url: str, root: Path) -> dict:
    """生成：8 层分类树（每节点 2 子，叶层 256 节点）+ 50k 书签 + 平均 3 标签。"""
    engine = create_engine_from_settings(
        load_settings(
            env={
                "APP_ENV": "testing",
                "SESSION_SECRET": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c",
                "DATABASE_URL": db_url,
            },
            use_dotenv=False,
        )
    )
    session = create_session_factory(engine)()
    started = time.perf_counter()

    # 分类树：8 层，每层 2 子；记录叶节点 id 列表
    leaf_ids: list[int] = []
    root_ids: list[int] = []
    level: list[tuple[int | None, str]] = [(None, "R0")]
    for depth in range(1, 9):  # depth 1..8
        next_level: list[tuple[int | None, str]] = []
        for index, (parent_id, _name) in enumerate(level):
            for suffix in ("A", "B"):
                name = f"C{depth}-{index}-{suffix}"
                result = session.execute(
                    insert(CategoryTable()).values(
                        name=name, normalized_name=name.lower(), parent_id=parent_id
                    )
                )
                category_id = result.inserted_primary_key[0]
                if depth == 1:
                    root_ids.append(category_id)
                if depth == 8:
                    leaf_ids.append(category_id)
                else:
                    next_level.append((category_id, name))
        if depth < 8:
            level = next_level
    # 上面的树结构每层以父 level 生成 children，leaf 收集在 depth==8（子层创建时 depth==8）

    # 标签池
    tag_ids: list[int] = []
    for index in range(TAG_POOL):
        result = session.execute(
            insert(TagTable()).values(name=f"标签{index}", normalized_name=f"tag{index}")
        )
        tag_ids.append(result.inserted_primary_key[0])
    session.commit()

    # 书签 50k（bulk insert + 关联批量）
    bookmark_rows = []
    for index in range(BOOKMARK_COUNT):
        title = f"b{index}"
        if index % 100 == 0:
            title = f"{TARGET_WORD} 页面 {index}"
        bookmark_rows.append(
            {
                "title": title,
                "url": f"https://example.com/{index}",
                "normalized_url": f"https://example.com/{index}",
                "category_id": leaf_ids[index % len(leaf_ids)],
                "is_favorite": index % 20 == 0,
                "description": "",
                "created_at": datetime(2026, 1, 1, 0, 0, 0),
                "updated_at": datetime(2026, 1, 1, 0, 0, 0),
                "version": 1,
            }
        )
    _insert_chunked(session, BookmarkTable(), bookmark_rows)
    bookmark_id_rows = session.execute(
        select(BookmarkTable().c.id).order_by(BookmarkTable().c.id)
    ).all()
    link_rows = []
    rng = random.Random(7)
    for (bookmark_id,) in bookmark_id_rows:
        chosen = rng.sample(tag_ids, TAGS_PER_BOOKMARK)
        for tag_id in chosen:
            link_rows.append({"bookmark_id": bookmark_id, "tag_id": tag_id})
    _insert_chunked(session, BookmarkTagsTable(), link_rows, chunk_size=300)
    elapsed = time.perf_counter() - started
    session.close()
    engine.dispose()
    return {
        "bookmarks": BOOKMARK_COUNT,
        "tags_per_bookmark": TAGS_PER_BOOKMARK,
        "tag_pool": TAG_POOL,
        "leaf_categories": len(leaf_ids),
        "root_sample": root_ids[:2],
        "build_seconds": round(elapsed, 1),
        "leaf_sample": leaf_ids[:5],
    }


# 避免顶层模型导入成本：用 raw metadata 表
def CategoryTable():
    from app.models.category import Category

    return Category.__table__


def TagTable():
    from app.models.tag import Tag

    return Tag.__table__


def BookmarkTable():
    from app.models.bookmark import Bookmark

    return Bookmark.__table__


def BookmarkTagsTable():
    from app.models.bookmark import bookmark_tags

    return bookmark_tags


def measure_queries(db_url: str, category_id: int) -> dict:
    settings = load_settings(
        env={
            "APP_ENV": "testing",
            "SESSION_SECRET": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c",
            "DATABASE_URL": db_url,
        },
        use_dotenv=False,
    )
    engine = create_engine_from_settings(settings)
    session = create_session_factory(engine)()
    svc = BookmarkService(session)
    results: dict = {}

    def first_page() -> None:
        svc.list_page(page_size=50)

    results["list_first_page"] = _timed(first_page)

    def search_word() -> None:
        svc.list_page(q=TARGET_WORD, page_size=50)

    results["search_keyword"] = _timed(search_word)

    def category_filter() -> None:
        # 使用有 7 层后代的根分类，真实覆盖递归后代筛选路径。
        svc.list_page(category_id=category_id, q="", page_size=50)

    results["category_descendants"] = _timed(category_filter)

    def favorite() -> None:
        svc.list_page(favorite=True, page_size=50)

    results["favorite_filter"] = _timed(favorite)

    def deep_page() -> None:
        svc.list_page(q="", page=990, page_size=50)

    results["deep_page_990"] = _timed(deep_page)
    session.close()
    engine.dispose()
    return results


def measure_concurrent_reads(db_url: str, concurrent_requests: int = 50) -> dict:
    """同时发起 50 个只读查询，记录总耗时、单请求分位数和错误数。"""
    settings = load_settings(
        env={
            "APP_ENV": "testing",
            "SESSION_SECRET": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c",
            "DATABASE_URL": db_url,
        },
        use_dotenv=False,
    )
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    barrier = Barrier(concurrent_requests)

    def read_once(index: int) -> float:
        session = session_factory()
        try:
            barrier.wait(timeout=10)
            started = time.perf_counter()
            BookmarkService(session).list_page(page=(index % 20) + 1, page_size=50)
            return (time.perf_counter() - started) * 1000
        finally:
            session.close()

    started = time.perf_counter()
    errors: list[str] = []
    latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=concurrent_requests) as executor:
        futures = [executor.submit(read_once, index) for index in range(concurrent_requests)]
        for future in futures:
            try:
                latencies.append(future.result())
            except Exception as exc:  # noqa: BLE001 - 报告需归档所有并发失败
                errors.append(type(exc).__name__)
    wall_ms = (time.perf_counter() - started) * 1000
    engine.dispose()
    return {
        "requests": concurrent_requests,
        "errors": len(errors),
        "error_types": sorted(set(errors)),
        "wall_ms": round(wall_ms, 1),
        "p50_ms": round(_percentile(latencies, 0.50), 1) if latencies else None,
        "p95_ms": round(_percentile(latencies, 0.95), 1) if latencies else None,
        "max_ms": round(max(latencies), 1) if latencies else None,
    }


def measure_import(db_root: Path, settings) -> dict:
    """CSV 50k 行：预览（解析）与执行（一次性写入独立库）计时。"""
    import_db = db_root / "import_bench.db"
    import_db.unlink(missing_ok=True)
    _upgrade_to_head(f"sqlite:///{import_db}")
    settings_env = load_settings(
        env={
            "APP_ENV": "testing",
            "SESSION_SECRET": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c",
            "DATABASE_URL": f"sqlite:///{import_db}",
            "IMPORT_TMP_DIR": str(db_root / "import_tmp"),
        },
        use_dotenv=False,
    )
    engine = create_engine_from_settings(settings_env)
    session = create_session_factory(engine)()
    # 生成 CSV
    lines = ["title,url,category,tags,favorite"]
    for index in range(BOOKMARK_COUNT):
        lines.append(f"i{index},https://import.example/{index},工具/子类,tag{index % TAG_POOL},0")
    content = ("\n".join(lines)).encode("utf-8")
    preview_started = time.perf_counter()
    job, parsed = create_previewed_job(
        session,
        settings_env,
        source_type="CSV",
        original_filename="bench.csv",
        content=content,
        session_cookie="bench-session",
        folder_policy="category",
    )
    preview_seconds = time.perf_counter() - preview_started
    session.commit()
    # 解析本身计时（纯函数）
    parse_started = time.perf_counter()
    parse_csv(content)
    parse_seconds = time.perf_counter() - parse_started

    execute_started = time.perf_counter()
    executor = ImportExecutor(session, settings_env, "bench-session")
    counts = executor.execute(job)
    execute_seconds = time.perf_counter() - execute_started
    session.commit()
    result = {
        "parse_50k_seconds": round(parse_seconds, 1),
        "preview_total_seconds": round(preview_seconds, 1),
        "execute_50k_seconds": round(execute_seconds, 1),
        "created": counts["created"],
        "categories_created": counts["categories_created"],
    }
    session.close()
    engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None)
    args = parser.parse_args()
    base = Path(args.root) if args.root else Path("dev_data") / "acceptance"
    base.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat()
    db_file = base / "bench_queries.db"
    db_file.unlink(missing_ok=True)
    db_url = f"sqlite:///{db_file}"
    _upgrade_to_head(db_url)
    dataset = build_dataset(db_url, base)
    queries = measure_queries(db_url, dataset["root_sample"][0])
    concurrency = measure_concurrent_reads(db_url)
    # tracemalloc 会明显放大多线程分配延迟，因此只在独立导入阶段统计峰值，
    # 不让内存仪表污染查询和并发延迟指标。
    tracemalloc.start()
    import_results = measure_import(base, None)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report = {
        "started_at": started_at,
        "baseline": dataset,
        "queries": queries,
        "concurrency": concurrency,
        "import": import_results,
        "peak_memory_bytes": peak,
        "python": sys.version.split()[0],
        "notes": [
            "并发探针为 50 个同时只读请求；写入仍遵循 SQLite 单写者模型",
            "查询场景含 selectinload(tags) 与组合过滤（无 N+1）",
        ],
    }
    output = base / f"perf-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("ARCHIVED", output)


if __name__ == "__main__":
    main()
