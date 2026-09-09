/* Bookmark Manager V1 — 标签管理页交互。 */
(function () {
  "use strict";

  var M = window.ManageUI;

  function refreshList() {
    return M.fetchHtml("/api/tags/list")
      .then(function (html) {
        var app = document.getElementById("tag-app");
        app.innerHTML = html;
      })
      .catch(function () {
        M.toast("列表刷新失败，请重试。", true);
      });
  }

  function bindTagForm(modalElement, editing, tagId) {
    var form = modalElement.querySelector("#tag-form");
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
      var payload = { name: form.querySelector('[name="name"]').value };
      var url = "/api/tags";
      var method = "POST";
      if (editing) {
        url = "/api/tags/" + tagId;
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
            M.toast(editing ? "标签已更新" : "标签已创建");
            refreshList();
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

  function openTagModal(url, editing, tagId) {
    M.fetchHtml(url)
      .then(function (html) {
        var ctx = M.loadModalInto("#tag-modal-root", html);
        bindTagForm(ctx.element, editing, tagId);
      })
      .catch(function () {
        M.toast("无法打开编辑窗口，请重试。", true);
      });
  }

  function openDeleteConfirm(tagId) {
    M.fetchHtml("/api/tags/" + tagId + "/delete-info")
      .then(function (html) {
        var ctx = M.loadModalInto("#tag-modal-root", html);
        var modalEl = ctx.element;
        var button = document.getElementById("confirm-delete-btn");
        button.addEventListener("click", function () {
          button.disabled = true;
          M.fetchJson("/api/tags/" + tagId, {
            method: "DELETE",
            body: { version: Number(modalEl.getAttribute("data-version")) },
          })
            .then(function (result) {
              if (result.status === 200) {
                ctx.instance.hide();
                M.toast("标签已删除");
                refreshList();
              } else if (result.status === 409) {
                M.toast("数据已变化，列表已刷新，请重新确认。", true);
                ctx.instance.hide();
                refreshList();
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

  window.TagUI = {
    init: function () {
      document.addEventListener("click", function (event) {
        var target = event.target.closest(
          "[data-tag-new],[data-tag-edit],[data-tag-delete]"
        );
        if (!target) {
          return;
        }
        if (target.hasAttribute("data-tag-new")) {
          openTagModal("/api/tags/new-modal", false, null);
        } else if (target.hasAttribute("data-tag-edit")) {
          var id = target.getAttribute("data-id");
          openTagModal("/api/tags/" + id + "/edit-modal", true, Number(id));
        } else if (target.hasAttribute("data-tag-delete")) {
          openDeleteConfirm(target.getAttribute("data-id"));
        }
      });
    },
    refreshList: refreshList,
  };
})();
