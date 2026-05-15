/**
 * Dashboard polling and live stat refresh manager.
 */
class DashboardRefresh {
  /**
   * @param {number} refreshInterval Default refresh interval in milliseconds.
   */
  constructor(refreshInterval = 30000) {
    this.interval = refreshInterval;
    this.timer = null;
    this.lastUpdatedTimer = null;
    this.clockTimer = null;
    this.lastUpdated = new Date();
  }

  /**
   * Start polling and live timers.
   * @returns {void}
   */
  start() {
    this.stop();

    this.timer = window.setInterval(() => {
      this.fetchStats();
    }, this.interval);

    this.lastUpdatedTimer = window.setInterval(() => {
      this.updateLastUpdatedText();
    }, 1000);

    this.clockTimer = window.setInterval(() => {
      this.updateLiveClock();
    }, 1000);

    this.updateLiveClock();
    this.updateLastUpdatedText();
  }

  /**
   * Stop polling and timer loops.
   * @returns {void}
   */
  stop() {
    if (this.timer) {
      window.clearInterval(this.timer);
      this.timer = null;
    }

    if (this.lastUpdatedTimer) {
      window.clearInterval(this.lastUpdatedTimer);
      this.lastUpdatedTimer = null;
    }

    if (this.clockTimer) {
      window.clearInterval(this.clockTimer);
      this.clockTimer = null;
    }
  }

  /**
   * Update polling interval and restart poll timer when needed.
   * @param {number} nextInterval Polling interval in milliseconds.
   * @returns {void}
   */
  setPollingInterval(nextInterval) {
    var safeInterval = Number(nextInterval || 30000);
    if (safeInterval === this.interval) {
      return;
    }

    this.interval = safeInterval;

    if (this.timer) {
      window.clearInterval(this.timer);
      this.timer = window.setInterval(() => {
        this.fetchStats();
      }, this.interval);
    }
  }

  /**
   * Fetch dashboard stats from backend and refresh the UI.
   * @returns {Promise<void>}
   */
  async fetchStats() {
    try {
      var response = await fetch("/api/dashboard/stats", {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin"
      });

      if (!response.ok) {
        throw new Error("Dashboard stats request failed");
      }

      var payload = await response.json();
      if (!payload || !payload.success || !payload.data) {
        throw new Error("Invalid dashboard payload");
      }

      this.updateDOM(payload.data);
      this.lastUpdated = new Date();

      if (payload.data.tasks_running > 0) {
        this.setPollingInterval(10000);
      } else {
        this.setPollingInterval(30000);
      }
    } catch (error) {
      if (window.showToast) {
        window.showToast("Could not refresh stats. Retrying...", "warning", 3200);
      }
      console.warn("Dashboard refresh failed", error);
    }
  }

  /**
   * Apply fresh stat values to dashboard elements.
   * @param {Object} data Dashboard stats payload.
   * @returns {void}
   */
  updateDOM(data) {
    if (!data) {
      return;
    }

    this.setText("stat-tasks-today", this.formatNumber(data.tasks_today));
    this.flashElement("stat-tasks-today");

    this.setText("stat-tasks-month", this.formatNumber(data.tasks_this_month));
    this.flashElement("stat-tasks-month");

    this.setText("stat-running", this.formatNumber(data.tasks_running));
    this.flashElement("stat-running");

    this.setText("stat-success-rate", String(data.success_rate) + "%");
    this.flashElement("stat-success-rate");

    var quotaBar = document.getElementById("quota-bar");
    var quotaText = document.getElementById("quota-text");
    if (quotaBar) {
      quotaBar.style.width = String(Math.max(0, Math.min(Number(data.quota_percent || 0), 100))) + "%";
    }

    if (quotaText) {
      if (Number(data.quota_limit) === -1) {
        quotaText.innerText = String(data.quota_used) + " / Unlimited";
      } else {
        quotaText.innerText = String(data.quota_used) + " / " + String(data.quota_limit) + " tasks";
      }
      this.flashElement("quota-text");
    }

    var badge = document.getElementById("notification-badge");
    if (badge) {
      var unread = Number(data.unread_notifications || 0);
      badge.innerText = String(unread);
      if (unread > 0) {
        badge.classList.remove("d-none");
      } else {
        badge.classList.add("d-none");
      }
    }

    var runningCard = document.getElementById("running-card");
    var runningLink = document.getElementById("running-card-link");
    if (runningCard) {
      if (Number(data.tasks_running || 0) > 0) {
        runningCard.classList.add("is-running");
        if (runningLink) {
          runningLink.classList.remove("d-none");
          runningLink.setAttribute("title", "View running tasks");
        }
      } else {
        runningCard.classList.remove("is-running");
        if (runningLink) {
          runningLink.classList.add("d-none");
        }
      }
    }
  }

  /**
   * Update relative "last updated" label.
   * @returns {void}
   */
  updateLastUpdatedText() {
    var target = document.getElementById("last-updated-text");
    if (!target) {
      return;
    }

    var elapsedSeconds = Math.max(
      0,
      Math.floor((Date.now() - this.lastUpdated.getTime()) / 1000)
    );

    if (elapsedSeconds <= 1) {
      target.innerText = "Last updated 1 second ago";
      return;
    }

    target.innerText = "Last updated " + String(elapsedSeconds) + " seconds ago";
  }

  /**
   * Update live clock text in IST timezone.
   * @returns {void}
   */
  updateLiveClock() {
    var target = document.getElementById("live-time");
    if (!target) {
      return;
    }

    target.innerText = new Date().toLocaleTimeString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour12: false
    }) + " IST";
  }

  /**
   * Format numbers with en-IN locale.
   * @param {number|string} value Number value.
   * @returns {string}
   */
  formatNumber(value) {
    return Number(value || 0).toLocaleString("en-IN");
  }

  /**
   * Set inner text if element exists.
   * @param {string} elementId Target element id.
   * @param {string} value Text value.
   * @returns {void}
   */
  setText(elementId, value) {
    var element = document.getElementById(elementId);
    if (!element) {
      return;
    }
    element.innerText = value;
  }

  /**
   * Apply a brief highlight animation to updated values.
   * @param {string} elementId Target element id.
   * @returns {void}
   */
  flashElement(elementId) {
    var element = document.getElementById(elementId);
    if (!element) {
      return;
    }

    element.classList.remove("stat-flash");
    window.requestAnimationFrame(function () {
      element.classList.add("stat-flash");
      window.setTimeout(function () {
        element.classList.remove("stat-flash");
      }, 500);
    });
  }
}

/**
 * Initialize dashboard polling once the page is ready.
 */
document.addEventListener("DOMContentLoaded", () => {
  const refresh = new DashboardRefresh();
  refresh.start();
  refresh.fetchStats();
  window.dashboardRefresh = refresh;
});
