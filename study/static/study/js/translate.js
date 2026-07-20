/* Highlight-to-English translation using the browser's local Translator API. */
(function () {
  "use strict";

  var action = document.querySelector("[data-selection-translate]");
  var selectionCopyButton = document.querySelector("[data-copy-selection]");
  var selectionCopyLabel = document.querySelector("[data-copy-selection-label]");
  var selectionCopyIcon = document.querySelector("[data-copy-selection-icon]");
  var readButton = document.querySelector("[data-read-selection]");
  var readLabel = document.querySelector("[data-read-selection-label]");
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
  var copyLabel = panel.querySelector("[data-translation-copy-label]");
  var copyIcon = panel.querySelector("[data-translation-copy-icon]");
  var fallbackLink = panel.querySelector("[data-translation-fallback]");
  var fallbackLabel = panel.querySelector("[data-translation-fallback-label]");
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
  var frenchSpeech = window.HeureuxFrenchSpeech;
  var reading = false;
  var readingNumber = 0;
  var readingChunks = [];
  var readingIndex = 0;
  var readResetTimer = null;

  function setSpriteIcon(icon, name) {
    if (!icon) return;
    var href = icon.getAttribute("href") || "";
    icon.setAttribute(
      "href",
      href.replace(/#icon-[a-z0-9-]+$/, "#icon-" + name)
    );
  }

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
      panel.contains(element) ||
      element.closest(
        "button, input, textarea, select, [contenteditable='true'], " +
        "[data-note-panel]"
      )
    ) {
      return null;
    }

    var text = normalizeSelection(range.cloneContents().textContent || "");
    var rect = range.getBoundingClientRect();
    if (!text || (!rect.width && !rect.height)) return null;
    return { text: text, rect: rect };
  }

  function hideAction() {
    stopReading();
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
    if (!details) return;
    var selectionChanged = details.text !== selectedText;
    if (selectionChanged && reading) stopReading();
    selectedText = details.text;
    selectedRect = details.rect;
    selectionCopyButton.classList.remove("is-copied");
    selectionCopyLabel.textContent = "Copy";
    if (selectionChanged) resetReadButton();
    positionAction(details.rect);
  }

  function scheduleSelectionAction(delay) {
    window.clearTimeout(selectionTimer);
    selectionTimer = window.setTimeout(updateSelectionAction, delay);
  }

  function setReadButton(label, isReading) {
    if (!readButton || !readLabel) return;
    window.clearTimeout(readResetTimer);
    readLabel.textContent = label;
    readButton.classList.toggle("is-reading", isReading);
    readButton.setAttribute("aria-pressed", isReading ? "true" : "false");
    readButton.setAttribute(
      "aria-label",
      isReading
        ? "Stop reading selected text"
        : "Read selected text in French"
    );
  }

  function resetReadButton() {
    setReadButton("Read", false);
  }

  function stopReading(cancelSpeech) {
    if (!readButton) return;
    var wasReading = reading;
    reading = false;
    readingNumber += 1;
    readingChunks = [];
    readingIndex = 0;
    if (
      wasReading &&
      cancelSpeech !== false &&
      frenchSpeech &&
      frenchSpeech.supported
    ) {
      frenchSpeech.synthesis.cancel();
      frenchSpeech.synthesis.resume();
    }
    resetReadButton();
  }

  function finishReading() {
    reading = false;
    readingChunks = [];
    readingIndex = 0;
    setReadButton("Read again", false);
  }

  function showReadError() {
    reading = false;
    readingChunks = [];
    readingIndex = 0;
    setReadButton("Unavailable", false);
    readResetTimer = window.setTimeout(resetReadButton, 1800);
  }

  function speakNextChunk(number) {
    if (!reading || number !== readingNumber) return;
    if (readingIndex >= readingChunks.length) {
      finishReading();
      return;
    }

    var utterance = new frenchSpeech.Utterance(readingChunks[readingIndex]);
    var voice = frenchSpeech.preferredVoice();
    utterance.lang = "fr-FR";
    utterance.rate = 0.92;
    utterance.pitch = 1;
    if (voice) utterance.voice = voice;
    utterance.onend = function () {
      if (!reading || number !== readingNumber) return;
      readingIndex += 1;
      speakNextChunk(number);
    };
    utterance.onerror = function () {
      if (!reading || number !== readingNumber) return;
      showReadError();
    };
    frenchSpeech.synthesis.speak(utterance);
  }

  function startReading() {
    if (!selectedText || !frenchSpeech || !frenchSpeech.supported) return;
    frenchSpeech.refreshVoices();
    var chunks = frenchSpeech.chunks(selectedText);
    if (!chunks.length) return;

    document.dispatchEvent(new CustomEvent("heureux:speech-start", {
      detail: { source: "selection-toolbar" }
    }));
    frenchSpeech.synthesis.resume();
    readingNumber += 1;
    reading = true;
    readingChunks = chunks;
    readingIndex = 0;
    setReadButton("Stop", true);
    var currentReading = readingNumber;
    speakNextChunk(currentReading);
  }

  function updateReadVoiceTitle() {
    if (!readButton || !frenchSpeech || !frenchSpeech.supported) return;
    var voice = frenchSpeech.preferredVoice();
    readButton.title = voice
      ? "French voice: " + voice.name
      : "Read with the best French voice available";
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
    copyLabel.textContent = "Copy";
    setSpriteIcon(copyIcon, "copy");
  }

  function showFallback(message) {
    setStatus(message, false);
    fallbackLabel.textContent = "Continue with Google Translate";
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

  if (readButton) {
    if (!readLabel || !frenchSpeech || !frenchSpeech.supported) {
      readButton.hidden = true;
    } else {
      resetReadButton();
      updateReadVoiceTitle();
      if (frenchSpeech.synthesis.addEventListener) {
        frenchSpeech.synthesis.addEventListener(
          "voiceschanged",
          updateReadVoiceTitle
        );
      }
      readButton.addEventListener("click", function () {
        if (reading) stopReading();
        else startReading();
      });
    }
  }
  document.addEventListener("heureux:speech-start", function (event) {
    if (
      reading &&
      (!event.detail || event.detail.source !== "selection-toolbar")
    ) {
      stopReading(false);
    }
  });

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
        selectionCopyLabel.textContent = "Copied";
        setSpriteIcon(selectionCopyIcon, "check");
        selectionCopyTimer = window.setTimeout(function () {
          selectionCopyButton.classList.remove("is-copied");
          selectionCopyLabel.textContent = "Copy";
          setSpriteIcon(selectionCopyIcon, "copy");
        }, 1600);
      })
      .catch(function () {
        selectionCopyButton.classList.remove("is-copied");
        selectionCopyLabel.textContent = "Copy failed";
        setSpriteIcon(selectionCopyIcon, "copy");
        selectionCopyTimer = window.setTimeout(function () {
          selectionCopyLabel.textContent = "Copy";
        }, 1600);
      });
  });

  translateButton.addEventListener("click", function () {
    if (!selectedText || !selectedRect) return;
    if (reading) stopReading();
    var text = selectedText;
    var rect = selectedRect;
    var currentRequest = ++requestNumber;
    var localTranslation = text.length <= maxLocalLength
      ? getTranslator()
      : null;

    sourceElement.textContent = text;
    resultElement.textContent = "";
    output.classList.add("hidden");
    copyButton.classList.add("hidden");
    copyLabel.textContent = "Copy";
    setSpriteIcon(copyIcon, "copy");
    fallbackLink.href = googleTranslateUrl(text);
    fallbackLabel.textContent = "Google Translate";
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
        copyLabel.textContent = "Copied";
        setSpriteIcon(copyIcon, "check");
        window.setTimeout(function () {
          copyLabel.textContent = "Copy";
          setSpriteIcon(copyIcon, "copy");
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
    var outsideAction = !action.contains(event.target);
    var outsideTranslation = !panel.contains(event.target);
    var outsideNote = !notePanel || !notePanel.contains(event.target);
    if (
      !panel.classList.contains("hidden") &&
      outsideTranslation &&
      outsideAction
    ) {
      closePanel();
    }
    if (
      !action.classList.contains("hidden") &&
      outsideAction &&
      outsideTranslation &&
      outsideNote
    ) {
      hideAction();
    }
  });
  window.addEventListener("resize", repositionPanel);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && reading) stopReading();
    if (event.key === "Escape" && !panel.classList.contains("hidden")) {
      closePanel();
    }
  });
  window.addEventListener("pagehide", function () {
    stopReading();
    if (translatorInstance && typeof translatorInstance.destroy === "function") {
      translatorInstance.destroy();
    }
  });
})();
