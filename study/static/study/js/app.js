/* Réviser — front-end behaviour: theme, nav, and the review session. */
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

  /* ---------- Review session ---------- */
  var app = document.getElementById("review-app");
  if (!app) return;

  var nextUrl = app.dataset.nextUrl;
  var answerUrl = app.dataset.answerUrl;
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
  var counters = {
    new: document.getElementById("c-new"),
    learn: document.getElementById("c-learn"),
    review: document.getElementById("c-review")
  };

  var currentId = null;
  var revealed = false;
  var startTime = 0;
  var reviewed = 0;
  var busy = false;

  function params(extra) {
    var p = new URLSearchParams();
    Object.keys(scope).forEach(function (k) { p.append(k, scope[k]); });
    if (extra) Object.keys(extra).forEach(function (k) { p.append(k, extra[k]); });
    return p;
  }

  function updateCounters(c) {
    if (counters.new) counters.new.textContent = c.new_available;
    if (counters.learn) counters.learn.textContent = c.learning_due;
    if (counters.review) counters.review.textContent = c.review_due;
    var remaining = c.total_due;
    var total = reviewed + remaining;
    var pct = total > 0 ? Math.round((reviewed / total) * 100) : 100;
    if (progressEl) progressEl.style.width = pct + "%";
  }

  function setIntervals(previews) {
    ["1", "2", "3", "4"].forEach(function (r) {
      var el = gradesEl.querySelector('[data-int="' + r + '"]');
      if (el) el.textContent = previews[r] || "";
    });
  }

  function showDone(c) {
    cardZone.classList.add("hidden");
    doneZone.classList.remove("hidden");
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
      "<kbd>3</kbd> Correct &nbsp; <kbd>4</kbd> Facile";
  }

  function grade(rating) {
    if (!revealed || busy || currentId === null) return;
    busy = true;
    var body = params({
      card_id: currentId,
      rating: rating,
      elapsed_ms: Date.now() - startTime
    });
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
        busy = false;
        handleState(data);
      })
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

  document.addEventListener("keydown", function (e) {
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (!revealed && (e.code === "Space" || e.code === "Enter")) {
      e.preventDefault();
      reveal();
    } else if (revealed && ["1", "2", "3", "4"].indexOf(e.key) !== -1) {
      e.preventDefault();
      grade(parseInt(e.key, 10));
    }
  });

  loadNext();
})();
