/* Bookmark Manager V1 — 分类管理页交互。 */
(function () {
  "use strict";

  var M = window.ManageUI;

  function refreshTree() {
    return M.fetchHtml("/api/categories/tree")
      .then(function (html) {
        var app = document.getElementById("category-app");
        app.innerHTML = html;
      })
      .catch(function () {
        M.toast("列表刷新失败，请重试。", true);
      });
  }

  function siblingContext(button) {
    var group = button.closest(".sib-order");
    var ids = JSON.parse(group.getAttribute("data-ids") || "[]");
    var versions = JSON.parse(group.getAttribute("data-versions") || "[]");
    var id = Number(button.getAttribute("data-id"));
    return {
      parentId: group.getAttribute("data-parent"),
      ids: ids,
      versions: versions,
      index: ids.indexOf(id),
    };
  }

  function moveSibling(button, up) {
    var ctx = siblingContext(button);
    if (ctx.index < 0 || (up && ctx.index === 0) || (!up && ctx.index === ctx.ids.length - 1)) {
      return; // 已在边界
    }
    var ids = ctx.ids.slice();
    var versions = ctx.versions.slice();
    var target = up ? ctx.index - 1 : ctx.index + 1;
    var tmpId = ids[ctx.index];
    ids[ctx.index] = ids[target];
    ids[target] = tmpId;
    var tmpV = versions[ctx.index];
    versions[ctx.index] = versions[target];
    versions[target] = tmpV;
    var ordered = ids.map(function (id, i) {
      return { id: id, version: versions[i] };
    });
    M.fetchJson("/api/categories/reorder", {
      method: "POST",
      body: {
        parent_id: ctx.parentId === "" ? null : Number(ctx.parentId),
        ordered: ordered,
        category_tree_revision: Number(group.getAttribute("data-revision") || 0),
      },
    })
      .then(function (result) {
        if (result.status === 200) {
          refreshTree();
        } else {
          var error = result.data.error || {};
          M.toast(error.message || "排序失败，请重试。", true);
          refreshTree(); // 版本可能已过期，强制刷新
        }
      })
      .catch(function () {
        M.toast("网络错误，请重试。", true);
      });
  }

  function bindCategoryForm(modalElement, editing, categoryId) {
    var form = modalElement.querySelector("#category-form");
    if (!form || form.dataset.bound) {
      return;
    }
    form.dataset.bound = "true";
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var submitBtn = form.querySelector('button[type="submit"]');
      if (submitBtn && submitBtn.disabled) {
        return;
      }
      if (submitBtn) {
        submitBtn.disabled = true;
      }
      var payload = {
        name: form.querySelector('[name="name"]').value,
        parent_id: form.querySelector('[name="parent_id"]').value || null,
        category_tree_revision: Number(
          form.querySelector('[name="category_tree_revision"]').value
        ),
      };
      var url = "/api/categories";
      var method = "POST";
      if (editing) {
        url = "/api/categories/" + categoryId;
        method = "PUT";
        payload.version = Number(form.querySelector('[name="version"]').value);
      }
      M.fetchJson(url, { method: method, body: payload })
        .then(function (result) {
          if (result.status === 200 || result.status === 201) {
            var instance = bootstrap.Modal.getInstance(modalElement);
            if (instance) {
              instance.hide();
            }
            M.toast(editing ? "分类已更新" : "分类已创建");
            refreshTree();
          } else if (result.status === 409 || result.status === 422) {
            var error = result.data.error || {};
            M.showFieldErrors(form, error.field_errors || {});
            M.showNotice(form, error.message || "保存失败。", true);
          } else {
            M.showNotice(form, "保存失败，请重试。", true);
            if (submitBtn) {
              submitBtn.disabled = false;
            }
          }
        })
        .catch(function () {
          M.showNotice(form, "网络错误，请重试。", true);
          if (submitBtn) {
            submitBtn.disabled = false;
          }
        });
    });
  }

  function openCategoryModal(url, editing, categoryId) {
    M.fetchHtml(url)
      .then(function (html) {
        var ctx = M.loadModalInto("#category-modal-root", html);
        bindCategoryForm(ctx.element, editing, categoryId);
      })
      .catch(function () {
        M.toast("无法打开编辑窗口，请重试。", true);
      });
  }

  function openDeleteConfirm(categoryId) {
    M.fetchHtml("/api/categories/" + categoryId + "/delete-info")
      .then(function (html) {
        var ctx = M.loadModalInto("#category-modal-root", html);
        var modalEl = ctx.element;
        var button = document.getElementById("confirm-delete-btn");
        button.addEventListener("click", function () {
          var payload = {
            version: Number(modalEl.getAttribute("data-version")),
            category_tree_revision: Number(modalEl.getAttribute("data-revision")),
            move_bookmarks_to_category_id: (function () {
              var value = document.getElementById("confirm-move-to").value;
              return value ? Number(value) : null;
            })(),
          };
          button.disabled = true;
          M.fetchJson("/api/categories/" + categoryId, {
            method: "DELETE",
            body: payload,
          })
            .then(function (result) {
              if (result.status === 200) {
                ctx.instance.hide();
                M.toast("分类已删除");
                refreshTree();
              } else if (result.status === 409) {
                M.toast("数据已变化，列表已刷新，请重新确认。", true);
                ctx.instance.hide();
                refreshTree();
              } else {
                var error = result.data.error || {};
                M.toast(error.message || "删除失败。", true);
              }
            })
            .catch(function () {
              M.toast("网络错误，请重试。", true);
              button.disabled = false;
            });
        });
      })
      .catch(function () {
        M.toast("无法加载删除确认，请重试。", true);
      });
  }

  function handleAction(target) {
    if (target.hasAttribute("data-cat-new")) {
      var parent = target.getAttribute("data-parent");
      openCategoryModal(
        "/api/categories/new-modal" + (parent ? "?parent=" + parent : ""),
        false,
        null
      );
    } else if (target.hasAttribute("data-cat-edit")) {
      var id = target.getAttribute("data-id");
      openCategoryModal("/api/categories/" + id + "/edit-modal", true, Number(id));
    } else if (target.hasAttribute("data-cat-delete")) {
      openDeleteConfirm(target.getAttribute("data-id"));
    } else if (target.hasAttribute("data-cat-move")) {
      moveSibling(target, target.getAttribute("data-up") === "1");
    }
  }

  window.CategoryUI = {
    init: function () {
      document.addEventListener("click", function (event) {
        var target = event.target.closest(
          "[data-cat-new],[data-cat-edit],[data-cat-delete],[data-cat-move]"
        );
        if (target) {
          handleAction(target);
        }
      });
    },
    refreshTree: refreshTree,
  };
})();
