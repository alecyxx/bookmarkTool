/* Bookmark Manager V1 — 网络搜索首页（BM-V1-709/710）。
   隐私边界：搜索词只在本页构造第三方 URL；不发送到 FastAPI、不进入本站 URL/存储。
   引擎映射固定于代码；localStorage 只记录最近使用的引擎标识（不保存搜索词）。 */
(function () {
  "use strict";

  var STORAGE_KEY = "bm_web_engine";

  // 固定映射：仅这三个白名单目标（origin/path/参数名全部硬编码）
  var ENGINES = {
    google: { url: "https://www.google.com/search", param: "q" },
    bing: { url: "https://www.bing.com/search", param: "q" },
    baidu: { url: "https://www.baidu.com/s", param: "wd" },
  };
  var MAX_LENGTH = 500;

  function storageGet() {
    try {
      var value = window.localStorage.getItem(STORAGE_KEY);
      return value && ENGINES[value] ? value : null;
    } catch (e) {
      return null;
    }
  }

  function storageSet(value) {
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (e) {
      /* 存储不可用时忽略（隐私优先：失败也不记录） */
    }
  }

  function submit(form, input, errorBox) {
    var engine = form.querySelector('[name="engine"]:checked');
    var engineValue = engine ? engine.value : "google";
    if (!ENGINES[engineValue]) {
      engineValue = "google"; // 篡改/异常值回退默认，不产生任意目标
    }
    var query = input.value.trim();
    if (!query) {
      errorBox.textContent = "请输入搜索词。";
      input.focus();
      return;
    }
    if (query.length > MAX_LENGTH) {
      errorBox.textContent = "搜索词过长（最多 500 字符）。";
      input.focus();
      return;
    }
    errorBox.textContent = "";
    storageSet(engineValue);
    var target = ENGINES[engineValue];
    var url = new URL(target.url);
    url.searchParams.set(target.param, query); // 一次编码（中文/&/=/#/%/+/emoji）
    window.location.href = url.toString();
  }

  window.WebSearch = {
    init: function () {
      var form = document.getElementById("web-search-form");
      if (!form) {
        return;
      }
      var input = document.getElementById("web-search-input");
      var errorBox = document.getElementById("web-search-error");
      // 首次默认读取服务端 DEFAULT_WEB_SEARCH_ENGINE；此后优先最近使用的引擎
      var remembered = storageGet();
      if (remembered) {
        var radio = form.querySelector('[name="engine"][value="' + remembered + '"]');
        if (radio) {
          radio.checked = true;
        }
      }
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        submit(form, input, errorBox);
      });
      // 切换引擎即时清空错误提示
      form.querySelectorAll('[name="engine"]').forEach(function (radio) {
        radio.addEventListener("change", function () {
          errorBox.textContent = "";
        });
      });
    },
  };
})();
