(function () {
  "use strict";

  /**
   * Safely show toast message when helper exists.
   * @param {string} message
   * @param {string} type
   */
  function notify(message, type) {
    if (window.showToast) {
      window.showToast(message, type || "info");
    }
  }

  /**
   * Escape user controlled values before injecting into HTML.
   * @param {unknown} value
   * @returns {string}
   */
  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * Convert date object to YYYY-MM-DD string.
   * @param {Date} date
   * @returns {string}
   */
  function toDateInputValue(date) {
    var year = date.getFullYear();
    var month = String(date.getMonth() + 1).padStart(2, "0");
    var day = String(date.getDate()).padStart(2, "0");
    return year + "-" + month + "-" + day;
  }

  /**
   * Validate and upload account avatar image.
   */
  function initAvatarUpload() {
    var input = document.getElementById("avatar-upload-input");
    var trigger = document.getElementById("change-avatar-btn");
    var preview = document.getElementById("avatar-preview");

    if (trigger && input) {
      trigger.addEventListener("click", function () {
        input.click();
      });
    }

    if (!input) {
      return;
    }

    input.addEventListener("change", function (event) {
      var file = event.target.files && event.target.files[0];
      if (!file) {
        return;
      }

      var allowedTypes = ["image/jpeg", "image/png", "image/webp", "image/gif"];
      if (allowedTypes.indexOf(file.type) === -1) {
        notify("Please upload an image file (JPG, PNG, WEBP)", "warning");
        input.value = "";
        return;
      }

      if (file.size > 5 * 1024 * 1024) {
        notify("Image must be under 5MB", "warning");
        input.value = "";
        return;
      }

      var reader = new FileReader();
      reader.onload = function (loadEvent) {
        if (preview) {
          if (preview.tagName === "IMG") {
            preview.src = loadEvent.target.result;
          } else {
            var newImg = document.createElement("img");
            newImg.id = "avatar-preview";
            newImg.src = loadEvent.target.result;
            newImg.alt = "Profile Avatar";
            newImg.className = "rounded-circle border object-fit-cover";
            newImg.style.width = "100px";
            newImg.style.height = "100px";
            newImg.style.display = "block";
            preview.parentNode.replaceChild(newImg, preview);
            preview = newImg;
          }
        }
      };
      reader.readAsDataURL(file);

      var formData = new FormData();
      formData.append("avatar", file);

      if (window.setButtonLoading && trigger) {
        window.setButtonLoading(trigger, "Uploading...");
      }

      fetch("/settings/account/avatar", {
        method: "POST",
        headers: {
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : ""
        },
        body: formData
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (payload) {
          if (!payload.success) {
            throw new Error(payload.message || "Failed to upload photo");
          }

          notify("Profile photo updated!", "success");

          var topbarAvatarImg = document.querySelector(".topbar-user-btn img.topbar-avatar");
          var topbarAvatarSpan = document.querySelector(".topbar-user-btn span.topbar-avatar");
          
          if (payload.data && payload.data.avatar_url) {
            if (topbarAvatarImg) {
              topbarAvatarImg.src = payload.data.avatar_url;
            } else if (topbarAvatarSpan) {
              var newTopbarImg = document.createElement("img");
              newTopbarImg.src = payload.data.avatar_url;
              newTopbarImg.className = "topbar-avatar";
              newTopbarImg.alt = "User avatar";
              topbarAvatarSpan.parentNode.replaceChild(newTopbarImg, topbarAvatarSpan);
            }
          }
        })
        .catch(function (error) {
          notify(error.message || "Failed to upload photo", "danger");
        })
        .finally(function () {
          if (window.resetButton && trigger) {
            window.resetButton(trigger);
          }
        });
    });
  }

  /**
   * Return password strength rules map.
   * @param {string} password
   * @returns {{length: boolean, upper: boolean, lower: boolean, digit: boolean, special: boolean}}
   */
  function getPasswordRules(password) {
    return {
      length: password.length >= 8,
      upper: /[A-Z]/.test(password),
      lower: /[a-z]/.test(password),
      digit: /\d/.test(password),
      special: /[^A-Za-z0-9]/.test(password)
    };
  }

  /**
   * Update password strength meter and checklist.
   * @param {string} password
   */
  function updatePasswordStrength(password) {
    var rules = getPasswordRules(password || "");
    var passed = Object.keys(rules).reduce(function (total, key) {
      return total + (rules[key] ? 1 : 0);
    }, 0);

    var percentage = (passed / 5) * 100;
    var bar = document.getElementById("password-strength-bar");
    if (bar) {
      bar.style.width = percentage + "%";
      bar.classList.remove("bg-danger", "bg-warning", "bg-success");
      if (percentage < 40) {
        bar.classList.add("bg-danger");
      } else if (percentage < 80) {
        bar.classList.add("bg-warning");
      } else {
        bar.classList.add("bg-success");
      }
    }

    var ruleMap = {
      length: "pw-rule-length",
      upper: "pw-rule-upper",
      lower: "pw-rule-lower",
      digit: "pw-rule-digit",
      special: "pw-rule-special"
    };

    Object.keys(ruleMap).forEach(function (key) {
      var element = document.getElementById(ruleMap[key]);
      if (!element) {
        return;
      }
      element.classList.toggle("text-success", rules[key]);
      element.classList.toggle("text-muted", !rules[key]);
    });
  }

  /**
   * Initialize password strength monitoring for change password form.
   */
  function initPasswordStrengthMeter() {
    var input = document.getElementById("new_password");
    if (!input) {
      return;
    }

    input.addEventListener("input", function () {
      updatePasswordStrength(input.value || "");
    });
  }

  /**
   * Initialize slug preview and sanitization behavior.
   */
  function initOrgSlugPreview() {
    var slugInput = document.getElementById("slug-input");
    var preview = document.getElementById("slug-preview");

    if (!slugInput) {
      return;
    }

    slugInput.addEventListener("input", function () {
      var slug = (slugInput.value || "")
        .toLowerCase()
        .replace(/[^a-z0-9-]/g, "-")
        .replace(/--+/g, "-")
        .replace(/^-+|-+$/g, "");

      slugInput.value = slug;
      if (preview) {
        preview.textContent = "agentflow.ai/" + (slug || "your-org");
      }
    });
  }

  /**
   * Keep branding color text synchronized with color input.
   */
  function initBrandingColorSync() {
    var colorInput = document.getElementById("brand-color-input");
    var colorText = document.getElementById("brand-color-text");

    if (!colorInput || !colorText) {
      return;
    }

    colorInput.addEventListener("input", function () {
      colorText.value = colorInput.value;
    });
  }

  /**
   * Preview organization logo image before upload.
   */
  function initOrgLogoPreview() {
    var logoInput = document.getElementById("org-logo-upload");
    var logoPreview = document.getElementById("org-logo-preview");

    if (!logoInput || !logoPreview) {
      return;
    }

    logoInput.addEventListener("change", function () {
      var file = logoInput.files && logoInput.files[0];
      if (!file) {
        return;
      }

      var reader = new FileReader();
      reader.onload = function (event) {
        logoPreview.src = event.target.result;
      };
      reader.readAsDataURL(file);
    });
  }

  /**
   * Require exact delete confirmation phrase before enabling account deletion.
   */
  function initDeleteAccountConfirmation() {
    var input = document.getElementById("delete-confirmation-text");
    var submitButton = document.getElementById("delete-account-submit");

    if (!input || !submitButton) {
      return;
    }

    input.addEventListener("input", function () {
      submitButton.disabled = input.value !== "DELETE MY ACCOUNT";
    });
  }

  /**
   * Enable organization delete button only when slug confirmation matches.
   */
  function initDeleteOrganizationConfirmation() {
    var input = document.getElementById("org-delete-confirm");
    var form = document.getElementById("delete-org-form");
    var button = document.getElementById("delete-org-btn");

    if (!input || !form || !button) {
      return;
    }

    var expected = String(window.currentOrgSlug || "").trim().toLowerCase();

    input.addEventListener("input", function () {
      button.disabled = String(input.value || "").trim().toLowerCase() !== expected;
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();

      if (button.disabled) {
        return;
      }

      var confirmed = window.confirm("This will permanently delete your organization. Continue?");
      if (!confirmed) {
        return;
      }

      if (window.setButtonLoading) {
        window.setButtonLoading(button, "Deleting...");
      }

      fetch("/settings/organization", {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : ""
        },
        body: JSON.stringify({ confirmation_slug: input.value.trim() })
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (payload) {
          if (!payload.success) {
            throw new Error(payload.message || "Unable to delete organization");
          }

          var redirect = payload.data && payload.data.redirect ? payload.data.redirect : "/";
          window.location.href = redirect;
        })
        .catch(function (error) {
          notify(error.message || "Unable to delete organization", "danger");
        })
        .finally(function () {
          if (window.resetButton) {
            window.resetButton(button);
          }
        });
    });
  }

  /**
   * Handle MFA disable modal form with AJAX submission.
   */
  function initMfaDisableSubmission() {
    var form = document.getElementById("disable-mfa-form");
    if (!form) {
      return;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();

      var submitButton = form.querySelector('button[type="submit"], input[type="submit"]');
      if (submitButton && window.setButtonLoading) {
        window.setButtonLoading(submitButton, "Disabling...");
      }

      var formData = new FormData(form);
      fetch("/auth/mfa/disable", {
        method: "POST",
        headers: {
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : ""
        },
        body: formData
      })
        .then(function () {
          window.location.href = "/settings/account";
        })
        .catch(function () {
          notify("Unable to update MFA settings right now.", "danger");
        })
        .finally(function () {
          if (submitButton && window.resetButton) {
            window.resetButton(submitButton);
          }
        });
    });
  }

  /**
   * Toggle custom API key expiry input based on selected expiry option.
   */
  function initApiKeyExpiryToggle() {
    var radios = document.querySelectorAll('input[name="expiry_option"]');
    var customInput = document.getElementById("api-key-custom-expiry");

    if (!radios.length || !customInput) {
      return;
    }

    function updateState() {
      var selected = document.querySelector('input[name="expiry_option"]:checked');
      var isCustom = selected && selected.value === "custom";
      customInput.disabled = !isCustom;
      if (!isCustom) {
        customInput.value = "";
      }
    }

    radios.forEach(function (radio) {
      radio.addEventListener("change", updateState);
    });

    updateState();
  }

  /**
   * Resolve API key expiry date value from selected option.
   * @param {HTMLFormElement} form
   * @returns {string}
   */
  function resolveApiKeyExpiry(form) {
    var selected = form.querySelector('input[name="expiry_option"]:checked');
    var customInput = document.getElementById("api-key-custom-expiry");

    if (!selected || selected.value === "never") {
      return "";
    }

    if (selected.value === "custom") {
      return customInput && customInput.value ? customInput.value : "";
    }

    var days = parseInt(selected.value, 10);
    if (!days || Number.isNaN(days)) {
      return "";
    }

    var targetDate = new Date();
    targetDate.setDate(targetDate.getDate() + days);
    return toDateInputValue(targetDate);
  }

  /**
   * Render one time API key alert and start hide countdown.
   * @param {string} rawKey
   */
  function renderNewApiKeyAlert(rawKey) {
    var container = document.getElementById("new-key-alert-container");
    if (!container) {
      return;
    }

    var existing = document.getElementById("new-key-alert");
    if (existing) {
      existing.remove();
    }

    var safeRawKey = escapeHtml(rawKey);

    var alert = document.createElement("div");
    alert.id = "new-key-alert";
    alert.className = "alert alert-success border shadow-sm";
    alert.innerHTML =
      '<div class="d-flex justify-content-between align-items-start gap-2">' +
      '<div>' +
      '<h5 class="alert-heading mb-2">Your new API key has been generated:</h5>' +
      '<pre class="mb-2"><code id="new-api-key-value" class="font-monospace">' + safeRawKey + '</code></pre>' +
      '<div class="small text-danger">WARNING: Copy this key now. You will NOT be able to see it again after closing this alert.</div>' +
      '<div class="small mt-2" id="new-key-countdown-text">This message will auto-hide in 120 seconds</div>' +
      '<div class="progress mt-2" style="height:6px;"><div id="new-key-progress" class="progress-bar bg-success" style="width:100%"></div></div>' +
      '</div>' +
      '<div class="d-flex flex-column gap-2">' +
      '<button type="button" class="btn btn-sm btn-outline-success" id="copy-new-key-btn">Copy Key</button>' +
      '<button type="button" class="btn btn-sm btn-outline-secondary" id="dismiss-new-key-btn">Dismiss</button>' +
      '</div>' +
      '</div>';

    container.prepend(alert);

    var copyButton = document.getElementById("copy-new-key-btn");
    var dismissButton = document.getElementById("dismiss-new-key-btn");

    if (copyButton) {
      copyButton.addEventListener("click", function () {
        if (window.copyToClipboard) {
          window.copyToClipboard(rawKey, copyButton);
        } else if (navigator.clipboard) {
          navigator.clipboard.writeText(rawKey);
          notify("API key copied", "success");
        }
      });
    }

    if (dismissButton) {
      dismissButton.addEventListener("click", function () {
        alert.remove();
      });
    }

    startApiKeyCountdown();
  }

  /**
   * Append a newly created API key row to the table.
   * @param {{key_id: string, label: string, key_prefix: string}} data
   * @param {string[]} scopes
   */
  function appendApiKeyRow(data, scopes) {
    var tableBody = document.querySelector("#api-keys-table tbody");
    if (!tableBody) {
      return;
    }

    var scopeBadges = scopes
      .map(function (scope) {
        if (scope === "read") {
          return '<span class="badge text-bg-primary">read</span>';
        }
        if (scope === "write") {
          return '<span class="badge text-bg-success">write</span>';
        }
        if (scope === "admin") {
          return '<span class="badge text-bg-warning">admin</span>';
        }
        return "";
      })
      .join(" ");

    var safeLabel = escapeHtml(data.label || "-");
    var safePrefix = escapeHtml(data.key_prefix || "api");

    var row = document.createElement("tr");
    row.id = "api-key-row-" + data.key_id;
    row.innerHTML =
      '<td class="fw-semibold">' + safeLabel + "</td>" +
      '<td><span class="badge text-bg-light border font-monospace">' + safePrefix + '...</span></td>' +
      '<td>' + scopeBadges + "</td>" +
      "<td>just now</td>" +
      "<td>Never used</td>" +
      "<td>Never</td>" +
      '<td><span class="badge text-bg-success">Active</span></td>' +
      '<td class="text-end"><button type="button" class="btn btn-sm btn-outline-danger revoke-api-key-btn" data-key-id="' + data.key_id + '">Revoke</button></td>';

    tableBody.prepend(row);
  }

  /**
   * Start 120 second countdown for new API key visibility.
   */
  function startApiKeyCountdown() {
    var alert = document.getElementById("new-key-alert");
    var progress = document.getElementById("new-key-progress");
    var countdownText = document.getElementById("new-key-countdown-text");

    if (!alert || !progress || !countdownText) {
      return;
    }

    var remaining = 120;
    var timer = window.setInterval(function () {
      remaining -= 1;
      var percentage = Math.max((remaining / 120) * 100, 0);
      progress.style.width = percentage + "%";
      countdownText.textContent = "This message will auto-hide in " + remaining + " seconds";

      if (remaining <= 0) {
        window.clearInterval(timer);
        alert.classList.add("fade");
        setTimeout(function () {
          if (alert.parentNode) {
            alert.parentNode.removeChild(alert);
          }
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText("");
          }
        }, 300);
      }
    }, 1000);
  }

  /**
   * Initialize API key creation form handling.
   */
  function initApiKeyCreateForm() {
    var form = document.getElementById("create-key-form");
    if (!form) {
      return;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();

      var submitButton = form.querySelector('button[type="submit"], input[type="submit"]');

      if (window.canCreateApiKeys === false) {
        notify("API access requires Pro plan or higher.", "warning");
        return;
      }

      var formData = new FormData(form);
      var scopes = formData.getAll("scopes");
      if (!scopes.length) {
        notify("Please select at least one scope", "warning");
        return;
      }

      var expiresAt = resolveApiKeyExpiry(form);
      if (expiresAt) {
        formData.set("expires_at", expiresAt);
      } else {
        formData.delete("expires_at");
      }

      if (submitButton && window.setButtonLoading) {
        window.setButtonLoading(submitButton, "Generating key...");
      }

      fetch("/settings/api-keys", {
        method: "POST",
        headers: {
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : ""
        },
        body: formData
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (payload) {
          if (!payload.success) {
            throw new Error(payload.message || "Unable to create API key");
          }

          var data = payload.data || {};
          renderNewApiKeyAlert(data.raw_key || "");
          appendApiKeyRow(data, scopes);
          notify(data.message || "API key created", "success");

          var modalElement = document.getElementById("create-key-modal");
          var modalInstance = modalElement ? bootstrap.Modal.getInstance(modalElement) : null;
          if (modalInstance) {
            modalInstance.hide();
          }

          form.reset();
          initApiKeyExpiryToggle();
        })
        .catch(function (error) {
          notify(error.message || "Unable to create API key", "danger");
        })
        .finally(function () {
          if (submitButton && window.resetButton) {
            window.resetButton(submitButton);
          }
        });

    });
  }

  /**
   * Initialize API key revoke button behavior.
   */
  function initApiKeyRevocation() {
    document.addEventListener("click", function (event) {
      var button = event.target.closest(".revoke-api-key-btn");
      if (!button) {
        return;
      }

      var keyId = button.getAttribute("data-key-id");
      if (!keyId) {
        return;
      }

      var confirmed = window.confirm("Are you sure you want to revoke this API key?");
      if (!confirmed) {
        return;
      }

      if (window.setButtonLoading) {
        window.setButtonLoading(button, "Revoking...");
      }

      fetch("/settings/api-keys/" + keyId, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : ""
        }
      })
        .then(function (response) {
          return response.json();
        })
        .then(function (payload) {
          if (!payload.success) {
            throw new Error(payload.message || "Unable to revoke API key");
          }

          var row = document.getElementById("api-key-row-" + keyId);
          if (row) {
            row.classList.add("text-decoration-line-through", "text-muted");
            var statusCell = row.children[6];
            var actionCell = row.children[7];
            if (statusCell) {
              statusCell.innerHTML = '<span class="badge text-bg-danger">Revoked</span>';
            }
            if (actionCell) {
              actionCell.innerHTML = '<span class="small text-muted">No actions</span>';
            }
          }

          notify("API key revoked", "success");
        })
        .catch(function (error) {
          notify(error.message || "Unable to revoke API key", "danger");
        })
        .finally(function () {
          if (window.resetButton) {
            window.resetButton(button);
          }
        });
    });
  }

  /**
   * Initialize settings page scripts.
   */
  function initSettingsPage() {
    initAvatarUpload();
    initPasswordStrengthMeter();
    initOrgSlugPreview();
    initBrandingColorSync();
    initOrgLogoPreview();
    initDeleteAccountConfirmation();
    initDeleteOrganizationConfirmation();
    initMfaDisableSubmission();
    initApiKeyExpiryToggle();
    initApiKeyCreateForm();
    initApiKeyRevocation();
  }

  window.startApiKeyCountdown = startApiKeyCountdown;

  document.addEventListener("DOMContentLoaded", initSettingsPage);
})();
