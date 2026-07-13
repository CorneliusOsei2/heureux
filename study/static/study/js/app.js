/* Heureux — front-end behaviour: theme, nav, and the review session. */
(function () {
  "use strict";

  /* ---------- Theme toggle ---------- */
  var root = document.documentElement;
  function setTheme(name) {
    root.setAttribute("data-theme", name);
    try { localStorage.setItem("theme", name); } catch (e) {}
  }
  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var current = root.getAttribute("data-theme") === "dark" ? "dark" : "light";
      setTheme(current === "dark" ? "light" : "dark");
    });
  });

  /* ---------- Mobile nav ---------- */
  var toggle = document.querySelector("[data-nav-toggle]");
  var links = document.querySelector("[data-nav-links]");
  if (toggle && links) {
    toggle.addEventListener("click", function () {
      var open = links.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  /* ---------- Service worker (PWA) ---------- */
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  /* ---------- Install prompt (PWA) ---------- */
  (function () {
    var installBtn = document.querySelector("[data-install-app]");
    if (!installBtn) return;
    function isStandalone() {
      return window.matchMedia("(display-mode: standalone)").matches ||
        navigator.standalone === true;
    }
    function isIOS() {
      return /iphone|ipad|ipod/i.test(navigator.userAgent) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    }
    if (isStandalone()) { return; } // already installed — nothing to offer

    var deferred = null;
    window.addEventListener("beforeinstallprompt", function (e) {
      e.preventDefault();
      deferred = e;
      installBtn.hidden = false;
    });
    window.addEventListener("appinstalled", function () {
      deferred = null;
      installBtn.hidden = true;
    });
    installBtn.addEventListener("click", function () {
      if (deferred) {
        deferred.prompt();
        deferred.userChoice.then(function () {
          deferred = null;
          installBtn.hidden = true;
        });
      } else if (isIOS()) {
        alert("Pour installer Heureux : appuyez sur le bouton Partager, puis « Sur l'écran d'accueil ».");
      }
    });
    // iOS Safari never fires beforeinstallprompt — surface manual instructions.
    if (isIOS()) { installBtn.hidden = false; }
  })();

  /* ---------- Review session ---------- */
  var app = document.getElementById("review-app");
  if (!app) return;

  var nextUrl = app.dataset.nextUrl;
  var answerUrl = app.dataset.answerUrl;
  var undoUrl = app.dataset.undoUrl;
  var suspendUrl = app.dataset.suspendUrl;
  var csrf = app.dataset.csrf;
  var scope = {};
  try { scope = JSON.parse(app.dataset.scope || "{}"); } catch (e) {}

  var frontEl = document.getElementById("card-front");
  var backEl = document.getElementById("card-back");
  var revealBtn = document.getElementById("reveal");
  var gradesEl = document.getElementById("grades");
  var kbdHint = document.getElementById("kbd-hint");
  var cardZone = document.getElementById("card-zone");
  var doneZone = document.getElementById("done-zone");
  var progressEl = document.getElementById("progress");
  var undoBtn = document.getElementById("undo-btn");
  var undoBtnDone = document.getElementById("undo-btn-done");
  var suspendBtn = document.getElementById("suspend-btn");
  var summaryEl = document.getElementById("session-summary");
  var counters = {
    new: document.getElementById("c-new"),
    learn: document.getElementById("c-learn"),
    review: document.getElementById("c-review")
  };

  var currentId = null;
  var revealed = false;
  var startTime = 0;
  var reviewed = 0;
  var again = 0;
  var sumElapsed = 0;
  var history = [];
  var canUndo = app.dataset.canUndo === "1";
  var busy = false;

  function params(extra) {
    var p = new URLSearchParams();
    Object.keys(scope).forEach(function (k) { p.append(k, scope[k]); });
    if (extra) Object.keys(extra).forEach(function (k) { p.append(k, extra[k]); });
    return p;
  }

  function setCanUndo(flag) {
    canUndo = !!flag;
    if (undoBtn) undoBtn.disabled = !canUndo || busy;
    if (undoBtnDone) undoBtnDone.disabled = !canUndo || busy;
  }

  function updateCounters(c) {
    if (!c) return;
    if (counters.new) counters.new.textContent = c.new_available;
    if (counters.learn) counters.learn.textContent = c.learning_due;
    if (counters.review) counters.review.textContent = c.review_due;
    var remaining = c.total_due;
    var total = reviewed + remaining;
    var pct = total > 0 ? Math.round((reviewed / total) * 100) : 100;
    if (progressEl) progressEl.style.width = pct + "%";
    if (typeof c.can_undo !== "undefined") setCanUndo(c.can_undo);
  }

  function setIntervals(previews) {
    ["1", "2", "3", "4"].forEach(function (r) {
      var el = gradesEl.querySelector('[data-int="' + r + '"]');
      if (el) el.textContent = previews[r] || "";
    });
  }

  function fmtTime(ms) {
    var s = Math.round(ms / 1000);
    if (s < 60) return s + " s";
    var m = Math.round(s / 60);
    return m + " min";
  }

  function showDone(c) {
    cardZone.classList.add("hidden");
    doneZone.classList.remove("hidden");
    currentId = null;
    if (summaryEl) {
      if (reviewed === 0) {
        summaryEl.textContent = "Aucune carte révisée dans cette session.";
      } else {
        var pct = Math.round((100 * (reviewed - again)) / reviewed);
        summaryEl.innerHTML =
          "<strong>" + reviewed + "</strong> carte" + (reviewed > 1 ? "s" : "") +
          " · <strong>" + pct + "&nbsp;%</strong> réussi · " + fmtTime(sumElapsed);
      }
    }
    updateCounters(c);
    if (progressEl) progressEl.style.width = "100%";
  }

  function renderCard(data) {
    currentId = data.card_id;
    revealed = false;
    frontEl.innerHTML = data.front_html;
    backEl.innerHTML = data.back_html;
    backEl.classList.add("hidden");
    revealBtn.classList.remove("hidden");
    gradesEl.classList.add("hidden");
    setIntervals(data.previews);
    updateCounters(data.counts);
    kbdHint.innerHTML = "Appuyez sur <kbd>Espace</kbd> pour révéler la réponse";
    startTime = Date.now();
  }

  function handleState(data) {
    if (data.done) { showDone(data.counts); return; }
    cardZone.classList.remove("hidden");
    doneZone.classList.add("hidden");
    renderCard(data);
  }

  function reveal() {
    if (revealed) return;
    revealed = true;
    backEl.classList.remove("hidden");
    revealBtn.classList.add("hidden");
    gradesEl.classList.remove("hidden");
    kbdHint.innerHTML =
      "<kbd>1</kbd> Encore &nbsp; <kbd>2</kbd> Difficile &nbsp; " +
      "<kbd>3</kbd> Correct &nbsp; <kbd>4</kbd> Facile &nbsp;·&nbsp; " +
      "<kbd>u</kbd> annuler &nbsp; <kbd>s</kbd> suspendre";
  }

  function grade(rating) {
    if (!revealed || busy || currentId === null) return;
    busy = true;
    var elapsed = Date.now() - startTime;
    var body = params({ card_id: currentId, rating: rating, elapsed_ms: elapsed });
    fetch(answerUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrf,
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: body.toString()
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        reviewed += 1;
        if (rating === 1) again += 1;
        sumElapsed += elapsed;
        history.push({ rating: rating, elapsed: elapsed });
        busy = false;
        handleState(data);
      })
      .catch(function () {
        busy = false;
        kbdHint.textContent = "Erreur réseau — réessayez.";
      });
  }

  function undo() {
    if (busy || !canUndo) return;
    busy = true;
    fetch(undoUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrf,
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: params().toString()
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        busy = false;
        if (data.undone && history.length) {
          var last = history.pop();
          reviewed = Math.max(0, reviewed - 1);
          if (last.rating === 1) again = Math.max(0, again - 1);
          sumElapsed = Math.max(0, sumElapsed - last.elapsed);
        }
        handleState(data);
      })
      .catch(function () {
        busy = false;
        kbdHint.textContent = "Erreur réseau — réessayez.";
      });
  }

  function suspend() {
    if (busy || currentId === null) return;
    busy = true;
    var body = params({ card_id: currentId });
    fetch(suspendUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrf,
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: body.toString()
    })
      .then(function (r) { return r.json(); })
      .then(function (data) { busy = false; handleState(data); })
      .catch(function () {
        busy = false;
        kbdHint.textContent = "Erreur réseau — réessayez.";
      });
  }

  function loadNext() {
    fetch(nextUrl + "?" + params().toString(), {
      headers: { "X-Requested-With": "fetch" }
    })
      .then(function (r) { return r.json(); })
      .then(handleState)
      .catch(function () {
        kbdHint.textContent = "Erreur de chargement.";
      });
  }

  revealBtn.addEventListener("click", reveal);
  gradesEl.querySelectorAll(".grade").forEach(function (btn) {
    btn.addEventListener("click", function () {
      grade(parseInt(btn.dataset.rating, 10));
    });
  });
  if (undoBtn) undoBtn.addEventListener("click", undo);
  if (undoBtnDone) undoBtnDone.addEventListener("click", undo);
  if (suspendBtn) suspendBtn.addEventListener("click", suspend);

  document.addEventListener("keydown", function (e) {
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === "u") {
      e.preventDefault();
      undo();
    } else if (e.key === "s" && currentId !== null) {
      e.preventDefault();
      suspend();
    } else if (!revealed && (e.code === "Space" || e.code === "Enter")) {
      e.preventDefault();
      reveal();
    } else if (revealed && ["1", "2", "3", "4"].indexOf(e.key) !== -1) {
      e.preventDefault();
      grade(parseInt(e.key, 10));
    }
  });

  setCanUndo(canUndo);
  loadNext();
})();
