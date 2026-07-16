(function () {
  "use strict";
  try {
    var saved = localStorage.getItem("theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (error) {
    // Storage can be unavailable in private browsing; the default theme is safe.
  }
})();
