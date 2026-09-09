"""分类树领域服务（BM-V1-106）。

- 加载树：一次性读取全部节点后在内存构树，自带环/孤儿防御，不无限递归；
- 读：祖先、后代、面包屑、深度、子树高度、含后代书签计数；
- 写：创建、改名、移动（整棵子树）、删除（只删当前节点 + 提升直接子分类 + 迁移直接书签）、同级排序；
- 约束：根为第 1 层最多 8 层；防移动至自身/后代；同级规范化唯一（不同父级允许同名）；
- 所有结构写操作通过 category_tree_revision CAS 串行化（先验证后 bump，同一事务内原子）。

表结构规则（设计文档 §24/31）：
- parent_id 只能由本服务修改；完整路径与深度根据父链计算，不持久化；
- 删除分类：先迁移全部直接关联书签（含回收站），再把直接子分类提升到当前父级，
  最后删除节点；任一步失败则整个事务回滚；不递归删除子树。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.errors import conflict, not_found, validation_error
from app.models.bookmark import Bookmark
from app.models.category import Category
from app.services.normalize import is_valid_length, normalize_name, strip_display_name
from app.services.versioned import apply_versioned_update, bump_tree_revision

MAX_TREE_DEPTH = 8
CATEGORY_NAME_MAX = 100


@dataclass
class CategoryNode:
    """内存树节点。depth：根为第 1 层。"""

    category: Category
    depth: int
    path: list[str]  # 根到自身的展示名称面包屑（含自身名称）
    subtree_height: int  # 以自身为根的子树最大高度（自身所在层计 1）
    children: list[CategoryNode] = field(default_factory=list)


class CategoryTree:
    """分类树只读快照（单次全量加载构树，查询无 N+1、无数据库递归）。"""

    def __init__(
        self,
        nodes: dict[int, CategoryNode],
        parent_map: dict[int, int | None],
        roots: list[CategoryNode],
        broken: set[int],
    ):
        self.nodes = nodes
        self.parent_map = parent_map
        self.roots = roots
        self.broken = broken

    @property
    def has_broken(self) -> bool:
        return bool(self.broken)

    def get(self, category_id: int) -> CategoryNode | None:
        return self.nodes.get(category_id)

    def require(self, category_id: int, message: str = "分类不存在。") -> CategoryNode:
        node = self.nodes.get(category_id)
        if node is None:
            raise not_found(message)
        return node

    def ancestor_ids(self, category_id: int) -> list[int]:
        """从根到父节点的祖先 id（不含自身）。"""
        node = self.nodes.get(category_id)
        if node is None:
            return []
        result: list[int] = []
        current = self.parent_map.get(category_id)
        while current is not None:
            result.append(current)
            current = self.parent_map.get(current)
        result.reverse()
        return result

    def ancestor_names(self, category_id: int) -> list[str]:
        """面包屑路径（不含自身名称，展示 '全部 / A / B' 用）。"""
        node = self.nodes.get(category_id)
        if node is None:
            return []
        return node.path[:-1]

    def descendant_ids(self, category_id: int, *, include_self: bool = True) -> set[int]:
        """全部后代分类 id（不含跨分类重复；include_self 时含自身）。"""
        start = self.nodes.get(category_id)
        if start is None:
            return set()
        result: set[int] = {start.category.id} if include_self else set()
        stack = list(start.children)
        while stack:
            node = stack.pop()
            result.add(node.category.id)
            stack.extend(node.children)
        return result

    def direct_children_ids(self, category_id: int | None) -> list[int]:
        """某父级（None=根层）下按 (sort_order, id) 排序的直接子节点 id。"""
        if category_id is None:
            nodes = self.roots
        else:
            parent = self.nodes.get(category_id)
            nodes = parent.children if parent else []
        return [
            node.category.id
            for node in sorted(nodes, key=lambda n: (n.category.sort_order, n.category.id))
        ]


def build_tree(categories: Sequence[Category]) -> CategoryTree:
    """内存构树。历史坏数据（环 / 悬空父引用）不进入正常树，记录到 broken。"""
    node_holders: dict[int, CategoryNode] = {}
    parent_of: dict[int, int | None] = {}
    categories_by_id: dict[int, Category] = {}
    for category in categories:
        categories_by_id[category.id] = category
        parent_of[category.id] = category.parent_id

    valid_ids = set(categories_by_id)
    broken: set[int] = set()

    def resolve_depth_path(start_id: int) -> tuple[int, list[str]] | None:
        """沿父链回溯计算深度与路径；visited 防环，缺失父引用判孤儿。"""
        chain: list[int] = []
        seen: set[int] = set()
        current = start_id
        while current is not None:
            if current in seen or current not in valid_ids:
                return None
            seen.add(current)
            chain.append(current)
            current = parent_of[current]
        chain.reverse()
        names = [categories_by_id[cid].name for cid in chain]
        return len(chain), names

    # 第一遍：计算深度/路径/高度，标记坏节点
    for category in categories:
        resolved = resolve_depth_path(category.id)
        if resolved is None:
            broken.add(category.id)
            continue
        depth, path = resolved
        node_holders[category.id] = CategoryNode(
            category=category, depth=depth, path=path, subtree_height=1
        )

    # 第二遍：组装父子（后序遍历求子树高度）
    def attach(parent_id: int | None) -> list[CategoryNode]:
        children: list[CategoryNode] = []
        for category_id, category in sorted(
            categories_by_id.items(), key=lambda item: (item[1].sort_order, item[1].id)
        ):
            if category.parent_id != parent_id or category_id in broken:
                continue
            child = node_holders[category_id]
            child.children = attach(category_id)
            height = 1
            for grandchild in child.children:
                if grandchild.subtree_height + 1 > height:
                    height = grandchild.subtree_height + 1
            child.subtree_height = height
            children.append(child)
        return children

    roots = attach(None)
    return CategoryTree(
        nodes=node_holders,
        parent_map={cid: parent_of[cid] for cid in categories_by_id},
        roots=roots,
        broken=broken,
    )


def _check_sibling_unique(
    tree: CategoryTree,
    parent_id: int | None,
    normalized: str,
    exclude_id: int | None = None,
) -> None:
    """同一父节点下的规范化重名校验；不同父节点允许同名。"""
    if parent_id is None:
        siblings = [node.category for node in tree.roots]
    else:
        parent = tree.nodes.get(parent_id)
        siblings = [child.category for child in parent.children] if parent else []
    for sibling in siblings:
        if sibling.id == exclude_id:
            continue
        if sibling.normalized_name == normalized:
            raise conflict("同一层级下已存在同名分类，请换一个名称。")


def _validate_name(name: str) -> str:
    display = strip_display_name(name)
    if not is_valid_length(display, CATEGORY_NAME_MAX):
        raise validation_error({"name": f"分类名称长度必须在 1～{CATEGORY_NAME_MAX} 字符之间。"})
    return display


def _new_sort_order(tree: CategoryTree, parent_id: int | None) -> int:
    ids = tree.direct_children_ids(parent_id)
    if not ids:
        return 0
    return max(tree.nodes[cid].category.sort_order for cid in ids) + 1


class CategoryService:
    """分类树写操作。每个方法在调用方提供的事务内完成，方法结束后不自行提交。"""

    def __init__(self, session: Session):
        self.session = session

    # ---------- 读 ----------

    def list_all(self) -> list[Category]:
        return list(self.session.scalars(select(Category).order_by(Category.id)))

    def tree(self) -> CategoryTree:
        return build_tree(self.list_all())

    def counts(self, *, include_deleted: bool = False) -> tuple[dict[int, int], int]:
        """返回 (各分类含后代的未删除书签总数, 未分类书签数)。

        只统计未进入回收站的书签；回收站数量不计入分类使用次数。
        """
        tree = self.tree()
        query = select(Bookmark.category_id, func.count(Bookmark.id)).group_by(Bookmark.category_id)
        if not include_deleted:
            query = query.where(Bookmark.deleted_at.is_(None))
        direct: dict[int, int] = defaultdict(int)
        uncategorized = 0
        for category_id, count in self.session.execute(query):
            if category_id is None:
                uncategorized += count
            elif category_id in tree.nodes:
                direct[category_id] += count
        totals: dict[int, int] = defaultdict(int)
        for category_id, count in direct.items():
            totals[category_id] += count
            current = tree.parent_map.get(category_id)
            while current is not None:
                totals[current] += count
                current = tree.parent_map.get(current)
        return dict(totals), uncategorized

    # ---------- 写 ----------

    def create(self, name: str, parent_id: int | None, expected_revision: int) -> Category:
        display = _validate_name(name)
        tree = self.tree()
        if parent_id is not None:
            parent = tree.require(parent_id, "父分类不存在。")
            if parent.depth >= MAX_TREE_DEPTH:
                raise conflict(f"分类最多支持 {MAX_TREE_DEPTH} 层。")
        normalized = normalize_name(display)
        _check_sibling_unique(tree, parent_id, normalized)
        category = Category(
            name=display,
            normalized_name=normalized,
            parent_id=parent_id,
            sort_order=_new_sort_order(tree, parent_id),
        )
        self.session.add(category)
        self.session.flush()
        bump_tree_revision(self.session, expected_revision)
        return category

    def rename(self, category_id: int, new_name: str, version: int, expected_revision: int) -> None:
        display = _validate_name(new_name)
        tree = self.tree()
        node = tree.require(category_id)
        normalized = normalize_name(display)
        _check_sibling_unique(tree, node.category.parent_id, normalized, exclude_id=category_id)
        apply_versioned_update(
            self.session,
            Category,
            category_id,
            version,
            {"name": display, "normalized_name": normalized},
        )
        bump_tree_revision(self.session, expected_revision)

    def update(
        self,
        category_id: int,
        name: str,
        parent_id: int | None,
        version: int,
        expected_revision: int,
    ) -> None:
        """名称与父节点组合更新（管理页整量提交）：单次 CAS + 单次 tree revision。

        同时执行改名与移动的校验：最终父级下的同级唯一、防环（不能移入自身/后代）、
        深度上限；任一不满足抛 409/422 且不修改数据。
        """
        display = _validate_name(name)
        tree = self.tree()
        node = tree.require(category_id)
        normalized = normalize_name(display)
        new_parent: int | None = None
        if parent_id is not None:
            target = tree.require(parent_id, "目标父分类不存在。")
            if target.category.id == category_id:
                raise conflict("不能移动到自身分类下。")
            if target.category.id in tree.descendant_ids(category_id, include_self=False):
                raise conflict("不能移动到自身的后代分类下。")
            if target.depth + node.subtree_height > MAX_TREE_DEPTH:
                raise conflict(f"移动后子树深度超过 {MAX_TREE_DEPTH} 层上限。")
            new_parent = target.category.id
        _check_sibling_unique(tree, new_parent, normalized, exclude_id=category_id)
        apply_versioned_update(
            self.session,
            Category,
            category_id,
            version,
            {"name": display, "normalized_name": normalized, "parent_id": new_parent},
        )
        bump_tree_revision(self.session, expected_revision)

    def move(
        self, category_id: int, new_parent_id: int | None, version: int, expected_revision: int
    ) -> None:
        """移动整棵子树。目标可以是任意现存节点或 None（提升为根）。"""
        tree = self.tree()
        node = tree.require(category_id)
        if new_parent_id is not None:
            target = tree.require(new_parent_id, "目标父分类不存在。")
            if target.category.id == category_id:
                raise conflict("不能移动到自身分类下。")
            # 防环：目标是本节点后代时，移动会把祖先挂到后代之下形成环
            if target.category.id in tree.descendant_ids(category_id, include_self=False):
                raise conflict("不能移动到自身的后代分类下。")
            if target.depth + node.subtree_height > MAX_TREE_DEPTH:
                raise conflict(f"移动后子树深度超过 {MAX_TREE_DEPTH} 层上限。")
            new_parent: int | None = target.category.id
            sibling_parent_id = target.category.id
        else:
            new_parent = None
            sibling_parent_id = None
            if node.category.parent_id is None:
                # 已是根分类且目标仍为根：no-op，但保持版本推进语义
                pass
        _check_sibling_unique(
            tree, sibling_parent_id, node.category.normalized_name, exclude_id=category_id
        )
        apply_versioned_update(
            self.session, Category, category_id, version, {"parent_id": new_parent}
        )
        bump_tree_revision(self.session, expected_revision)

    def delete(
        self,
        category_id: int,
        move_bookmarks_to_category_id: int | None,
        version: int,
        expected_revision: int,
    ) -> None:
        """只删除当前节点：直接书签迁移到目标分类/未分类，直接子分类提升到当前父级。

        不递归删除子树；提升冲突（坏数据防御）使整个事务回滚。
        """
        tree = self.tree()
        node = tree.require(category_id)
        if move_bookmarks_to_category_id == category_id:
            raise conflict("不能把书签迁移到正在删除的分类。")
        if move_bookmarks_to_category_id is not None:
            tree.require(move_bookmarks_to_category_id, "书签迁移目标分类不存在。")

        target_parent_id = node.category.parent_id

        # 1) 校验版本（在修改任何行之前，保证全事务失败语义清晰）
        apply_versioned_update(
            self.session,
            Category,
            category_id,
            version,
            {},  # 仅递增 version
        )

        # 2) 迁移直接关联书签（含回收站中的书签）
        self.session.execute(
            update(Bookmark)
            .where(Bookmark.category_id == category_id)
            .values(category_id=move_bookmarks_to_category_id)
        )

        # 3) 直接子分类提升到当前父级；防御性检查同级重名与深度
        direct_children = [child.category for child in node.children]
        for child in direct_children:
            _check_sibling_unique(
                tree, target_parent_id, child.normalized_name, exclude_id=category_id
            )
        for child in direct_children:
            child.parent_id = target_parent_id

        # 4) 删除节点（children 已提升，RESTRICT 外键放行）
        self.session.delete(node.category)
        self.session.flush()
        bump_tree_revision(self.session, expected_revision)

    def reorder(
        self,
        parent_id: int | None,
        ordered_items: list[tuple[int, int]],
        expected_revision: int,
    ) -> None:
        """同级排序：ordered_items 为 (id, version) 全量顺序，只调整同一父节点下的直接子节点。"""
        tree = self.tree()
        current_ids = tree.direct_children_ids(parent_id)
        submitted_ids = [item_id for item_id, _ in ordered_items]
        if len(submitted_ids) != len(set(submitted_ids)):
            raise validation_error({"items": "排序列表中存在重复分类。"})
        if set(submitted_ids) != set(current_ids):
            raise conflict("排序列表与本层分类不一致，请刷新后重试。")
        version_map = dict(ordered_items)
        for child_id in submitted_ids:
            node = tree.nodes.get(child_id)
            if node is None:
                raise conflict("排序列表中存在不存在的分类。")
            if node.category.version != version_map[child_id]:
                raise conflict(f"分类 {child_id} 已被其它会话修改，请重新加载后再试。")
        # 按用户提交顺序重排为连续整数（0..n-1）
        for index, child_id in enumerate(submitted_ids):
            self.session.execute(
                update(Category).where(Category.id == child_id).values(sort_order=index)
            )
        bump_tree_revision(self.session, expected_revision)
