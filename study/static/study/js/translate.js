/* Highlight-to-English translation using the browser's local Translator API. */
(function () {
  "use strict";

  var action = document.querySelector("[data-selection-translate]");
  var selectionCopyButton = document.querySelector("[data-copy-selection]");
  var selectionCopyLabel = document.querySelector("[data-copy-selection-label]");
  var translateButton = document.querySelector("[data-translate-selection]");
  var panel = document.querySelector("[data-translation-panel]");
  var notePanel = document.querySelector("[data-note-panel]");
  if (
    !action ||
    !selectionCopyButton ||
    !selectionCopyLabel ||
    !translateButton ||
    !panel
  ) {
    return;
  }

  var closeButton = panel.querySelector("[data-translation-close]");
  var sourceElement = panel.querySelector("[data-translation-source]");
  var statusElement = panel.querySelector("[data-translation-status-text]");
  var spinner = panel.querySelector("[data-translation-spinner]");
  var output = panel.querySelector("[data-translation-output]");
  var resultElement = panel.querySelector("[data-translation-result]");
  var copyButton = panel.querySelector("[data-translation-copy]");
  var fallbackLink = panel.querySelector("[data-translation-fallback]");
  var mobileActionQuery = window.matchMedia(
    "(max-width: 760px), (hover: none), (pointer: coarse)"
  );
  var mobilePanelQuery = window.matchMedia("(max-width: 520px)");
  var translatorOptions = {
    sourceLanguage: "fr",
    targetLanguage: "en"
  };
  var maxLocalLength = 2000;
  var selectedText = "";
  var selectedRect = null;
  var selectionTimer = null;
  var selectionCopyTimer = null;
  var requestNumber = 0;
  var translatorPromise = null;
  var translatorInstance = null;

  function normalizeSelection(text) {
    return text
      .replace(/\u00a0/g, " ")
      .replace(/[ \t]+/g, " ")
      .replace(/\n[ \t]+/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function selectionDetails() {
    var selection = window.getSelection();
    var main = document.getElementById("main");
    if (!selection || selection.isCollapsed || selection.rangeCount === 0 || !main) {
      return null;
    }

    var range = selection.getRangeAt(0);
    var container = range.commonAncestorContainer;
    var element = container.nodeType === Node.ELEMENT_NODE
      ? container
      : container.parentElement;
    if (
      !element ||
      !main.contains(element) ||
      action.contains(element) ||
      panel.contains(element)
    ) {
      return null;
    }

    var text = normalizeSelection(selection.toString());
    var rect = range.getBoundingClientRect();
    if (!text || (!rect.width && !rect.height)) return null;
    return { text: text, rect: rect };
  }

  function hideAction() {
    action.classList.add("hidden");
  }

  function positionAction(rect) {
    action.classList.remove("hidden");
    if (mobileActionQuery.matches) {
      action.style.left = "";
      action.style.top = "";
      return;
    }

    action.style.left = "0";
    action.style.top = "0";
    var actionRect = action.getBoundingClientRect();
    var left = rect.left + (rect.width - actionRect.width) / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - actionRect.width - 8));
    var top = rect.top - actionRect.height - 8;
    if (top < 8) top = rect.bottom + 8;
    action.style.left = Math.round(left) + "px";
    action.style.top = Math.round(top) + "px";
  }

  function updateSelectionAction() {
    if (
      !panel.classList.contains("hidden") ||
      (notePanel && !notePanel.classList.contains("hidden"))
    ) {
      return;
    }
    var details = selectionDetails();
    if (!details) {
      hideAction();
      return;
    }
    selectedText = details.text;
    selectedRect = details.rect;
    selectionCopyButton.classList.remove("is-copied");
    selectionCopyLabel.textContent = "Copy";
    positionAction(details.rect);
  }

  function scheduleSelectionAction(delay) {
    window.clearTimeout(selectionTimer);
    selectionTimer = window.setTimeout(updateSelectionAction, delay);
  }

  function googleTranslateUrl(text) {
    var url = new URL("https://translate.google.com/");
    url.searchParams.set("sl", "fr");
    url.searchParams.set("tl", "en");
    url.searchParams.set("text", text);
    url.searchParams.set("op", "translate");
    return url.toString();
  }

  function setStatus(message, loading) {
    statusElement.textContent = message;
    spinner.classList.toggle("hidden", !loading);
  }

  function positionPanel(rect) {
    panel.classList.remove("hidden");
    if (mobilePanelQuery.matches) {
      panel.style.left = "";
      panel.style.top = "";
      return;
    }

    panel.style.left = "0";
    panel.style.top = "0";
    var panelRect = panel.getBoundingClientRect();
    var left = rect.left + (rect.width - panelRect.width) / 2;
    left = Math.max(12, Math.min(left, window.innerWidth - panelRect.width - 12));
    var top = rect.bottom + 10;
    if (top + panelRect.height > window.innerHeight - 12) {
      top = Math.max(12, rect.top - panelRect.height - 10);
    }
    panel.style.left = Math.round(left) + "px";
    panel.style.top = Math.round(top) + "px";
  }

  function repositionPanel() {
    if (!selectedRect || panel.classList.contains("hidden")) return;
    window.requestAnimationFrame(function () {
      positionPanel(selectedRect);
    });
  }

  function closePanel() {
    requestNumber += 1;
    panel.classList.add("hidden");
    copyButton.classList.add("hidden");
    copyButton.textContent = "Copy";
  }

  function showFallback(message) {
    setStatus(message, false);
    fallbackLink.textContent = "Continue with Google Translate ↗";
    fallbackLink.classList.add("btn--primary");
    repositionPanel();
  }

  function updateDownloadProgress(event) {
    if (panel.classList.contains("hidden")) return;
    var loaded = Number(event.loaded) || 0;
    var total = Number(event.total) || 0;
    var fraction = total > 0 ? loaded / total : loaded;
    var percent = Math.max(0, Math.min(100, Math.round(fraction * 100)));
    setStatus("Downloading the local French–English model · " + percent + "%", true);
  }

  function getTranslator() {
    if (
      !window.Translator ||
      typeof window.Translator.create !== "function"
    ) {
      return null;
    }
    if (translatorPromise) return translatorPromise;

    try {
      translatorPromise = Promise.resolve(
        window.Translator.create({
          sourceLanguage: translatorOptions.sourceLanguage,
          targetLanguage: translatorOptions.targetLanguage,
          monitor: function (monitor) {
            monitor.addEventListener("downloadprogress", updateDownloadProgress);
          }
        })
      )
        .then(function (translator) {
          translatorInstance = translator;
          return translator;
        })
        .catch(function (error) {
          translatorPromise = null;
          throw error;
        });
    } catch (error) {
      translatorPromise = null;
      return Promise.reject(error);
    }
    return translatorPromise;
  }

  function showTranslation(translation) {
    resultElement.textContent = translation;
    output.classList.remove("hidden");
    copyButton.classList.remove("hidden");
    setStatus("Translated locally on this device.", false);
    repositionPanel();
  }

  function writeClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }

    var textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    var copied = document.execCommand("copy");
    textarea.remove();
    return copied
      ? Promise.resolve()
      : Promise.reject(new Error("Copy failed"));
  }

  action.querySelectorAll("button").forEach(function (button) {
    button.addEventListener("pointerdown", function (event) {
      event.preventDefault();
    });
  });

  selectionCopyButton.addEventListener("click", function () {
    if (!selectedText) return;
    window.clearTimeout(selectionCopyTimer);
    writeClipboard(selectedText)
      .then(function () {
        selectionCopyButton.classList.add("is-copied");
        selectionCopyLabel.textContent = "Copied ✓";
        selectionCopyTimer = window.setTimeout(function () {
          selectionCopyButton.classList.remove("is-copied");
          selectionCopyLabel.textContent = "Copy";
        }, 1600);
      })
      .catch(function () {
        selectionCopyButton.classList.remove("is-copied");
        selectionCopyLabel.textContent = "Copy failed";
        selectionCopyTimer = window.setTimeout(function () {
          selectionCopyLabel.textContent = "Copy";
        }, 1600);
      });
  });

  translateButton.addEventListener("click", function () {
    if (!selectedText || !selectedRect) return;
    var text = selectedText;
    var rect = selectedRect;
    var currentRequest = ++requestNumber;
    var localTranslation = text.length <= maxLocalLength
      ? getTranslator()
      : null;

    hideAction();
    sourceElement.textContent = text;
    resultElement.textContent = "";
    output.classList.add("hidden");
    copyButton.classList.add("hidden");
    copyButton.textContent = "Copy";
    fallbackLink.href = googleTranslateUrl(text);
    fallbackLink.textContent = "Google Translate ↗";
    fallbackLink.classList.remove("btn--primary");
    positionPanel(rect);
    panel.focus({ preventScroll: true });

    if (text.length > maxLocalLength) {
      showFallback("Select a shorter passage for local translation (maximum 2,000 characters).");
      return;
    }
    if (!localTranslation) {
      showFallback("Local translation is not available on this device.");
      return;
    }

    setStatus("Preparing local translation…", true);
    localTranslation
      .then(function (translator) {
        return translator.translate(text);
      })
      .then(function (translation) {
        if (currentRequest !== requestNumber) return;
        showTranslation(translation);
      })
      .catch(function () {
        if (currentRequest !== requestNumber) return;
        showFallback("Local translation could not start on this device.");
      });
  });

  copyButton.addEventListener("click", function () {
    var text = resultElement.textContent;
    if (!text) return;

    writeClipboard(text)
      .then(function () {
        copyButton.textContent = "Copied ✓";
        window.setTimeout(function () {
          copyButton.textContent = "Copy";
        }, 1600);
      })
      .catch(function () {
        setStatus("Copy failed. Select the translation and copy it manually.", false);
      });
  });

  closeButton.addEventListener("click", closePanel);
  document.addEventListener("selectionchange", function () {
    scheduleSelectionAction(100);
  });
  document.addEventListener("pointerup", function () {
    scheduleSelectionAction(30);
  });
  document.addEventListener("keyup", function (event) {
    if (event.key === "Shift" || event.shiftKey) scheduleSelectionAction(30);
  });
  document.addEventListener("pointerdown", function (event) {
    if (
      !panel.classList.contains("hidden") &&
      !panel.contains(event.target) &&
      !action.contains(event.target)
    ) {
      closePanel();
    }
  });
  window.addEventListener("resize", repositionPanel);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.classList.contains("hidden")) {
      closePanel();
    }
  });
  document.addEventListener("scroll", hideAction, true);
  window.addEventListener("resize", hideAction);
  window.addEventListener("pagehide", function () {
    if (translatorInstance && typeof translatorInstance.destroy === "function") {
      translatorInstance.destroy();
    }
  });
})();
