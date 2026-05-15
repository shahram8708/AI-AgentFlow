(function () {
  "use strict";

  let currentTaskId = "";
  let currentTaskType = "";

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }

  function startElapsedTimer(startTime) {
    const timerEl = document.getElementById("elapsed-timer");
    if (!timerEl || !startTime) {
      return null;
    }

    const startMs = new Date(startTime + "Z").getTime();

    const interval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startMs) / 1000);
      const h = Math.floor(elapsed / 3600)
        .toString()
        .padStart(2, "0");
      const m = Math.floor((elapsed % 3600) / 60)
        .toString()
        .padStart(2, "0");
      const s = (elapsed % 60).toString().padStart(2, "0");
      timerEl.textContent = `${h}:${m}:${s}`;
    }, 1000);

    return interval;
  }

  function connectToTaskStream(taskId) {
    const eventSource = new EventSource(`/api/tasks/${taskId}/stream`);
    const indicator = document.getElementById("sse-indicator");
    let reconnectAttempts = 0;
    const maxReconnects = 5;

    eventSource.onopen = () => {
      if (indicator) {
        indicator.className = "badge bg-success";
        indicator.innerHTML = '<i class="bi bi-circle-fill me-1"></i> Connected';
      }
      reconnectAttempts = 0;
    };

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleSSEEvent(data);
      } catch (error) {
        console.warn("Failed to parse SSE event:", event.data);
      }
    };

    eventSource.onerror = () => {
      if (indicator) {
        indicator.className = "badge bg-danger";
        indicator.innerHTML = '<i class="bi bi-circle-fill me-1"></i> Disconnected';
      }

      reconnectAttempts += 1;
      if (reconnectAttempts >= maxReconnects) {
        eventSource.close();
        if (indicator) {
          indicator.innerHTML = 'Connection lost. <a href="">Refresh page</a>';
        }
        startStatusPolling(taskId);
      }
    };

    return eventSource;
  }

  function handleSSEEvent(data) {
    switch (data.type) {
      case "connected":
        appendLogEntry("info", "Connected to agent stream", data.timestamp || new Date().toISOString());
        break;

      case "log":
        handleLogEvent(data);
        break;

      case "status":
        updateStatusBanner(data.status);
        break;

      case "final":
        handleFinalEvent(data);
        break;

      case "error":
      case "timeout":
        appendLogEntry("error", data.message || "Stream error", new Date().toISOString());
        break;

      default:
        break;
    }
  }

  function handleLogEvent(data) {
    appendLogEntry(data.level || "info", data.message || "", data.timestamp || new Date().toISOString());

    if (data.step_number && data.step_status) {
      updateStepStatus(data.step_number, data.step_status);
    }

    if (data.output_chunk) {
      appendOutputChunk(data.output_chunk);
    }
  }

  function appendLogEntry(level, message, timestamp) {
    const log = document.getElementById("agent-log");
    if (!log) {
      return;
    }

    const safeTimestamp = timestamp || new Date().toISOString();
    const normalized = safeTimestamp.endsWith("Z") ? safeTimestamp : `${safeTimestamp}Z`;
    const time = new Date(normalized).toLocaleTimeString("en-IN", { hour12: false });

    const icons = {
      info: "→",
      warning: "⚠",
      error: "✗",
      step_start: "▶",
      step_complete: "✓",
      step_failed: "✗",
      complete: "✅",
      failed: "❌",
    };
    const colors = {
      info: "#e2e8f0",
      warning: "#fbbf24",
      error: "#f87171",
      step_start: "#67e8f9",
      step_complete: "#4ade80",
      step_failed: "#f87171",
      complete: "#22c55e",
      failed: "#ef4444",
    };

    const initialPlaceholder = log.querySelector(".text-muted");
    if (initialPlaceholder) {
      initialPlaceholder.remove();
    }

    const entry = document.createElement("div");
    entry.className = "log-entry";
    entry.style.color = colors[level] || "#e2e8f0";
    entry.innerHTML = `<span style="color:#6b7280">[${time}]</span> ${icons[level] || "·"} ${escapeHtml(message)}`;

    log.appendChild(entry);
    log.scrollTop = log.scrollHeight;
  }

  function updateStepStatus(stepNumber, status) {
    const stepEl = document.getElementById(`step-${stepNumber}`);
    if (!stepEl) {
      return;
    }

    stepEl.dataset.status = status;
    const iconEl = stepEl.querySelector(".step-icon");
    const statusTextEl = stepEl.querySelector(".small.text-muted");

    const iconMap = {
      pending: '<i class="bi bi-clock text-secondary"></i>',
      running: '<div class="spinner-border spinner-border-sm text-primary" role="status" aria-hidden="true"></div>',
      done: '<i class="bi bi-check-circle-fill text-success"></i>',
      failed: '<i class="bi bi-x-circle-fill text-danger"></i>',
    };

    if (iconEl) {
      iconEl.innerHTML = iconMap[status] || iconMap.pending;
    }
    if (statusTextEl) {
      statusTextEl.textContent = status;
    }
  }

  function appendOutputChunk(chunk) {
    const preview = document.getElementById("output-preview");
    if (!preview) {
      return;
    }

    const placeholder = preview.querySelector(".text-muted.fst-italic");
    if (placeholder) {
      placeholder.remove();
    }

    preview.textContent += chunk;
    preview.scrollTop = preview.scrollHeight;

    const content = preview.textContent.trim();
    const wordCount = content.split(/\s+/).filter((word) => word).length;
    const wordCountEl = document.getElementById("output-word-count");
    if (wordCountEl) {
      wordCountEl.textContent = `${wordCount.toLocaleString("en-IN")} words`;
    }
  }

  function handleFinalEvent(data) {
    if (window.elapsedTimerInterval) {
      clearInterval(window.elapsedTimerInterval);
    }

    updateStatusBanner(data.status);

    const cancelBtn = document.getElementById("cancel-btn");
    if (cancelBtn) {
      cancelBtn.disabled = true;
    }

    if (window.taskEventSource) {
      window.taskEventSource.close();
      window.taskEventSource = null;
    }

    if (data.status === "done" && data.redirect_url) {
      appendLogEntry("complete", "Redirecting to result page...", new Date().toISOString());
      setTimeout(() => {
        window.location.href = data.redirect_url;
      }, 2000);
    }
  }

  function updateStatusBanner(status) {
    const banner = document.getElementById("status-banner");
    if (!banner) {
      return;
    }

    const classMap = {
      pending: "alert alert-secondary d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3",
      running: "alert alert-primary d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3",
      done: "alert alert-success d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3",
      failed: "alert alert-danger d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3",
      cancelled: "alert alert-warning d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3",
    };

    const htmlMap = {
      pending:
        '<div><span>Task queued, waiting for an AI agent to pick it up...</span></div><div class="status-banner-actions d-flex gap-2"></div>',
      running:
        '<div><span class="d-inline-flex align-items-center"><span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>AI agent is working on your task...</span></div><div class="status-banner-actions d-flex gap-2"></div>',
      done:
        `<div><span>Task completed successfully.</span></div><div class="status-banner-actions d-flex gap-2"><a href="/tasks/${currentTaskId}/result" class="btn btn-sm btn-success">View Result</a></div>`,
      failed:
        `<div><span>Task failed. Please review logs and retry.</span></div><div class="status-banner-actions d-flex gap-2"><a href="/tasks/configure/${currentTaskType}" class="btn btn-sm btn-outline-danger">Retry</a></div>`,
      cancelled:
        '<div><span>Task was cancelled.</span></div><div class="status-banner-actions d-flex gap-2"></div>',
    };

    banner.className = classMap[status] || classMap.pending;
    banner.innerHTML = htmlMap[status] || htmlMap.pending;
  }

  function startStatusPolling(taskId) {
    const pollInterval = setInterval(async () => {
      try {
        const resp = await fetch(`/api/tasks/${taskId}/status`, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          credentials: "same-origin",
        });
        const data = await resp.json();

        if (data.success) {
          updateStatusBanner(data.data.status);

          if (["done", "failed", "cancelled"].includes(data.data.status)) {
            clearInterval(pollInterval);
            if (data.data.status === "done") {
              setTimeout(() => {
                window.location.href = `/tasks/${taskId}/result`;
              }, 1500);
            }
          }
        }
      } catch (error) {
        return;
      }
    }, 5000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const taskData = document.getElementById("task-data");
    if (!taskData) {
      return;
    }

    const taskId = taskData.dataset.taskId;
    const taskCreatedAt = taskData.dataset.createdAt;
    const taskStatus = taskData.dataset.status;
    const taskType = taskData.dataset.taskType;
    currentTaskId = taskId || "";
    currentTaskType = taskType || "";

    window.elapsedTimerInterval = startElapsedTimer(taskCreatedAt || "");

    const clearLogBtn = document.getElementById("clear-log-btn");
    if (clearLogBtn) {
      clearLogBtn.addEventListener("click", () => {
        const log = document.getElementById("agent-log");
        if (log) {
          log.innerHTML = '<div class="text-muted">Log cleared.</div>';
        }
      });
    }

    document.getElementById("cancel-btn")?.addEventListener("click", async (event) => {
      if (!window.confirm("Are you sure you want to cancel this task?")) {
        return;
      }

      const cancelButton = event.currentTarget;
      if (window.setButtonLoading) {
        window.setButtonLoading(cancelButton, "Cancelling task...");
      }

      try {
        const resp = await fetch(`/api/tasks/${taskId}/cancel`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : "",
          },
          credentials: "same-origin",
        });
        const data = await resp.json();

        if (data.success) {
          if (window.showToast) {
            window.showToast("Task cancelled", "info");
          }
          setTimeout(() => {
            window.location.href = "/tasks";
          }, 1500);
        } else if (window.showToast) {
          window.showToast(data.message || "Could not cancel task", "warning");
        }
      } catch (error) {
        if (window.showToast) {
          window.showToast("Error cancelling task", "danger");
        }
      } finally {
        if (window.resetButton) {
          window.resetButton(cancelButton);
        }
      }
    });

    if (["done", "failed", "cancelled"].includes(taskStatus || "")) {
      if (window.elapsedTimerInterval) {
        clearInterval(window.elapsedTimerInterval);
      }
      updateStatusBanner(taskStatus || "pending");
      return;
    }

    const eventSource = connectToTaskStream(taskId);
    window.taskEventSource = eventSource;

    window.addEventListener("beforeunload", () => {
      if (window.taskEventSource) {
        window.taskEventSource.close();
      }
    });
  });
})();
