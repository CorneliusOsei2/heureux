(function () {
  "use strict";

  var root = document.querySelector("[data-question-bank]");
  if (!root) return;

  var errorMessage = document.querySelector("[data-memory-progress-error]");
  var statusClasses = [
    "progress-status--new",
    "progress-status--active",
    "progress-status--done"
  ];
  var mutationQueue = Promise.resolve();

  function setStatus(element, status, label) {
    if (!element) return;
    element.classList.remove.apply(element.classList, statusClasses);
    element.classList.add("progress-status--" + status);
    element.textContent = label;
  }

  function setProgress(bar, percent, status) {
    if (!bar) return;
    var fill = bar.querySelector("span");
    if (fill) {
      fill.classList.remove(
        "memory-progress-fill--new",
        "memory-progress-fill--active",
        "memory-progress-fill--done"
      );
      fill.classList.add("memory-progress-fill--" + status);
      fill.style.width = percent + "%";
    }
    bar.setAttribute("aria-label", percent + " % terminé");
  }

  function showError(message) {
    if (!errorMessage) return;
    errorMessage.textContent = message;
    errorMessage.classList.remove("hidden");
    errorMessage.scrollIntoView({
      behavior: "smooth",
      block: "nearest"
    });
  }

  function clearError() {
    if (!errorMessage) return;
    errorMessage.textContent = "";
    errorMessage.classList.add("hidden");
  }

  function readJson(response) {
    return response.json().catch(function () {
      throw new Error("La réponse du serveur est inattendue.");
    }).then(function (data) {
      if (!response.ok) {
        throw new Error(
          data.error || "Impossible d’enregistrer cette progression."
        );
      }
      return data;
    });
  }

  function updateQuestion(form, completed) {
    var row = form.closest("[data-question-bank-question]");
    var button = form.querySelector("button");
    var completedInput = form.querySelector("[data-memory-completed-input]");
    if (row) row.classList.toggle("is-complete", completed);
    if (completedInput) completedInput.value = completed ? "0" : "1";
    if (!button) return;
    var questionText = button.dataset.questionText || "cette question";
    button.setAttribute("aria-checked", completed ? "true" : "false");
    button.setAttribute(
      "aria-label",
      completed
        ? "Marquer comme non apprise : " + questionText
        : "Marquer comme apprise : " + questionText
    );
    button.title = completed
      ? "Question apprise"
      : "Marquer comme apprise";
  }

  function updateMemory(progress) {
    var summary = document.querySelector("[data-memory-progress-summary]");
    if (summary) {
      summary.classList.remove(
        "memory-learning-summary--new",
        "memory-learning-summary--active",
        "memory-learning-summary--done"
      );
      summary.classList.add(
        "memory-learning-summary--" + progress.status
      );
    }
    document.querySelectorAll("[data-memory-completed]").forEach(
      function (element) {
        element.textContent = progress.completed;
      }
    );
    setStatus(
      document.querySelector("[data-memory-status]"),
      progress.status,
      progress.label
    );
    setProgress(
      document.querySelector("[data-memory-progress-bar]"),
      progress.percent,
      progress.status
    );
  }

  function updateSection(progress) {
    var section = document.querySelector(
      '[data-memory-section="' + progress.number + '"]'
    );
    if (section) {
      var count = section.querySelector("[data-section-completed]");
      if (count) count.textContent = progress.completed;
      setStatus(
        section.querySelector("[data-section-status]"),
        progress.status,
        progress.label
      );
      setProgress(
        section.querySelector("[data-section-progress-bar]"),
        progress.percent,
        progress.status
      );
    }
    var indexEntry = document.querySelector(
      '[data-index-section="' + progress.number + '"]'
    );
    if (indexEntry) {
      var indexCount = indexEntry.querySelector("[data-index-completed]");
      if (indexCount) indexCount.textContent = progress.completed;
    }
  }

  root.addEventListener("submit", function (event) {
    var form = event.target.closest("[data-memory-progress-form]");
    if (!form) return;
    event.preventDefault();

    var button = form.querySelector("button");
    if (!button || form.dataset.pending === "true") return;
    clearError();
    form.dataset.pending = "true";
    button.setAttribute("aria-busy", "true");
    button.setAttribute("aria-disabled", "true");
    var formData = new FormData(form);

    mutationQueue = mutationQueue.then(function () {
      return fetch(form.action, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "X-CSRFToken": form.querySelector(
            "input[name='csrfmiddlewaretoken']"
          ).value,
          "X-Requested-With": "fetch"
        }
      })
        .then(readJson)
        .then(function (data) {
          updateQuestion(form, data.completed);
          updateMemory(data.memory);
          updateSection(data.section);
        })
        .catch(function (error) {
          showError(
            error.message || "Impossible d’enregistrer cette progression."
          );
        })
        .finally(function () {
          delete form.dataset.pending;
          button.removeAttribute("aria-busy");
          button.removeAttribute("aria-disabled");
        });
    });
  });
})();
