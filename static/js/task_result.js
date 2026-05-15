(function () {
  "use strict";

  function initTableSort() {
    document.querySelectorAll("#output-display table th").forEach((th, i) => {
      th.style.cursor = "pointer";
      th.addEventListener("click", () => sortTable(th.closest("table"), i));
    });
  }

  function sortTable(table, columnIndex) {
    if (!table) {
      return;
    }

    const tbody = table.querySelector("tbody");
    if (!tbody) {
      return;
    }

    const rows = Array.from(tbody.querySelectorAll("tr"));
    const ascending = table.dataset.sortDir !== "asc";
    table.dataset.sortDir = ascending ? "asc" : "desc";

    rows.sort((a, b) => {
      const aVal = (a.cells[columnIndex]?.textContent || "").trim();
      const bVal = (b.cells[columnIndex]?.textContent || "").trim();
      return ascending ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    });

    rows.forEach((row) => tbody.appendChild(row));
  }

  async function saveNote(outputId) {
    const noteInput = document.getElementById("output-note-input");
    const note = (noteInput?.value || "").trim();

    if (!note) {
      if (window.showToast) {
        window.showToast("Please write a note first.", "warning");
      }
      return;
    }

    const response = await fetch(`/api/outputs/${outputId}/notes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
      },
      credentials: "same-origin",
      body: JSON.stringify({ note }),
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.message || "Failed to save note");
    }

    if (noteInput) {
      noteInput.value = "";
    }

    const notesList = document.getElementById("notes-list");
    if (notesList) {
      const entry = document.createElement("div");
      entry.className = "border rounded p-2 mb-2";
      entry.innerHTML = `<div class="small text-muted mb-1">${new Date().toLocaleString("en-IN")}</div><div>${note.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>`;
      notesList.prepend(entry);
    }

    if (window.showToast) {
      window.showToast("Note saved.", "success");
    }
  }

  async function saveToProject(outputId) {
    const projectSelect = document.getElementById("project-select");
    const projectNote = document.getElementById("project-note");

    const projectId = projectSelect?.value || "";
    const note = (projectNote?.value || "").trim();

    if (!projectId) {
      if (window.showToast) {
        window.showToast("Please select a project.", "warning");
      }
      return;
    }

    const response = await fetch(`/api/outputs/${outputId}/save`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
      },
      credentials: "same-origin",
      body: JSON.stringify({
        project_id: projectId,
        note,
      }),
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.message || "Could not save output to project");
    }

    if (window.showToast) {
      window.showToast("Output saved to project.", "success");
    }

    const modalEl = document.getElementById("save-project-modal");
    if (modalEl && window.bootstrap) {
      const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
      modal.hide();
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("code.hljs, pre code").forEach((el) => {
      if (window.hljs) {
        window.hljs.highlightElement(el);
      }
    });

    document.getElementById("copy-output-btn")?.addEventListener("click", () => {
      const outputEl = document.getElementById("markdown-output") || document.getElementById("code-output");
      const text = outputEl?.textContent || "";

      if (window.copyToClipboard) {
        window.copyToClipboard(text, document.getElementById("copy-output-btn"));
      }
      if (window.showToast) {
        window.showToast("Output copied to clipboard!", "success");
      }
    });

    initTableSort();

    document.querySelectorAll("[data-download-format]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const format = btn.dataset.downloadFormat;
        if (window.showToast) {
          window.showToast(`Preparing ${String(format || "").toUpperCase()} download...`, "info");
        }
      });
    });

    document.getElementById("copy-permalink-btn")?.addEventListener("click", () => {
      if (window.copyToClipboard) {
        window.copyToClipboard(window.location.href, document.getElementById("copy-permalink-btn"));
      }
      if (window.showToast) {
        window.showToast("Permalink copied.", "success");
      }
    });

    document.getElementById("share-permalink-btn")?.addEventListener("click", () => {
      if (window.copyToClipboard) {
        window.copyToClipboard(window.location.href, document.getElementById("share-permalink-btn"));
      }
      if (window.showToast) {
        window.showToast("Share link copied.", "success");
      }
    });

    const outputData = document.getElementById("output-data");
    const outputId = outputData?.dataset.outputId || "";

    document.getElementById("save-note-btn")?.addEventListener("click", async () => {
      if (!outputId) {
        return;
      }
      try {
        await saveNote(outputId);
      } catch (error) {
        if (window.showToast) {
          window.showToast(error.message || "Could not save note", "danger");
        }
      }
    });

    document.getElementById("save-project-btn")?.addEventListener("click", async () => {
      if (!outputId) {
        return;
      }
      try {
        await saveToProject(outputId);
      } catch (error) {
        if (window.showToast) {
          window.showToast(error.message || "Could not save output", "danger");
        }
      }
    });
  });
})();
