(function () {
  "use strict";

  var ALLOWED_EXTENSIONS = ["pdf", "docx", "txt", "csv", "md", "xlsx", "json"];
  var MAX_FILE_BYTES = 16 * 1024 * 1024;

  /**
   * Escape html entities.
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
   * Return extension from filename.
   * @param {string} fileName
   * @returns {string}
   */
  function getExtension(fileName) {
    var parts = String(fileName || "").toLowerCase().split(".");
    return parts.length > 1 ? parts.pop() : "";
  }

  /**
   * Get bootstrap modal instance.
   * @param {string} id
   * @returns {bootstrap.Modal|null}
   */
  function getModal(id) {
    var el = document.getElementById(id);
    if (!el || !window.bootstrap) {
      return null;
    }
    return bootstrap.Modal.getOrCreateInstance(el);
  }

  /**
   * Render a lightweight markdown preview.
   * @param {string} markdown
   * @returns {string}
   */
  function renderSimpleMarkdown(markdown) {
    var text = escapeHtml(markdown || "");
    text = text.replace(/^###\s+(.*)$/gm, "<h5>$1</h5>");
    text = text.replace(/^##\s+(.*)$/gm, "<h4>$1</h4>");
    text = text.replace(/^#\s+(.*)$/gm, "<h3>$1</h3>");
    text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
    text = text.replace(/\n/g, "<br>");
    return text;
  }

  /**
   * Build list row for new entry append.
   * @param {Object} entry
   * @returns {string}
   */
  function buildEntryRow(entry) {
    var id = escapeHtml(entry.entry_id || "");
    var title = escapeHtml(entry.title || "Untitled");
    var sourceType = escapeHtml(entry.source_type || "text");

    return (
      '<tr id="knowledge-row-' + id + '" class="knowledge-entry-row" data-entry-id="' + id + '" data-source="' + sourceType + '" data-title="' + String(title).toLowerCase() + '">' +
      '<td><div class="d-flex align-items-center gap-2"><span class="source-icon-circle" style="background:#2563eb;"><i class="bi bi-file-earmark-text"></i></span>' +
      '<div><a href="#" class="fw-semibold text-decoration-none preview-entry-link" data-entry-id="' + id + '">' + title + '</a><div class="small text-muted">New entry</div></div></div></td>' +
      '<td><span class="badge text-bg-light border text-uppercase">' + sourceType + "</span></td>" +
      '<td><span class="text-muted">Unassigned</span></td>' +
      '<td><div class="small text-muted">just now</div><div class="small">You</div></td>' +
      '<td class="text-end"><div class="btn-group btn-group-sm">' +
      '<button type="button" class="btn btn-outline-primary preview-entry-btn" data-entry-id="' + id + '"><i class="bi bi-eye"></i></button>' +
      '<button type="button" class="btn btn-outline-dark edit-title-btn" data-entry-id="' + id + '" data-title="' + title + '"><i class="bi bi-pencil"></i></button>' +
      '<button type="button" class="btn btn-outline-danger delete-entry-btn" data-entry-id="' + id + '" data-confirm="Delete this knowledge entry?"><i class="bi bi-trash"></i></button>' +
      "</div></td></tr>"
    );
  }

  /**
   * Build grid card for new entry append.
   * @param {Object} entry
   * @returns {string}
   */
  function buildEntryCard(entry) {
    var id = escapeHtml(entry.entry_id || "");
    var title = escapeHtml(entry.title || "Untitled");
    var sourceType = escapeHtml(entry.source_type || "text");

    return (
      '<div class="col knowledge-grid-item" id="knowledge-grid-card-' + id + '" data-entry-id="' + id + '" data-source="' + sourceType + '" data-title="' + String(title).toLowerCase() + '">' +
      '<div class="knowledge-card h-100 p-0">' +
      '<div class="source-strip" style="background:#2563eb;"></div>' +
      '<div class="p-3 d-flex flex-column h-100">' +
      '<h6 class="fw-semibold mb-1"><a href="#" class="preview-entry-link text-decoration-none" data-entry-id="' + id + '">' + title + '</a></h6>' +
      '<div class="small text-muted mb-2 text-uppercase">' + sourceType + '</div>' +
      '<div class="small text-muted mb-2">just now</div>' +
      '<div class="mt-auto d-flex gap-1">' +
      '<button type="button" class="btn btn-sm btn-outline-primary preview-entry-btn" data-entry-id="' + id + '">Preview</button>' +
      '<button type="button" class="btn btn-sm btn-outline-danger delete-entry-btn" data-entry-id="' + id + '" data-confirm="Delete this knowledge entry?">Delete</button>' +
      "</div></div></div></div>"
    );
  }

  /**
   * Update visible result count.
   */
  function updateVisibleCount() {
    var listViewVisible = document.getElementById("knowledge-list-view") && document.getElementById("knowledge-list-view").style.display !== "none";
    var selector = listViewVisible ? ".knowledge-entry-row" : ".knowledge-grid-item";
    var count = 0;
    document.querySelectorAll(selector).forEach(function (el) {
      if (el.style.display !== "none") {
        count += 1;
      }
    });
    var countEl = document.getElementById("knowledge-count");
    if (countEl) {
      countEl.textContent = String(count);
    }
  }

  /**
   * Apply local search and source filters.
   */
  function applyClientFilters() {
    var search = String((document.getElementById("knowledge-search") || {}).value || "").trim().toLowerCase();
    var selectedSource = String((document.getElementById("source-type-input") || {}).value || "all").toLowerCase();

    [".knowledge-entry-row", ".knowledge-grid-item"].forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (item) {
        var title = String(item.dataset.title || "").toLowerCase();
        var source = String(item.dataset.source || "").toLowerCase();

        var matchesSearch = !search || title.indexOf(search) >= 0;
        var matchesSource = selectedSource === "all" || source === selectedSource;

        item.style.display = matchesSearch && matchesSource ? "" : "none";
      });
    });

    updateVisibleCount();
  }

  /**
   * Upload file with XHR progress.
   * @param {File} file
   */
  function uploadFile(file, triggerButton) {
    var progressWrap = document.getElementById("upload-progress-wrap");
    var progressBar = document.getElementById("upload-progress-bar");
    var errorEl = document.getElementById("upload-error");
    var titleInput = document.getElementById("upload-title");
    var projectInput = document.getElementById("upload-project-id");

    if (!file) {
      return;
    }

    var extension = getExtension(file.name);
    if (ALLOWED_EXTENSIONS.indexOf(extension) < 0) {
      if (errorEl) {
        errorEl.textContent = "Unsupported file type.";
        errorEl.style.display = "block";
      }
      return;
    }

    if (file.size > MAX_FILE_BYTES) {
      if (errorEl) {
        errorEl.textContent = "File exceeds 16 MB limit.";
        errorEl.style.display = "block";
      }
      return;
    }

    if (errorEl) {
      errorEl.style.display = "none";
      errorEl.textContent = "";
    }

    if (triggerButton && window.setButtonLoading) {
      window.setButtonLoading(triggerButton, "Uploading...");
    }

    var formData = new FormData();
    formData.append("source_type", "file");
    formData.append("file", file);
    formData.append("title", String(titleInput ? titleInput.value : "").trim());
    formData.append("project_id", String(projectInput ? projectInput.value : ""));

    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/knowledge", true);
    xhr.setRequestHeader("X-CSRFToken", window.getCSRFToken ? window.getCSRFToken() : "");
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");

    if (progressWrap) {
      progressWrap.style.display = "flex";
    }

    xhr.upload.onprogress = function (event) {
      if (!event.lengthComputable || !progressBar) {
        return;
      }
      var percent = Math.round((event.loaded / event.total) * 100);
      progressBar.style.width = percent + "%";
      progressBar.textContent = percent + "%";
    };

    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) {
        return;
      }

      if (progressWrap) {
        window.setTimeout(function () {
          progressWrap.style.display = "none";
          if (progressBar) {
            progressBar.style.width = "0%";
            progressBar.textContent = "0%";
          }
        }, 500);
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        var payload = {};
        try {
          payload = JSON.parse(xhr.responseText);
        } catch (_error) {
          payload = { success: false, message: "Invalid server response" };
        }

        if (!payload.success) {
          if (errorEl) {
            errorEl.textContent = payload.message || "Upload failed";
            errorEl.style.display = "block";
          }
          return;
        }

        var listBody = document.getElementById("knowledge-list-body");
        var gridBody = document.getElementById("knowledge-grid-body");
        if (listBody) {
          listBody.insertAdjacentHTML("afterbegin", buildEntryRow(payload.data));
        }
        if (gridBody) {
          gridBody.insertAdjacentHTML("afterbegin", buildEntryCard(payload.data));
        }

        var modal = getModal("upload-file-modal");
        modal && modal.hide();

        if (window.showToast) {
          window.showToast("File uploaded successfully", "success");
        }
        applyClientFilters();
      } else {
        var message = "Upload failed";
        try {
          var errorPayload = JSON.parse(xhr.responseText);
          message = errorPayload.message || message;
        } catch (_ignored) {
          message = "Upload failed with status " + xhr.status;
        }
        if (errorEl) {
          errorEl.textContent = message;
          errorEl.style.display = "block";
        }
      }

      if (triggerButton && window.resetButton) {
        window.resetButton(triggerButton);
      }
    };

    xhr.send(formData);
  }

  /**
   * Submit URL or note entry with fetch.
   * @param {Object} payload
   */
  async function submitEntry(payload) {
    var response = await fetch("/knowledge", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
        "X-Requested-With": "XMLHttpRequest"
      },
      credentials: "same-origin",
      body: JSON.stringify(payload)
    });

    var result = await response.json();
    if (!response.ok || !result.success) {
      throw new Error(result.message || "Request failed");
    }

    return result.data;
  }

  /**
   * Open preview modal and load entry.
   * @param {string} entryId
   */
  async function openPreview(entryId) {
    var previewModal = getModal("preview-modal");
    var body = document.getElementById("preview-modal-body");
    var title = document.getElementById("preview-modal-title");
    var downloadBtn = document.getElementById("preview-download-btn");

    if (body) {
      body.innerHTML = '<div class="text-muted">Loading preview...</div>';
    }
    if (downloadBtn) {
      downloadBtn.style.display = "none";
      downloadBtn.href = "#";
    }

    previewModal && previewModal.show();

    try {
      var response = await fetch("/knowledge/" + encodeURIComponent(entryId) + "?preview=1", {
        headers: {
          "X-Requested-With": "XMLHttpRequest"
        },
        credentials: "same-origin"
      });
      var payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.message || "Could not load preview");
      }

      var data = payload.data || {};
      if (title) {
        title.textContent = data.title || "Knowledge Preview";
      }

      if (data.source_type === "text" && data.content_text) {
        body.innerHTML = '<div style="max-height:62vh;overflow:auto;">' + renderSimpleMarkdown(data.content_text) + "</div>";
      } else if (data.source_type === "url") {
        body.innerHTML =
          '<div class="small text-muted mb-2">Stored URL reference</div>' +
          '<a href="' + escapeHtml(data.source_url || "") + '" target="_blank" rel="noopener">' + escapeHtml(data.source_url || "") + "</a>";
      } else {
        body.innerHTML =
          '<div class="small text-muted mb-2">File entry</div>' +
          '<div><strong>File:</strong> ' + escapeHtml(data.file_name || "Uploaded file") + "</div>" +
          '<div><strong>MIME:</strong> ' + escapeHtml(data.file_mime || "Unknown") + "</div>" +
          '<div><strong>Size:</strong> ' + escapeHtml(String(data.file_size || 0)) + " bytes</div>";

        if (downloadBtn) {
          downloadBtn.style.display = "inline-block";
          downloadBtn.href = data.download_url || ("/knowledge/" + encodeURIComponent(entryId));
        }
      }
    } catch (error) {
      body.innerHTML = '<div class="text-danger">' + escapeHtml(error.message || "Preview failed") + "</div>";
    }
  }

  /**
   * Delete knowledge entry.
   * @param {string} entryId
   */
  async function deleteEntry(entryId) {
    var response = await fetch("/knowledge/" + encodeURIComponent(entryId), {
      method: "DELETE",
      headers: {
        "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
        "X-Requested-With": "XMLHttpRequest"
      },
      credentials: "same-origin"
    });

    var payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.message || "Delete failed");
    }
  }

  /**
   * Set list or grid view mode.
   * @param {string} mode
   */
  function setViewMode(mode) {
    var listView = document.getElementById("knowledge-list-view");
    var gridView = document.getElementById("knowledge-grid-view");
    var listBtn = document.getElementById("knowledge-list-view-btn");
    var gridBtn = document.getElementById("knowledge-grid-view-btn");
    var gridMode = mode === "grid";

    if (listView) {
      listView.style.display = gridMode ? "none" : "block";
    }
    if (gridView) {
      gridView.style.display = gridMode ? "block" : "none";
    }

    if (listBtn) {
      listBtn.classList.toggle("btn-primary", !gridMode);
      listBtn.classList.toggle("btn-outline-secondary", gridMode);
    }
    if (gridBtn) {
      gridBtn.classList.toggle("btn-primary", gridMode);
      gridBtn.classList.toggle("btn-outline-secondary", !gridMode);
    }

    localStorage.setItem("knowledge_view_preference", gridMode ? "grid" : "list");
    updateVisibleCount();
  }

  document.addEventListener("DOMContentLoaded", function () {
    var dropZone = document.getElementById("knowledge-drop-zone");
    var fileInput = document.getElementById("knowledge-file-input");
    var uploadForm = document.getElementById("upload-file-form");

    if (dropZone && fileInput) {
      dropZone.addEventListener("click", function () {
        fileInput.click();
      });

      ["dragenter", "dragover"].forEach(function (eventName) {
        dropZone.addEventListener(eventName, function (event) {
          event.preventDefault();
          dropZone.classList.add("active");
        });
      });

      ["dragleave", "drop"].forEach(function (eventName) {
        dropZone.addEventListener(eventName, function (event) {
          event.preventDefault();
          dropZone.classList.remove("active");
        });
      });

      dropZone.addEventListener("drop", function (event) {
        var files = event.dataTransfer && event.dataTransfer.files;
        if (!files || !files.length) {
          return;
        }
        uploadFile(files[0], document.getElementById("upload-file-submit-btn"));
      });

      fileInput.addEventListener("change", function () {
        if (fileInput.files && fileInput.files.length) {
          uploadFile(fileInput.files[0], document.getElementById("upload-file-submit-btn"));
        }
      });
    }

    uploadForm && uploadForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var uploadSubmit = uploadForm.querySelector('button[type="submit"], input[type="submit"]');
      if (fileInput && fileInput.files && fileInput.files.length) {
        uploadFile(fileInput.files[0], uploadSubmit);
      } else {
        var errorEl = document.getElementById("upload-error");
        if (errorEl) {
          errorEl.textContent = "Please select a file.";
          errorEl.style.display = "block";
        }
      }
    });

    var addUrlForm = document.getElementById("add-url-form");
    addUrlForm && addUrlForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      var submitButton = addUrlForm.querySelector('button[type="submit"], input[type="submit"]');
      var url = String((document.getElementById("url-input") || {}).value || "").trim();
      var title = String((document.getElementById("url-title") || {}).value || "").trim();
      var projectId = String((document.getElementById("url-project-id") || {}).value || "");
      var errorEl = document.getElementById("url-error");

      try {
        if (submitButton && window.setButtonLoading) {
          window.setButtonLoading(submitButton, "Saving...");
        }
        var data = await submitEntry({
          source_type: "url",
          url: url,
          title: title,
          project_id: projectId
        });

        var listBody = document.getElementById("knowledge-list-body");
        var gridBody = document.getElementById("knowledge-grid-body");
        if (listBody) {
          listBody.insertAdjacentHTML("afterbegin", buildEntryRow(data));
        }
        if (gridBody) {
          gridBody.insertAdjacentHTML("afterbegin", buildEntryCard(data));
        }

        var modal = getModal("add-url-modal");
        modal && modal.hide();
        addUrlForm.reset();

        if (window.showToast) {
          window.showToast("URL entry added", "success");
        }
        applyClientFilters();
      } catch (error) {
        if (errorEl) {
          errorEl.textContent = error.message || "Could not save URL";
          errorEl.style.display = "block";
        }
      } finally {
        if (submitButton && window.resetButton) {
          window.resetButton(submitButton);
        }
      }
    });

    var addNoteForm = document.getElementById("add-note-form");
    var noteContent = document.getElementById("note-content");
    var noteCounter = document.getElementById("note-char-count");

    noteContent && noteContent.addEventListener("input", function () {
      if (noteCounter) {
        noteCounter.textContent = String(noteContent.value.length);
      }
    });

    addNoteForm && addNoteForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      var submitButton = addNoteForm.querySelector('button[type="submit"], input[type="submit"]');
      var title = String((document.getElementById("note-title") || {}).value || "").trim();
      var content = String((document.getElementById("note-content") || {}).value || "").trim();
      var projectId = String((document.getElementById("note-project-id") || {}).value || "");
      var errorEl = document.getElementById("note-error");

      if (content.length < 10) {
        if (errorEl) {
          errorEl.textContent = "Content must be at least 10 characters.";
          errorEl.style.display = "block";
        }
        return;
      }

      try {
        if (submitButton && window.setButtonLoading) {
          window.setButtonLoading(submitButton, "Saving...");
        }
        var data = await submitEntry({
          source_type: "text",
          title: title,
          content: content,
          project_id: projectId
        });

        var listBody = document.getElementById("knowledge-list-body");
        var gridBody = document.getElementById("knowledge-grid-body");
        if (listBody) {
          listBody.insertAdjacentHTML("afterbegin", buildEntryRow(data));
        }
        if (gridBody) {
          gridBody.insertAdjacentHTML("afterbegin", buildEntryCard(data));
        }

        var modal = getModal("add-note-modal");
        modal && modal.hide();
        addNoteForm.reset();
        if (noteCounter) {
          noteCounter.textContent = "0";
        }

        if (window.showToast) {
          window.showToast("Note added", "success");
        }
        applyClientFilters();
      } catch (error) {
        if (errorEl) {
          errorEl.textContent = error.message || "Could not save note";
          errorEl.style.display = "block";
        }
      } finally {
        if (submitButton && window.resetButton) {
          window.resetButton(submitButton);
        }
      }
    });

    document.addEventListener("click", function (event) {
      var sourceFilter = event.target.closest(".source-filter-btn");
      if (sourceFilter) {
        document.querySelectorAll(".source-filter-btn").forEach(function (btn) {
          btn.classList.remove("btn-primary");
          btn.classList.add("btn-outline-secondary");
        });
        sourceFilter.classList.remove("btn-outline-secondary");
        sourceFilter.classList.add("btn-primary");

        var hidden = document.getElementById("source-type-input");
        if (hidden) {
          hidden.value = sourceFilter.dataset.source || "all";
        }

        applyClientFilters();
        return;
      }

      var previewBtn = event.target.closest(".preview-entry-link, .preview-entry-btn");
      if (previewBtn) {
        event.preventDefault();
        var previewId = previewBtn.dataset.entryId;
        if (previewId) {
          openPreview(previewId);
        }
        return;
      }

      var editBtn = event.target.closest(".edit-title-btn");
      if (editBtn) {
        var entryId = editBtn.dataset.entryId;
        var currentTitle = editBtn.dataset.title || "";
        var newTitle = window.prompt("Update title", currentTitle);
        if (!entryId || newTitle === null) {
          return;
        }
        newTitle = String(newTitle).trim();
        if (!newTitle) {
          return;
        }

        if (window.setButtonLoading) {
          window.setButtonLoading(editBtn, "Saving...");
        }

        fetch("/knowledge/" + encodeURIComponent(entryId), {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
            "X-Requested-With": "XMLHttpRequest"
          },
          credentials: "same-origin",
          body: JSON.stringify({ title: newTitle })
        }).then(function (response) {
          return response.json().then(function (payload) {
            if (!response.ok || !payload.success) {
              throw new Error(payload.message || "Update failed");
            }

            var row = document.querySelector('#knowledge-row-' + entryId + ' .preview-entry-link');
            if (row) {
              row.textContent = newTitle;
            }
            var card = document.querySelector('#knowledge-grid-card-' + entryId + ' .preview-entry-link');
            if (card) {
              card.textContent = newTitle;
            }

            document.querySelectorAll('[data-entry-id="' + entryId + '"]').forEach(function (el) {
              if (el.classList.contains("edit-title-btn")) {
                el.dataset.title = newTitle;
              }
            });

            var listRow = document.getElementById("knowledge-row-" + entryId);
            var gridCard = document.getElementById("knowledge-grid-card-" + entryId);
            if (listRow) {
              listRow.dataset.title = newTitle.toLowerCase();
            }
            if (gridCard) {
              gridCard.dataset.title = newTitle.toLowerCase();
            }

            if (window.showToast) {
              window.showToast("Title updated", "success");
            }
            applyClientFilters();
          });
        }).catch(function (error) {
          if (window.showToast) {
            window.showToast(error.message || "Update failed", "danger");
          }
        }).finally(function () {
          if (window.resetButton) {
            window.resetButton(editBtn);
          }
        });
        return;
      }

      var deleteBtn = event.target.closest(".delete-entry-btn");
      if (!deleteBtn) {
        return;
      }

      if (deleteBtn.dataset.confirmed !== "true") {
        return;
      }
      deleteBtn.dataset.confirmed = "false";

      var deleteId = deleteBtn.dataset.entryId;
      if (!deleteId) {
        return;
      }

      if (window.setButtonLoading) {
        window.setButtonLoading(deleteBtn, "Deleting...");
      }

      deleteEntry(deleteId).then(function () {
        var row = document.getElementById("knowledge-row-" + deleteId);
        if (row) {
          row.remove();
        }
        var card = document.getElementById("knowledge-grid-card-" + deleteId);
        if (card) {
          card.remove();
        }
        if (window.showToast) {
          window.showToast("Entry deleted", "success");
        }
        updateVisibleCount();
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

    var searchInput = document.getElementById("knowledge-search");
    searchInput && searchInput.addEventListener("input", applyClientFilters);

    var listBtn = document.getElementById("knowledge-list-view-btn");
    var gridBtn = document.getElementById("knowledge-grid-view-btn");
    listBtn && listBtn.addEventListener("click", function () {
      setViewMode("list");
    });
    gridBtn && gridBtn.addEventListener("click", function () {
      setViewMode("grid");
    });

    setViewMode(localStorage.getItem("knowledge_view_preference") || "list");
    applyClientFilters();
  });
})();
