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

  function getDataRoot() {
    return document.getElementById("chart-data");
  }

  function shortDate(isoDate) {
    if (!isoDate) {
      return "";
    }
    var parts = String(isoDate).split("-");
    if (parts.length !== 3) {
      return String(isoDate);
    }
    var dateObj = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    if (Number.isNaN(dateObj.getTime())) {
      return String(isoDate);
    }
    return dateObj.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  }

  function formatInt(value) {
    return Number(value || 0).toLocaleString("en-IN");
  }

  function formatDuration(seconds) {
    var total = Math.max(0, Math.round(Number(seconds || 0)));
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var remaining = total % 60;

    if (hours > 0) {
      return hours + "h " + minutes + "m";
    }
    if (minutes > 0) {
      return minutes + "m " + remaining + "s";
    }
    return remaining + "s";
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

  function renderLineChart(items) {
    var labels = items.map(function (item) {
      return shortDate(item.date);
    });

    createOrUpdateChart("reports-line-chart", {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Total",
            data: items.map(function (item) {
              return Number(item.count || 0);
            }),
            borderColor: "#0d6efd",
            backgroundColor: "rgba(13,110,253,0.14)",
            tension: 0.3,
            fill: true,
          },
          {
            label: "Completed",
            data: items.map(function (item) {
              return Number(item.completed || 0);
            }),
            borderColor: "#198754",
            backgroundColor: "rgba(25,135,84,0.12)",
            tension: 0.3,
          },
          {
            label: "Failed",
            data: items.map(function (item) {
              return Number(item.failed || 0);
            }),
            borderColor: "#dc3545",
            backgroundColor: "rgba(220,53,69,0.12)",
            tension: 0.3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: true },
        },
      },
    });
  }

  function statusColor(status) {
    var map = {
      done: "#198754",
      failed: "#dc3545",
      running: "#0d6efd",
      pending: "#6c757d",
      cancelled: "#f59f00",
    };
    return map[String(status || "").toLowerCase()] || "#94a3b8";
  }

  function renderDoughnutChart(items) {
    var labels = items.map(function (item) {
      return item.label || item.status;
    });
    var values = items.map(function (item) {
      return Number(item.count || 0);
    });
    var colors = items.map(function (item) {
      return statusColor(item.status);
    });

    createOrUpdateChart("reports-doughnut-chart", {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: colors,
          borderWidth: 1,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
      },
    });
  }

  function renderBarChart(items) {
    var labels = items.map(function (item) {
      return item.label || item.category;
    });
    var values = items.map(function (item) {
      return Number(item.count || 0);
    });

    createOrUpdateChart("reports-bar-chart", {
      type: "bar",
      data: {
        labels: labels,
        datasets: [{
          label: "Tasks",
          data: values,
          backgroundColor: "rgba(13,110,253,0.78)",
          borderRadius: 6,
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

  function renderRateChart(items) {
    var labels = items.map(function (item) {
      return shortDate(item.date);
    });
    var values = items.map(function (item) {
      return Number(item.rate || 0);
    });

    createOrUpdateChart("reports-rate-chart", {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          label: "Success Rate %",
          data: values,
          borderColor: "#20c997",
          backgroundColor: "rgba(32,201,151,0.18)",
          fill: true,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            min: 0,
            max: 100,
          },
        },
      },
    });
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (!el) {
      return;
    }
    el.textContent = value;
  }

  function updateKpis(kpis) {
    if (!kpis) {
      return;
    }

    setText("kpi-total-tasks", formatInt(kpis.total_tasks));
    setText("kpi-completed-tasks", formatInt(kpis.completed_tasks));
    setText("kpi-failed-tasks", formatInt(kpis.failed_tasks));
    setText("kpi-success-rate", Number(kpis.success_rate || 0).toFixed(1) + "%");
    setText("kpi-avg-duration", formatDuration(kpis.avg_duration_seconds));
  }

  function renderAll(data) {
    renderLineChart(data.line_chart_data || []);
    renderDoughnutChart(data.pie_chart_data || []);
    renderBarChart(data.bar_chart_data || []);
    renderRateChart(data.daily_success_rate || []);
    if (data.kpis) {
      updateKpis(data.kpis);
    }
  }

  function parseInitialData() {
    var root = getDataRoot();
    if (!root) {
      return null;
    }

    return {
      line_chart_data: parseJson(root.getAttribute("data-line-chart") || "[]", []),
      pie_chart_data: parseJson(root.getAttribute("data-pie-chart") || "[]", []),
      bar_chart_data: parseJson(root.getAttribute("data-bar-chart") || "[]", []),
      daily_success_rate: parseJson(root.getAttribute("data-rate-chart") || "[]", []),
    };
  }

  function initDateRangeInteractions() {
    var trigger = document.getElementById("custom-period-trigger");
    var form = document.getElementById("reports-range-form");
    var periodInput = document.getElementById("period-input");
    var fromInput = document.getElementById("date-from");
    var toInput = document.getElementById("date-to");

    if (trigger && periodInput && fromInput && toInput) {
      trigger.addEventListener("click", function () {
        periodInput.value = "custom";
        fromInput.classList.remove("d-none");
        toInput.classList.remove("d-none");
      });
    }

    if (!form || !periodInput || !fromInput || !toInput) {
      return;
    }

    form.addEventListener("submit", function (event) {
      if (periodInput.value !== "custom") {
        return;
      }

      if (!fromInput.value || !toInput.value) {
        event.preventDefault();
        if (window.showToast) {
          window.showToast("Select both start and end date for custom range.", "warning");
        }
        return;
      }

      var fromDate = new Date(fromInput.value + "T00:00:00");
      var toDate = new Date(toInput.value + "T00:00:00");

      if (fromDate > toDate) {
        event.preventDefault();
        if (window.showToast) {
          window.showToast("Start date cannot be after end date.", "warning");
        }
        return;
      }

      var dayMs = 24 * 60 * 60 * 1000;
      var diffDays = Math.floor((toDate - fromDate) / dayMs);
      if (diffDays > 365) {
        event.preventDefault();
        if (window.showToast) {
          window.showToast("Date range cannot exceed 365 days.", "warning");
        }
      }
    });
  }

  function refreshChartData() {
    var root = getDataRoot();
    if (!root) {
      return;
    }

    var params = new URLSearchParams(window.location.search || "");
    if (!params.get("period")) {
      params.set("period", root.getAttribute("data-period") || "30d");
    }

    var dateFrom = root.getAttribute("data-date-from");
    var dateTo = root.getAttribute("data-date-to");
    if (dateFrom && !params.get("date_from")) {
      params.set("date_from", dateFrom);
    }
    if (dateTo && !params.get("date_to")) {
      params.set("date_to", dateTo);
    }

    fetch("/api/reports/chart-data?" + params.toString(), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        if (!payload || !payload.success || !payload.data) {
          return;
        }
        renderAll(payload.data);
      })
      .catch(function () {
      });
  }

  function init() {
    var initialData = parseInitialData();
    if (!initialData) {
      return;
    }

    renderAll(initialData);
    initDateRangeInteractions();
    window.setInterval(refreshChartData, 30000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
