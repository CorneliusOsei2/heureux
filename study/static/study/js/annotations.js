/* Private notes and persistent highlights for selected study text. */
(function () {
  "use strict";

  var main = document.getElementById("main");
  var action = document.querySelector("[data-selection-translate]");
  var noteButton = document.querySelector("[data-note-selection]");
  var highlightButton = document.querySelector("[data-highlight-selection]");
  var notePanel = document.querySelector("[data-note-panel]");
  var sourceUrl = document.body.dataset.annotationSourceUrl;
  var createUrl = document.body.dataset.annotationCreateUrl;
  if (
    !main ||
    !action ||
    !noteButton ||
    !highlightButton ||
    !notePanel ||
    !sourceUrl ||
    !createUrl
  ) {
    return;
  }

  var noteSource = notePanel.querySelector("[data-note-source]");
  var noteBody = notePanel.querySelector("[data-note-body]");
  var noteStatus = notePanel.querySelector("[data-note-status]");
  var noteSave = notePanel.querySelector("[data-note-save]");
  var noteView = notePanel.querySelector("[data-note-view]");
  var noteCloseButtons = notePanel.querySelectorAll(
    "[data-note-close], [data-note-cancel]"
  );
  var toast = document.querySelector("[data-annotation-toast]");
  var sourcePath = window.location.pathname + window.location.search;
  var currentSelection = null;
  var noteSelection = null;
  var highlights = [];
  var toastTimer = null;
  var mutationTimer = null;

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function selectionElement(range) {
    var node = range.commonAncestorContainer;
    return node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  }

  function captureSelection() {
    var selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      return null;
    }
    var range = selection.getRangeAt(0);
    var element = selectionElement(range);
    if (
      !element ||
      !main.contains(element) ||
      element.closest(
        "button, input, textarea, select, [contenteditable='true'], " +
        "[data-translation-panel], [data-note-panel]"
      )
    ) {
      return null;
    }
    var quote = range.cloneContents().textContent || "";
    if (!quote.trim()) return null;

    var before = range.cloneRange();
    before.selectNodeContents(main);
    before.setEnd(range.startContainer, range.startOffset);
    var start = (before.cloneContents().textContent || "").length;
    var end = start + quote.length;
    var pageText = main.textContent || "";
    var intersectsHighlight = Array.from(
      main.querySelectorAll("[data-user-highlight]")
    ).some(function (mark) {
      try {
        return range.intersectsNode(mark);
      } catch (error) {
        return false;
      }
    });
    return {
      quote: quote,
      start: start,
      end: end,
      prefix: pageText.slice(Math.max(0, start - 160), start),
      suffix: pageText.slice(end, end + 160),
      intersectsHighlight: intersectsHighlight
    };
  }

  function rememberSelection() {
    var details = captureSelection();
    if (details) currentSelection = details;
  }

  function clearBrowserSelection() {
    var selection = window.getSelection();
    if (selection) selection.removeAllRanges();
  }

  function hideAction() {
    action.classList.add("hidden");
  }

  function showToast(message) {
    if (!toast) return;
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.remove("hidden");
    toastTimer = window.setTimeout(function () {
      toast.classList.add("hidden");
    }, 2200);
  }

  function readJson(response) {
    return response.json().catch(function () {
      return {};
    }).then(function (data) {
      if (!response.ok) {
        throw new Error(data.error || "L'enregistrement a échoué.");
      }
      return data;
    });
  }

  function annotationBody(kind, details, body) {
    var values = new URLSearchParams();
    values.set("kind", kind);
    values.set("quote", details.quote);
    values.set("start_offset", details.start);
    values.set("end_offset", details.end);
    values.set("prefix", details.prefix);
    values.set("suffix", details.suffix);
    values.set("source_path", sourcePath);
    values.set("source_title", document.title);
    values.set("body", body || "");
    var taskId = document.body.dataset.annotationTaskId;
    if (taskId) values.set("task_id", taskId);
    return values;
  }

  function createAnnotation(kind, details, body) {
    return fetch(createUrl, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "fetch",
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: annotationBody(kind, details, body).toString()
    }).then(readJson);
  }

  function closeNotePanel() {
    notePanel.classList.add("hidden");
    noteStatus.textContent = "";
    noteSave.disabled = false;
    noteSelection = null;
  }

  function openNotePanel() {
    rememberSelection();
    if (!currentSelection) return;
    noteSelection = currentSelection;
    hideAction();
    noteSource.textContent = noteSelection.quote;
    noteBody.value = "";
    noteStatus.textContent = "";
    noteView.classList.add("hidden");
    notePanel.classList.remove("hidden");
    notePanel.focus({ preventScroll: true });
    window.setTimeout(function () {
      noteBody.focus({ preventScroll: true });
    }, 0);
  }

  function saveNote() {
    if (!noteSelection || noteSave.disabled) return;
    noteSave.disabled = true;
    noteStatus.textContent = "Enregistrement…";
    createAnnotation("note", noteSelection, noteBody.value)
      .then(function (data) {
        noteStatus.textContent = "Note enregistrée.";
        noteView.href = data.notes_url;
        noteView.classList.remove("hidden");
        noteSave.disabled = false;
        clearBrowserSelection();
      })
      .catch(function (error) {
        noteStatus.textContent = error.message;
        noteSave.disabled = false;
      });
  }

  function bestOffsets(item) {
    var text = main.textContent || "";
    if (
      item.start_offset >= 0 &&
      item.end_offset > item.start_offset &&
      text.slice(item.start_offset, item.end_offset) === item.quote
    ) {
      return { start: item.start_offset, end: item.end_offset };
    }

    var best = null;
    var index = text.indexOf(item.quote);
    while (index !== -1) {
      var score = 0;
      if (item.prefix && text.slice(Math.max(0, index - item.prefix.length), index) === item.prefix) {
        score += 2;
      }
      var end = index + item.quote.length;
      if (item.suffix && text.slice(end, end + item.suffix.length) === item.suffix) {
        score += 2;
      }
      score -= Math.min(Math.abs(index - item.start_offset) / 10000, 1);
      if (!best || score > best.score) {
        best = { start: index, end: end, score: score };
      }
      index = text.indexOf(item.quote, index + 1);
    }
    return best;
  }

  function textSegments(start, end) {
    var walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    var segments = [];
    var offset = 0;
    var node;
    while ((node = walker.nextNode())) {
      var nodeStart = offset;
      var nodeEnd = offset + node.data.length;
      if (nodeEnd > start && nodeStart < end) {
        var parent = node.parentElement;
        if (
          parent &&
          !parent.closest(
            "script, style, button, textarea, select, option, " +
            "[data-user-highlight]"
          )
        ) {
          segments.push({
            node: node,
            start: Math.max(0, start - nodeStart),
            end: Math.min(node.data.length, end - nodeStart)
          });
        }
      }
      offset = nodeEnd;
      if (offset >= end) break;
    }
    return segments;
  }

  function wrapSegment(segment, highlightId) {
    var node = segment.node;
    if (!node.parentNode || segment.start >= segment.end) return;
    if (segment.end < node.data.length) node.splitText(segment.end);
    var selected = segment.start > 0 ? node.splitText(segment.start) : node;
    var mark = document.createElement("mark");
    mark.className = "user-highlight";
    mark.dataset.userHighlight = highlightId;
    mark.dataset.highlightId = highlightId;
    selected.parentNode.insertBefore(mark, selected);
    mark.appendChild(selected);
  }

  function applyHighlight(item) {
    if (
      main.querySelector(
        '[data-highlight-id="' + String(item.id).replace(/"/g, "") + '"]'
      )
    ) {
      return true;
    }
    var offsets = bestOffsets(item);
    if (!offsets) return false;
    var segments = textSegments(offsets.start, offsets.end);
    if (!segments.length) return false;
    segments.reverse().forEach(function (segment) {
      wrapSegment(segment, item.id);
    });
    return true;
  }

  function applySavedHighlights() {
    highlights.forEach(applyHighlight);
  }

  function fetchHighlights() {
    var url = new URL(sourceUrl, window.location.origin);
    url.searchParams.set("source_path", sourcePath);
    fetch(url.toString(), {
      headers: { "X-Requested-With": "fetch" }
    })
      .then(readJson)
      .then(function (data) {
        highlights = data.highlights || [];
        applySavedHighlights();
      })
      .catch(function () {});
  }

  function saveHighlight() {
    rememberSelection();
    var details = currentSelection;
    if (!details) return;
    if (details.intersectsHighlight) {
      showToast("Ce passage est déjà surligné.");
      hideAction();
      return;
    }
    highlightButton.disabled = true;
    createAnnotation("highlight", details, "")
      .then(function (data) {
        var item = {
          id: data.id,
          quote: details.quote,
          start_offset: details.start,
          end_offset: details.end,
          prefix: details.prefix,
          suffix: details.suffix
        };
        highlights.push(item);
        clearBrowserSelection();
        hideAction();
        applyHighlight(item);
        showToast(
          data.created ? "Passage surligné." : "Ce passage est déjà surligné."
        );
        highlightButton.disabled = false;
      })
      .catch(function (error) {
        showToast(error.message);
        highlightButton.disabled = false;
      });
  }

  action.querySelectorAll("button").forEach(function (button) {
    button.addEventListener("pointerdown", function (event) {
      rememberSelection();
      event.preventDefault();
    });
  });
  document.addEventListener("selectionchange", function () {
    window.setTimeout(rememberSelection, 0);
  });
  document.addEventListener("pointerup", rememberSelection);
  noteButton.addEventListener("click", openNotePanel);
  highlightButton.addEventListener("click", saveHighlight);
  noteSave.addEventListener("click", saveNote);
  noteCloseButtons.forEach(function (button) {
    button.addEventListener("click", closeNotePanel);
  });
  document.addEventListener("pointerdown", function (event) {
    if (
      !notePanel.classList.contains("hidden") &&
      !notePanel.contains(event.target) &&
      !action.contains(event.target)
    ) {
      closeNotePanel();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !notePanel.classList.contains("hidden")) {
      closeNotePanel();
    }
  });

  var observer = new MutationObserver(function () {
    window.clearTimeout(mutationTimer);
    mutationTimer = window.setTimeout(applySavedHighlights, 80);
  });
  observer.observe(main, { childList: true, subtree: true });
  window.addEventListener("pagehide", function () {
    observer.disconnect();
  });
  fetchHighlights();
})();
