(function () {
  "use strict";

  /**
   * Return modal instance for element id.
   * @param {string} modalId
   * @returns {bootstrap.Modal|null}
   */
  function getModal(modalId) {
    var modalEl = document.getElementById(modalId);
    if (!modalEl || !window.bootstrap) {
      return null;
    }
    return bootstrap.Modal.getOrCreateInstance(modalEl);
  }

  /**
   * Escape HTML text.
   * @param {string} value
   * @returns {string}
   */
  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * Build new vault row HTML.
   * @param {Object} data
   * @returns {string}
   */
  function buildVaultRow(data) {
    var id = String(data.id || "");
    var serviceName = String(data.service_name || "");
    var label = String(data.label || "");

    return (
      '<tr id="vault-row-' + escapeHtml(id) + '" data-entry-id="' + escapeHtml(id) + '" class="vault-row-fade">' +
      '<td><div class="d-flex align-items-center gap-2"><span class="service-circle" style="background:#1a56db;"><i class="bi bi-plug"></i></span><span class="text-capitalize">' + escapeHtml(serviceName) + "</span></div></td>" +
      '<td class="text-truncate" style="max-width:260px;" title="' + escapeHtml(label) + '">' + escapeHtml(label) + "</td>" +
      "<td>You</td>" +
      "<td>just now</td>" +
      "<td>Never used</td>" +
      '<td class="text-end"><div class="btn-group btn-group-sm">' +
      '<button type="button" class="reveal-btn btn btn-sm btn-outline-secondary" data-entry-id="' + escapeHtml(id) + '" data-service-name="' + escapeHtml(serviceName) + '" data-label="' + escapeHtml(label) + '">Reveal</button>' +
      '<button type="button" class="copy-btn btn btn-sm btn-outline-primary" data-entry-id="' + escapeHtml(id) + '" data-service-name="' + escapeHtml(serviceName) + '" data-label="' + escapeHtml(label) + '">Copy</button>' +
      '<button type="button" class="edit-btn btn btn-sm btn-outline-dark" data-entry-id="' + escapeHtml(id) + '" data-service-name="' + escapeHtml(serviceName) + '" data-label="' + escapeHtml(label) + '">Edit</button>' +
      '<button type="button" class="delete-credential-btn btn btn-sm btn-outline-danger" data-entry-id="' + escapeHtml(id) + '" data-confirm="Delete this credential from vault?">Delete</button>' +
      "</div></td></tr>"
    );
  }

  /**
   * Reset reveal modal to initial state.
   */
  function resetRevealModal() {
    var revealModal = document.getElementById("reveal-modal");
    if (!revealModal) {
      return;
    }

    revealModal.dataset.failures = "0";

    var passwordInput = document.getElementById("reveal-password");
    var errorEl = document.getElementById("reveal-error");
    var passwordSection = document.getElementById("reveal-password-section");
    var valueSection = document.getElementById("revealed-value-section");
    var valueInput = document.getElementById("revealed-value");
    var countdown = document.getElementById("countdown-seconds");

    if (passwordInput) {
      passwordInput.value = "";
    }
    if (errorEl) {
      errorEl.style.display = "none";
      errorEl.textContent = "";
    }
    if (passwordSection) {
      passwordSection.style.display = "block";
    }
    if (valueSection) {
      valueSection.style.display = "none";
    }
    if (valueInput) {
      valueInput.value = "";
    }
    if (countdown) {
      countdown.textContent = "60";
    }

    if (window.__vaultCountdownTimer) {
      window.clearInterval(window.__vaultCountdownTimer);
      window.__vaultCountdownTimer = null;
    }

    var revealSubmitBtn = document.getElementById("reveal-submit-btn");
    if (revealSubmitBtn && window.resetButton) {
      window.resetButton(revealSubmitBtn);
    }
  }

  /**
   * Reveal credential from API.
   * @param {string} entryId
   * @param {string} password
   * @returns {Promise<string>}
   */
  async function revealCredential(entryId, password) {
    var response = await fetch("/vault/" + encodeURIComponent(entryId) + "/reveal", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
        "X-Requested-With": "XMLHttpRequest"
      },
      credentials: "same-origin",
      body: JSON.stringify({ password: password })
    });

    var payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || "Could not reveal credential");
    }

    return String(payload.data && payload.data.value ? payload.data.value : "");
  }

  /**
   * Update row label and service text.
   * @param {string} entryId
   * @param {string} serviceName
   * @param {string} label
   */
  function updateRowTexts(entryId, serviceName, label) {
    var row = document.getElementById("vault-row-" + entryId);
    if (!row) {
      return;
    }

    var serviceCell = row.querySelector("td:first-child .text-capitalize");
    var labelCell = row.querySelector("td:nth-child(2)");

    if (serviceCell) {
      serviceCell.textContent = serviceName;
    }
    if (labelCell) {
      labelCell.textContent = label;
      labelCell.title = label;
    }

    row.querySelectorAll('[data-entry-id="' + entryId + '"]').forEach(function (btn) {
      btn.dataset.serviceName = serviceName;
      btn.dataset.label = label;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var addForm = document.getElementById("add-credential-form");
    var revealModalEl = document.getElementById("reveal-modal");
    var copyModalEl = document.getElementById("copy-password-modal");
    var editModalEl = document.getElementById("edit-label-modal");
    var revealModal = getModal("reveal-modal");
    var copyModal = getModal("copy-password-modal");
    var editModal = getModal("edit-label-modal");
    var addModal = getModal("add-credential-modal");

    var securityBanner = document.getElementById("vault-security-banner");
    var dismissButton = document.getElementById("vault-security-banner-dismiss");
    if (securityBanner && localStorage.getItem("vault_security_banner_dismissed") === "1") {
      securityBanner.style.display = "none";
    }
    dismissButton && dismissButton.addEventListener("click", function () {
      localStorage.setItem("vault_security_banner_dismissed", "1");
    });

    if (revealModalEl) {
      revealModalEl.addEventListener("hidden.bs.modal", resetRevealModal);
    }

    document.addEventListener("click", function (event) {
      var revealBtn = event.target.closest(".reveal-btn");
      if (revealBtn && revealModalEl && revealModal) {
        revealModalEl.querySelector(".modal-content").dataset.targetEntryId = revealBtn.dataset.entryId || "";
        var meta = document.getElementById("reveal-entry-meta");
        if (meta) {
          meta.textContent = (revealBtn.dataset.serviceName || "") + " • " + (revealBtn.dataset.label || "");
        }
        resetRevealModal();
        revealModal.show();
        return;
      }

      var copyBtn = event.target.closest(".copy-btn");
      if (copyBtn && copyModalEl && copyModal) {
        copyModalEl.querySelector(".modal-content").dataset.targetEntryId = copyBtn.dataset.entryId || "";
        var copyInput = document.getElementById("copy-password-input");
        var copyError = document.getElementById("copy-password-error");
        if (copyInput) {
          copyInput.value = "";
        }
        if (copyError) {
          copyError.style.display = "none";
          copyError.textContent = "";
        }
        copyModal.show();
        return;
      }

      var editBtn = event.target.closest(".edit-btn");
      if (editBtn && editModalEl && editModal) {
        editModalEl.querySelector(".modal-content").dataset.targetEntryId = editBtn.dataset.entryId || "";
        var serviceInput = document.getElementById("edit-service-name");
        var labelInput = document.getElementById("edit-label");
        if (serviceInput) {
          serviceInput.value = editBtn.dataset.serviceName || "";
        }
        if (labelInput) {
          labelInput.value = editBtn.dataset.label || "";
        }
        editModal.show();
        return;
      }

      var deleteBtn = event.target.closest(".delete-credential-btn");
      if (!deleteBtn) {
        return;
      }

      if (deleteBtn.dataset.confirmed !== "true") {
        return;
      }
      deleteBtn.dataset.confirmed = "false";

      var entryId = deleteBtn.dataset.entryId;
      if (!entryId) {
        return;
      }

      if (window.setButtonLoading) {
        window.setButtonLoading(deleteBtn, "Deleting...");
      }

      fetch("/vault/" + encodeURIComponent(entryId), {
        method: "DELETE",
        headers: {
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
          "X-Requested-With": "XMLHttpRequest"
        },
        credentials: "same-origin"
      }).then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok || !payload.success) {
            throw new Error(payload.message || "Delete failed");
          }

          var row = document.getElementById("vault-row-" + entryId);
          if (row) {
            row.classList.add("removing");
            window.setTimeout(function () {
              row.remove();
              var tableBody = document.getElementById("vault-table-body");
              if (tableBody && tableBody.children.length === 0) {
                tableBody.innerHTML = '<tr id="vault-empty-row"><td colspan="6" class="text-center text-muted py-4">No credentials yet. Add your first credential to secure your integration keys.</td></tr>';
              }
            }, 260);
          }

          if (window.showToast) {
            window.showToast("Credential deleted", "success");
          }
        });
      }).catch(function (error) {
        if (window.showToast) {
          window.showToast(error.message || "Delete failed", "danger");
        }
      }).finally(function () {
        if (window.resetButton) {
          window.resetButton(deleteBtn);
        }
      });
    });

    var revealSubmitBtn = document.getElementById("reveal-submit-btn");
    revealSubmitBtn && revealSubmitBtn.addEventListener("click", async function () {
      var entryId = revealModalEl ? revealModalEl.querySelector(".modal-content").dataset.targetEntryId : "";
      var passwordInput = document.getElementById("reveal-password");
      var errorEl = document.getElementById("reveal-error");
      var password = String(passwordInput ? passwordInput.value : "");

      if (!entryId || !password) {
        if (errorEl) {
          errorEl.textContent = "Password is required.";
          errorEl.style.display = "block";
        }
        return;
      }

      var shouldResetRevealButton = true;

      try {
        if (window.setButtonLoading) {
          window.setButtonLoading(revealSubmitBtn, "Verifying password...");
        } else {
          revealSubmitBtn.disabled = true;
        }
        var value = await revealCredential(entryId, password);

        var passwordSection = document.getElementById("reveal-password-section");
        var valueSection = document.getElementById("revealed-value-section");
        var valueInput = document.getElementById("revealed-value");
        var countdownEl = document.getElementById("countdown-seconds");

        if (passwordSection) {
          passwordSection.style.display = "none";
        }
        if (valueSection) {
          valueSection.style.display = "block";
        }
        if (valueInput) {
          valueInput.value = value;
        }
        if (errorEl) {
          errorEl.style.display = "none";
        }

        var remaining = 60;
        if (countdownEl) {
          countdownEl.textContent = String(remaining);
        }

        if (window.__vaultCountdownTimer) {
          window.clearInterval(window.__vaultCountdownTimer);
          window.__vaultCountdownTimer = null;
        }

        window.__vaultCountdownTimer = window.setInterval(function () {
          remaining -= 1;
          if (countdownEl) {
            countdownEl.textContent = String(remaining);
          }

          if (remaining <= 0) {
            window.clearInterval(window.__vaultCountdownTimer);
            window.__vaultCountdownTimer = null;
            if (valueInput) {
              valueInput.value = "";
            }
            if (window.showToast) {
              window.showToast("Value hidden for security", "info");
            }
            resetRevealModal();
          }
        }, 1000);

        shouldResetRevealButton = false;
      } catch (error) {
        var failures = Number(revealModalEl.dataset.failures || "0") + 1;
        revealModalEl.dataset.failures = String(failures);
        var remainingAttempts = Math.max(0, 3 - failures);

        if (errorEl) {
          errorEl.textContent = "Incorrect password. " + remainingAttempts + " attempts remaining.";
          errorEl.style.display = "block";
        }

        if (failures >= 3) {
          if (revealModal) {
            revealModal.hide();
          }
          if (window.showToast) {
            window.showToast("Too many failed attempts. Please wait before trying again.", "warning");
          }
        }
      } finally {
        if (shouldResetRevealButton) {
          if (window.resetButton) {
            window.resetButton(revealSubmitBtn);
          } else {
            revealSubmitBtn.disabled = false;
          }
        }
      }
    });

    var copyValueButton = document.getElementById("copy-revealed-btn");
    copyValueButton && copyValueButton.addEventListener("click", function () {
      var valueInput = document.getElementById("revealed-value");
      var value = String(valueInput ? valueInput.value : "");
      if (!value) {
        return;
      }
      if (!navigator.clipboard || !navigator.clipboard.writeText) {
        if (window.showToast) {
          window.showToast("Clipboard access unavailable", "danger");
        }
        return;
      }
      navigator.clipboard.writeText(value).then(function () {
        if (window.showToast) {
          window.showToast("Copied to clipboard", "success");
        }
      }).catch(function () {
        if (window.showToast) {
          window.showToast("Copy failed", "danger");
        }
      });
    });

    var hideRevealedBtn = document.getElementById("hide-revealed-btn");
    hideRevealedBtn && hideRevealedBtn.addEventListener("click", function () {
      resetRevealModal();
      if (window.showToast) {
        window.showToast("Value hidden for security", "info");
      }
    });

    var copyPasswordSubmit = document.getElementById("copy-password-submit");
    copyPasswordSubmit && copyPasswordSubmit.addEventListener("click", async function () {
      var entryId = copyModalEl ? copyModalEl.querySelector(".modal-content").dataset.targetEntryId : "";
      var passwordInput = document.getElementById("copy-password-input");
      var errorEl = document.getElementById("copy-password-error");
      var password = String(passwordInput ? passwordInput.value : "");

      if (!entryId || !password) {
        if (errorEl) {
          errorEl.textContent = "Password is required.";
          errorEl.style.display = "block";
        }
        return;
      }

      try {
        if (window.setButtonLoading) {
          window.setButtonLoading(copyPasswordSubmit, "Verifying...");
        } else {
          copyPasswordSubmit.disabled = true;
        }
        var value = await revealCredential(entryId, password);
        if (!navigator.clipboard || !navigator.clipboard.writeText) {
          throw new Error("Clipboard access unavailable");
        }
        await navigator.clipboard.writeText(value);

        if (window.showToast) {
          window.showToast("Copied to clipboard. Please clear clipboard manually after 30 seconds.", "success");
        }
        window.setTimeout(function () {
          if (window.showToast) {
            window.showToast("Reminder: clear your clipboard if no longer needed.", "info");
          }
        }, 30000);

        if (copyModal) {
          copyModal.hide();
        }
        if (passwordInput) {
          passwordInput.value = "";
        }
      } catch (error) {
        if (errorEl) {
          errorEl.textContent = error.message || "Could not copy credential";
          errorEl.style.display = "block";
        }
      } finally {
        if (window.resetButton) {
          window.resetButton(copyPasswordSubmit);
        } else {
          copyPasswordSubmit.disabled = false;
        }
      }
    });

    var editSubmitBtn = document.getElementById("edit-credential-submit");
    editSubmitBtn && editSubmitBtn.addEventListener("click", async function () {
      var entryId = editModalEl ? editModalEl.querySelector(".modal-content").dataset.targetEntryId : "";
      var serviceInput = document.getElementById("edit-service-name");
      var labelInput = document.getElementById("edit-label");
      var serviceName = String(serviceInput ? serviceInput.value : "").trim();
      var label = String(labelInput ? labelInput.value : "").trim();

      if (!entryId || !serviceName || !label) {
        if (window.showToast) {
          window.showToast("Service and label are required", "warning");
        }
        return;
      }

      try {
        if (window.setButtonLoading) {
          window.setButtonLoading(editSubmitBtn, "Saving...");
        } else {
          editSubmitBtn.disabled = true;
        }
        var response = await fetch("/vault/" + encodeURIComponent(entryId), {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
            "X-Requested-With": "XMLHttpRequest"
          },
          credentials: "same-origin",
          body: JSON.stringify({ service_name: serviceName, label: label })
        });
        var payload = await response.json();
        if (!response.ok || !payload.success) {
          throw new Error(payload.message || "Could not update credential");
        }

        updateRowTexts(entryId, serviceName, label);
        if (editModal) {
          editModal.hide();
        }
        if (window.showToast) {
          window.showToast("Credential updated", "success");
        }
      } catch (error) {
        if (window.showToast) {
          window.showToast(error.message || "Update failed", "danger");
        }
      } finally {
        if (window.resetButton) {
          window.resetButton(editSubmitBtn);
        } else {
          editSubmitBtn.disabled = false;
        }
      }
    });

    addForm && addForm.addEventListener("submit", async function (event) {
      event.preventDefault();

      var serviceNameInput = document.getElementById("add-service-name");
      var labelInput = document.getElementById("add-label");
      var valueInput = document.getElementById("add-credential-value");
      var saveButton = document.getElementById("save-credential-btn");

      var serviceName = String(serviceNameInput ? serviceNameInput.value : "").trim();
      var label = String(labelInput ? labelInput.value : "").trim();
      var credentialValue = String(valueInput ? valueInput.value : "");

      if (!serviceName || !label || !credentialValue) {
        if (window.showToast) {
          window.showToast("All fields are required", "warning");
        }
        return;
      }

      try {
        if (saveButton && window.setButtonLoading) {
          window.setButtonLoading(saveButton, "Encrypting & saving...");
        } else if (saveButton) {
          saveButton.disabled = true;
        }

        var response = await fetch("/vault", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
            "X-Requested-With": "XMLHttpRequest"
          },
          credentials: "same-origin",
          body: JSON.stringify({
            service_name: serviceName,
            label: label,
            credential_value: credentialValue
          })
        });

        var payload = await response.json();
        if (!response.ok || !payload.success) {
          throw new Error(payload.message || "Could not save credential");
        }

        var body = document.getElementById("vault-table-body");
        var emptyRow = document.getElementById("vault-empty-row");
        if (emptyRow) {
          emptyRow.remove();
        }

        if (body) {
          body.insertAdjacentHTML("afterbegin", buildVaultRow(payload.data));
        }

        if (addModal) {
          addModal.hide();
        }

        addForm.reset();
        if (window.showToast) {
          window.showToast("Credential added and encrypted", "success");
        }
      } catch (error) {
        if (window.showToast) {
          window.showToast(error.message || "Save failed", "danger");
        }
      } finally {
        if (saveButton && window.resetButton) {
          window.resetButton(saveButton);
        } else if (saveButton) {
          saveButton.disabled = false;
        }
        if (valueInput) {
          valueInput.value = "";
        }
      }
    });

    var toggleAddBtn = document.getElementById("toggle-add-credential");
    toggleAddBtn && toggleAddBtn.addEventListener("click", function () {
      var input = document.getElementById("add-credential-value");
      if (!input) {
        return;
      }
      var hidden = input.type === "password";
      input.type = hidden ? "text" : "password";
      toggleAddBtn.innerHTML = hidden ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
    });

    var integrityLink = document.getElementById("vault-run-integrity-check");
    integrityLink && integrityLink.addEventListener("click", function (event) {
      event.preventDefault();
      if (window.setButtonLoading) {
        window.setButtonLoading(integrityLink, "Checking...");
      }
      fetch("/vault/verify-integrity", {
        headers: {
          "X-Requested-With": "XMLHttpRequest"
        },
        credentials: "same-origin"
      }).then(function (response) {
        return response.json();
      }).then(function (payload) {
        if (!payload.success) {
          throw new Error(payload.message || "Integrity check failed");
        }
        var data = payload.data || {};
        if (window.showToast) {
          window.showToast(
            "Integrity check complete. " + data.passing + " passed, " + data.failing + " failed.",
            data.failing > 0 ? "warning" : "success"
          );
        }
      }).catch(function (error) {
        if (window.showToast) {
          window.showToast(error.message || "Integrity check failed", "danger");
        }
      }).finally(function () {
        if (window.resetButton) {
          window.resetButton(integrityLink);
        }
      });
    });
  });
})();
