"""乐观并发与事务工具测试（BM-V1-107）。"""

from __future__ import annotations

import pytest
from app.errors import AppError
from app.models.bookmark import Bookmark
from app.services.versioned import (
    apply_versioned_update,
    bump_tree_revision,
    fetch_versioned,
    get_tree_revision,
    validate_versions,
)


def _add_bookmark(db_session, title: str = "t") -> Bookmark:
    bookmark = Bookmark(
        title=title,
        url="https://example.com/" + title,
        normalized_url="https://example.com/" + title,
    )
    db_session.add(bookmark)
    db_session.commit()
    return bookmark


def test_versioned_update_success(db_session):
    bookmark = _add_bookmark(db_session)
    apply_versioned_update(db_session, Bookmark, bookmark.id, bookmark.version, {"title": "new"})
    db_session.commit()
    fresh = db_session.get(Bookmark, bookmark.id)
    assert fresh.title == "new"
    assert fresh.version == 2


def test_versioned_update_conflict_on_stale(db_session):
    bookmark = _add_bookmark(db_session)
    stale_version = bookmark.version  # 编辑者读取到的版本
    apply_versioned_update(db_session, Bookmark, bookmark.id, stale_version, {"title": "第一人"})
    db_session.commit()
    with pytest.raises(AppError) as excinfo:
        apply_versioned_update(
            db_session, Bookmark, bookmark.id, stale_version, {"title": "第二人"}
        )
    assert excinfo.value.status_code == 409
    db_session.rollback()
    fresh = db_session.get(Bookmark, bookmark.id)
    assert fresh.title == "第一人"  # 第二人的修改没有覆盖


def test_two_editors_only_one_wins(db_session):
    """模拟两个标签页同时编辑：只有第一个成功，失败方不覆盖。"""
    bookmark = _add_bookmark(db_session)
    # 编辑者 A 与 B 同时读到 version=1
    version_a = bookmark.version
    version_b = bookmark.version
    apply_versioned_update(db_session, Bookmark, bookmark.id, version_a, {"title": "A 的标题"})
    db_session.commit()
    with pytest.raises(AppError):
        apply_versioned_update(db_session, Bookmark, bookmark.id, version_b, {"title": "B 的标题"})
    db_session.rollback()
    assert db_session.get(Bookmark, bookmark.id).title == "A 的标题"


def test_versioned_update_not_found(db_session):
    with pytest.raises(AppError) as excinfo:
        apply_versioned_update(db_session, Bookmark, 424242, 1, {"title": "x"})
    assert excinfo.value.status_code == 404


def test_validate_versions_batch(db_session):
    first = _add_bookmark(db_session, "a")
    second = _add_bookmark(db_session, "b")
    # 全部有效 -> 通过
    validate_versions(
        db_session, Bookmark, [(first.id, first.version), (second.id, second.version)]
    )
    # 任一过期 -> 409（second.version 为读取时的旧值）
    stale_second = second.version
    apply_versioned_update(db_session, Bookmark, second.id, stale_second, {"title": "b2"})
    db_session.commit()
    with pytest.raises(AppError) as excinfo:
        validate_versions(
            db_session, Bookmark, [(first.id, first.version), (second.id, stale_second)]
        )
    assert excinfo.value.status_code == 409
    # 不存在的记录 -> 404
    with pytest.raises(AppError) as excinfo:
        validate_versions(db_session, Bookmark, [(999999, 1)])
    assert excinfo.value.status_code == 404
    # 空列表安全通过
    validate_versions(db_session, Bookmark, [])


def test_fetch_versioned(db_session):
    bookmark = _add_bookmark(db_session)
    assert fetch_versioned(db_session, Bookmark, bookmark.id).id == bookmark.id
    with pytest.raises(AppError):
        fetch_versioned(db_session, Bookmark, 777)


def test_tree_revision_cas(db_session):
    assert get_tree_revision(db_session) == 1
    bump_tree_revision(db_session, 1)
    db_session.commit()
    assert get_tree_revision(db_session) == 2
    with pytest.raises(AppError):
        bump_tree_revision(db_session, 1)  # 过期期望
    db_session.rollback()
    assert get_tree_revision(db_session) == 2
