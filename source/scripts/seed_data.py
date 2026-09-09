"""性能数据生成工具（BM-V1-108）。

在**已迁移**的数据库上批量生成书签/分类/标签样本，供查询与性能基线测试使用。
- 默认目标规模：50,000 条书签、每条恰好 3 个标签；
- 生成数据只应写入显式指定的测试/开发库，绝不写入生产数据目录：--database 必填；
- 可重复执行（默认先清空业务表）。

用法：
    python -m scripts.seed_data --database sqlite:///dev_data/perf.db --rows 50000
"""

from __future__ import annotations

import argparse
import random
import sys

from app.config import load_settings
from app.database import create_engine_from_settings
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

HOST_POOL = [
    "github.com",
    "chatgpt.com",
    "fastapi.tiangolo.com",
    "python.org",
    "docker.com",
    "stackoverflow.com",
    "developer.mozilla.org",
    "news.ycombinator.com",
    "wikipedia.org",
    "gitlab.com",
    "pypi.org",
    "sqlalchemy.org",
    "cloudflare.com",
    "jetbrains.com",
]
TAG_POOL = [
    "Python",
    "AI",
    "开发",
    "工具",
    "API",
    "Git",
    "Docker",
    "前端",
    "后端",
    "数据库",
    "算法",
    "效率",
    "阅读",
    "设计",
    "运维",
]
TITLE_PREFIXES = ["使用", "深入", "快速上手", "最佳实践", "指南", "笔记", "案例", "源码解析"]
BATCH = 2_000


def seed(engine: Engine, rows: int = 50_000, *, clear_first: bool = True) -> dict[str, int]:
    """生成样本数据并返回计数。随机种子固定，保证可复现。

    目标库必须已执行 Alembic 迁移；未迁移时抛 RuntimeError（不猜测建表）。
    """
    with engine.connect() as conn:
        table_names = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        if not {"bookmarks", "categories", "bookmark_tags"} <= table_names:
            raise RuntimeError(
                "目标数据库尚未迁移：请先执行 alembic upgrade head"
                "（DATABASE_URL 与 --database 保持一致）"
            )
    random.seed(20260908)
    bookmark_total = 0
    with engine.begin() as conn:
        if clear_first:
            for table in ("bookmark_tags", "bookmarks", "tags"):
                conn.execute(text(f"DELETE FROM {table}"))
            # categories 自引用 RESTRICT：先删子层再删根层
            conn.execute(text("DELETE FROM categories WHERE parent_id IS NOT NULL"))
            conn.execute(text("DELETE FROM categories WHERE parent_id IS NULL"))
            conn.execute(text("DELETE FROM users"))
            conn.execute(text("DELETE FROM app_meta"))
            conn.execute(
                text(
                    "INSERT INTO app_meta (id, category_tree_revision, updated_at)"
                    " VALUES (1, 1, CURRENT_TIMESTAMP)"
                )
            )

        category_ids: list[int] = []
        for root_index in range(8):
            root_id = conn.execute(
                text(
                    "INSERT INTO categories (name, normalized_name, sort_order, version)"
                    " VALUES (:n, :n, :s, 1) RETURNING id"
                ),
                {"n": f"根分类{root_index}", "s": root_index},
            ).scalar_one()
            category_ids.append(root_id)
            for child_index in range(3):
                child_id = conn.execute(
                    text(
                        "INSERT INTO categories (name, normalized_name, parent_id, sort_order, version)"
                        " VALUES (:n, :n, :p, :s, 1) RETURNING id"
                    ),
                    {
                        "n": f"根分类{root_index}/子分类{child_index}",
                        "p": root_id,
                        "s": child_index,
                    },
                ).scalar_one()
                category_ids.append(child_id)

        tag_ids: list[int] = []
        for tag_name in TAG_POOL:
            tag_id = conn.execute(
                text(
                    "INSERT INTO tags (name, normalized_name, version)"
                    " VALUES (:n, :n, 1) RETURNING id"
                ),
                {"n": tag_name},
            ).scalar_one()
            tag_ids.append(tag_id)

        for batch_start in range(0, rows, BATCH):
            bookmark_rows: list[dict] = []
            for index in range(batch_start, min(batch_start + BATCH, rows)):
                host = random.choice(HOST_POOL)
                url = f"https://{host}/docs/{random.randint(1, 1_000_000)}?ref=seed"
                bookmark_rows.append(
                    {
                        "title": f"{random.choice(TITLE_PREFIXES)}-页面-{index:06d}",
                        "url": url,
                        "normalized_url": url.lower(),
                        "category_id": random.choice(category_ids) if index % 7 else None,
                        "is_favorite": 1 if index % 23 == 0 else 0,
                    }
                )
            # sqlite3 的 executemany 不支持 RETURNING：先批量插入，再用本批唯一 title 反查 id
            conn.execute(
                text(
                    "INSERT INTO bookmarks"
                    " (title, url, normalized_url, description, category_id, is_favorite, version)"
                    " VALUES (:title, :url, :normalized_url, '', :category_id, :is_favorite, 1)"
                ),
                bookmark_rows,
            )
            titles = [row["title"] for row in bookmark_rows]
            title_bind = bindparam("titles", expanding=True)
            inserted_ids = [
                row[0]
                for row in conn.execute(
                    text("SELECT id FROM bookmarks WHERE title IN :titles ORDER BY id").bindparams(
                        title_bind
                    ),
                    {"titles": titles},
                )
            ]
            bookmark_total += len(inserted_ids)
            link_rows = [
                {"bookmark_id": bookmark_id, "tag_id": tag_id}
                for bookmark_id in inserted_ids
                for tag_id in random.sample(tag_ids, k=3)
            ]
            conn.execute(
                text(
                    "INSERT INTO bookmark_tags (bookmark_id, tag_id) VALUES (:bookmark_id, :tag_id)"
                ),
                link_rows,
            )

    return {"bookmarks": bookmark_total, "tags": len(tag_ids), "categories": len(category_ids)}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成书签性能测试样本数据")
    parser.add_argument("--database", required=True, help="目标数据库 URL（必须显式指定）")
    parser.add_argument("--rows", type=int, default=50_000, help="书签条数（默认 50000）")
    args = parser.parse_args()
    if args.rows <= 0 or args.rows > 200_000:
        print("rows 必须在 1～200000 之间", file=sys.stderr)
        return 2

    settings = load_settings(env={"DATABASE_URL": args.database}, use_dotenv=False)
    engine = create_engine_from_settings(settings)
    try:
        counts = seed(engine, rows=args.rows)
    except Exception as exc:  # noqa: BLE001 - CLI 顶层需要可读错误
        print(f"种子数据生成失败：{exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(
        f"生成完成：bookmarks={counts['bookmarks']} tags={counts['tags']}"
        f" categories={counts['categories']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
