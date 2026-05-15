(function () {
  "use strict";

  /**
   * Return JSON response or throw.
   * @param {Response} response
   * @returns {Promise<Object>}
   */
  async function parseJsonResponse(response) {
    var payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || "Request failed");
    }
    return payload;
  }

  /**
   * Send JSON request with CSRF header.
   * @param {string} url
   * @param {string} method
   * @param {Object=} body
   * @returns {Promise<Object>}
   */
  async function sendJson(url, method, body) {
    var response = await fetch(url, {
      method: method,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
        "X-Requested-With": "XMLHttpRequest"
      },
      credentials: "same-origin",
      body: body ? JSON.stringify(body) : null
    });
    return parseJsonResponse(response);
  }

  /**
   * Sync nav tab active states from current URL.
   */
  function syncProjectTabState() {
    var params = new URLSearchParams(window.location.search);
    var activeTab = String(params.get("tab") || "tasks").toLowerCase();

    document.querySelectorAll(".nav-tabs .nav-link").forEach(function (link) {
      var href = link.getAttribute("href") || "";
      var tabName = "tasks";
      if (href.indexOf("?tab=") >= 0) {
        tabName = href.split("?tab=")[1].split("&")[0];
      }
      var isActive = tabName === activeTab;
      link.classList.toggle("active", isActive);
    });
  }

  /**
   * Initialize inline description edit behavior.
   */
  function initInlineDescriptionEdit() {
    var descriptionEl = document.getElementById("project-description");
    if (!descriptionEl) {
      return;
    }

    descriptionEl.addEventListener("click", function () {
      if (descriptionEl.dataset.editing === "1") {
        return;
      }
      descriptionEl.dataset.editing = "1";

      var projectId = descriptionEl.dataset.projectId;
      var originalText = descriptionEl.dataset.description || "";
      var wrapper = document.createElement("div");
      wrapper.className = "mt-2";
      wrapper.id = "project-description-editor";

      wrapper.innerHTML =
        '<textarea class="form-control mb-2" id="project-description-textarea" rows="3" maxlength="1000"></textarea>' +
        '<div class="d-flex gap-2">' +
        '<button type="button" class="btn btn-sm btn-primary" id="save-description-btn">Save</button>' +
        '<button type="button" class="btn btn-sm btn-outline-secondary" id="cancel-description-btn">Cancel</button>' +
        '<span class="small text-success align-self-center" id="description-saved-indicator" style="display:none;">Saved ✓</span>' +
        "</div>";

      descriptionEl.style.display = "none";
      descriptionEl.parentNode.appendChild(wrapper);

      var textarea = document.getElementById("project-description-textarea");
      if (textarea) {
        textarea.value = originalText;
        textarea.focus();
      }

      var saveBtn = document.getElementById("save-description-btn");
      var cancelBtn = document.getElementById("cancel-description-btn");
      var savedIndicator = document.getElementById("description-saved-indicator");

      function cleanupEditor() {
        var editor = document.getElementById("project-description-editor");
        if (editor) {
          editor.remove();
        }
        descriptionEl.style.display = "block";
        descriptionEl.dataset.editing = "0";
      }

      saveBtn && saveBtn.addEventListener("click", function () {
        var updatedDescription = String((textarea && textarea.value) || "").trim();

        if (window.setButtonLoading) {
          window.setButtonLoading(saveBtn, "Saving...");
        }

        sendJson("/projects/" + encodeURIComponent(projectId), "PUT", { description: updatedDescription })
          .then(function () {
            descriptionEl.textContent = updatedDescription || "Click to add a project description.";
            descriptionEl.dataset.description = updatedDescription;
            if (savedIndicator) {
              savedIndicator.style.display = "inline";
            }
            window.setTimeout(function () {
              cleanupEditor();
            }, 700);
          })
          .catch(function (error) {
            if (window.showToast) {
              window.showToast(error.message || "Could not update description", "danger");
            }
          })
          .finally(function () {
            if (window.resetButton) {
              window.resetButton(saveBtn);
            }
          });
      });

      cancelBtn && cancelBtn.addEventListener("click", function () {
        cleanupEditor();
      });
    });
  }

  /**
   * Initialize color and icon pickers in create modal.
   */
  function initCreateProjectPickers() {
    var colorInput = document.getElementById("project-color");
    var colorPreview = document.getElementById("project-color-preview");
    var iconInput = document.getElementById("project-icon");

    document.querySelectorAll(".project-color-swatch[data-color]").forEach(function (swatch) {
      swatch.addEventListener("click", function () {
        document.querySelectorAll(".project-color-swatch[data-color]").forEach(function (item) {
          item.classList.remove("selected");
        });
        swatch.classList.add("selected");

        var color = swatch.dataset.color || "#1a56db";
        if (colorInput) {
          colorInput.value = color;
        }
        if (colorPreview) {
          colorPreview.style.background = color;
        }
      });
    });

    document.querySelectorAll(".project-icon-option").forEach(function (option) {
      option.addEventListener("click", function () {
        document.querySelectorAll(".project-icon-option").forEach(function (item) {
          item.classList.remove("selected");
        });
        option.classList.add("selected");
        if (iconInput) {
          iconInput.value = option.dataset.icon || "bi-folder";
        }
      });
    });
  }

  /**
   * Initialize create project form ajax submit.
   */
  function initCreateProjectForm() {
    var form = document.getElementById("create-project-form");
    if (!form) {
      return;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var submitButton = form.querySelector('button[type="submit"], input[type="submit"]');

      var errorEl = document.getElementById("project-create-error");
      if (errorEl) {
        errorEl.style.display = "none";
        errorEl.textContent = "";
      }

      var payload = {
        name: String((document.getElementById("project-name") || {}).value || "").trim(),
        description: String((document.getElementById("project-description-input") || {}).value || "").trim(),
        color: String((document.getElementById("project-color") || {}).value || "").trim(),
        icon: String((document.getElementById("project-icon") || {}).value || "").trim()
      };

      if (submitButton && window.setButtonLoading) {
        window.setButtonLoading(submitButton, "Creating...");
      }

      sendJson("/projects", "POST", payload)
        .then(function (result) {
          var redirectUrl = result.data && result.data.redirect;
          if (window.showToast) {
            window.showToast("Project created", "success");
          }
          if (redirectUrl) {
            window.location.href = redirectUrl;
            return;
          }
          window.location.reload();
        })
        .catch(function (error) {
          if (errorEl) {
            errorEl.textContent = error.message || "Could not create project";
            errorEl.style.display = "block";
          }
        })
        .finally(function () {
          if (submitButton && window.resetButton) {
            window.resetButton(submitButton);
          }
        });
    });
  }

  /**
   * Initialize settings form save for project detail page.
   */
  function initSettingsFormSave() {
    var settingsForm = document.getElementById("project-settings-form");
    if (!settingsForm) {
      return;
    }

    settingsForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var submitButton = settingsForm.querySelector('button[type="submit"], input[type="submit"]');
      var projectId = settingsForm.dataset.projectId;
      if (!projectId) {
        return;
      }

      var payload = {
        name: String((document.getElementById("settings-project-name") || {}).value || "").trim(),
        description: String((document.getElementById("settings-project-description") || {}).value || "").trim(),
        color: String((document.getElementById("settings-project-color") || {}).value || "").trim(),
        icon: String((document.getElementById("settings-project-icon") || {}).value || "").trim()
      };

      if (submitButton && window.setButtonLoading) {
        window.setButtonLoading(submitButton, "Saving...");
      }

      sendJson("/projects/" + encodeURIComponent(projectId), "PUT", payload)
        .then(function () {
          if (window.showToast) {
            window.showToast("Project updated", "success");
          }
          window.location.reload();
        })
        .catch(function (error) {
          if (window.showToast) {
            window.showToast(error.message || "Update failed", "danger");
          }
        })
        .finally(function () {
          if (submitButton && window.resetButton) {
            window.resetButton(submitButton);
          }
        });
    });
  }

  /**
   * Toggle archive state.
   * @param {string} projectId
   */
  function toggleArchive(projectId, triggerButton) {
    if (triggerButton && window.setButtonLoading) {
      window.setButtonLoading(triggerButton, "Updating...");
    }

    sendJson("/projects/" + encodeURIComponent(projectId) + "/archive", "POST")
      .then(function (payload) {
        var archived = payload.data && payload.data.is_archived;
        if (window.showToast) {
          window.showToast(archived ? "Project archived" : "Project restored", "success");
        }
        window.location.reload();
      })
      .catch(function (error) {
        if (window.showToast) {
          window.showToast(error.message || "Archive operation failed", "danger");
        }
      })
      .finally(function () {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
      });
  }

  /**
   * Duplicate project container.
   * @param {string} projectId
   */
  function duplicateProject(projectId, triggerButton) {
    if (triggerButton && window.setButtonLoading) {
      window.setButtonLoading(triggerButton, "Duplicating...");
    }

    sendJson("/projects/" + encodeURIComponent(projectId) + "/duplicate", "POST")
      .then(function (payload) {
        var newId = payload.data && payload.data.project_id;
        if (window.showToast) {
          window.showToast("Project duplicated", "success");
        }
        if (newId) {
          window.location.href = "/projects/" + encodeURIComponent(newId);
          return;
        }
        window.location.reload();
      })
      .catch(function (error) {
        if (window.showToast) {
          window.showToast(error.message || "Duplicate failed", "danger");
        }
      })
      .finally(function () {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
      });
  }

  /**
   * Delete project by id.
   * @param {string} projectId
   */
  function deleteProject(projectId, triggerButton) {
    if (triggerButton && window.setButtonLoading) {
      window.setButtonLoading(triggerButton, "Deleting...");
    }

    fetch("/projects/" + encodeURIComponent(projectId), {
      method: "DELETE",
      headers: {
        "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
        "X-Requested-With": "XMLHttpRequest"
      },
      credentials: "same-origin"
    }).then(parseJsonResponse)
      .then(function () {
        if (window.showToast) {
          window.showToast("Project deleted", "success");
        }
        window.location.href = "/projects";
      })
      .catch(function (error) {
        if (window.showToast) {
          window.showToast(error.message || "Delete failed", "danger");
        }
      })
      .finally(function () {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
      });
  }

  /**
   * Enable deletion button only after exact name match.
   */
  function initDeleteConfirmation() {
    var input = document.getElementById("delete-confirm-input");
    var deleteBtn = document.getElementById("delete-project-btn");
    if (!input || !deleteBtn) {
      return;
    }

    input.addEventListener("input", function () {
      var expectedName = deleteBtn.dataset.projectName || input.dataset.projectName || "";
      var matches = input.value.trim() === expectedName;
      deleteBtn.disabled = !matches;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    syncProjectTabState();
    initInlineDescriptionEdit();
    initCreateProjectPickers();
    initCreateProjectForm();
    initSettingsFormSave();
    initDeleteConfirmation();

    var archiveToggleBtn = document.getElementById("project-archive-toggle");
    archiveToggleBtn && archiveToggleBtn.addEventListener("click", function () {
      var projectId = archiveToggleBtn.dataset.projectId;
      if (projectId) {
        toggleArchive(projectId, archiveToggleBtn);
      }
    });

    var settingsArchiveBtn = document.getElementById("settings-archive-btn");
    settingsArchiveBtn && settingsArchiveBtn.addEventListener("click", function () {
      var projectId = settingsArchiveBtn.dataset.projectId;
      if (projectId) {
        toggleArchive(projectId, settingsArchiveBtn);
      }
    });

    document.addEventListener("click", function (event) {
      var archiveBtn = event.target.closest(".project-archive-btn");
      if (archiveBtn) {
        var archiveProjectId = archiveBtn.dataset.projectId;
        if (archiveProjectId) {
          toggleArchive(archiveProjectId, archiveBtn);
        }
        return;
      }

      var duplicateBtn = event.target.closest(".project-duplicate-btn");
      if (duplicateBtn) {
        var duplicateProjectId = duplicateBtn.dataset.projectId;
        if (duplicateProjectId) {
          duplicateProject(duplicateProjectId, duplicateBtn);
        }
        return;
      }

      var deleteBtn = event.target.closest(".project-delete-btn, #delete-project-btn");
      if (deleteBtn) {
        if (deleteBtn.id !== "delete-project-btn" && deleteBtn.dataset.confirmed !== "true") {
          return;
        }

        if (deleteBtn.dataset.confirmed === "true") {
          deleteBtn.dataset.confirmed = "false";
        }

        var projectId = deleteBtn.dataset.projectId;
        if (!projectId) {
          return;
        }

        if (deleteBtn.id === "delete-project-btn" && deleteBtn.disabled) {
          return;
        }

        deleteProject(projectId, deleteBtn);
      }
    });
  });
})();
