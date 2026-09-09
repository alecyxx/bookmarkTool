"""性能验收探针自身的可信度回归（BM-V1-904）。"""

from __future__ import annotations

from app.models.bookmark import Bookmark
from app.models.category import Category
from scripts.acceptance.perf_report import (
    _percentile,
    measure_concurrent_reads,
    measure_queries,
)


def test_percentile_uses_nearest_rank():
    samples = [float(value) for value in range(1, 21)]
    assert _percentile(samples, 0.50) == 10.0
    assert _percentile(samples, 0.95) == 19.0


def test_query_probe_exercises_category_descendants(db_session, db):
    root = Category(name="根", normalized_name="根")
    db_session.add(root)
    db_session.flush()
    child = Category(name="子", normalized_name="子", parent_id=root.id)
    db_session.add(child)
    db_session.flush()
    db_session.add(
        Bookmark(
            title="分类后代书签",
            url="https://example.com/category-child",
            normalized_url="https://example.com/category-child",
            category_id=child.id,
        )
    )
    db_session.commit()

    result = measure_queries(str(db.url), root.id)
    assert "category_descendants" in result
    assert result["category_descendants"]["samples"] == 20


def test_concurrent_read_probe_reports_real_requests(db):
    result = measure_concurrent_reads(str(db.url), concurrent_requests=8)
    assert result["requests"] == 8
    assert result["errors"] == 0
    assert result["p95_ms"] is not None
