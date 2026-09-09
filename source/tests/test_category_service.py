"""分类树领域服务测试（BM-V1-106/107，设计文档 §24/§31/§32.1）。"""

from __future__ import annotations

import pytest
from app.errors import AppError
from app.models.bookmark import Bookmark
from app.models.category import Category
from app.services.category_service import MAX_TREE_DEPTH, CategoryService, build_tree
from app.services.versioned import get_tree_revision
from sqlalchemy import select, text


@pytest.fixture
def svc(db_session):
    return CategoryService(db_session)


def _commit(db_session):
    db_session.commit()


def create_chain(svc, db_session, names: list[str], parent_id: int | None = None) -> list[int]:
    """按名称序列创建一条链，返回各层 id。"""
    ids = []
    current_parent = parent_id
    for name in names:
        revision = get_tree_revision(db_session)
        category = svc.create(name, current_parent, revision)
        db_session.flush()
        ids.append(category.id)
        current_parent = category.id
    _commit(db_session)
    return ids


class TestTreeReads:
    def test_tree_depth_path_descendants(self, svc, db_session):
        [root_id, child_id, grand_id] = create_chain(svc, db_session, ["工作", "开发", "后端"])
        [other_root] = create_chain(svc, db_session, ["AI"])
        tree = svc.tree()
        assert not tree.has_broken
        root = tree.require(root_id)
        assert root.depth == 1
        assert root.path == ["工作"]
        assert root.subtree_height == 3
        grand = tree.require(grand_id)
        assert grand.depth == 3
        assert grand.path == ["工作", "开发", "后端"]
        assert tree.descendant_ids(root_id) == {root_id, child_id, grand_id}
        assert tree.descendant_ids(root_id, include_self=False) == {child_id, grand_id}
        assert tree.ancestor_ids(child_id) == [root_id]
        assert tree.ancestor_names(child_id) == ["工作"]
        assert tree.direct_children_ids(None) == sorted([root_id, other_root])


class TestCreate:
    def test_root_and_child_create(self, svc, db_session):
        root = svc.create("工作", None, get_tree_revision(db_session))
        db_session.flush()
        child = svc.create("开发", root.id, get_tree_revision(db_session))
        db_session.flush()
        assert child.parent_id == root.id
        assert get_tree_revision(db_session) == 3  # 初始 1 -> 两次 bump

    def test_same_name_conflict_at_same_parent(self, svc, db_session):
        create_chain(svc, db_session, ["AI"])
        with pytest.raises(AppError) as excinfo:
            svc.create("ai", None, get_tree_revision(db_session))  # 大小写等价
        assert excinfo.value.status_code == 409

    def test_same_name_allowed_different_parents(self, svc, db_session):
        create_chain(svc, db_session, ["工作", "子工作"])
        root_b_id = create_chain(svc, db_session, ["资料"])[0]
        svc.create("子工作", root_b_id, get_tree_revision(db_session))  # 不同父级同名 OK
        db_session.commit()

    def test_fullwidth_conflict(self, svc, db_session):
        create_chain(svc, db_session, ["AI"])
        with pytest.raises(AppError):
            svc.create("ＡＩ", None, get_tree_revision(db_session))

    def test_empty_or_too_long_name(self, svc, db_session):
        with pytest.raises(AppError) as excinfo:
            svc.create("   ", None, get_tree_revision(db_session))
        assert excinfo.value.status_code == 422
        with pytest.raises(AppError):
            svc.create("长" * 101, None, get_tree_revision(db_session))

    def test_missing_parent(self, svc, db_session):
        with pytest.raises(AppError) as excinfo:
            svc.create("孤儿", 9999, get_tree_revision(db_session))
        assert excinfo.value.status_code == 404

    def test_depth_limit_eight(self, svc, db_session):
        ids = create_chain(svc, db_session, [f"L{i}" for i in range(1, MAX_TREE_DEPTH + 1)])
        # 第 8 层可创建；第 9 层拒绝
        assert len(ids) == MAX_TREE_DEPTH
        with pytest.raises(AppError) as excinfo:
            svc.create("L9", ids[-1], get_tree_revision(db_session))
        assert excinfo.value.status_code == 409
        assert f"{MAX_TREE_DEPTH}" in excinfo.value.message


