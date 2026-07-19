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

  /* ---------- Collection view toggle ---------- */
  (function () {
    var toggles = Array.from(
      document.querySelectorAll("[data-collection-view-toggle]")
    );
    var collections = document.querySelectorAll("[data-collection-view]");
    if (!toggles.length || !collections.length) return;

    function scrollActiveAnnotationAnchor() {
      var anchorId = window.location.hash.slice(1);
      if (!anchorId) return;
      try { anchorId = decodeURIComponent(anchorId); } catch (e) {}

      document.querySelectorAll(".is-annotation-anchor").forEach(
        function (element) {
          element.classList.remove("is-annotation-anchor");
        }
      );
      var target = document.getElementById(anchorId);
      if (target && target.offsetParent === null) {
        target = Array.from(
          document.querySelectorAll("[data-annotation-anchor]")
        ).find(function (candidate) {
          return candidate.dataset.annotationAnchor === anchorId;
        });
      }
      if (!target || target.offsetParent === null) return;

      target.classList.add("is-annotation-anchor");
      window.requestAnimationFrame(function () {
        target.scrollIntoView({ block: "center" });
      });
    }

    function setCollectionView(mode, persist) {
      if (mode !== "cards" && mode !== "table") mode = "cards";
      root.setAttribute("data-collection-view-mode", mode);
      toggles.forEach(function (toggle) {
        toggle.querySelectorAll("[data-collection-view-option]").forEach(
          function (button) {
            button.setAttribute(
              "aria-pressed",
              button.dataset.collectionViewOption === mode ? "true" : "false"
            );
          }
        );
      });
      if (persist) {
        try { localStorage.setItem("collectionViewMode", mode); } catch (e) {}
      }
      scrollActiveAnnotationAnchor();
    }

    var initial =
      root.getAttribute("data-collection-view-mode") === "table"
        ? "table"
        : "cards";
    setCollectionView(initial, false);

    toggles.forEach(function (toggle) {
      toggle.addEventListener("click", function (event) {
        var button = event.target.closest("[data-collection-view-option]");
        if (!button || !toggle.contains(button)) return;
        setCollectionView(button.dataset.collectionViewOption, true);
      });
    });
    window.addEventListener("hashchange", scrollActiveAnnotationAnchor);
  })();

  /* ---------- Form dialogs ---------- */
  (function () {
    var dialogs = Array.from(document.querySelectorAll(".form-dialog"));
    if (!dialogs.length) return;

    function openDialog(dialog, trigger) {
      if (!dialog || dialog.open) return;
      dialog._returnFocus = trigger || document.activeElement;
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
      window.requestAnimationFrame(function () {
        var firstField = dialog.querySelector("input:not([type='hidden']), textarea");
        if (firstField) firstField.focus();
      });
    }

    function closeDialog(dialog) {
      if (!dialog || !dialog.open) return;
      if (typeof dialog.close === "function") {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
    }

    document.querySelectorAll("[data-dialog-open]").forEach(function (trigger) {
      trigger.addEventListener("click", function () {
        openDialog(
          document.getElementById(trigger.dataset.dialogOpen),
          trigger
        );
      });
    });

    dialogs.forEach(function (dialog) {
      dialog.querySelectorAll("[data-dialog-close]").forEach(function (button) {
        button.addEventListener("click", function () {
          closeDialog(dialog);
        });
      });
      dialog.addEventListener("click", function (event) {
        if (event.target === dialog) closeDialog(dialog);
      });
      dialog.addEventListener("close", function () {
        if (
          dialog._returnFocus &&
          dialog._returnFocus.isConnected
        ) {
          dialog._returnFocus.focus();
        }
      });
    });

    var editDialog = document.getElementById("note-edit-dialog");
    var editForm = editDialog
      ? editDialog.querySelector("[data-annotation-edit-form]")
      : null;
    var editError = editDialog
      ? editDialog.querySelector("[data-annotation-edit-error]")
      : null;
    function setEditError(message) {
      if (!editError) return;
      editError.textContent = message || "";
      editError.hidden = !message;
    }
    document.querySelectorAll("[data-annotation-edit]").forEach(
      function (button) {
        button.addEventListener("click", function () {
          if (!editDialog || !editForm) return;
          var annotationId = button.dataset.annotationEdit;
          var source = Array.from(
            document.querySelectorAll("[data-annotation-edit-source]")
          ).find(function (candidate) {
            return candidate.dataset.annotationEditSource === annotationId;
          });
          if (!source) return;
          editForm.action = button.dataset.annotationUpdateUrl;
          editForm.querySelector("[data-annotation-edit-title]").value =
            source.dataset.annotationEditTitle || "";
          editForm.querySelector("[data-annotation-edit-body]").value =
            source.dataset.annotationEditBody || "";
          setEditError("");
          openDialog(editDialog, button);
        });
      }
    );
    if (editForm && window.fetch) {
      editForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var submitButton =
          event.submitter || editForm.querySelector("[type='submit']");
        submitButton.disabled = true;
        setEditError("");
        fetch(editForm.action, {
          method: "POST",
          body: new FormData(editForm),
          credentials: "same-origin",
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "fetch"
          }
        })
          .then(function (response) {
            return response.json().then(
              function (payload) {
                return { response: response, payload: payload };
              },
              function () {
                throw new Error(
                  "Impossible de lire la réponse d’enregistrement."
                );
              }
            );
          })
          .then(function (result) {
            if (!result.response.ok) {
              throw new Error(
                result.payload.error ||
                "Impossible d’enregistrer cette note."
              );
            }
            if (!result.payload.redirect_url) {
              throw new Error("La réponse d’enregistrement est incomplète.");
            }
            var target = new URL(
              result.payload.redirect_url,
              window.location.origin
            );
            if (
              target.pathname === window.location.pathname &&
              target.search === window.location.search
            ) {
              window.location.hash = target.hash;
              window.location.reload();
            } else {
              window.location.assign(target.href);
            }
          })
          .catch(function (error) {
            submitButton.disabled = false;
            setEditError(error.message);
          });
      });
    }

    dialogs.forEach(function (dialog) {
      if (dialog.hasAttribute("data-dialog-open-on-load")) {
        openDialog(dialog);
      }
    });
  })();

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

  /* ---------- Subject vocabulary search ---------- */
  (function () {
    var input = document.querySelector("[data-subject-vocabulary-search]");
    var directory = document.querySelector("[data-subject-vocabulary-directory]");
    if (!input || !directory) return;

    var groups = Array.from(directory.querySelectorAll("[data-subject-theme]"));
    var rows = Array.from(directory.querySelectorAll("[data-subject-vocabulary-row]"));
    var status = document.querySelector("[data-subject-vocabulary-status]");
    var empty = document.querySelector("[data-subject-vocabulary-empty]");
    var searching = false;

    function normalized(value) {
      return value
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .trim();
    }

    function updateDirectory() {
      var query = normalized(input.value);
      if (query && !searching) {
        groups.forEach(function (group) {
          group.dataset.wasOpen = group.open ? "true" : "false";
        });
      }

      var visibleCount = 0;
      groups.forEach(function (group) {
        var groupRows = Array.from(
          group.querySelectorAll("[data-subject-vocabulary-row]")
        );
        var groupCount = 0;
        groupRows.forEach(function (row) {
          var matches = !query || normalized(row.textContent).includes(query);
          row.hidden = !matches;
          if (matches) {
            groupCount += 1;
            visibleCount += 1;
          }
        });
        group.hidden = groupCount === 0;
        if (query && groupCount) group.open = true;
      });

      if (!query && searching) {
        groups.forEach(function (group) {
          group.hidden = false;
          group.open = group.dataset.wasOpen === "true";
          delete group.dataset.wasOpen;
        });
      }
      searching = Boolean(query);

      if (status) {
        if (query) {
          var plural = visibleCount === 1 ? "" : "s";
          status.textContent =
            visibleCount + " sujet" + plural + " trouvé" + plural;
        } else {
          status.textContent = rows.length + " sujets";
        }
      }
      if (empty) empty.hidden = visibleCount !== 0;
    }

    input.addEventListener("input", updateDirectory);
  })();

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

  /* ---------- CO French audio ---------- */
  (function () {
    var readers = Array.from(
      document.querySelectorAll("[data-co-audio-reader]")
    );
    if (!readers.length) return;

    var synthesis = window.speechSynthesis;
    var Utterance = window.SpeechSynthesisUtterance;
    var frenchVoices = [];
    var feminineVoiceNames = [
      "amelie",
      "audrey",
      "aurelie",
      "caroline",
      "celine",
      "charlotte",
      "chloe",
      "claire",
      "denise",
      "elise",
      "eloise",
      "francoise",
      "hortense",
      "julie",
      "lea",
      "manon",
      "marie",
      "sandrine",
      "sylvie",
      "valerie",
      "virginie",
      "vivienne"
    ];
    var active = null;
    var playbackId = 0;

    function setStatus(reader, message) {
      var status = reader.querySelector("[data-co-audio-status]");
      if (status) status.textContent = message;
    }

    function setButtonLabel(button, label) {
      var node = button.querySelector("[data-co-audio-button-label]");
      if (node) node.textContent = label;
    }

    function resetReader(reader, message) {
      reader.classList.remove("is-playing", "is-paused");
      reader.querySelectorAll("[data-co-audio-play]").forEach(function (button) {
        setButtonLabel(button, button.dataset.coAudioName === "question"
          ? "Question"
          : "Dialogue");
        button.classList.remove("is-active");
        button.setAttribute("aria-pressed", "false");
        button.setAttribute(
          "aria-label",
          "Écouter " + (button.dataset.coAudioName === "question"
            ? "la question"
            : "le dialogue") + " en français"
        );
      });
      var stop = reader.querySelector("[data-co-audio-stop]");
      if (stop) stop.disabled = true;
      setStatus(reader, message || readyVoiceStatus());
    }

    function cancelPlayback(message) {
      var previous = active;
      active = null;
      playbackId += 1;
      synthesis.cancel();
      synthesis.resume();
      if (previous) resetReader(previous.reader, message);
    }

    function refreshVoices() {
      frenchVoices = synthesis.getVoices().filter(function (voice) {
        return /^fr(?:[-_]|$)/i.test(voice.lang || "");
      });
      readers.forEach(function (reader) {
        if (!active || active.reader !== reader) {
          setStatus(reader, readyVoiceStatus());
        }
      });
    }

    function normalizedVoiceName(voice) {
      return (voice.name || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase();
    }

    function isFeminineVoice(voice) {
      var name = normalizedVoiceName(voice);
      return feminineVoiceNames.some(function (candidate) {
        return name.indexOf(candidate) !== -1;
      });
    }

    function preferredVoice() {
      return frenchVoices.slice().sort(function (first, second) {
        function score(voice) {
          var language = (voice.lang || "").replace("_", "-").toLowerCase();
          var value = language === "fr-fr" ? 100 : 50;
          var name = normalizedVoiceName(voice);
          if (isFeminineVoice(voice)) value += 1000;
          else if (name.indexOf("google") !== -1) value += 500;
          if (voice.localService) value += 10;
          if (voice.default) value += 1;
          return value;
        }
        return score(second) - score(first);
      })[0] || null;
    }

    function readyVoiceStatus() {
      var voice = preferredVoice();
      if (!voice) return "Prêt à écouter · voix française";
      return (isFeminineVoice(voice) ? "Voix féminine · " : "Voix française · ")
        + voice.name;
    }

    function splitLongSegment(segment) {
      var chunks = [];
      var remainder = segment.trim();
      while (remainder.length > 220) {
        var splitAt = remainder.lastIndexOf(", ", 220);
        if (splitAt < 120) splitAt = remainder.lastIndexOf("; ", 220);
        if (splitAt < 120) splitAt = remainder.lastIndexOf(" ", 220);
        if (splitAt < 1) splitAt = 220;
        chunks.push(remainder.slice(0, splitAt + 1).trim());
        remainder = remainder.slice(splitAt + 1).trim();
      }
      if (remainder) chunks.push(remainder);
      return chunks;
    }

    function speechChunks(text) {
      var normalized = text
        .replace(/\u00a0/g, " ")
        .replace(/^\s*--\s*/, "")
        .replace(/\s+--\s+/g, ". ")
        .replace(/\s+/g, " ")
        .trim();
      if (!normalized) return [];

      var sentences;
      if (typeof Intl !== "undefined" && Intl.Segmenter) {
        var segmenter = new Intl.Segmenter("fr", { granularity: "sentence" });
        sentences = Array.from(segmenter.segment(normalized)).map(
          function (part) { return part.segment.trim(); }
        );
      } else {
        sentences = normalized.match(/[^.!?…]+(?:[.!?…]+|$)/g) || [normalized];
      }

      return sentences.reduce(function (chunks, sentence) {
        return chunks.concat(splitLongSegment(sentence));
      }, []).filter(Boolean);
    }

    function finishPlayback(message) {
      if (!active) return;
      var reader = active.reader;
      active = null;
      resetReader(reader, message || "Lecture terminée · cliquez pour réécouter");
    }

    function scheduleSpeech(id) {
      window.setTimeout(function () {
        if (!active || active.id !== id || active.state !== "playing") return;
        synthesis.resume();
        speakNext(id);
      }, 120);
    }

    function speakNext(id) {
      if (!active || active.id !== id || active.state !== "playing") return;
      if (active.index >= active.chunks.length) {
        finishPlayback();
        return;
      }

      var utterance = new Utterance(active.chunks[active.index]);
      var utteranceId = active.utteranceId + 1;
      active.utteranceId = utteranceId;
      var voice = preferredVoice();
      utterance.lang = "fr-FR";
      utterance.rate = active.rate;
      utterance.pitch = 1;
      if (voice) utterance.voice = voice;

      utterance.onend = function () {
        if (
          !active ||
          active.id !== id ||
          active.utteranceId !== utteranceId ||
          active.state !== "playing"
        ) return;
        active.index += 1;
        speakNext(id);
      };
      utterance.onerror = function () {
        if (
          !active ||
          active.id !== id ||
          active.utteranceId !== utteranceId ||
          active.state !== "playing"
        ) return;
        finishPlayback("Lecture indisponible. Vérifiez la voix de l’appareil.");
      };
      active.utterance = utterance;
      synthesis.speak(utterance);
    }

    function startPlayback(reader, button) {
      var target = button.dataset.coAudioTarget;
      var textNode = reader.querySelector(
        '[data-co-audio-text="' + target + '"]'
      );
      var chunks = speechChunks(textNode ? textNode.textContent : "");
      if (!chunks.length) {
        setStatus(reader, "Aucun texte français à lire.");
        return;
      }

      cancelPlayback();
      var rateControl = reader.querySelector("[data-co-audio-rate]");
      var rate = rateControl ? parseFloat(rateControl.value) : 1;
      var id = playbackId + 1;
      playbackId = id;
      active = {
        id: id,
        reader: reader,
        button: button,
        chunks: chunks,
        index: 0,
        rate: rate,
        state: "playing",
        utteranceId: 0
      };

      reader.classList.add("is-playing");
      button.classList.add("is-active");
      button.setAttribute("aria-pressed", "true");
      button.setAttribute(
        "aria-label",
        "Mettre en pause " + (target === "question"
          ? "la question"
          : "le dialogue")
      );
      setButtonLabel(button, "Pause");
      var stop = reader.querySelector("[data-co-audio-stop]");
      if (stop) stop.disabled = false;
      setStatus(
        reader,
        "Lecture de " + (target === "question" ? "la question" : "du dialogue")
          + " en français…"
      );
      scheduleSpeech(id);
    }

    function pausePlayback() {
      if (!active || active.state !== "playing") return;
      active.state = "paused";
      active.utteranceId += 1;
      synthesis.cancel();
      synthesis.resume();
      active.reader.classList.remove("is-playing");
      active.reader.classList.add("is-paused");
      setButtonLabel(active.button, "Reprendre");
      active.button.setAttribute("aria-label", "Reprendre la lecture");
      setStatus(active.reader, "Lecture en pause.");
    }

    function resumePlayback() {
      if (!active || active.state !== "paused") return;
      active.state = "playing";
      active.reader.classList.remove("is-paused");
      active.reader.classList.add("is-playing");
      setButtonLabel(active.button, "Pause");
      active.button.setAttribute("aria-label", "Mettre la lecture en pause");
      setStatus(active.reader, "Lecture reprise en français…");
      scheduleSpeech(active.id);
    }

    if (!synthesis || !Utterance) {
      readers.forEach(function (reader) {
        reader.classList.add("is-unavailable");
        setStatus(reader, "Audio français indisponible dans ce navigateur.");
      });
      return;
    }

    refreshVoices();
    if (synthesis.addEventListener) {
      synthesis.addEventListener("voiceschanged", refreshVoices);
    } else {
      synthesis.onvoiceschanged = refreshVoices;
    }

    readers.forEach(function (reader) {
      reader.querySelectorAll("[data-co-audio-play]").forEach(function (button) {
        button.disabled = false;
        button.addEventListener("click", function () {
          if (active && active.reader === reader && active.button === button) {
            if (active.state === "paused") resumePlayback();
            else pausePlayback();
            return;
          }
          startPlayback(reader, button);
        });
      });

      var stop = reader.querySelector("[data-co-audio-stop]");
      if (stop) {
        stop.addEventListener("click", function () {
          if (active && active.reader === reader) {
            cancelPlayback("Lecture arrêtée.");
          }
        });
      }

      var rate = reader.querySelector("[data-co-audio-rate]");
      if (rate) {
        rate.disabled = false;
        rate.addEventListener("change", function () {
          if (active && active.reader === reader) {
            cancelPlayback("Vitesse réglée sur " + rate.options[rate.selectedIndex].text + ".");
          } else {
            setStatus(
              reader,
              "Vitesse réglée sur " + rate.options[rate.selectedIndex].text + "."
            );
          }
        });
      }
      resetReader(reader);
    });

    window.addEventListener("pagehide", function () {
      if (active) cancelPlayback();
    });
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
      if (!r.ok) {
        var error = new Error(data.error || "Erreur de révision.");
        error.status = r.status;
        error.data = data;
        throw error;
      }
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

  function gradeError(error) {
    busy = false;
    kbdHint.textContent = error.message + " Rechargez la page.";
  }

  function fetchCurrentState() {
    return fetch(nextUrl + "?" + params().toString(), {
      headers: { "X-Requested-With": "fetch" }
    }).then(readJson);
  }

  function recoverGradeConflict(
    action,
    cardId,
    attemptedToken,
    elapsed,
    error,
    canRetry
  ) {
    var conflict = error.data || {};
    var replacementToken = conflict.presentation_token || "";
    if (
      canRetry &&
      conflict.current_card_id === cardId &&
      replacementToken &&
      replacementToken !== attemptedToken
    ) {
      presentationToken = replacementToken;
      submitGrade(action, cardId, replacementToken, elapsed, false);
      return;
    }

    fetchCurrentState()
      .then(function (data) {
        if (
          canRetry &&
          !data.done &&
          data.card_id === cardId &&
          data.presentation_token &&
          data.presentation_token !== attemptedToken
        ) {
          presentationToken = data.presentation_token;
          submitGrade(
            action,
            cardId,
            data.presentation_token,
            elapsed,
            false
          );
          return;
        }
        busy = false;
        handleState(data);
      })
      .catch(gradeError);
  }

  function submitGrade(action, cardId, token, elapsed, canRetry) {
    var body = params({
      card_id: cardId,
      action: action,
      elapsed_ms: elapsed,
      presentation_token: token
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
        if (error.status === 409) {
          recoverGradeConflict(
            action,
            cardId,
            token,
            elapsed,
            error,
            canRetry
          );
          return;
        }
        gradeError(error);
      });
  }

  function grade(action) {
    if (!revealed || viewingPrevious || busy || currentId === null) return;
    busy = true;
    submitGrade(
      action,
      currentId,
      presentationToken,
      Date.now() - startTime,
      true
    );
  }

  function loadNext() {
    fetchCurrentState()
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
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    busy = false;
    loadNext();
  });
})();
