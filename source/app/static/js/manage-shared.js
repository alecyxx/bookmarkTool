/* Bookmark Manager V1 — 管理页共享交互设施（Toast/CSRF/通用 Modal）。 */
(function () {
  "use strict";

  var CSRF_COOKIE = "bookmark_csrf";

  function getCookie(name) {
    var match = document.cookie.match("(?:^|; )" + name + "=([^;]*)");
    return match ? decodeURIComponent(match[1]) : null;
  }

  function csrfToken() {
    return getCookie(CSRF_COOKIE) || "";
  }

  function toast(message, isError) {
    var region = document.getElementById("toast-region");
    if (!region) {
      return;
    }
    var item = document.createElement("div");
    item.className = "toast-item" + (isError ? " toast-error" : " toast-ok");
    item.setAttribute("role", isError ? "alert" : "status");
    var text = document.createElement("span");
    text.className = "toast-text";
    text.textContent = message;
    item.appendChild(text);
    region.appendChild(item);
    if (isError) {
      // 错误提示常驻，直到用户手动关闭（避免只依赖自动消失的 Toast）
      var close = document.createElement("button");
      close.type = "button";
      close.className = "toast-close";
      close.setAttribute("aria-label", "关闭提示");
      close.textContent = "×";
      close.addEventListener("click", function () {
        item.remove();
      });
      item.appendChild(close);
    } else {
      setTimeout(function () {
        item.remove();
      }, 3500);
    }
    return item;
  }

  function fetchJson(url, options) {
    var method = (options && options.method) || "GET";
    var body = options && options.body;
    var headers = {
      "X-CSRF-Token": csrfToken(),
      "Accept": "application/json",
    };
    if (body !== undefined && body !== null) {
      headers["Content-Type"] = "application/json";
    }
    return fetch(url, {
      method: method,
      headers: headers,
      body: body === undefined || body === null ? undefined : JSON.stringify(body),
    }).then(function (response) {
      var contentType = response.headers.get("content-type") || "";
      if (contentType.indexOf("application/json") === 0 || contentType.indexOf("text/json") === 0) {
        return response.json().then(function (data) {
          return { status: response.status, data: data };
        });
      }
      // fragment / 纯文本响应：原样返回文本
      return response.text().then(function (text) {
        return { status: response.status, data: text };
      });
    });
  }

  function fetchHtml(url) {
    return fetch(url, { headers: { "Accept": "text/html" } }).then(function (response) {
      if (!response.ok) {
        throw new Error("html load failed");
      }
      return response.text();
    });
  }

  function loadModalInto(rootSelector, html) {
    var root = document.querySelector(rootSelector);
    root.innerHTML = html;
    var modalElement = root.querySelector(".modal");
    if (!modalElement) {
      throw new Error("no modal in fragment");
    }
    var modal = bootstrap.Modal.getOrCreateInstance(modalElement);
    modal.show();
    return { element: modalElement, instance: modal };
  }

  function showFieldErrors(form, fieldErrors) {
    form.querySelectorAll("[data-field-error]").forEach(function (el) {
      el.textContent = fieldErrors[el.getAttribute("data-field-error")] || "";
    });
  }

  function showNotice(form, message, isError) {
    var notice = form.querySelector(".modal-notice");
    if (!notice) {
      return;
    }
    notice.textContent = message || "";
    notice.className = "modal-notice" + (isError ? " notice-error" : " notice-info");
  }

  window.ManageUI = {
    getCookie: getCookie,
    csrfToken: csrfToken,
    toast: toast,
    fetchJson: fetchJson,
    fetchHtml: fetchHtml,
    loadModalInto: loadModalInto,
    showFieldErrors: showFieldErrors,
    showNotice: showNotice,
  };
})();