class TestRename:
    def test_rename_success(self, svc, db_session):
        [root_id] = create_chain(svc, db_session, ["工作"])
        tree = svc.tree()
        node = tree.require(root_id)
        svc.rename(root_id, "职业", node.category.version, get_tree_revision(db_session))
        db_session.commit()
        category = db_session.get(Category, root_id)
        assert category.name == "职业"
        assert category.normalized_name == "职业"
        assert category.version == 2

    def test_rename_conflict(self, svc, db_session):
        ids = create_chain(svc, db_session, ["工作", "开发"])
        [child] = create_chain(svc, db_session, ["子项"], parent_id=ids[0])
        tree = svc.tree()
        with pytest.raises(AppError) as excinfo:
            svc.rename(
                child, "开发", tree.require(child).category.version, get_tree_revision(db_session)
            )
        assert excinfo.value.status_code == 409

    def test_rename_stale_version(self, svc, db_session):
        [root_id] = create_chain(svc, db_session, ["工作"])
        with pytest.raises(AppError) as excinfo:
            svc.rename(root_id, "改名", 99, get_tree_revision(db_session))
        assert excinfo.value.status_code == 409


class TestMove:
    def test_move_subtree_success(self, svc, db_session):
        # 工作/开发/后端
        ids = create_chain(svc, db_session, ["工作", "开发", "后端"])
        [资料] = create_chain(svc, db_session, ["资料"])
        work, dev, back = ids
        tree = svc.tree()
        svc.move(dev, 资料, tree.require(dev).category.version, get_tree_revision(db_session))
        db_session.commit()
        tree = svc.tree()
        node = tree.require(dev)
        assert node.depth == 2
        assert node.path == ["资料", "开发"]
        assert tree.descendant_ids(资料) == {资料, dev, back}

    def test_move_to_self_or_descendant_rejected(self, svc, db_session):
        ids = create_chain(svc, db_session, ["工作", "开发", "后端"])
        work, dev, _back = ids
        tree = svc.tree()
        with pytest.raises(AppError):
            svc.move(work, work, tree.require(work).category.version, get_tree_revision(db_session))
        with pytest.raises(AppError):
            svc.move(work, dev, tree.require(work).category.version, get_tree_revision(db_session))

    def test_move_depth_exceeded_rejected(self, svc, db_session):
        deep = create_chain(svc, db_session, [f"D{i}" for i in range(1, 9)])  # 8 层
        other = create_chain(svc, db_session, ["X", "Y"])  # Y depth 2
        tree = svc.tree()
        node = tree.require(deep[1])  # depth 2, height 7
        # 移到 Y(depth2) 下：2 + 7 = 9 > 8 拒绝
        with pytest.raises(AppError) as excinfo:
            svc.move(deep[1], other[1], node.category.version, get_tree_revision(db_session))
        assert excinfo.value.status_code == 409

    def test_move_sibling_name_conflict(self, svc, db_session):
        # 工作/开发 与 资料/开发：把 工作/开发 移到 资料 下 -> 同级重名
        work_chain = create_chain(svc, db_session, ["工作", "开发"])
        info_chain = create_chain(svc, db_session, ["资料", "开发"])
        tree = svc.tree()
        with pytest.raises(AppError):
            svc.move(
                work_chain[1],
                info_chain[0],
                tree.require(work_chain[1]).category.version,
                get_tree_revision(db_session),
            )


