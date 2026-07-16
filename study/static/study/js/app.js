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
  var utilityMenu = document.querySelector("[data-nav-more]");
  var mobileNavQuery = window.matchMedia("(max-width: 760px)");

  function setNavOpen(open, returnFocus) {
    if (!toggle || !links) return;
    links.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Fermer le menu" : "Ouvrir le menu");
    root.classList.toggle("nav-open", open && mobileNavQuery.matches);
    if (!open && utilityMenu) utilityMenu.removeAttribute("open");
    if (!open && returnFocus) toggle.focus();
  }

  if (toggle && links) {
    toggle.addEventListener("click", function () {
      setNavOpen(!links.classList.contains("is-open"), false);
    });
    links.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        setNavOpen(false, false);
      });
    });
  }

  if (utilityMenu) {
    utilityMenu.addEventListener("toggle", function () {
      if (utilityMenu.open && mobileNavQuery.matches) {
        window.setTimeout(function () {
          utilityMenu.scrollIntoView({ block: "nearest" });
        }, 0);
      }
    });
  }

  document.addEventListener("click", function (event) {
    if (utilityMenu && !utilityMenu.contains(event.target)) {
      utilityMenu.removeAttribute("open");
    }
    if (
      toggle &&
      links &&
      links.classList.contains("is-open") &&
      !links.contains(event.target) &&
      !toggle.contains(event.target)
    ) {
      setNavOpen(false, false);
    } else if (
      links &&
      links.classList.contains("is-open") &&
      event.target === links
    ) {
      setNavOpen(false, false);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (links && links.classList.contains("is-open")) {
      setNavOpen(false, true);
    } else if (utilityMenu && utilityMenu.open) {
      utilityMenu.removeAttribute("open");
      utilityMenu.querySelector("summary").focus();
    }
  });

  function closeNavAboveMobile() {
    if (!mobileNavQuery.matches) setNavOpen(false, false);
  }

  if (mobileNavQuery.addEventListener) {
    mobileNavQuery.addEventListener("change", closeNavAboveMobile);
  } else {
    mobileNavQuery.addListener(closeNavAboveMobile);
  }

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  /* ---------- Service worker (PWA) ---------- */
  if ("serviceWorker" in navigator) {
    var updateBanner = document.querySelector("[data-pwa-update-banner]");
    var updateButton = document.querySelector("[data-pwa-update]");
    var waitingRegistration = null;
    var reloadingForUpdate = false;
    var reloadOnControllerChange = false;

    function showWorkerUpdate(registration) {
      if (!navigator.serviceWorker.controller || !registration.waiting) return;
      waitingRegistration = registration;
      if (updateBanner) updateBanner.classList.remove("hidden");
    }

    if (updateButton) {
      updateButton.addEventListener("click", function () {
        if (!waitingRegistration || !waitingRegistration.waiting) return;
        updateButton.disabled = true;
        reloadOnControllerChange = true;
        waitingRegistration.waiting.postMessage({ type: "SKIP_WAITING" });
      });
    }
    navigator.serviceWorker.addEventListener("controllerchange", function () {
      if (!reloadOnControllerChange || reloadingForUpdate) return;
      reloadingForUpdate = true;
      window.location.reload();
    });
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" })
        .then(function (registration) {
          showWorkerUpdate(registration);
          registration.addEventListener("updatefound", function () {
            var worker = registration.installing;
            if (!worker) return;
            worker.addEventListener("statechange", function () {
              if (worker.state === "installed") {
                showWorkerUpdate(registration);
              }
            });
          });
          registration.update().catch(function () {});
        })
        .catch(function () {});
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
  var previousUrl = app.dataset.previousUrl;
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
  var summaryEl = document.getElementById("session-summary");
  var previousButton = document.getElementById("previous-card");
  var currentButton = document.getElementById("current-card");
  var previousLabel = document.getElementById("previous-card-label");
  var counters = {
    new: document.getElementById("c-new"),
    learn: document.getElementById("c-learn"),
    review: document.getElementById("c-review"),
    revisit: document.getElementById("c-revisit")
  };

  var currentId = null;
  var presentationToken = "";
  var revealed = false;
  var startTime = 0;
  var reviewed = 0;
  var revisited = 0;
  var sumElapsed = 0;
  var busy = false;
  var currentData = null;
  var currentView = null;
  var viewingPrevious = false;

  function params(extra) {
    var p = new URLSearchParams();
    Object.keys(scope).forEach(function (k) { p.append(k, scope[k]); });
    if (extra) Object.keys(extra).forEach(function (k) { p.append(k, extra[k]); });
    return p;
  }

  function updateCounters(c) {
    if (!c) return;
    if (counters.new) counters.new.textContent = c.new_available;
    if (counters.learn) counters.learn.textContent = c.learning_due;
    if (counters.review) counters.review.textContent = c.review_due;
    if (counters.revisit) counters.revisit.textContent = c.revisit_total;
    var remaining = c.total_due;
    var total = reviewed + remaining;
    var pct = total > 0 ? Math.round((reviewed / total) * 100) : 100;
    if (progressEl) progressEl.style.width = pct + "%";
  }

  function fmtTime(ms) {
    var s = Math.round(ms / 1000);
    if (s < 60) return s + " s";
    var m = Math.round(s / 60);
    return m + " min";
  }

  function setAnnotationRoots(sourceKey) {
    frontEl.dataset.annotationRoot = "";
    backEl.dataset.annotationRoot = "";
    frontEl.dataset.annotationSourceKey = sourceKey + ":front";
    backEl.dataset.annotationSourceKey = sourceKey + ":back";
  }

  function readJson(r) {
    return r.json().catch(function () { return {}; }).then(function (data) {
      if (r.status === 401 && data.login_url) {
        window.location.assign(data.login_url);
      }
      if (!r.ok) throw new Error(data.error || "Erreur de révision.");
      return data;
    });
  }

  function showDone(data) {
    var c = data.counts;
    cardZone.classList.add("hidden");
    doneZone.classList.remove("hidden");
    currentId = null;
    presentationToken = "";
    currentData = null;
    viewingPrevious = false;
    if (previousButton) previousButton.disabled = !data.can_previous;
    if (previousButton) previousButton.classList.remove("hidden");
    if (currentButton) currentButton.classList.add("hidden");
    if (previousLabel) previousLabel.classList.add("hidden");
    if (summaryEl) {
      if (reviewed === 0) {
        summaryEl.textContent = "Aucune carte révisée dans cette session.";
      } else {
        var correct = reviewed - revisited;
        var pct = Math.round((100 * correct) / reviewed);
        summaryEl.innerHTML =
          "<strong>" + reviewed + "</strong> carte" + (reviewed > 1 ? "s" : "") +
          " · <strong>" + pct + "&nbsp;%</strong> correct" +
          (revisited ? " · <strong>" + revisited + "</strong> à revoir" : "") +
          " · " + fmtTime(sumElapsed);
      }
    }
    updateCounters(c);
    if (progressEl) progressEl.style.width = "100%";
  }

  function renderCard(data) {
    currentData = data;
    currentId = data.card_id;
    presentationToken = data.presentation_token;
    revealed = false;
    frontEl.innerHTML = data.front_html;
    backEl.innerHTML = data.back_html;
    setAnnotationRoots(data.annotation_source_key);
    backEl.classList.add("hidden");
    revealBtn.classList.remove("hidden");
    gradesEl.classList.add("hidden");
    updateCounters(data.counts);
    kbdHint.innerHTML = "Appuyez sur <kbd>Espace</kbd> pour révéler la réponse";
    if (previousButton) previousButton.disabled = !data.can_previous;
    if (previousButton) previousButton.classList.remove("hidden");
    if (currentButton) currentButton.classList.add("hidden");
    if (previousLabel) previousLabel.classList.add("hidden");
    viewingPrevious = false;
    startTime = Date.now();
  }

  function handleState(data) {
    if (data.done) { showDone(data); return; }
    cardZone.classList.remove("hidden");
    doneZone.classList.add("hidden");
    renderCard(data);
  }

  function reveal() {
    if (revealed || viewingPrevious) return;
    revealed = true;
    backEl.classList.remove("hidden");
    revealBtn.classList.add("hidden");
    gradesEl.classList.remove("hidden");
    kbdHint.innerHTML =
      "<kbd>1</kbd> Revoir &nbsp; <kbd>2</kbd> Correct";
  }

  function grade(action) {
    if (!revealed || viewingPrevious || busy || currentId === null) return;
    busy = true;
    var elapsed = Date.now() - startTime;
    var body = params({
      card_id: currentId,
      action: action,
      elapsed_ms: elapsed,
      presentation_token: presentationToken
    });
    fetch(answerUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrf,
        "X-Requested-With": "fetch",
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: body.toString()
    })
      .then(readJson)
      .then(function (data) {
        reviewed += 1;
        if (action === "revisit") revisited += 1;
        sumElapsed += elapsed;
        busy = false;
        handleState(data);
      })
      .catch(function (error) {
        busy = false;
        kbdHint.textContent = error.message + " Rechargez la page.";
      });
  }

  function loadNext() {
    fetch(nextUrl + "?" + params().toString(), {
      headers: { "X-Requested-With": "fetch" }
    })
      .then(readJson)
      .then(handleState)
      .catch(function (error) {
        kbdHint.textContent = error.message;
      });
  }

  function viewPrevious() {
    var fromDone = !doneZone.classList.contains("hidden");
    if (
      busy ||
      viewingPrevious ||
      !previousUrl ||
      (!currentData && !fromDone) ||
      previousButton.disabled
    ) {
      return;
    }
    busy = true;
    fetch(previousUrl + "?" + params().toString(), {
      headers: { "X-Requested-With": "fetch" }
    })
      .then(readJson)
      .then(function (data) {
        currentView = {
          done: fromDone,
          revealed: revealed,
          startTime: startTime,
          pausedAt: Date.now()
        };
        viewingPrevious = true;
        if (fromDone) {
          doneZone.classList.add("hidden");
          cardZone.classList.remove("hidden");
        }
        frontEl.innerHTML = data.front_html;
        backEl.innerHTML = data.back_html;
        setAnnotationRoots(data.annotation_source_key);
        backEl.classList.remove("hidden");
        revealBtn.classList.add("hidden");
        gradesEl.classList.add("hidden");
        previousButton.classList.add("hidden");
        currentButton.classList.remove("hidden");
        currentButton.textContent = fromDone
          ? "Retour au résumé →"
          : "Retour à la carte actuelle →";
        previousLabel.classList.remove("hidden");
        kbdHint.textContent = "Consultation uniquement · votre carte actuelle est conservée.";
        busy = false;
      })
      .catch(function (error) {
        busy = false;
        kbdHint.textContent = error.message;
      });
  }

  function returnToCurrent() {
    if (!viewingPrevious || !currentView) return;
    if (currentView.done) {
      cardZone.classList.add("hidden");
      doneZone.classList.remove("hidden");
      previousButton.classList.remove("hidden");
      currentButton.classList.add("hidden");
      currentButton.textContent = "Retour à la carte actuelle →";
      previousLabel.classList.add("hidden");
      viewingPrevious = false;
      currentView = null;
      return;
    }
    if (!currentData) return;
    frontEl.innerHTML = currentData.front_html;
    backEl.innerHTML = currentData.back_html;
    setAnnotationRoots(currentData.annotation_source_key);
    revealed = currentView.revealed;
    startTime = currentView.startTime + (Date.now() - currentView.pausedAt);
    backEl.classList.toggle("hidden", !revealed);
    revealBtn.classList.toggle("hidden", revealed);
    gradesEl.classList.toggle("hidden", !revealed);
    previousButton.classList.remove("hidden");
    currentButton.classList.add("hidden");
    previousLabel.classList.add("hidden");
    kbdHint.innerHTML = revealed
      ? "<kbd>1</kbd> Revoir &nbsp; <kbd>2</kbd> Correct"
      : "Appuyez sur <kbd>Espace</kbd> pour révéler la réponse";
    viewingPrevious = false;
    currentView = null;
  }

  revealBtn.addEventListener("click", reveal);
  gradesEl.querySelectorAll(".grade").forEach(function (btn) {
    btn.addEventListener("click", function () {
      grade(btn.dataset.action);
    });
  });
  if (previousButton) previousButton.addEventListener("click", viewPrevious);
  if (currentButton) currentButton.addEventListener("click", returnToCurrent);

  document.addEventListener("keydown", function (e) {
    if (
      e.target &&
      e.target.closest &&
      e.target.closest(
        "input, textarea, select, button, a, [contenteditable='true'], [data-translation-panel], [data-note-panel]"
      )
    ) {
      return;
    }
    if (viewingPrevious) {
      return;
    }
    if (!revealed && (e.code === "Space" || e.code === "Enter")) {
      e.preventDefault();
      reveal();
    } else if (revealed && (e.key === "1" || e.key.toLowerCase() === "r")) {
      e.preventDefault();
      grade("revisit");
    } else if (revealed && (e.key === "2" || e.key.toLowerCase() === "c")) {
      e.preventDefault();
      grade("correct");
    }
  });

  loadNext();
})();
