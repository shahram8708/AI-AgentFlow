(function () {
  "use strict";

  var charts = {};

  function parseJson(value, fallback) {
    try {
      return JSON.parse(value);
    } catch (error) {
      return fallback;
    }
  }

  function toast(message, type) {
    if (window.showToast) {
      window.showToast(message, type || "success");
    }
  }

  function getCsrf() {
    if (typeof window.getCSRFToken === "function") {
      return window.getCSRFToken();
    }
    return "";
  }

  function requestJson(url, method, body) {
    var headers = {
      "X-Requested-With": "XMLHttpRequest",
      "X-CSRFToken": getCsrf(),
    };

    var options = {
      method: method || "GET",
      headers: headers,
    };

    if (body && typeof body === "object") {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    return fetch(url, options).then(function (response) {
      return response.json();
    });
  }

  function createOrUpdateChart(id, config) {
    if (!window.Chart) {
      return;
    }

    var canvas = document.getElementById(id);
    if (!canvas) {
      return;
    }

    if (charts[id]) {
      charts[id].data = config.data;
      charts[id].options = config.options || {};
      charts[id].update();
      return;
    }

    charts[id] = new window.Chart(canvas.getContext("2d"), config);
  }

  function initDashboardPlanChart() {
    var root = document.getElementById("admin-plan-data");
    if (!root) {
      return;
    }

    var distribution = parseJson(root.getAttribute("data-distribution") || "[]", []);
    if (!distribution.length) {
      return;
    }

    createOrUpdateChart("admin-plan-chart", {
      type: "doughnut",
      data: {
        labels: distribution.map(function (item) {
          return item.plan;
        }),
        datasets: [{
          data: distribution.map(function (item) {
            return Number(item.count || 0);
          }),
          backgroundColor: ["#0d6efd", "#198754", "#fd7e14", "#6f42c1", "#dc3545", "#20c997"],
        }],
      },
      options: { responsive: true, maintainAspectRatio: false },
    });
  }

  function initBillingCharts() {
    var root = document.getElementById("admin-billing-data");
    if (!root) {
      return;
    }

    var mrr = parseJson(root.getAttribute("data-mrr") || "[]", []);
    var plan = parseJson(root.getAttribute("data-plan") || "[]", []);

    if (mrr.length) {
      createOrUpdateChart("admin-mrr-chart", {
        type: "line",
        data: {
          labels: mrr.map(function (item) {
            return item.month;
          }),
          datasets: [{
            label: "MRR (INR)",
            data: mrr.map(function (item) {
              return Number(item.mrr || 0);
            }),
            borderColor: "#0d6efd",
            backgroundColor: "rgba(13,110,253,0.16)",
            fill: true,
            tension: 0.3,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: { beginAtZero: true },
          },
        },
      });
    }

    if (plan.length) {
      createOrUpdateChart("admin-plan-distribution-chart", {
        type: "doughnut",
        data: {
          labels: plan.map(function (item) {
            return item.plan;
          }),
          datasets: [{
            data: plan.map(function (item) {
              return Number(item.count || 0);
            }),
            backgroundColor: ["#0d6efd", "#198754", "#ffc107", "#dc3545", "#6f42c1", "#20c997"],
          }],
        },
        options: { responsive: true, maintainAspectRatio: false },
      });
    }
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) {
      el.textContent = value;
    }
  }

  function statusTone(status) {
    if (status === "operational") {
      return "text-success";
    }
    if (status === "degraded") {
      return "text-warning";
    }
    return "text-danger";
  }

  function setStatusIcon(cardId, status) {
    var card = document.getElementById(cardId);
    if (!card) {
      return;
    }

    var icon = card.querySelector(".bi-circle-fill");
    if (!icon) {
      return;
    }

    icon.classList.remove("text-success", "text-warning", "text-danger");
    icon.classList.add(statusTone(status));
  }

  function humanLabel(value) {
    return String(value || "").replace(/_/g, " ").replace(/\b\w/g, function (c) {
      return c.toUpperCase();
    });
  }

  function formatIso(value) {
    if (!value) {
      return "";
    }
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return date.toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function updateSystemDisplay(metrics) {
    if (!metrics) {
      return;
    }

    setStatusIcon("db-status", metrics.database && metrics.database.status);
    setStatusIcon("redis-status", metrics.redis && metrics.redis.status);
    setStatusIcon("celery-status", metrics.celery && metrics.celery.status);

    var appRate = Number(metrics.application && metrics.application.error_rate_24h || 0);
    var appStatus = appRate < 5 ? "operational" : (appRate < 10 ? "degraded" : "outage");
    setStatusIcon("app-status", appStatus);

    setText("db-health-label", humanLabel(metrics.database && metrics.database.status));
    setText("db-response-ms", ((metrics.database && metrics.database.response_ms) != null ? metrics.database.response_ms : "-") + " ms");

    setText("redis-health-label", humanLabel(metrics.redis && metrics.redis.status));
    setText("redis-response-ms", ((metrics.redis && metrics.redis.response_ms) != null ? metrics.redis.response_ms : "-") + " ms");

    setText("celery-health-label", humanLabel(metrics.celery && metrics.celery.status));
    setText("celery-worker-count", String((metrics.celery && metrics.celery.worker_count) || 0) + " workers");

    setText("app-error-rate", appRate.toFixed(2) + "%");
    setText("cpu-progress", String((metrics.system && metrics.system.cpu_percent) || 0) + "%");
    setText("memory-progress", String((metrics.system && metrics.system.memory_percent) || 0) + "%");
    setText("disk-progress", String((metrics.system && metrics.system.disk_percent) || 0) + "%");

    setText("queue-default", String((metrics.celery && metrics.celery.queue_default) || 0));
    setText("queue-high", String((metrics.celery && metrics.celery.queue_high) || 0));
    setText("queue-low", String((metrics.celery && metrics.celery.queue_low) || 0));

    var qDefault = document.getElementById("queue-default");
    if (qDefault) {
      qDefault.classList.toggle("text-danger", Number(metrics.celery && metrics.celery.queue_default || 0) > 50);
    }

    setText("last-refreshed", "Updated: " + formatIso(metrics.last_refreshed));
  }

  function banUser(userId, userName, shouldBan) {
    var triggerButton = document.activeElement && document.activeElement.tagName === "BUTTON"
      ? document.activeElement
      : null;
    var action = shouldBan ? "ban" : "unban";
    var verb = shouldBan ? "ban" : "unban";
    if (!window.confirm("Are you sure you want to " + verb + " " + userName + "?")) {
      return;
    }

    if (triggerButton && window.setButtonLoading) {
      window.setButtonLoading(triggerButton, shouldBan ? "Banning user..." : "Unbanning user...");
    }

    requestJson("/admin/users/" + userId + "/" + action, "POST")
      .then(function (payload) {
        if (payload && payload.success) {
          toast("User updated successfully", shouldBan ? "warning" : "success");
          window.location.reload();
          return;
        }
        toast((payload && payload.message) || "Unable to update user", "danger");
      })
      .catch(function () {
        toast("Unable to update user", "danger");
      })
      .finally(function () {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
      });
  }

  function impersonateUser(userId, userName) {
    var triggerButton = document.activeElement && document.activeElement.tagName === "BUTTON"
      ? document.activeElement
      : null;
    if (!window.confirm("Start impersonating " + userName + "?")) {
      return;
    }

    if (triggerButton && window.setButtonLoading) {
      window.setButtonLoading(triggerButton, "Impersonating...");
    }

    requestJson("/admin/users/" + userId + "/impersonate", "POST")
      .then(function (payload) {
        if (payload && payload.success) {
          var redirectUrl = (payload.data && payload.data.redirect) || "/dashboard";
          window.location.href = redirectUrl;
          return;
        }
        toast((payload && payload.message) || "Unable to impersonate user", "danger");
      })
      .catch(function () {
        toast("Unable to impersonate user", "danger");
      })
      .finally(function () {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
      });
  }

  function toggleFlag(flagKey, currentState) {
    var triggerButton = document.activeElement && document.activeElement.tagName === "BUTTON"
      ? document.activeElement
      : null;
    var _ = currentState;
    if (triggerButton && window.setButtonLoading) {
      window.setButtonLoading(triggerButton, "Updating...");
    }
    requestJson("/admin/flags/" + encodeURIComponent(flagKey) + "/toggle", "POST")
      .then(function (payload) {
        if (!payload || !payload.success || !payload.data) {
          toast((payload && payload.message) || "Unable to toggle flag", "danger");
          return;
        }

        var enabled = Boolean(payload.data.is_enabled);
        var statusEl = document.getElementById("flag-status-" + flagKey);
        if (statusEl) {
          statusEl.textContent = enabled ? "Enabled" : "Disabled";
          statusEl.classList.toggle("bg-success", enabled);
          statusEl.classList.toggle("bg-secondary", !enabled);
        }

        toast("Feature flag updated", "success");
      })
      .catch(function () {
        toast("Unable to toggle flag", "danger");
      })
      .finally(function () {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
      });
  }

  function init() {
    initDashboardPlanChart();
    initBillingCharts();
  }

  window.banUser = banUser;
  window.impersonateUser = impersonateUser;
  window.toggleFlag = toggleFlag;
  window.updateSystemDisplay = updateSystemDisplay;

  document.addEventListener("DOMContentLoaded", init);
})();