class TestDelete:
    def _make_with_bookmarks(self, svc, db_session):
        ids = create_chain(svc, db_session, ["工作", "开发"])
        work, dev = ids
        db_session.add_all(
            [
                Bookmark(
                    title="a", url="https://a.com", normalized_url="https://a.com", category_id=dev
                ),
                Bookmark(
                    title="b",
                    url="https://b.com",
                    normalized_url="https://b.com",
                    category_id=dev,
                    deleted_at=None,
                ),
            ]
        )
        db_session.commit()
        # 回收站书签（软删除）
        trashed = Bookmark(
            title="t", url="https://t.com", normalized_url="https://t.com", category_id=dev
        )
        db_session.add(trashed)
        db_session.commit()
        db_session.execute(
            text("UPDATE bookmarks SET deleted_at = CURRENT_TIMESTAMP WHERE id = :i"),
            {"i": trashed.id},
        )
        db_session.commit()
        return ids

    def test_delete_moves_bookmarks_and_promotes_children(self, svc, db_session):
        [work, dev] = self._make_with_bookmarks(svc, db_session)
        [资料] = create_chain(svc, db_session, ["资料"])
        tree = svc.tree()
        svc.delete(dev, 资料, tree.require(dev).category.version, get_tree_revision(db_session))
        db_session.commit()
        moved = db_session.execute(
            text("SELECT category_id FROM bookmarks WHERE category_id = :t"), {"t": 资料}
        ).all()
        assert len(moved) == 3  # 正常 2 + 回收站 1 全部迁移
        assert db_session.get(Category, dev) is None
        assert db_session.get(Category, work) is not None

    def test_delete_root_promotes_children_to_root(self, svc, db_session):
        [work, dev] = self._make_with_bookmarks(svc, db_session)
        tree = svc.tree()
        svc.delete(work, None, tree.require(work).category.version, get_tree_revision(db_session))
        db_session.commit()
        promoted = db_session.get(Category, dev)
        assert promoted.parent_id is None

    def test_delete_keeps_grandchildren(self, svc, db_session):
        ids = create_chain(svc, db_session, ["A", "B", "C"])
        tree = svc.tree()
        svc.delete(
            ids[1], None, tree.require(ids[1]).category.version, get_tree_revision(db_session)
        )
        db_session.commit()
        assert db_session.get(Category, ids[0]) is not None
        assert db_session.get(Category, ids[1]) is None
        grand = db_session.get(Category, ids[2])
        assert grand is not None and grand.parent_id == ids[0]

    def test_delete_target_self_rejected(self, svc, db_session):
        [work] = create_chain(svc, db_session, ["工作"])
        tree = svc.tree()
        with pytest.raises(AppError):
            svc.delete(
                work, work, tree.require(work).category.version, get_tree_revision(db_session)
            )

    def test_delete_stale_revision(self, svc, db_session):
        """期望 tree revision 过期时整个操作抛 409；本事务内的部分修改被回滚。"""
        [work] = create_chain(svc, db_session, ["工作"])
        tree = svc.tree()
        with pytest.raises(AppError) as excinfo:
            svc.delete(
                work, None, tree.require(work).category.version, get_tree_revision(db_session) - 5
            )
        assert excinfo.value.status_code == 409
        db_session.rollback()
        assert db_session.get(Category, work) is not None  # 未被删除
        assert get_tree_revision(db_session) == 2  # revision 未因失败而增加


def _make_siblings(svc, db_session, names: list[str]) -> list[int]:
    """创建同一层级（根）下的多个兄弟分类。"""
    ids: list[int] = []
    for name in names:
        category = svc.create(name, None, get_tree_revision(db_session))
        db_session.flush()
        ids.append(category.id)
    _commit(db_session)
    return ids


