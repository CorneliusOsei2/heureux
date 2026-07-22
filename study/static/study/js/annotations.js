/* Private notes and persistent highlights for selected study text. */
(function () {
  "use strict";

  var main = document.getElementById("main");
  var action = document.querySelector("[data-selection-translate]");
  var noteButton = document.querySelector("[data-note-selection]");
  var highlightButton = document.querySelector("[data-highlight-selection]");
  var highlightLabel = highlightButton
    ? highlightButton.querySelector(".selection-translate__label")
    : null;
  var notePanel = document.querySelector("[data-note-panel]");
  var sourceUrl = document.body.dataset.annotationSourceUrl;
  var createUrl = document.body.dataset.annotationCreateUrl;
  if (
    !main ||
    !action ||
    !noteButton ||
    !highlightButton ||
    !highlightLabel ||
    !notePanel ||
    !sourceUrl ||
    !createUrl
  ) {
    return;
  }

  var noteSource = notePanel.querySelector("[data-note-source]");
  var noteBody = notePanel.querySelector("[data-note-body]");
  var notePaste = notePanel.querySelector("[data-note-paste]");
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

  function rootForNode(node) {
    var element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    return element ? element.closest("[data-annotation-root]") : null;
  }

  function captureSelection() {
    var selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
      return null;
    }
    var range = selection.getRangeAt(0);
    var element = selectionElement(range);
    var startRoot = rootForNode(range.startContainer);
    var endRoot = rootForNode(range.endContainer);
    if (startRoot !== endRoot) return null;
    var root = startRoot || main;
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
    before.selectNodeContents(root);
    before.setEnd(range.startContainer, range.startOffset);
    var start = (before.cloneContents().textContent || "").length;
    var end = start + quote.length;
    var pageText = root.textContent || "";
    var coverage = highlightCoverage(root, start, end);
    var highlightStart = Math.min(start, coverage.start);
    var highlightEnd = Math.max(end, coverage.end);
    return {
      quote: quote,
      start: start,
      end: end,
      prefix: pageText.slice(Math.max(0, start - 160), start),
      suffix: pageText.slice(end, end + 160),
      fullyHighlighted: coverage.fullyHighlighted,
      highlightIds: coverage.ids,
      highlightRevisions: coverage.revisions,
      highlight: {
        quote: pageText.slice(highlightStart, highlightEnd),
        start: highlightStart,
        end: highlightEnd,
        prefix: pageText.slice(Math.max(0, highlightStart - 160), highlightStart),
        suffix: pageText.slice(highlightEnd, highlightEnd + 160)
      },
      sourceKey: root.dataset.annotationSourceKey || ""
    };
  }

  function updateHighlightButton(details) {
    var shouldRemove = Boolean(details && details.fullyHighlighted);
    highlightLabel.textContent = shouldRemove ? "Unhighlight" : "Highlight";
    highlightButton.setAttribute(
      "aria-label",
      shouldRemove ? "Unhighlight selected text" : "Highlight selected text"
    );
  }

  function rememberSelection() {
    var details = captureSelection();
    if (details) {
      currentSelection = details;
      updateHighlightButton(details);
    }
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
    if (response.redirected) {
      return Promise.reject(
        new Error("Votre session a expiré. Reconnectez-vous.")
      );
    }
    var contentType = response.headers.get("Content-Type") || "";
    if (contentType.indexOf("application/json") === -1) {
      return Promise.reject(
        new Error("La réponse du serveur est inattendue.")
      );
    }
    return response.json().catch(function () {
      throw new Error("La réponse du serveur est invalide.");
    }).then(function (data) {
      if (!response.ok) {
        var error = new Error(data.error || "L'enregistrement a échoué.");
        error.status = response.status;
        throw error;
      }
      return data;
    });
  }

  function annotationBody(kind, details, body) {
    var selected = kind === "highlight" ? details.highlight : details;
    var values = new URLSearchParams();
    values.set("kind", kind);
    values.set("quote", selected.quote);
    values.set("start_offset", selected.start);
    values.set("end_offset", selected.end);
    values.set("prefix", selected.prefix);
    values.set("suffix", selected.suffix);
    values.set("source_path", sourcePath);
    values.set("source_key", details.sourceKey || "");
    values.set("source_title", document.title);
    values.set("body", body || "");
    if (kind === "highlight") {
      values.set("overlap_ids", details.highlightIds.join(","));
      values.set(
        "overlap_revisions",
        JSON.stringify(details.highlightRevisions)
      );
    }
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

  function pasteNote() {
    if (!notePaste || notePaste.disabled) return;
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      noteStatus.textContent =
        "Collage automatique indisponible. Utilisez ⌘V ou Ctrl+V.";
      noteBody.focus({ preventScroll: true });
      return;
    }

    var start = noteBody.selectionStart;
    var end = noteBody.selectionEnd;
    notePaste.disabled = true;
    noteStatus.textContent = "Lecture du presse-papiers…";
    navigator.clipboard.readText()
      .then(function (text) {
        if (!text) {
          noteStatus.textContent = "Le presse-papiers est vide.";
          return;
        }
        var retainedLength = noteBody.value.length - (end - start);
        var available = Math.max(noteBody.maxLength - retainedLength, 0);
        var insertion = text.slice(0, available);
        if (!insertion) {
          noteStatus.textContent = "La note a atteint sa longueur maximale.";
          return;
        }
        noteBody.value =
          noteBody.value.slice(0, start)
          + insertion
          + noteBody.value.slice(end);
        var cursor = start + insertion.length;
        noteBody.setSelectionRange(cursor, cursor);
        noteBody.dispatchEvent(new Event("input", { bubbles: true }));
        noteStatus.textContent = insertion.length < text.length
          ? "Texte collé jusqu'à la limite de la note."
          : "Texte collé.";
      })
      .catch(function () {
        noteStatus.textContent =
          "Impossible d'accéder au presse-papiers. Utilisez ⌘V ou Ctrl+V.";
      })
      .then(function () {
        notePaste.disabled = false;
        noteBody.focus({ preventScroll: true });
      });
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
      })
      .catch(function (error) {
        noteStatus.textContent = error.message;
        noteSave.disabled = false;
      });
  }

  function normalizedContext(value) {
    return (value || "").replace(/\s+/g, " ").trim();
  }

  function commonPrefixLength(left, right) {
    var limit = Math.min(left.length, right.length);
    var index = 0;
    while (index < limit && left[index] === right[index]) index += 1;
    return index;
  }

  function commonSuffixLength(left, right) {
    var limit = Math.min(left.length, right.length);
    var count = 0;
    while (
      count < limit &&
      left[left.length - count - 1] === right[right.length - count - 1]
    ) {
      count += 1;
    }
    return count;
  }

  function normalizedTextMap(value) {
    var normalized = "";
    var starts = [];
    var ends = [];
    var whitespaceStart = -1;
    for (var index = 0; index < value.length; index += 1) {
      if (/\s/.test(value[index])) {
        if (normalized && whitespaceStart === -1) {
          whitespaceStart = index;
        }
        continue;
      }
      if (whitespaceStart !== -1) {
        normalized += " ";
        starts.push(whitespaceStart);
        ends.push(index);
        whitespaceStart = -1;
      }
      normalized += value[index];
      starts.push(index);
      ends.push(index + 1);
    }
    return {
      text: normalized,
      starts: starts,
      ends: ends
    };
  }

  function bestOffsets(item, root) {
    var text = root.textContent || "";
    var savedPrefix = normalizedContext(item.prefix);
    var savedSuffix = normalizedContext(item.suffix);
    var candidates = [];
    var candidateKeys = {};
    function addCandidate(start, end) {
      var key = start + ":" + end;
      if (candidateKeys[key]) return;
      candidateKeys[key] = true;
      candidates.push({ start: start, end: end });
    }

    var exactIndex = text.indexOf(item.quote);
    while (exactIndex !== -1) {
      addCandidate(exactIndex, exactIndex + item.quote.length);
      exactIndex = text.indexOf(item.quote, exactIndex + 1);
    }

    var normalizedQuote = normalizedContext(item.quote);
    if (normalizedQuote) {
      var mappedText = normalizedTextMap(text);
      var normalizedIndex = mappedText.text.indexOf(normalizedQuote);
      while (normalizedIndex !== -1) {
        var normalizedEnd = normalizedIndex + normalizedQuote.length;
        addCandidate(
          mappedText.starts[normalizedIndex],
          mappedText.ends[normalizedEnd - 1]
        );
        normalizedIndex = mappedText.text.indexOf(
          normalizedQuote,
          normalizedIndex + 1
        );
      }
    }

    var best = null;
    candidates.forEach(function (candidate) {
      var index = candidate.start;
      var end = candidate.end;
      var score = -Math.min(
        Math.abs(index - item.start_offset) / 10000,
        1
      );
      var currentPrefix = normalizedContext(
        text.slice(Math.max(0, index - 400), index)
      );
      var prefixMatch = commonSuffixLength(
        currentPrefix,
        savedPrefix
      );
      if (savedPrefix && currentPrefix.endsWith(savedPrefix)) {
        score += 200 + savedPrefix.length;
      } else if (prefixMatch >= 4) {
        score += prefixMatch;
      }
      var currentSuffix = normalizedContext(
        text.slice(end, end + 400)
      );
      var suffixMatch = commonPrefixLength(
        currentSuffix,
        savedSuffix
      );
      if (savedSuffix && currentSuffix.startsWith(savedSuffix)) {
        score += 200 + savedSuffix.length;
      } else if (suffixMatch >= 4) {
        score += suffixMatch;
      }
      if (!best || score > best.score) {
        best = { start: index, end: end, score: score };
      }
    });
    return best;
  }

  function highlightCoverage(root, start, end) {
    var intervals = [];
    highlights.forEach(function (item) {
      if (highlightRoot(item) !== root) return;
      var offsets = bestOffsets(item, root);
      if (!offsets || offsets.end <= start || offsets.start >= end) return;
      intervals.push({
        id: item.id,
        revision: item.revision,
        start: Math.max(start, offsets.start),
        end: Math.min(end, offsets.end),
        originalStart: offsets.start,
        originalEnd: offsets.end
      });
    });
    intervals.sort(function (left, right) {
      return left.start - right.start || right.end - left.end;
    });

    var coveredUntil = start;
    var hasGap = false;
    var ids = [];
    var revisions = {};
    intervals.forEach(function (interval) {
      if (interval.start > coveredUntil) hasGap = true;
      coveredUntil = Math.max(coveredUntil, interval.end);
      if (ids.indexOf(interval.id) === -1) {
        ids.push(interval.id);
        revisions[String(interval.id)] = interval.revision;
      }
    });
    return {
      ids: ids,
      revisions: revisions,
      fullyHighlighted: intervals.length > 0 && !hasGap && coveredUntil >= end,
      start: intervals.reduce(function (minimum, interval) {
        return Math.min(minimum, interval.originalStart);
      }, start),
      end: intervals.reduce(function (maximum, interval) {
        return Math.max(maximum, interval.originalEnd);
      }, end)
    };
  }

  function textSegments(root, start, end, includeNestedRoots) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var segments = [];
    var offset = 0;
    var node;
    while ((node = walker.nextNode())) {
      var nodeStart = offset;
      var nodeEnd = offset + node.data.length;
      if (nodeEnd > start && nodeStart < end) {
        var parent = node.parentElement;
        var segmentStart = Math.max(0, start - nodeStart);
        var segmentEnd = Math.min(node.data.length, end - nodeStart);
        if (
          parent &&
          node.data.slice(segmentStart, segmentEnd).trim() &&
          !(
            root === main &&
            !includeNestedRoots &&
            parent.closest("[data-annotation-root]")
          ) &&
          !parent.closest(
            "script, style, button, textarea, select, option, " +
            "[data-user-highlight]"
          )
        ) {
          segments.push({
            node: node,
            start: segmentStart,
            end: segmentEnd
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

  function highlightRoot(item) {
    if (!item.source_key) {
      var legacyMark = main.querySelector(
        '[data-highlight-id="' + String(item.id).replace(/"/g, "") + '"]'
      );
      return legacyMark
        ? legacyMark.closest("[data-annotation-root]") || main
        : main;
    }
    var roots = main.querySelectorAll(
      "[data-annotation-root][data-annotation-source-key]"
    );
    for (var index = 0; index < roots.length; index += 1) {
      if (roots[index].dataset.annotationSourceKey === item.source_key) {
        return roots[index];
      }
    }
    return null;
  }

  function applyHighlight(item) {
    var root = highlightRoot(item);
    if (!root) return false;
    if (
      root.querySelector(
        '[data-highlight-id="' + String(item.id).replace(/"/g, "") + '"]'
      )
    ) {
      return true;
    }
    var offsets = bestOffsets(item, root);
    if (!offsets) return false;
    var segments = textSegments(
      root,
      offsets.start,
      offsets.end,
      !item.source_key
    );
    if (!segments.length) return false;
    segments.reverse().forEach(function (segment) {
      wrapSegment(segment, item.id);
    });
    return true;
  }

  function applySavedHighlights() {
    highlights.forEach(applyHighlight);
  }

  function removeHighlightMarks(ids) {
    var selectedIds = ids.map(String);
    Array.from(main.querySelectorAll("[data-highlight-id]")).forEach(
      function (mark) {
        if (selectedIds.indexOf(mark.dataset.highlightId) === -1) return;
        var parent = mark.parentNode;
        if (!parent) return;
        while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
        parent.removeChild(mark);
        parent.normalize();
      }
    );
  }

  function replaceSavedHighlights(items) {
    removeHighlightMarks(
      Array.from(main.querySelectorAll("[data-highlight-id]")).map(function (mark) {
        return mark.dataset.highlightId;
      })
    );
    highlights = items;
    applySavedHighlights();
    rememberSelection();
  }

  function fetchHighlights() {
    var url = new URL(sourceUrl, window.location.origin);
    url.searchParams.set("source_path", sourcePath);
    fetch(url.toString(), {
      headers: { "X-Requested-With": "fetch" }
    })
      .then(readJson)
      .then(function (data) {
        replaceSavedHighlights(data.highlights || []);
      })
      .catch(function () {});
  }

  function deleteHighlight(item) {
    if (!item.delete_url) {
      return Promise.reject(new Error("Ce surlignage ne peut pas être supprimé."));
    }
    return fetch(item.delete_url, {
      method: "POST",
      headers: {
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "fetch"
      }
    }).then(readJson);
  }

  function removeHighlights(details) {
    var selectedIds = details.highlightIds.map(String);
    var selectedHighlights = highlights.filter(function (item) {
      return selectedIds.indexOf(String(item.id)) !== -1;
    });
    if (!selectedHighlights.length) return;

    highlightButton.disabled = true;
    Promise.all(selectedHighlights.map(deleteHighlight))
      .then(function () {
        highlights = highlights.filter(function (item) {
          return selectedIds.indexOf(String(item.id)) === -1;
        });
        removeHighlightMarks(selectedIds);
        details.fullyHighlighted = false;
        details.highlightIds = [];
        details.highlightRevisions = [];
        currentSelection = details;
        updateHighlightButton(details);
        showToast("Surlignage supprimé.");
        highlightButton.disabled = false;
      })
      .catch(function (error) {
        showToast(error.message);
        highlightButton.disabled = false;
        fetchHighlights();
      });
  }

  function toggleHighlight() {
    rememberSelection();
    var details = currentSelection;
    if (!details) return;
    if (details.fullyHighlighted) {
      removeHighlights(details);
      return;
    }
    highlightButton.disabled = true;
    createAnnotation("highlight", details, "")
      .then(function (data) {
        var selected = details.highlight;
        var item = {
          id: data.id,
          quote: selected.quote,
          start_offset: selected.start,
          end_offset: selected.end,
          prefix: selected.prefix,
          suffix: selected.suffix,
          source_key: details.sourceKey || "",
          revision: data.revision,
          delete_url: data.delete_url
        };
        var removedIds = (data.removed_ids || []).map(String);
        var replacedIds = removedIds.concat(String(item.id));
        removeHighlightMarks(replacedIds);
        highlights = highlights.filter(function (saved) {
          return (
            saved.id !== item.id &&
            removedIds.indexOf(String(saved.id)) === -1
          );
        });
        highlights.push(item);
        applyHighlight(item);
        details.fullyHighlighted = true;
        details.highlightIds = [item.id];
        details.highlightRevisions = [item.revision];
        currentSelection = details;
        updateHighlightButton(details);
        showToast("Passage surligné.");
        highlightButton.disabled = false;
      })
      .catch(function (error) {
        showToast(error.message);
        highlightButton.disabled = false;
        if (error.status === 409) fetchHighlights();
      });
  }

  function setupStudyDeck() {
    var deck = document.querySelector("[data-annotation-study]");
    if (!deck) return;
    var cards = Array.from(deck.querySelectorAll("[data-study-card]"));
    var progress = deck.querySelector("[data-study-progress]");
    var previous = deck.querySelector("[data-study-previous]");
    var reveal = deck.querySelector("[data-study-reveal]");
    var keep = deck.querySelector("[data-study-keep]");
    var learned = deck.querySelector("[data-study-learned]");
    var restart = deck.querySelector("[data-study-restart]");
    var done = deck.querySelector("[data-study-done]");
    var controls = deck.querySelector(".annotation-study__controls");
    var index = 0;
    var revealed = false;

    function render() {
      cards.forEach(function (card, cardIndex) {
        card.classList.toggle("hidden", cardIndex !== index);
      });
      var card = cards[index];
      if (!card) return;
      card.querySelector("[data-study-front]").classList.remove("hidden");
      card.querySelector("[data-study-back]").classList.add("hidden");
      revealed = false;
      reveal.classList.remove("hidden");
      keep.classList.add("hidden");
      learned.classList.add("hidden");
      previous.disabled = index === 0;
      controls.classList.remove("hidden");
      done.classList.add("hidden");
      progress.textContent = String(index + 1) + " / " + String(cards.length);
    }

    function showAnswer() {
      if (revealed) return;
      var card = cards[index];
      card.querySelector("[data-study-front]").classList.add("hidden");
      card.querySelector("[data-study-back]").classList.remove("hidden");
      revealed = true;
      reveal.classList.add("hidden");
      keep.classList.remove("hidden");
      learned.classList.remove("hidden");
    }

    function advance() {
      if (!revealed) return;
      if (index < cards.length - 1) {
        index += 1;
        render();
        return;
      }
      cards[index].classList.add("hidden");
      controls.classList.add("hidden");
      done.classList.remove("hidden");
      progress.textContent = String(cards.length) + " / " + String(cards.length);
    }

    previous.addEventListener("click", function () {
      if (index > 0) {
        index -= 1;
        render();
      }
    });
    reveal.addEventListener("click", showAnswer);
    keep.addEventListener("click", advance);
    learned.addEventListener("click", function () {
      var card = cards[index];
      var formData = new FormData();
      formData.set("study_later", "0");
      learned.disabled = true;
      fetch(card.dataset.studyToggleUrl, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "fetch"
        },
        body: formData,
        credentials: "same-origin"
      })
        .then(function (response) {
          if (!response.ok) throw new Error("Impossible de mettre à jour la note.");
          learned.disabled = false;
          advance();
        })
        .catch(function (error) {
          learned.disabled = false;
          showToast(error.message);
        });
    });
    restart.addEventListener("click", function () {
      index = 0;
      render();
    });
    document.addEventListener("keydown", function (event) {
      if (
        event.target.closest("input, textarea, select, button, a") ||
        done.classList.contains("hidden") === false
      ) {
        return;
      }
      if (event.key === " " && !revealed) {
        event.preventDefault();
        showAnswer();
      } else if (event.key === "ArrowRight" && revealed) {
        advance();
      } else if (event.key === "ArrowLeft" && index > 0) {
        index -= 1;
        render();
      }
    });
    render();
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
  if (notePaste) notePaste.addEventListener("click", pasteNote);
  highlightButton.addEventListener("click", toggleHighlight);
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
  setupStudyDeck();
  fetchHighlights();
})();
