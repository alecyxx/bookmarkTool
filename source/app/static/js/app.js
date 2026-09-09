/* Bookmark Manager V1 — 前端脚本
   能力：CSRF 头注入、JSON 表单提交（设置页）、Toast 提示（后续阶段扩展）。 */
(function () {
  "use strict";

  var CSRF_COOKIE = "bookmark_csrf";

  function getCookie(name) {
    var match = document.cookie.match("(?:^|; )" + name + "=([^;]*)");
    return match ? decodeURIComponent(match[1]) : null;
  }

  function csrfToken() {
    return getCookie(CSRF_COOKIE);
  }

  /* ---------- HTMX 全局请求注入 CSRF 头（所有写请求） ---------- */
  if (window.htmx) {
    document.addEventListener("htmx:configRequest", function (event) {
      var request = event.detail;
      if (request.method !== "GET" && request.method !== "HEAD" && request.method !== "OPTIONS") {
        request.headers["X-CSRF-Token"] = csrfToken() || "";
      }
    });
    // 4xx/5xx 响应交给页面内的错误区域展示，不插入正文
    document.addEventListener("htmx:beforeSwap", function (event) {
      var detail = event.detail;
      if (detail.isError && (detail.xhr.status === 401 || detail.xhr.status === 403)) {
        detail.shouldSwap = false;
      }
    });
  }

  /* ---------- 设置页 JSON PUT 表单（无刷新提交） ---------- */
  function showResult(targetSelector, html, isError) {
    var target = document.querySelector(targetSelector);
    if (!target) {
      return;
    }
    target.innerHTML = html;
    target.classList.toggle("settings-result-error", Boolean(isError));
    target.classList.toggle("settings-result-ok", !isError);
  }

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = String(value);
    return div.innerHTML;
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form.js-put-form").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var url = form.getAttribute("data-put");
        var resultSelector = "#" + form.getAttribute("data-result");
        var payload = {};
        var formData = new FormData(form);
        var button = form.querySelector('button[type="submit"]');
        formData.forEach(function (value, key) {
          payload[key] = value;
        });
        form.querySelectorAll("[data-field-error]").forEach(function (el) {
          el.textContent = "";
        });
        if (button) {
          button.disabled = true;
        }
        fetch(url, {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken() || "",
          },
          body: JSON.stringify(payload),
        })
          .then(function (response) {
            return response.json().then(function (data) {
              return { status: response.status, data: data };
            });
          })
          .then(function (result) {
            if (result.status >= 200 && result.status < 300) {
              showResult(
                resultSelector,
                '<span class="settings-result-ok-text">' +
                  escapeHtml(result.data.message || "已保存") +
                  "</span>",
                false
              );
            } else {
              var error = result.data.error || {};
              var fieldErrors = error.field_errors || {};
              Object.keys(fieldErrors).forEach(function (field) {
                var el = form.querySelector('[data-field-error="' + field + '"]');
                if (el) {
                  el.textContent = fieldErrors[field];
                }
              });
              showResult(
                resultSelector,
                escapeHtml(error.message || "操作失败，请重试。"),
                true
              );
            }
          })
          .catch(function () {
            showResult(
              resultSelector,
              "网络错误：提交失败，请检查网络后重试。",
              true
            );
          })
          .finally(function () {
            if (button) {
              button.disabled = false;
            }
          });
      });
    });
  });
})();
