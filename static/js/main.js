(function () {
  "use strict";

  /*
   * Global loading state system for action buttons and submit controls.
   * WHY: Prevent duplicate submissions and accidental double click actions across the app.
   * HOW TO USE:
   * 1) Add data-loading-text on a button for explicit copy, for example data-loading-text="Saving...".
   * 2) Add data-no-loading for UI only controls that should never show loading state.
   * 3) For custom async code, call window.setButtonLoading(button) before request and
   *    call window.resetButton(button) in finally.
   */

  var BUTTON_LOADING_STATE_ATTR = "data-original-html";
  var BUTTON_LOADING_INPUT_ATTR = "data-original-value";
  var BUTTON_LOADING_TS_ATTR = "data-loading-start-timestamp";
  var BUTTON_LOADING_ACTIVE_ATTR = "data-loading-active";
  var SWITCH_PREV_CHECKED_ATTR = "data-previous-checked";

  var loadingTextMap = [
    { key: "sign in", text: "Signing in..." },
    { key: "login", text: "Signing in..." },
    { key: "sign up", text: "Creating account..." },
    { key: "register", text: "Creating account..." },
    { key: "run", text: "Running..." },
    { key: "save", text: "Saving..." },
    { key: "submit", text: "Submitting..." },
    { key: "send", text: "Sending..." },
    { key: "create", text: "Creating..." },
    { key: "delete", text: "Deleting..." },
    { key: "remove", text: "Removing..." },
    { key: "upload", text: "Uploading..." },
    { key: "download", text: "Downloading..." },
    { key: "export", text: "Exporting..." },
    { key: "connect", text: "Connecting..." },
    { key: "disconnect", text: "Disconnecting..." },
    { key: "pay", text: "Processing payment..." },
    { key: "upgrade", text: "Processing..." },
    { key: "verify", text: "Verifying..." },
    { key: "confirm", text: "Confirming..." },
    { key: "invite", text: "Sending invitation..." },
    { key: "generate", text: "Generating..." },
    { key: "test", text: "Testing..." },
    { key: "reveal", text: "Verifying..." },
    { key: "enable", text: "Enabling..." },
    { key: "disable", text: "Disabling..." },
    { key: "ban", text: "Banning..." },
    { key: "unban", text: "Unbanning..." },
    { key: "impersonate", text: "Loading..." },
    { key: "archive", text: "Archiving..." },
    { key: "restore", text: "Restoring..." },
    { key: "duplicate", text: "Duplicating..." },
    { key: "cancel", text: "Cancelling..." },
    { key: "revoke", text: "Revoking..." },
    { key: "toggle", text: "Updating..." },
    { key: "mark", text: "Updating..." },
    { key: "reset", text: "Resetting..." },
    { key: "apply", text: "Applying..." },
    { key: "search", text: "Searching..." },
    { key: "filter", text: "Filtering..." },
    { key: "refresh", text: "Refreshing..." },
    { key: "resend", text: "Sending..." },
    { key: "import", text: "Importing..." },
    { key: "sync", text: "Syncing..." },
    { key: "continue", text: "Loading..." },
    { key: "next", text: "Loading..." },
    { key: "finish", text: "Processing..." },
    { key: "complete", text: "Processing..." }
  ];

  var lastClickedSubmit = null;

  /**
   * WHY: Keep classification centralized so selection controls are never treated as actions.
   * @param {HTMLElement} control
   * @returns {{ selection: boolean, action: boolean, excluded: boolean }}
   */
  function classifyButton(control) {
    if (!control) {
      return { selection: false, action: false, excluded: true };
    }

    var excluded = Boolean(
      control.disabled ||
      control.matches("[data-no-loading], [data-manual-loading='true'], .btn-close, .dropdown-toggle, .dropdown-toggle-split, [role='switch']") ||
      control.matches("[type='reset'], [data-bs-dismiss], [data-sidebar-toggle], [data-bs-toggle='dropdown'], [data-bs-toggle='modal'], [data-bs-toggle='collapse'], [data-bs-target]") ||
      control.classList.contains("nav-link")
    );

    var selection = Boolean(
      control.matches("[data-selection='true']") ||
      control.matches("[data-bs-toggle='pill'], [data-bs-toggle='tab']") ||
      control.matches("#view-list-btn, #view-grid-btn, #card-view-btn, #list-view-btn") ||
      control.matches("#billing-monthly-btn, #billing-annual-btn, #custom-period-trigger") ||
      control.matches(".task-category-pill, .template-filter-pill, .integration-filter-pill") ||
      control.matches(".category-filter-btn, .status-filter-btn, .trigger-pill, .category-pill") ||
      control.matches(".persona-card, .team-size-option, .schedule-type-card") ||
      control.matches(".day-pill-btn, .month-day-btn, .project-color-swatch, .project-icon-option")
    );

    if (control.matches("[data-confirm]") && control.getAttribute("data-confirmed") !== "true") {
      excluded = true;
    }

    var action = !excluded && !selection;

    if (action && control.tagName === "BUTTON") {
      var typeAttr = (control.getAttribute("type") || "").toLowerCase();
      var insideForm = Boolean(control.closest("form"));
      var isSubmitLike = typeAttr === "submit" || (typeAttr === "" && insideForm);
      if (!isSubmitLike) {
        action = Boolean(
          control.hasAttribute("data-loading-text") ||
          control.hasAttribute("data-auto-loading") ||
          control.hasAttribute("data-confirm") ||
          control.hasAttribute("data-action") ||
          control.hasAttribute("onclick")
        );
      }
    }

    if (action && control.tagName === "A") {
      if (!control.hasAttribute("data-loading-text") && !control.hasAttribute("data-action") && !control.hasAttribute("data-confirm")) {
        action = false;
      }

      var href = (control.getAttribute("href") || "").trim();
      if (action && (href === "" || href === "#" || href.indexOf("javascript:") === 0)) {
        action = control.hasAttribute("onclick") || control.hasAttribute("data-action") || control.hasAttribute("data-confirm");
      }
    }

    return {
      selection: selection,
      action: action,
      excluded: excluded
    };
  }

  /**
   * WHY: Provide consistent loading copy selection and allow explicit overrides first.
   * @param {HTMLElement} button
   * @returns {string}
   */
  function getLoadingText(button) {
    if (!button) {
      return "Please wait...";
    }

    var explicit = button.getAttribute("data-loading-text");
    if (explicit) {
      return explicit;
    }

    var source = [
      button.textContent || "",
      button.getAttribute("value") || "",
      button.getAttribute("name") || "",
      button.getAttribute("aria-label") || "",
      button.className || ""
    ].join(" ").toLowerCase();

    for (var i = 0; i < loadingTextMap.length; i += 1) {
      if (source.indexOf(loadingTextMap[i].key) >= 0) {
        return loadingTextMap[i].text;
      }
    }

    return "Please wait...";
  }

  /**
   * WHY: Keep spinner visible against filled button variants while respecting outline variants.
   * @param {HTMLElement} button
   * @returns {string}
   */
  function getSpinnerClasses(button) {
    var className = button && button.className ? button.className : "";
    var isFilledPrimary = className.indexOf("btn-primary") >= 0 || className.indexOf("btn-danger") >= 0;
    return isFilledPrimary
      ? "spinner-border spinner-border-sm text-white"
      : "spinner-border spinner-border-sm";
  }

  /**
   * WHY: Apply one source of truth for disabling action controls before network activity starts.
   * @param {HTMLElement} button
   * @param {string=} loadingText
   * @returns {HTMLElement|null}
   */
  function setButtonLoading(button, loadingText) {
    if (!button || button.disabled || button.getAttribute(BUTTON_LOADING_ACTIVE_ATTR) === "true") {
      return null;
    }

    var resolvedText = loadingText || getLoadingText(button);
    var isInputSubmit = button.tagName === "INPUT";
    if (isInputSubmit) {
      if (!button.hasAttribute(BUTTON_LOADING_INPUT_ATTR)) {
        button.setAttribute(BUTTON_LOADING_INPUT_ATTR, button.value || "");
      }
      button.value = resolvedText;
    } else {
      if (!button.hasAttribute(BUTTON_LOADING_STATE_ATTR)) {
        button.setAttribute(BUTTON_LOADING_STATE_ATTR, button.innerHTML);
      }
      var spinnerClass = getSpinnerClasses(button);
      button.innerHTML = '<span class="' + spinnerClass + '" role="status" aria-hidden="true"></span> ' + resolvedText;
    }

    button.setAttribute(BUTTON_LOADING_ACTIVE_ATTR, "true");
    button.setAttribute(BUTTON_LOADING_TS_ATTR, String(Date.now()));
    button._loading = true;
    button.disabled = true;
    button.classList.add("disabled");
    return button;
  }

  /**
   * WHY: Restore loading controls safely across success, error, and cancellation paths.
   * @param {HTMLElement} button
   * @returns {void}
   */
  function resetButton(button) {
    if (!button) {
      return;
    }

    if (button.hasAttribute(BUTTON_LOADING_STATE_ATTR)) {
      button.innerHTML = button.getAttribute(BUTTON_LOADING_STATE_ATTR) || "";
      button.removeAttribute(BUTTON_LOADING_STATE_ATTR);
    }

    if (button.hasAttribute(BUTTON_LOADING_INPUT_ATTR)) {
      button.value = button.getAttribute(BUTTON_LOADING_INPUT_ATTR) || "";
      button.removeAttribute(BUTTON_LOADING_INPUT_ATTR);
    }

    button.removeAttribute(BUTTON_LOADING_ACTIVE_ATTR);
    button.removeAttribute(BUTTON_LOADING_TS_ATTR);
    button._loading = false;
    button.disabled = false;
    button.classList.remove("disabled");

    var parentForm = button.closest ? button.closest("form") : null;
    if (parentForm) {
      parentForm._loading = false;
    }
  }

  /**
   * WHY: Ensure a single recovery path exists for unexpected runtime errors and unload edge cases.
   * @returns {void}
   */
  function resetAllButtons() {
    document.querySelectorAll("[" + BUTTON_LOADING_ACTIVE_ATTR + "='true']").forEach(function (button) {
      resetButton(button);
    });

    document.querySelectorAll("button, a, input[type='submit'], input[type='button']").forEach(function (control) {
      control._loading = false;
    });

    document.querySelectorAll("form").forEach(function (form) {
      form._loading = false;
    });
  }

  /**
   * WHY: Toggle switches cannot contain spinner HTML, so an adjacent indicator is required.
   * @param {HTMLInputElement} toggle
   * @returns {HTMLElement|null}
   */
  function setToggleLoading(toggle) {
    if (!toggle || toggle.disabled) {
      return null;
    }

    toggle.setAttribute(SWITCH_PREV_CHECKED_ATTR, toggle.checked ? "true" : "false");
    toggle.disabled = true;
    toggle.classList.add("disabled");

    var existing = toggle.parentNode ? toggle.parentNode.querySelector(".toggle-loading-spinner") : null;
    if (existing) {
      return existing;
    }

    var spinner = document.createElement("span");
    spinner.className = "toggle-loading-spinner spinner-border spinner-border-sm ms-2";
    spinner.setAttribute("role", "status");
    spinner.setAttribute("aria-hidden", "true");
    if (toggle.parentNode) {
      toggle.parentNode.appendChild(spinner);
    }
    return spinner;
  }

  /**
   * WHY: Normalize toggle cleanup and optional optimistic rollback for failed requests.
   * @param {HTMLInputElement} toggle
   * @param {boolean=} rollback
   * @returns {void}
   */
  function resetToggleLoading(toggle, rollback) {
    if (!toggle) {
      return;
    }

    var spinner = toggle.parentNode ? toggle.parentNode.querySelector(".toggle-loading-spinner") : null;
    if (spinner) {
      spinner.remove();
    }

    if (rollback === true && toggle.hasAttribute(SWITCH_PREV_CHECKED_ATTR)) {
      toggle.checked = toggle.getAttribute(SWITCH_PREV_CHECKED_ATTR) === "true";
    }

    toggle.removeAttribute(SWITCH_PREV_CHECKED_ATTR);
    toggle.disabled = false;
    toggle.classList.remove("disabled");
  }

  /**
   * WHY: Limit delegated lookup depth so nested icon spans still map to intended controls.
   * @param {EventTarget} target
   * @returns {HTMLElement|null}
   */
  function findActionControl(target) {
    var node = target;
    var depth = 0;
    while (node && node !== document && depth < 4) {
      if (node.matches && node.matches("button, a, input[type='submit'], input[type='button']")) {
        return node;
      }
      node = node.parentElement;
      depth += 1;
    }
    return null;
  }

  /**
   * WHY: Install delegated capture listeners once so all pages inherit consistent behavior automatically.
   * @returns {void}
   */
  function initGlobalButtonLoading() {
    document.addEventListener("click", function (event) {
      var control = findActionControl(event.target);
      if (!control) {
        return;
      }

      if (control.matches("input[type='checkbox'][role='switch'][data-toggle-loading='true']")) {
        setToggleLoading(control);
        return;
      }

      var classification = classifyButton(control);
      if (classification.excluded || classification.selection || !classification.action) {
        return;
      }

      if (control._loading === true || control.getAttribute(BUTTON_LOADING_ACTIVE_ATTR) === "true") {
        event.preventDefault();
        event.stopPropagation();
        return;
      }

      control._loading = true;

      if (control.matches("button[type='submit'], input[type='submit']")) {
        lastClickedSubmit = control;
      }

      window.setTimeout(function () {
        if (!control || !document.body.contains(control)) {
          return;
        }
        if (control._loading !== true || control.getAttribute(BUTTON_LOADING_ACTIVE_ATTR) === "true") {
          return;
        }
        setButtonLoading(control);
      }, 0);
    }, true);

    document.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form || form.tagName !== "FORM") {
        return;
      }

      if (form.matches(".onboarding-step-form, [data-skip-global-loading='true'], [data-manual-loading='true']")) {
        return;
      }

      if (form._loading === true) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }

      form._loading = true;

      var submitter = event.submitter || lastClickedSubmit;
      if (!submitter || !form.contains(submitter)) {
        submitter = form.querySelector("button[type='submit'], input[type='submit'], button:not([type])");
      }

      if (submitter) {
        var submitClassification = classifyButton(submitter);
        if (!submitClassification.excluded && !submitClassification.selection && submitClassification.action) {
          if (submitter._loading !== true) {
            submitter._loading = true;
          }

          window.setTimeout(function () {
            if (!submitter || !document.body.contains(submitter)) {
              return;
            }
            if (submitter._loading !== true || submitter.getAttribute(BUTTON_LOADING_ACTIVE_ATTR) === "true") {
              return;
            }
            setButtonLoading(submitter);
          }, 0);
        }
      }
    }, true);

    window.setInterval(function () {
      var now = Date.now();
      document.querySelectorAll("[" + BUTTON_LOADING_ACTIVE_ATTR + "='true']").forEach(function (button) {
        if (button.hasAttribute("data-no-reset")) {
          return;
        }
        var startedAt = Number(button.getAttribute(BUTTON_LOADING_TS_ATTR) || "0");
        if (startedAt > 0 && now - startedAt >= 30000) {
          resetButton(button);
        }
      });
    }, 5000);

    window.addEventListener("pagehide", resetAllButtons);
    window.addEventListener("beforeunload", resetAllButtons);
  }

  function formatNumber(value) {
    return Number(value || 0).toLocaleString("en-IN");
  }

  function formatINR(paise) {
    var rupees = Math.floor(Number(paise || 0) / 100);
    return "\u20B9" + rupees.toLocaleString("en-IN");
  }

  function getCSRFToken() {
    var tokenMeta = document.querySelector('meta[name="csrf-token"]');
    if (tokenMeta) {
      return tokenMeta.getAttribute("content") || "";
    }

    var csrfCookie = document.cookie
      .split(";")
      .map(function (part) {
        return part.trim();
      })
      .find(function (part) {
        return part.indexOf("csrf_token=") === 0;
      });

    if (!csrfCookie) {
      return "";
    }

    return decodeURIComponent(csrfCookie.split("=")[1] || "");
  }

  function getBootstrapObject() {
    if (typeof window.bootstrap === "undefined" || !window.bootstrap) {
      return null;
    }
    return window.bootstrap;
  }

  function showToast(message, type, duration) {
    var toastType = type || "success";
    var timeout = Number(duration || 4000);
    var container = document.getElementById("toast-container");
    if (!container) {
      return;
    }

    var toastEl = document.createElement("div");
    toastEl.className = "toast toast-" + toastType + " align-items-center border-0";
    toastEl.setAttribute("role", "alert");
    toastEl.setAttribute("aria-live", "assertive");
    toastEl.setAttribute("aria-atomic", "true");

    toastEl.innerHTML =
      '<div class="d-flex">' +
      '<div class="toast-body">' + message + "</div>" +
      '<button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>' +
      "</div>";

    container.appendChild(toastEl);
    var bootstrapObj = getBootstrapObject();
    if (!bootstrapObj || !bootstrapObj.Toast) {
      toastEl.classList.add("show");
      window.setTimeout(function () {
        toastEl.remove();
      }, timeout);
      return;
    }

    var bsToast = new bootstrapObj.Toast(toastEl, { delay: timeout, autohide: true });
    bsToast.show();

    toastEl.addEventListener("hidden.bs.toast", function () {
      toastEl.remove();
    });
  }

  function hidePageLoader() {
    var loader = document.getElementById("page-loader");
    if (!loader) {
      return;
    }
    loader.classList.add("hidden");
    window.setTimeout(function () {
      if (loader.parentNode) {
        loader.parentNode.removeChild(loader);
      }
    }, 450);
  }

  function handleNavbarScroll() {
    var navbar = document.querySelector(".navbar");
    if (!navbar) {
      return;
    }

    if (navbar.classList.contains("public-navbar")) {
      return;
    }

    if (window.scrollY > 50) {
      navbar.classList.add("scrolled");
    } else {
      navbar.classList.remove("scrolled");
    }
  }

  function animateCounters() {
    var counters = document.querySelectorAll(".counter-animate");
    if (!counters.length) {
      return;
    }

    var easeOutCubic = function (t) {
      return 1 - Math.pow(1 - t, 3);
    };

    var runCounter = function (el) {
      var target = Number(el.getAttribute("data-target") || "0");
      var duration = 2000;
      var startTime = null;
      el.classList.add("is-visible");

      var tick = function (timestamp) {
        if (!startTime) {
          startTime = timestamp;
        }
        var progress = Math.min((timestamp - startTime) / duration, 1);
        var eased = easeOutCubic(progress);
        var currentValue = Math.floor(target * eased);
        el.textContent = formatNumber(currentValue);

        if (progress < 1) {
          window.requestAnimationFrame(tick);
        } else {
          el.textContent = formatNumber(target);
        }
      };

      window.requestAnimationFrame(tick);
    };

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) {
            return;
          }
          var el = entry.target;
          if (!el.dataset.animated) {
            runCounter(el);
            el.dataset.animated = "true";
          }
          observer.unobserve(el);
        });
      },
      { threshold: 0.35 }
    );

    counters.forEach(function (counter) {
      observer.observe(counter);
    });
  }

  function autoDismissFlash() {
    var bootstrapObj = getBootstrapObject();
    if (!bootstrapObj || !bootstrapObj.Alert) {
      return;
    }

    var alerts = document.querySelectorAll(".alert");
    alerts.forEach(function (alert) {
      window.setTimeout(function () {
        if (!alert || !alert.classList.contains("show")) {
          return;
        }
        var bsAlert = bootstrapObj.Alert.getOrCreateInstance(alert);
        bsAlert.close();
      }, 5000);
    });
  }

  function connectTaskStream(taskId, onMessage) {
    var retryDelay = 2000;
    var source = null;
    var stopped = false;

    var connect = function () {
      if (stopped) {
        return;
      }
      source = new EventSource("/api/tasks/" + encodeURIComponent(taskId) + "/stream");

      source.onmessage = function (event) {
        retryDelay = 2000;
        try {
          var payload = JSON.parse(event.data);
          if (typeof onMessage === "function") {
            onMessage(payload);
          }
        } catch (error) {
          console.error("Invalid task stream payload", error);
        }
      };

      source.onerror = function () {
        if (source) {
          source.close();
        }
        if (stopped) {
          return;
        }
        window.setTimeout(function () {
          retryDelay = Math.min(retryDelay * 2, 30000);
          connect();
        }, retryDelay);
      };
    };

    connect();

    return {
      close: function () {
        stopped = true;
        if (source) {
          source.close();
        }
      },
    };
  }

  function applySidebarState() {
    if (window.innerWidth < 768) {
      document.body.classList.remove("sidebar-collapsed");
      return;
    }

    var collapsed = localStorage.getItem("sidebarCollapsed") === "true";
    document.body.classList.toggle("sidebar-collapsed", collapsed);
  }

  function initSidebarToggle() {
    applySidebarState();

    var toggles = document.querySelectorAll("[data-sidebar-toggle]");
    toggles.forEach(function (toggle) {
      toggle.addEventListener("click", function () {
        if (window.innerWidth < 768) {
          document.body.classList.toggle("mobile-sidebar-open");
          return;
        }

        document.body.classList.toggle("sidebar-collapsed");
        localStorage.setItem(
          "sidebarCollapsed",
          document.body.classList.contains("sidebar-collapsed") ? "true" : "false"
        );
      });
    });

    document.addEventListener("click", function (event) {
      if (window.innerWidth >= 768) {
        return;
      }

      var clickedInsideSidebar = event.target.closest("#app-sidebar");
      var clickedToggle = event.target.closest("[data-sidebar-toggle]");
      if (!clickedInsideSidebar && !clickedToggle) {
        document.body.classList.remove("mobile-sidebar-open");
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        document.body.classList.remove("mobile-sidebar-open");
      }
    });

    window.addEventListener("resize", function () {
      if (window.innerWidth >= 768) {
        document.body.classList.remove("mobile-sidebar-open");
        applySidebarState();
      }
    });
  }

  function initSidebarTooltips() {
    var bootstrapObj = getBootstrapObject();
    if (!bootstrapObj || !bootstrapObj.Tooltip) {
      return;
    }

    var tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggerList.forEach(function (tooltipTriggerEl) {
      bootstrapObj.Tooltip.getOrCreateInstance(tooltipTriggerEl);
    });
  }

  function markActiveSidebarLink() {
    var currentPath = window.location.pathname;
    var links = document.querySelectorAll(".sidebar-link");

    var hasServerAssignedActive = Array.prototype.some.call(links, function (link) {
      return link.classList.contains("active");
    });
    if (hasServerAssignedActive) {
      return;
    }

    links.forEach(function (link) {
      try {
        var href = new URL(link.href, window.location.origin).pathname;
        if (href !== "/" && (currentPath === href || currentPath.indexOf(href + "/") === 0)) {
          link.classList.add("active");
        } else if (href === "/" && currentPath === "/") {
          link.classList.add("active");
        }
      } catch (error) {
        console.warn("Unable to parse link URL", error);
      }
    });
  }

  function copyToClipboard(text, buttonElement) {
    return navigator.clipboard.writeText(text).then(function () {
      if (!buttonElement) {
        return;
      }
      var originalText = buttonElement.textContent;
      buttonElement.textContent = "Copied! \u2713";
      buttonElement.disabled = true;
      window.setTimeout(function () {
        buttonElement.textContent = originalText;
        buttonElement.disabled = false;
      }, 1400);
    });
  }

  function initDynamicValidation() {
    var forms = document.querySelectorAll(".needs-validation");

    forms.forEach(function (form) {
      form.setAttribute("novalidate", "novalidate");

      form.addEventListener("submit", function (event) {
        if (!form.checkValidity()) {
          event.preventDefault();
          event.stopPropagation();
        }
        form.classList.add("was-validated");
      });

      var fields = form.querySelectorAll("input, select, textarea");
      fields.forEach(function (field) {
        field.addEventListener("blur", function () {
          if (!field.checkValidity()) {
            field.classList.add("is-invalid");
            field.classList.remove("is-valid");
          } else {
            field.classList.add("is-valid");
            field.classList.remove("is-invalid");
          }
        });
      });
    });
  }

  function createConfirmModal() {
    var existing = document.getElementById("confirmActionModal");
    if (existing) {
      return existing;
    }

    var modal = document.createElement("div");
    modal.className = "modal fade";
    modal.id = "confirmActionModal";
    modal.tabIndex = -1;
    modal.setAttribute("aria-hidden", "true");
    modal.innerHTML =
      '<div class="modal-dialog modal-dialog-centered">' +
      '<div class="modal-content">' +
      '<div class="modal-header">' +
      '<h5 class="modal-title">Please confirm</h5>' +
      '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
      "</div>" +
      '<div class="modal-body"><p id="confirmActionMessage" class="mb-0"></p></div>' +
      '<div class="modal-footer">' +
      '<button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>' +
      '<button type="button" class="btn btn-primary" id="confirmActionProceed">Confirm</button>' +
      "</div>" +
      "</div>" +
      "</div>";

    document.body.appendChild(modal);
    return modal;
  }

  function initConfirmDialogs() {
    var bootstrapObj = getBootstrapObject();
    if (!bootstrapObj || !bootstrapObj.Modal) {
      return;
    }

    var modalEl = createConfirmModal();
    var messageEl = modalEl.querySelector("#confirmActionMessage");
    var proceedBtn = modalEl.querySelector("#confirmActionProceed");
    var modal = bootstrapObj.Modal.getOrCreateInstance(modalEl);
    var pendingAction = null;

    document.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-confirm]");
      if (!trigger) {
        return;
      }

      if (trigger.dataset.confirmed === "true") {
        trigger.dataset.confirmed = "false";
        return;
      }

      event.preventDefault();
      var message = trigger.getAttribute("data-confirm") || "Are you sure you want to continue?";
      messageEl.textContent = message;

      pendingAction = function () {
        if (trigger.tagName === "A" && trigger.href) {
          window.location.href = trigger.href;
          return;
        }

        var parentForm = trigger.closest("form");
        if (parentForm) {
          parentForm.submit();
          return;
        }

        trigger.dataset.confirmed = "true";
        trigger.click();
      };

      modal.show();
    });

    proceedBtn.addEventListener("click", function () {
      modal.hide();
      if (typeof pendingAction === "function") {
        pendingAction();
        pendingAction = null;
      }
    });
  }

  function initKeyboardShortcuts() {
    document.addEventListener("keydown", function (event) {
      var isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
      var comboPressed = (isMac && event.metaKey && event.key.toLowerCase() === "k") ||
        (!isMac && event.ctrlKey && event.key.toLowerCase() === "k");

      if (!comboPressed) {
        return;
      }

      event.preventDefault();
      var searchInput = document.querySelector("#global-search, [data-topbar-search]");
      if (searchInput) {
        searchInput.focus();
      }
    });
  }

  function initGlobalSearch() {
    var searchForm = document.getElementById("global-search-form");
    var searchInput = document.getElementById("global-search");

    if (!searchForm || !searchInput) {
      return;
    }

    searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var query = String(searchInput.value || "").trim();
      if (!query) {
        window.location.href = "/tasks";
        return;
      }
      window.location.href = "/tasks?search=" + encodeURIComponent(query);
    });
  }

  function initCopyButtons() {
    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-copy-text]");
      if (!button) {
        return;
      }
      event.preventDefault();
      var text = button.getAttribute("data-copy-text") || "";
      copyToClipboard(text, button).catch(function (error) {
        console.error("Copy failed", error);
        showToast("Could not copy text", "error");
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initGlobalButtonLoading();
    handleNavbarScroll();
    window.addEventListener("scroll", handleNavbarScroll, { passive: true });
    animateCounters();
    autoDismissFlash();
    initSidebarToggle();
    initSidebarTooltips();
    markActiveSidebarLink();
    initDynamicValidation();
    initConfirmDialogs();
    initKeyboardShortcuts();
    initCopyButtons();
    initGlobalSearch();
    hidePageLoader();
  });

  window.addEventListener("load", hidePageLoader);
  window.addEventListener("pageshow", hidePageLoader);
  window.setTimeout(hidePageLoader, 2200);

  window.showToast = showToast;
  window.connectTaskStream = connectTaskStream;
  window.copyToClipboard = copyToClipboard;
  window.formatINR = formatINR;
  window.getCSRFToken = getCSRFToken;
  window.setButtonLoading = setButtonLoading;
  window.resetButton = resetButton;
  window.resetAllButtons = resetAllButtons;
  window.getLoadingText = getLoadingText;
  window.setToggleLoading = setToggleLoading;
  window.resetToggleLoading = resetToggleLoading;
})();
