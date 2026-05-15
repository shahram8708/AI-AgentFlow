/**
 * Onboarding wizard controller for step navigation and AJAX persistence.
 */
(function () {
  "use strict";

  var currentStep = 1;
  var totalSteps = 4;
  var demoStarted = false;
  var demoTimers = [];

  /**
   * Return CSRF token for AJAX requests.
   * @returns {string}
   */
  function getCSRFToken() {
    if (window.getCSRFToken) {
      return window.getCSRFToken();
    }

    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  /**
   * Display inline onboarding validation error.
   * @param {string} message Error message.
   * @returns {void}
   */
  function showError(message) {
    var errorBox = document.getElementById("onboarding-error");
    if (!errorBox) {
      return;
    }

    errorBox.textContent = message;
    errorBox.classList.remove("d-none");
  }

  /**
   * Hide inline onboarding validation error.
   * @returns {void}
   */
  function clearError() {
    var errorBox = document.getElementById("onboarding-error");
    if (!errorBox) {
      return;
    }

    errorBox.classList.add("d-none");
    errorBox.textContent = "";
  }

  /**
   * Show a specific onboarding step and update progress indicators.
   * @param {number} stepNumber Step number from 1 to 4.
   * @returns {void}
   */
  function showStep(stepNumber) {
    var safeStep = Math.max(1, Math.min(Number(stepNumber || 1), totalSteps));
    currentStep = safeStep;

    document.querySelectorAll(".onboarding-step").forEach(function (stepEl) {
      stepEl.classList.add("d-none");
    });

    var targetStep = document.getElementById("step-" + String(safeStep));
    if (targetStep) {
      targetStep.classList.remove("d-none");
    }

    document.querySelectorAll(".onboarding-step-indicator").forEach(function (indicator) {
      var stepValue = Number(indicator.getAttribute("data-step"));
      indicator.classList.remove("current", "completed");

      if (stepValue < safeStep) {
        indicator.classList.add("completed");
      }
      if (stepValue === safeStep) {
        indicator.classList.add("current");
      }
    });

    var progressBar = document.getElementById("onboarding-progress-bar");
    if (progressBar) {
      progressBar.style.width = String(safeStep * 25) + "%";
    }

    localStorage.setItem("onboarding_step", String(safeStep));

    if (safeStep === 3) {
      runDemoAnimation();
    }

    clearError();
  }

  /**
   * Validate requirements for current step.
   * @param {number} step Step number.
   * @returns {boolean}
   */
  function validateStep(step) {
    if (step === 1) {
      var selectedPersona = document.querySelector(".persona-card.selected");
      if (!selectedPersona) {
        var personaGrid = document.getElementById("persona-grid");
        if (personaGrid) {
          personaGrid.classList.add("shake");
          window.setTimeout(function () {
            personaGrid.classList.remove("shake");
          }, 400);
        }
        showError("Please select your role to continue");
        return false;
      }
      return true;
    }

    if (step === 2) {
      var selectedTeam = document.querySelector(".team-size-option.selected");
      if (!selectedTeam) {
        showError("Please select your team size to continue");
        return false;
      }
      return true;
    }

    return true;
  }

  /**
   * Move to next step if validation succeeds.
   * @returns {Promise<void>}
   */
  async function nextStep() {
    if (!validateStep(currentStep)) {
      return;
    }

    var stepData = collectStepData(currentStep);
    var result = await submitStep(stepData);
    if (!result || !result.success) {
      showError("Could not save your progress. Please try again.");
      return;
    }

    showStep(Math.min(currentStep + 1, totalSteps));
  }

  /**
   * Move to previous onboarding step.
   * @returns {void}
   */
  function prevStep() {
    showStep(Math.max(currentStep - 1, 1));
  }

  /**
   * Collect current step values from UI.
   * @param {number} step Step number.
   * @returns {Object}
   */
  function collectStepData(step) {
    var selectedPersonaInput = document.getElementById("selected-persona");
    var selectedTeamInput = document.getElementById("selected-team-size");
    var useCaseInput = document.getElementById("primary-use-case");

    return {
      step: Number(step || currentStep),
      persona: selectedPersonaInput ? selectedPersonaInput.value : "",
      team_size: selectedTeamInput ? selectedTeamInput.value : "",
      use_case: useCaseInput ? useCaseInput.value : ""
    };
  }

  /**
   * Persist onboarding step through AJAX.
   * @param {Object} stepData Payload for backend endpoint.
   * @returns {Promise<Object|null>}
   */
  async function submitStep(stepData) {
    try {
      var response = await fetch("/onboarding", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCSRFToken(),
          "X-Requested-With": "XMLHttpRequest"
        },
        credentials: "same-origin",
        body: JSON.stringify(stepData)
      });

      if (!response.ok) {
        return null;
      }

      return response.json();
    } catch (error) {
      console.warn("Failed to submit onboarding step", error);
      return null;
    }
  }

  /**
   * Update visual status of a demo step row.
   * @param {number} index Demo step index 1 to 4.
   * @param {string} status One of pending, running, complete.
   * @param {string} label Label text.
   * @returns {void}
   */
  function setDemoStepStatus(index, status, label) {
    var row = document.getElementById("demo-step-" + String(index));
    if (!row) {
      return;
    }

    var iconWrap = row.querySelector(".demo-step-icon");
    var textEl = row.querySelector(".demo-step-text");

    row.classList.remove("pending", "running", "complete");
    row.classList.add(status);

    if (textEl && label) {
      textEl.textContent = label;
    }

    if (iconWrap) {
      if (status === "running") {
        iconWrap.innerHTML = '<i class="bi bi-arrow-repeat spinning"></i>';
      } else if (status === "complete") {
        iconWrap.innerHTML = '<i class="bi bi-check-circle-fill text-success"></i>';
      } else {
        iconWrap.innerHTML = '<i class="bi bi-clock text-muted"></i>';
      }
    }
  }

  /**
   * Run step 3 animation sequence once per visit.
   * @returns {void}
   */
  function runDemoAnimation() {
    if (demoStarted) {
      return;
    }

    demoStarted = true;
    clearDemoTimers();

    setDemoStepStatus(1, "pending", "Searching web sources for TCS...");
    setDemoStepStatus(2, "pending", "Extracting financial data...");
    setDemoStepStatus(3, "pending", "Generating structured report...");
    setDemoStepStatus(4, "pending", "Report complete! 847 words, 6 sources cited.");

    var outputPreview = document.getElementById("demo-output-preview");
    var downloadBtn = document.getElementById("demo-download-btn");
    if (outputPreview) {
      outputPreview.classList.remove("expanded");
    }
    if (downloadBtn) {
      downloadBtn.classList.add("d-none");
    }

    demoTimers.push(window.setTimeout(function () {
      setDemoStepStatus(1, "running", "Searching web sources for TCS...");
    }, 500));

    demoTimers.push(window.setTimeout(function () {
      setDemoStepStatus(1, "complete", "Searching web sources for TCS...");
      setDemoStepStatus(2, "running", "Extracting financial data...");
    }, 2000));

    demoTimers.push(window.setTimeout(function () {
      setDemoStepStatus(2, "complete", "Extracting financial data...");
      setDemoStepStatus(3, "running", "Generating structured report...");
    }, 3500));

    demoTimers.push(window.setTimeout(function () {
      setDemoStepStatus(3, "complete", "Generating structured report...");
      setDemoStepStatus(4, "running", "Report complete! 847 words, 6 sources cited.");
    }, 5000));

    demoTimers.push(window.setTimeout(function () {
      setDemoStepStatus(4, "complete", "Report complete! 847 words, 6 sources cited.");
      if (outputPreview) {
        outputPreview.classList.add("expanded");
      }
      if (downloadBtn) {
        downloadBtn.classList.remove("d-none");
      }
    }, 6500));

    demoTimers.push(window.setTimeout(async function () {
      var result = await submitStep(collectStepData(3));
      if (result && result.success) {
        showStep(4);
      }
    }, 7000));
  }

  /**
   * Clear all scheduled demo timers.
   * @returns {void}
   */
  function clearDemoTimers() {
    while (demoTimers.length) {
      window.clearTimeout(demoTimers.pop());
    }
  }

  /**
   * Apply selection state consistently for card like buttons.
   * @param {NodeListOf<Element>} elements Selectable elements.
   * @param {Element} selectedElement Active element.
   * @returns {void}
   */
  function setSelectedState(elements, selectedElement) {
    elements.forEach(function (element) {
      var isSelected = element === selectedElement;
      element.classList.toggle("selected", isSelected);
      element.classList.toggle("is-selected", isSelected);
      element.setAttribute("aria-pressed", isSelected ? "true" : "false");
    });
  }

  /**
   * Initialize persona card click handlers.
   * @returns {void}
   */
  function initPersonaSelection() {
    var cards = document.querySelectorAll(".persona-card");
    var personaInput = document.getElementById("selected-persona");
    var personaInputStep2 = document.getElementById("step2-persona");
    var continueButton = document.getElementById("step-1-continue");

    if (!cards.length) {
      return;
    }

    var initiallySelected = document.querySelector(".persona-card.selected, .persona-card.is-selected");
    setSelectedState(cards, initiallySelected || null);

    cards.forEach(function (card) {
      card.addEventListener("click", function () {
        setSelectedState(cards, card);
        var personaValue = card.getAttribute("data-persona") || "";

        if (personaInput) {
          personaInput.value = personaValue;
        }
        if (personaInputStep2) {
          personaInputStep2.value = personaValue;
        }
        if (continueButton) {
          continueButton.disabled = !personaValue;
        }
        clearError();
      });
    });
  }

  /**
   * Initialize team size button handlers.
   * @returns {void}
   */
  function initTeamSelection() {
    var options = document.querySelectorAll(".team-size-option");
    var teamInput = document.getElementById("selected-team-size");
    var continueButton = document.getElementById("step-2-continue");

    if (!options.length) {
      return;
    }

    var initiallySelected = document.querySelector(".team-size-option.selected, .team-size-option.is-selected");
    setSelectedState(options, initiallySelected || null);

    options.forEach(function (option) {
      option.addEventListener("click", function () {
        setSelectedState(options, option);
        var value = option.getAttribute("data-team-size") || "";
        if (teamInput) {
          teamInput.value = value;
        }
        if (continueButton) {
          continueButton.disabled = !value;
        }
        clearError();
      });
    });
  }

  /**
   * Attach AJAX handlers to step forms.
   * @returns {void}
   */
  function initFormSubmissions() {
    document.querySelectorAll(".onboarding-step-form").forEach(function (form) {
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        var submitButton = form.querySelector('button[type="submit"], input[type="submit"]');

        var stepField = form.querySelector('input[name="step"]');
        var stepNumber = stepField ? Number(stepField.value || currentStep) : currentStep;

        if (!validateStep(stepNumber)) {
          return;
        }

        var payload = collectStepData(stepNumber);
        if (stepNumber === 4) {
          payload.completed = true;
        }

        try {
          if (submitButton && window.setButtonLoading) {
            window.setButtonLoading(submitButton);
          }

          var result = await submitStep(payload);
          if (!result || !result.success) {
            showError("Could not save your progress. Please try again.");
            return;
          }

          if (stepNumber === 4 && result.data && result.data.redirect) {
            localStorage.removeItem("onboarding_step");
            window.location.href = result.data.redirect;
            return;
          }

          if (stepNumber < 4) {
            showStep(stepNumber + 1);
          }
        } finally {
          if (submitButton && window.resetButton) {
            window.resetButton(submitButton);
          }
        }
      });
    });
  }

  /**
   * Initialize wizard state and event hooks.
   * @returns {void}
   */
  function initWizard() {
    initPersonaSelection();
    initTeamSelection();
    initFormSubmissions();

    var storedStep = Number(localStorage.getItem("onboarding_step") || "1");
    if (storedStep > 1 && storedStep < 4) {
      showStep(storedStep);
      return;
    }

    var visibleStep = document.querySelector(".onboarding-step:not(.d-none)");
    if (visibleStep) {
      var initialStep = Number(visibleStep.getAttribute("data-step") || "1");
      showStep(initialStep);
    } else {
      showStep(1);
    }
  }

  window.showStep = showStep;
  window.nextStep = nextStep;
  window.prevStep = prevStep;

  document.addEventListener("DOMContentLoaded", initWizard);
})();
