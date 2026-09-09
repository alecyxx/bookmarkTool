/* Bookmark Manager V1 — 批量选择与回收站操作（书签页/回收站页共用）。
   mode="bookmarks"：分类/标签/软删除；mode="trash"：恢复/永久删除/清空。 */
(function () {
  "use strict";

  var M = window.ManageUI;
  var MODE = "bookmarks";
  var selected = {}; // id -> version（当前页内存选择，翻页/筛选自动清空）
  var barVisible = false;

  function containerId() {
    return MODE === "trash" ? "#trash-list-container" : "#bookmark-list-container";
  }

  function listContainer() {
    return document.querySelector(containerId());
  }

  function count() {
    return Object.keys(selected).length;
  }

  function updateBar() {
    var bar = document.getElementById("bulk-bar");
    var label = document.getElementById("bulk-count");
    if (!bar) {
      return;
    }
    barVisible = count() > 0;
    bar.hidden = !barVisible;
    if (label) {
      label.textContent = "已选择 " + count() + " 项";
    }
  }

  function setSelected(id, version, on) {
    if (on) {
      selected[String(id)] = Number(version);
    } else {
      delete selected[String(id)];
    }
    updateBar();
  }

  function clearSelection() {
    selected = {};
    var container = listContainer();
    if (container) {
      container.querySelectorAll(".bm-select").forEach(function (box) {
        box.checked = false;
      });
      container.querySelectorAll(".is-selected").forEach(function (card) {
        card.classList.remove("is-selected");
      });
      var all = container.querySelector(".bm-select-all");
      if (all) {
        all.checked = false;
      }
    }
    updateBar();
  }

  function collectFromCheckboxes(container) {
    container.querySelectorAll(".bm-select:checked").forEach(function (box) {
      var row = box.closest("[data-bookmark-id]");
      if (row) {
        setSelected(row.getAttribute("data-bookmark-id"), row.getAttribute("data-version") || 1, true);
      }
    });
  }

  function itemsPayload() {
    return Object.keys(selected).map(function (id) {
      return [Number(id), Number(selected[id])];
    });
  }

  function refreshList() {
    var url = MODE === "trash" ? "/api/trash/list" + window.location.search : "/api/bookmarks" + window.location.search;
    return fetch(url, { headers: { "Accept": "text/html" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("refresh failed");
        }
        return response.text();
      })
      .then(function (html) {
        var container = listContainer();
        container.innerHTML = html;
        clearSelection();
      });
  }

  function postBulk(path, body, submitBtn) {
    if (submitBtn) {
      submitBtn.disabled = true; // 防止重复点击发送重复写
    }
    return M.fetchJson(path, { method: "POST", body: body })
      .then(function (result) {
        if (result.status === 200) {
          M.toast("操作成功");
          var modalEl = document.querySelector("#bulk-modal-root .modal");
          if (modalEl) {
            var instance = bootstrap.Modal.getInstance(modalEl);
            if (instance) {
              instance.hide();
            }
          }
          clearSelection();
          refreshList();
        } else if (result.status === 409) {
          M.toast("数据已变化，列表已刷新，请重新选择。", true);
          var staleModal = document.querySelector("#bulk-modal-root .modal");
          if (staleModal) {
            var staleInstance = bootstrap.Modal.getInstance(staleModal);
            if (staleInstance) {
              staleInstance.hide();
            }
          }
          refreshList();
        } else {
          var error = result.data.error || {};
          M.toast(error.message || "操作失败。", true);
          if (submitBtn) {
            submitBtn.disabled = false; // 可修正后立即重试
          }
        }
        return result.status;
      })
      .catch(function () {
        M.toast("网络错误，请重试。", true);
        if (submitBtn) {
          submitBtn.disabled = false; // 网络失败允许立即重试
        }
      });
  }

  function openModal(url) {
    return M.fetchHtml(url).then(function (html) {
      var root = document.querySelector("#bulk-modal-root");
      root.innerHTML = html;
      var modalEl = root.querySelector(".modal");
      var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
      modal.show();
      return { element: modalEl, instance: modal };
    });
  }

  function openCategory() {
    openModal("/api/bulk/category-modal").then(function (ctx) {
      document.getElementById("bulk-submit-btn").addEventListener("click", function () {
        var value = document.getElementById("bulk-category-select").value;
        postBulk(
          "/api/bookmarks/bulk-category",
          {
            items: itemsPayload(),
            category_id: value === "" ? null : Number(value),
          },
          document.getElementById("bulk-submit-btn")
        );
      });
    });
  }

  function openTags() {
    openModal("/api/bulk/tags-modal").then(function (ctx) {
      document.getElementById("bulk-submit-btn").addEventListener("click", function () {
        var raw = document.getElementById("bulk-tags-input").value;
        var tags = raw
          .split(",")
          .map(function (s) {
            return s.trim();
          })
          .filter(Boolean);
        postBulk(
          "/api/bookmarks/bulk-tags",
          { items: itemsPayload(), tags: tags },
          document.getElementById("bulk-submit-btn")
        );
      });
    });
  }

  function openConfirm(action, countValue) {
    openModal("/api/bulk/confirm-modal?action=" + action + "&count=" + countValue).then(function (ctx) {
      var modalEl = ctx.element;
      document.getElementById("bulk-submit-btn").addEventListener("click", function () {
        var word = modalEl.getAttribute("data-word");
        if (word) {
          var input = document.getElementById("bulk-confirm-input");
          if (!input || input.value.trim() !== word) {
            M.toast("请输入正确的确认词。", true);
            return;
          }
        }
        var endpoints = {
          delete: "/api/bookmarks/bulk-delete",
          restore: "/api/bookmarks/bulk-restore",
          permanent: "/api/bookmarks/bulk-permanent-delete",
        };
        postBulk(
          endpoints[action],
          { items: itemsPayload() },
          document.getElementById("bulk-submit-btn")
        );
      });
    });
  }

  function openEmptyTrash() {
    openModal("/api/bulk/confirm-modal?action=empty&count=0").then(function (ctx) {
      var modalEl = ctx.element;
      document.getElementById("bulk-submit-btn").addEventListener("click", function () {
        var input = document.getElementById("bulk-confirm-input");
        if (!input || input.value.trim() !== modalEl.getAttribute("data-word")) {
          M.toast("请输入正确的确认词。", true);
          return;
        }
        postBulk(
          "/api/trash/empty",
          { confirm: input.value.trim() },
          document.getElementById("bulk-submit-btn")
        );
      });
    });
  }

  function singleRestore(id, version) {
    M.fetchJson("/api/bookmarks/" + id + "/restore", { method: "POST", body: { version: version } }).then(function (
      result
    ) {
      if (result.status === 200) {
        M.toast("已恢复");
        refreshList();
      } else {
        var error = result.data.error || {};
        M.toast(error.message || "恢复失败。", true);
        refreshList();
      }
    });
  }

  function singlePermanent(id, version) {
    openModal("/api/bulk/confirm-modal?action=permanent&count=1").then(function (ctx) {
      var modalEl = ctx.element;
      document.getElementById("bulk-submit-btn").addEventListener("click", function () {
        var input = document.getElementById("bulk-confirm-input");
        if (!input || input.value.trim() !== modalEl.getAttribute("data-word")) {
          M.toast("请输入正确的确认词。", true);
          return;
        }
        M.fetchJson("/api/bookmarks/" + id + "/permanent", {
          method: "DELETE",
          body: { version: version },
        }).then(function (result) {
          if (result.status === 200) {
            var instance = bootstrap.Modal.getInstance(modalEl);
            if (instance) {
              instance.hide();
            }
            M.toast("已永久删除");
            refreshList();
          } else {
            var error = result.data.error || {};
            M.toast(error.message || "删除失败。", true);
            refreshList();
          }
        });
      });
    });
  }

  window.BulkUI = {
    init: function (mode) {
      MODE = mode || "bookmarks";
      document.addEventListener("change", function (event) {
        var target = event.target;
        if (target.classList && target.classList.contains("bm-select")) {
          var row = target.closest("[data-bookmark-id]");
          if (row) {
            setSelected(
              row.getAttribute("data-bookmark-id"),
              row.getAttribute("data-version") || 1,
              target.checked
            );
          }
          return;
        }
        if (target.classList && target.classList.contains("bm-select-all")) {
          var container = listContainer();
          if (!container) {
            return;
          }
          var on = target.checked;
          if (MODE === "bookmarks") {
            // 书签页：卡片点击选中（无勾选框），全选控制 .is-selected
            container.querySelectorAll("[data-bookmark-id]").forEach(function (card) {
              var id = card.getAttribute("data-bookmark-id");
              var version = card.getAttribute("data-version") || 1;
              if (on) {
                card.classList.add("is-selected");
                selected[String(id)] = Number(version);
              } else {
                card.classList.remove("is-selected");
                delete selected[String(id)];
              }
            });
            updateBar();
          } else {
            container.querySelectorAll(".bm-select").forEach(function (box) {
              box.checked = on;
            });
            if (on) {
              selected = {};
              collectFromCheckboxes(container);
            } else {
              clearSelection();
            }
            updateBar();
          }
        }
      });

      document.addEventListener("click", function (event) {
        var target = event.target;
        if (target.closest && target.closest("[data-bulk-clear]")) {
          clearSelection();
          return;
        }
        if (target.closest && target.closest("[data-bulk-action]")) {
          var action = target.closest("[data-bulk-action]").getAttribute("data-bulk-action");
          if (action === "category") {
            openCategory();
          } else if (action === "tags") {
            openTags();
          } else if (action === "delete" || action === "restore" || action === "permanent") {
            openConfirm(action, count());
          } else if (action === "empty") {
            openEmptyTrash();
          }
          return;
        }
        if (MODE === "bookmarks") {
          // 书签页：点击卡片（非标题/标签链接、非操作按钮）切换选中
          var card = target.closest && target.closest("[data-bookmark-id]");
          if (card && !(target.closest && target.closest("a, button"))) {
            var id = card.getAttribute("data-bookmark-id");
            var version = card.getAttribute("data-version") || 1;
            var on = !card.classList.contains("is-selected");
            card.classList.toggle("is-selected", on);
            setSelected(id, version, on);
            return;
          }
        }
        if (target.closest && target.closest("[data-trash-restore]")) {
          var restoreBtn = target.closest("[data-trash-restore]");
          singleRestore(restoreBtn.getAttribute("data-id"), restoreBtn.getAttribute("data-version"));
          return;
        }
        if (target.closest && target.closest("[data-trash-permanent]")) {
          var permBtn = target.closest("[data-trash-permanent]");
          singlePermanent(permBtn.getAttribute("data-id"), permBtn.getAttribute("data-version"));
        }
      });

      // 筛选/翻页/列表重绘后选择自动清空（MutationObserver）
      var observed = listContainer();
      if (observed) {
        new MutationObserver(function () {
          clearSelection();
        }).observe(observed, { childList: true, subtree: true });
      }
      updateBar();
    },
    clearSelection: clearSelection,
    refreshList: refreshList,
  };
})();