class TestReorder:
    def test_reorder_siblings(self, svc, db_session):
        ids = _make_siblings(svc, db_session, ["A", "B", "C"])
        tree = svc.tree()
        ordered = [(cid, tree.require(cid).category.version) for cid in reversed(ids)]
        svc.reorder(None, ordered, get_tree_revision(db_session))
        db_session.commit()
        tree = svc.tree()
        assert tree.direct_children_ids(None) == list(reversed(ids))

    def test_reorder_mismatch_rejected(self, svc, db_session):
        ids = _make_siblings(svc, db_session, ["A", "B", "C"])
        tree = svc.tree()
        partial = [(ids[0], tree.require(ids[0]).category.version)]
        with pytest.raises(AppError):
            svc.reorder(None, partial, get_tree_revision(db_session))
        duplicated = [
            (ids[0], tree.require(ids[0]).category.version),
            (ids[0], tree.require(ids[0]).category.version),
            (ids[1], tree.require(ids[1]).category.version),
        ]
        with pytest.raises(AppError):
            svc.reorder(None, duplicated, get_tree_revision(db_session))

    def test_reorder_stale_version(self, svc, db_session):
        ids = _make_siblings(svc, db_session, ["A", "B"])
        tree = svc.tree()
        bad = [(ids[0], 999), (ids[1], tree.require(ids[1]).category.version)]
        with pytest.raises(AppError):
            svc.reorder(None, bad, get_tree_revision(db_session))

    def test_reorder_cannot_cross_parent(self, svc, db_session):
        [root, child] = create_chain(svc, db_session, ["根", "子"])
        tree = svc.tree()
        # 试图用排序把「子」伪装成根层排序项
        child_item = (child, tree.require(child).category.version)
        root_item = (root, tree.require(root).category.version)
        with pytest.raises(AppError):
            svc.reorder(None, [child_item, root_item], get_tree_revision(db_session))


class TestCounts:
    def test_counts_include_descendants_and_exclude_trash(self, svc, db_session):
        ids = create_chain(svc, db_session, ["工作", "开发"])
        work, dev = ids
        [other_root] = create_chain(svc, db_session, ["AI"])
        db_session.add_all(
            [
                Bookmark(title="a", url="https://a.com", normalized_url="n1", category_id=dev),
                Bookmark(title="b", url="https://b.com", normalized_url="n2", category_id=work),
                Bookmark(
                    title="c", url="https://c.com", normalized_url="n3", category_id=other_root
                ),
                Bookmark(title="d", url="https://d.com", normalized_url="n4", category_id=None),
            ]
        )
        db_session.commit()
        trashed = Bookmark(title="t", url="https://t.com", normalized_url="n5", category_id=dev)
        db_session.add(trashed)
        db_session.commit()
        db_session.execute(
            text("UPDATE bookmarks SET deleted_at = CURRENT_TIMESTAMP WHERE id = :i"),
            {"i": trashed.id},
        )
        db_session.commit()
        totals, uncategorized = svc.counts()
        assert totals[work] == 2  # dev 1 + work 1
        assert totals[dev] == 1
        assert totals[other_root] == 1
        assert uncategorized == 1
        # 包含回收站时统计口径变化（回收站不计入默认口径）
        totals_with_trash, _ = svc.counts(include_deleted=True)
        assert totals_with_trash[dev] == 2


class TestBrokenDataDefense:
    def test_cycle_marked_broken(self, db_session):
        """历史坏数据（环）不得使构树无限递归或进入正常树。"""
        a = Category(name="A", normalized_name="a", parent_id=None)
        b = Category(name="B", normalized_name="b")
        db_session.add_all([a, b])
        db_session.commit()
        # 绕过服务直接构造环：a.parent -> b, b.parent -> a
        db_session.execute(
            text("UPDATE categories SET parent_id = :p WHERE id = :i"), {"p": b.id, "i": a.id}
        )
        db_session.execute(
            text("UPDATE categories SET parent_id = :p WHERE id = :i"), {"p": a.id, "i": b.id}
        )
        db_session.commit()
        db_session.expire_all()  # 丢弃身份映射里的旧 parent_id
        tree = build_tree(list(db_session.scalars(select(Category).order_by(Category.id))))
        assert tree.has_broken
        assert {a.id, b.id} <= tree.broken
        # 正常分类不受影响
        c = Category(name="C", normalized_name="c", parent_id=None)
        db_session.add(c)
        db_session.commit()
        tree = build_tree(list(db_session.scalars(select(Category).order_by(Category.id))))
        assert tree.require(c.id).depth == 1
