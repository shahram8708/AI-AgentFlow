(function () {
  "use strict";

  /**
   * Return selected output ids.
   * @returns {string[]}
   */
  function getSelectedIds() {
    return Array.from(document.querySelectorAll(".task-checkbox:checked")).map(function (cb) {
      return cb.value;
    });
  }

  /**
   * Update selected counter, buttons, and bulk action bar.
   */
  function updateSelectionState() {
    var selectedIds = getSelectedIds();
    var count = selectedIds.length;

    var selectedCountEl = document.getElementById("selected-count");
    var bulkDownloadBtn = document.getElementById("bulk-download-btn");
    var bulkBar = document.getElementById("bulk-actions-bar");

    if (selectedCountEl) {
      selectedCountEl.textContent = String(count);
    }

    if (bulkDownloadBtn) {
      bulkDownloadBtn.disabled = count === 0;
    }

    if (bulkBar) {
      bulkBar.style.display = count > 0 ? "flex" : "none";
    }
  }

  /**
   * Apply table and card filtering by search text.
   */
  function applySearchFilter() {
    var searchInput = document.getElementById("outputs-search");
    var query = String(searchInput ? searchInput.value : "").trim().toLowerCase();

    var visibleRows = 0;
    document.querySelectorAll("#outputs-table tbody tr").forEach(function (row) {
      var name = String(row.dataset.name || "").toLowerCase();
      var visible = !query || name.indexOf(query) >= 0;
      row.style.display = visible ? "" : "none";
      if (visible) {
        visibleRows += 1;
      }
    });

    var visibleCards = 0;
    document.querySelectorAll(".output-card-item").forEach(function (card) {
      var name = String(card.dataset.name || "").toLowerCase();
      var visible = !query || name.indexOf(query) >= 0;
      card.style.display = visible ? "" : "none";
      if (visible) {
        visibleCards += 1;
      }
    });

    var count = visibleRows || visibleCards;
    var countBadge = document.getElementById("output-count");
    var inlineCount = document.getElementById("output-count-inline");
    if (countBadge) {
      countBadge.textContent = String(count);
    }
    if (inlineCount) {
      inlineCount.textContent = String(count);
    }
  }

  /**
   * Set output view mode.
   * @param {string} mode
   */
  function setViewMode(mode) {
    var listView = document.getElementById("outputs-list-view");
    var gridView = document.getElementById("outputs-grid-view");
    var listBtn = document.getElementById("view-list-btn");
    var gridBtn = document.getElementById("view-grid-btn");

    var useGrid = mode === "grid";
    if (listView) {
      listView.style.display = useGrid ? "none" : "block";
    }
    if (gridView) {
      gridView.style.display = useGrid ? "block" : "none";
    }

    if (listBtn) {
      listBtn.classList.toggle("btn-primary", !useGrid);
      listBtn.classList.toggle("btn-outline-secondary", useGrid);
    }
    if (gridBtn) {
      gridBtn.classList.toggle("btn-primary", useGrid);
      gridBtn.classList.toggle("btn-outline-secondary", !useGrid);
    }

    localStorage.setItem("outputs_view_preference", useGrid ? "grid" : "list");
  }

  /**
   * Trigger ZIP download for selected ids.
   */
  function triggerBulkDownload(event) {
    var selectedIds = getSelectedIds();
    if (!selectedIds.length) {
      if (window.showToast) {
        window.showToast("Select at least one output", "warning");
      }
      return;
    }

    if (window.showToast) {
      window.showToast("Preparing ZIP download...", "info");
    }

    if (event && event.currentTarget && window.setButtonLoading) {
      window.setButtonLoading(event.currentTarget, "Downloading...");
    }

    window.location.href = "/outputs/bulk-download?ids=" + encodeURIComponent(selectedIds.join(","));
  }

  /**
   * Delete selected outputs using bulk endpoint.
   */
  async function bulkDeleteSelected(event) {
    var triggerButton = event && event.currentTarget ? event.currentTarget : null;
    var selectedIds = getSelectedIds();
    if (!selectedIds.length) {
      return;
    }

    if (!window.confirm("Delete " + selectedIds.length + " outputs? This cannot be undone.")) {
      return;
    }

    try {
      if (triggerButton && window.setButtonLoading) {
        window.setButtonLoading(triggerButton, "Deleting...");
      }
      var response = await fetch("/outputs/bulk", {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
          "X-Requested-With": "XMLHttpRequest"
        },
        credentials: "same-origin",
        body: JSON.stringify({ ids: selectedIds })
      });

      var payload = await response.json();
      if (!response.ok || !payload.success) {
        throw new Error(payload.message || "Bulk delete failed");
      }

      selectedIds.forEach(function (id) {
        var row = document.getElementById("output-row-" + id);
        if (row) {
          row.remove();
        }
        var card = document.querySelector('.output-card-item[data-output-id="' + id + '"]');
        if (card) {
          card.remove();
        }
      });

      updateSelectionState();
      applySearchFilter();

      if (window.showToast) {
        window.showToast(payload.data.deleted_count + " outputs deleted", "success");
      }
    } catch (error) {
      if (window.showToast) {
        window.showToast(error.message || "Bulk delete failed", "danger");
      }
    } finally {
      if (triggerButton && window.resetButton) {
        window.resetButton(triggerButton);
      }
    }
  }

  /**
   * Delete single output entry.
   * @param {string} outputId
   */
  async function deleteOutput(outputId) {
    var response = await fetch("/outputs/" + encodeURIComponent(outputId), {
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

  document.addEventListener("DOMContentLoaded", function () {
    var selectAll = document.getElementById("select-all");
    var bulkDownloadBtn = document.getElementById("bulk-download-btn");
    var bulkDownloadSelectedBtn = document.getElementById("bulk-download-selected-btn");
    var bulkDeleteBtn = document.getElementById("bulk-delete-btn");
    var searchInput = document.getElementById("outputs-search");
    var listBtn = document.getElementById("view-list-btn");
    var gridBtn = document.getElementById("view-grid-btn");

    if (selectAll) {
      selectAll.addEventListener("change", function () {
        document.querySelectorAll(".task-checkbox").forEach(function (checkbox) {
          checkbox.checked = selectAll.checked;
        });
        updateSelectionState();
      });
    }

    document.addEventListener("change", function (event) {
      if (event.target && event.target.classList.contains("task-checkbox")) {
        updateSelectionState();
      }
    });

    bulkDownloadBtn && bulkDownloadBtn.addEventListener("click", triggerBulkDownload);
    bulkDownloadSelectedBtn && bulkDownloadSelectedBtn.addEventListener("click", triggerBulkDownload);
    bulkDeleteBtn && bulkDeleteBtn.addEventListener("click", bulkDeleteSelected);

    searchInput && searchInput.addEventListener("input", applySearchFilter);

    listBtn && listBtn.addEventListener("click", function () {
      setViewMode("list");
    });
    gridBtn && gridBtn.addEventListener("click", function () {
      setViewMode("grid");
    });

    document.addEventListener("click", function (event) {
      var deleteBtn = event.target.closest(".delete-output-btn");
      if (!deleteBtn) {
        return;
      }

      if (deleteBtn.dataset.confirmed !== "true") {
        return;
      }
      deleteBtn.dataset.confirmed = "false";

      var outputId = deleteBtn.dataset.outputId;
      if (!outputId) {
        return;
      }

      if (window.setButtonLoading) {
        window.setButtonLoading(deleteBtn, "Deleting...");
      }

      deleteOutput(outputId).then(function () {
        var row = document.getElementById("output-row-" + outputId);
        if (row) {
          row.remove();
        }
        var card = document.querySelector('.output-card-item[data-output-id="' + outputId + '"]');
        if (card) {
          card.remove();
        }

        updateSelectionState();
        applySearchFilter();

        if (window.showToast) {
          window.showToast("Output deleted", "success");
        }
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

    setViewMode(localStorage.getItem("outputs_view_preference") || "list");
    updateSelectionState();
    applySearchFilter();
  });
})();
