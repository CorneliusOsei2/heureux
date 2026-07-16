(function () {
  "use strict";
  document.querySelectorAll("[data-print-recovery]").forEach(function (button) {
    button.addEventListener("click", function () {
      window.print();
    });
  });
})();
