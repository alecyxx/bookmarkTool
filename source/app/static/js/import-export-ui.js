/* Bookmark Manager V1 — 导入导出页交互。 */
(function () {
  "use strict";

  var M = window.ManageUI;

  function root() {
    return document.getElementById("import-preview-root");
  }

  function setFragment(html) {
    root().innerHTML = html;
  }

  function reloadCard(jobId) {
    return M.fetchHtml("/api/imports/" + jobId + "/card").then(setFragment);
  }

  function jobIdFrom(rootEl) {
    var card = rootEl.querySelector("[data-job-id]");
    return card ? card.getAttribute("data-job-id") : null;
  }

  function submitForm() {
    var form = document.getElementById("import-form");
    var fileInput = document.getElementById("import-file");
    if (!fileInput.files.length) {
      M.toast("请先选择文件。", true);
      return;
    }
    var button = document.getElementById("import-preview-btn");
    var spinner = document.getElementById("import-spinner");
    button.disabled = true;
    spinner.hidden = false;
    var data = new FormData(form);
    data.append("csrf_token", M.csrfToken());
    fetch("/api/imports/preview", { method: "POST", body: data, headers: { "Accept": "text/html" } })
      .then(function (response) {
        if (response.status === 200) {
          return response.text().then(function (html) {
            setFragment(html);
            M.toast("预览已生成");
          });
        }
        if (response.status === 403) {
          M.toast("会话校验失败，请刷新页面后重试。", true);
          return null;
        }
        return response.json().then(function (payload) {
          var error = payload.error || {};
          M.toast(error.message || "解析失败。", true);
          return null;
        });
      })
      .catch(function () {
        M.toast("网络错误，请重试。", true);
      })
      .finally(function () {
        button.disabled = false;
        spinner.hidden = true;
      });
  }

  function changeOptions(card, jobId) {
    var duplicate = card.querySelector('[name="duplicate_policy"]:checked');
    var folder = card.querySelector('[name="folder_policy"]:checked');
    var payload = {
      duplicate_policy: duplicate ? duplicate.value : "skip",
      folder_policy: folder ? folder.value : "category",
    };
    M.fetchJson("/api/imports/" + jobId + "/options", { method: "PUT", body: payload })
      .then(function (result) {
        if (result.status === 200) {
          setFragment(result.data);
        } else {
          var error = result.data.error || {};
          M.toast(error.message || "策略修改失败。", true);
        }
      })
      .catch(function () {
        M.toast("网络错误，请重试。", true);
      });
  }

  function executeImport(card, jobId, button) {
    button.disabled = true;
    var status = document.getElementById("import-status");
    if (status) {
      status.textContent = "正在导入，请勿关闭页面……";
    }
    M.fetchJson("/api/imports/" + jobId + "/execute", { method: "POST", body: {} })
      .then(function (result) {
        if (result.status === 200) {
          M.toast("导入完成");
          return reloadCard(jobId);
        }
        var error = result.data.error || {};
        M.toast(error.message || "执行失败。", true);
        return reloadCard(jobId); // 失败摘要由服务端持久化后展示
      })
      .catch(function () {
        M.toast("网络错误，请重试。", true);
        button.disabled = false;
      });
  }

  function cancelImport(jobId) {
    M.fetchJson("/api/imports/" + jobId, { method: "DELETE" })
      .then(function (result) {
        if (result.status === 200) {
          M.toast("任务已取消");
          return reloadCard(jobId);
        }
        var error = result.data.error || {};
        M.toast(error.message || "取消失败。", true);
      })
      .catch(function () {
        M.toast("网络错误，请重试。", true);
      });
  }

  window.ImportExportUI = {
    init: function () {
      document.getElementById("import-form").addEventListener("submit", function (event) {
        event.preventDefault();
        submitForm();
      });
      // 策略单选变化即重新计算预览
      document.getElementById("import-preview-root").addEventListener("change", function (event) {
        if (event.target.name === "duplicate_policy" || event.target.name === "folder_policy") {
          var card = event.target.closest("[data-job-id]");
          if (card) {
            changeOptions(card, card.getAttribute("data-job-id"));
          }
        }
      });
      document.getElementById("import-preview-root").addEventListener("click", function (event) {
        var execute = event.target.closest("[data-import-execute]");
        if (execute) {
          var card = event.target.closest("[data-job-id]");
          if (card) {
            executeImport(card, card.getAttribute("data-job-id"), execute);
          }
          return;
        }
        var cancel = event.target.closest("[data-import-cancel]");
        if (cancel) {
          var cancelCard = event.target.closest("[data-job-id]");
          if (cancelCard) {
            cancelImport(cancelCard.getAttribute("data-job-id"));
          }
          return;
        }
        var dismiss = event.target.closest("[data-import-dismiss]");
        if (dismiss) {
          root().innerHTML = "";
        }
      });
    },
  };
})();
