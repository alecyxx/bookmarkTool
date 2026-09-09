/* Bookmark Manager V1 — CSP-safe page module initialization.
   This file replaces inline template scripts, which are intentionally blocked by script-src 'self'. */
(function () {
  "use strict";

  function init(moduleName, args) {
    var module = window[moduleName];
    if (!module || typeof module.init !== "function") {
      return;
    }
    try {
      module.init.apply(module, args || []);
    } catch (error) {
      console.error("Page module initialization failed: " + moduleName, error);
    }
  }

  if (document.getElementById("web-search-form")) {
    init("WebSearch");
  }
  if (document.getElementById("bookmark-list-container")) {
    init("BookmarkUI");
  }
  if (document.getElementById("category-app")) {
    init("CategoryUI");
  }
  if (document.getElementById("tag-app")) {
    init("TagUI");
  }
  if (document.getElementById("import-form")) {
    init("ImportExportUI");
  }
  if (document.getElementById("bulk-bar")) {
    init("BulkUI", [document.getElementById("trash-list-container") ? "trash" : "bookmarks"]);
  }
})();
