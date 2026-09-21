/* Bookmark Manager V1 — 全局键盘快捷键（BM-V1-705）。
   规则：
   - Ctrl+K：聚焦当前页主搜索框（页面通过 [data-shortcut-search] 声明；首页/书签页）；
   - N：在书签页新增（[data-shortcut-new] 元素触发）；
   - Esc：交由 Bootstrap Modal 处理，这里仅避免与页面其它 Esc 行为竞争；
   - 焦点位于可编辑控件、或中文输入法组合输入期间，不触发任何快捷键；
   - 其它带修饰键组合一律交给浏览器/辅助技术。 */
(function () {
  "use strict";

  function isEditable(target) {
    if (!target) {
      return false;
    }
    var tag = target.tagName;
    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      target.isContentEditable === true
    );
  }

  function modalOpen() {
    return document.querySelector(".modal.show") !== null;
  }

  document.addEventListener("keydown", function (event) {
    // 中文输入法组合输入：Ctrl+K / N 可能伴随组合状态，一律忽略
    if (event.isComposing || event.key === "Process") {
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "k") {
        var target = document.querySelector("[data-shortcut-search]");
        if (target && !isEditable(event.target)) {
          event.preventDefault();
          target.focus();
          target.select();
        }
      }
      return; // 其余带修饰键组合不处理
    }
    if (event.key === "Escape") {
      if (modalOpen()) {
        event.stopPropagation();
      }
      return;
    }
    if (isEditable(event.target)) {
      return;
    }
    if (event.key.toLowerCase() === "n") {
      if (modalOpen()) {
        return;
      }
      var newTrigger = document.querySelector("[data-shortcut-new]");
      if (newTrigger) {
        event.preventDefault();
        newTrigger.click();
      }
    }
  });
})();
