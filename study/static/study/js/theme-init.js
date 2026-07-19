(function () {
  "use strict";
  try {
    var saved = localStorage.getItem("theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    var collectionView = localStorage.getItem("collectionViewMode");
    if (collectionView === "cards" || collectionView === "table") {
      document.documentElement.setAttribute(
        "data-collection-view-mode",
        collectionView
      );
    }
  } catch (error) {
    // Storage can be unavailable in private browsing; defaults remain safe.
  }
})();
