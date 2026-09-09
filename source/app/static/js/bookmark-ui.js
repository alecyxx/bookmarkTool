/* Bookmark Manager V1 — 书签页交互（新增/编辑 Modal、收藏、列表刷新）。 */
(function () {
  "use strict";

  var LIST_CONTAINER = "#bookmark-list-container";
  var MODAL_ROOT = "#bookmark-modal-root";
  var csrfCookieName = "bookmark_csrf";

  function getCookie(name) {
    var match = document.cookie.match("(?:^|; )" + name + "=([^;]*)");
    return match ? decodeURIComponent(match[1]) : null;
  }

  function toast(message, isError) {
    var region = document.getElementById("toast-region");
    if (!region) {
      return;
    }
    var item = document.createElement("div");
    item.className = "toast-item" + (isError ? " toast-error" : " toast-ok");
    item.setAttribute("role", isError ? "alert" : "status");
    item.textContent = message;
    region.appendChild(item);
    setTimeout(function () {
      item.remove();
    }, 4000);
  }

  function refreshList() {
    return fetch("/api/bookmarks" + window.location.search, {
      headers: { "Accept": "text/html" },
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("list refresh failed");
        }
        return response.text();
      })
      .then(function (html) {
        var container = document.querySelector(LIST_CONTAINER);
        if (container) {
          container.innerHTML = html;
          bindDynamicActions(container);
        }
      });
  }

  function setFieldErrors(form, fieldErrors) {
    form.querySelectorAll("[data-field-error]").forEach(function (el) {
      el.textContent = fieldErrors[el.getAttribute("data-field-error")] || "";
    });
  }

  function clearFieldErrors(form) {
    form.querySelectorAll("[data-field-error]").forEach(function (el) {
      el.textContent = "";
    });
  }

  function setNotice(form, message, isError) {
    var notice = form.querySelector(".modal-notice");
    if (!notice) {
      return;
    }
    notice.textContent = message || "";
    notice.className = "modal-notice" + (isError ? " notice-error" : " notice-info");
  }

  function openModal(url) {
    return fetch(url, { headers: { "Accept": "text/html" } })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("modal load failed");
        }
        return response.text();
      })
      .then(function (html) {
        var root = document.querySelector(MODAL_ROOT);
        root.innerHTML = html;
        var modalElement = document.getElementById("bookmark-modal");
        var modal = new bootstrap.Modal(modalElement);
        modal.show();
        bindFormSubmit(modalElement);
      })
      .catch(function () {
        toast("无法打开编辑窗口，请重试。", true);
      });
  }

  function bindFormSubmit(modalElement) {
    var form = modalElement.querySelector("#bookmark-form");
    if (!form || form.dataset.bound) {
      return;
    }
    form.dataset.bound = "true";
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var mode = form.dataset.mode;
      var url = mode === "edit" ? "/api/bookmarks/" + form.dataset.id : "/api/bookmarks";
      var payload = {
        title: form.querySelector('[name="title"]').value,
        url: form.querySelector('[name="url"]').value,
        description: form.querySelector('[name="description"]').value,
        category_id: form.querySelector('[name="category_id"]').value || null,
        tags: form
          .querySelector('[name="tags"]')
          .value.split(",")
          .map(function (item) {
            return item.trim();
          })
          .filter(Boolean),
        is_favorite: form.querySelector('[name="is_favorite"]').checked,
      };
      var versionInput = form.querySelector('[name="version"]');
      if (versionInput) {
        url += "?version=" + encodeURIComponent(versionInput.value);
      }
      clearFieldErrors(form);
      setNotice(form, "", false);
      var submitButton = form.querySelector('[data-bm-submit]');
      if (submitButton) {
        submitButton.disabled = true;
      }
      fetch(url, {
        method: mode === "edit" ? "PUT" : "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": getCookie(csrfCookieName) || "",
        },
        body: JSON.stringify(payload),
      })
        .then(function (response) {
          return response.json().then(function (data) {
            return { status: response.status, data: data };
          });
        })
        .then(function (result) {
          if (result.status === 201 || result.status === 200) {
            var modal = bootstrap.Modal.getInstance(
              document.getElementById("bookmark-modal")
            );
            if (modal) {
              modal.hide();
            }
            toast(mode === "edit" ? "书签已保存" : "书签已新增");
            if (result.data.duplicate_active > 0 || result.data.duplicate_trashed > 0) {
              toast("提示：已有相同网址的书签。", false);
            }
            refreshList();
          } else if (result.status === 409) {
            setNotice(form, "数据已变化，请关闭窗口并重新加载后重试。", true);
          } else if (result.status === 422 || result.status === 400) {
            var error = result.data.error || {};
            setFieldErrors(form, error.field_errors || {});
            setNotice(form, error.message || "输入校验未通过。", true);
          } else if (result.status === 401 || result.status === 403) {
            setNotice(form, "会话已失效或校验未通过，请刷新页面。", true);
          } else {
            setNotice(form, "保存失败，请重试。", true);
          }
        })
        .catch(function () {
          setNotice(form, "网络错误：提交失败，请检查网络后重试。", true);
        })
        .finally(function () {
          if (submitButton) {
            submitButton.disabled = false;
          }
        });
    });
  }

  function bindDynamicActions(scope) {
    if (!scope) {
      return;
    }
    scope.querySelectorAll("[data-bm-edit]").forEach(function (button) {
      if (button.dataset.bound) {
        return;
      }
      button.dataset.bound = "true";
      button.addEventListener("click", function () {
        openModal("/api/bookmarks/" + button.getAttribute("data-id"));
      });
    });
    scope.querySelectorAll("[data-bm-favorite]").forEach(function (button) {
      if (button.dataset.bound) {
        return;
      }
      button.dataset.bound = "true";
      button.addEventListener("click", function () {
        button.disabled = true;
        var target = button.getAttribute("data-id");
        var version = button.getAttribute("data-version");
        var nextFavorite = button.getAttribute("aria-pressed") !== "true";
        fetch("/api/bookmarks/" + target + "/favorite", {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": getCookie(csrfCookieName) || "",
          },
          body: JSON.stringify({ version: Number(version), is_favorite: nextFavorite }),
        })
          .then(function (response) {
            return response.json().then(function (data) {
              return { status: response.status, data: data };
            });
          })
          .then(function (result) {
            if (result.status === 200) {
              button.setAttribute(
                "aria-pressed",
                result.data.is_favorite ? "true" : "false"
              );
              button.setAttribute("data-version", result.data.version);
              button.querySelector("span").textContent = result.data.is_favorite ? "★" : "☆";
              button.setAttribute(
                "aria-label",
                result.data.is_favorite ? "取消收藏" : "收藏"
              );
              if (window.location.search.indexOf("favorite=1") !== -1 && !result.data.is_favorite) {
                refreshList();
              }
            } else if (result.status === 409) {
              toast("数据已变化，列表已刷新。", true);
              refreshList();
            } else {
              toast("操作失败，请重试。", true);
            }
          })
          .catch(function () {
            toast("网络错误，请重试。", true);
          })
          .finally(function () {
            button.disabled = false;
          });
      });
    });
  }

  window.BookmarkUI = {
    init: function () {
      var scope = document;
      scope.querySelectorAll("[data-bm-new]").forEach(function (button) {
        if (button.dataset.bound) {
          return;
        }
        button.dataset.bound = "true";
        button.addEventListener("click", function () {
          openModal("/api/bookmarks/new-modal");
        });
      });
      bindDynamicActions(scope);
      // 筛选/搜索表单变更即提交（保持 URL 状态可刷新）
      var sortSelect = document.getElementById("sort-select");
      var searchForm = document.querySelector(".search-form");
      if (sortSelect) {
        sortSelect.addEventListener("change", function () {
          sortSelect.form.submit();
        });
      }
      if (searchForm) {
        searchForm.addEventListener("submit", function (event) {
          event.preventDefault();
          var value = searchForm.querySelector('[name="q"]').value.trim();
          var url = new URL(window.location.href);
          if (value) {
            url.searchParams.set("q", value);
          } else {
            url.searchParams.delete("q");
          }
          url.searchParams.delete("page");
          window.location.href = url.toString();
        });
      }
    },
    refreshList: refreshList,
    toast: toast,
  };
})();
