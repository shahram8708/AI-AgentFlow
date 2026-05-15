(function () {
  "use strict";

  /**
   * Return the currently selected billing cycle.
   * @returns {"monthly"|"annual"}
   */
  function getSelectedBillingCycle() {
    var saved = localStorage.getItem("billing_cycle_preference");
    if (saved === "annual") {
      return "annual";
    }

    if (window.defaultBillingCycle === "annual") {
      return "annual";
    }

    return "monthly";
  }

  /**
   * Update active state for monthly and annual toggle buttons.
   * @param {"monthly"|"annual"} cycle
   */
  function updateToggleButtons(cycle) {
    var monthlyBtn = document.getElementById("billing-monthly-btn");
    var annualBtn = document.getElementById("billing-annual-btn");

    if (!monthlyBtn || !annualBtn) {
      return;
    }

    if (cycle === "annual") {
      annualBtn.classList.add("active");
      monthlyBtn.classList.remove("active");
      return;
    }

    monthlyBtn.classList.add("active");
    annualBtn.classList.remove("active");
  }

  /**
   * Render monthly or annual prices in plan comparison table.
   * @param {"monthly"|"annual"} cycle
   */
  function showPrices(cycle) {
    document.querySelectorAll("[data-monthly-price]").forEach(function (element) {
      var monthlyPrice = element.getAttribute("data-monthly-price") || "";
      var annualPrice = element.getAttribute("data-annual-price") || "";
      element.textContent = cycle === "monthly" ? monthlyPrice : annualPrice;
    });

    document.querySelectorAll("[data-price-label]").forEach(function (element) {
      element.textContent = cycle === "monthly" ? "/month" : "/year";
    });

    localStorage.setItem("billing_cycle_preference", cycle);
    updateToggleButtons(cycle);
  }

  /**
   * Initialize monthly annual plan price toggle controls.
   */
  function initPlanToggle() {
    var monthlyBtn = document.getElementById("billing-monthly-btn");
    var annualBtn = document.getElementById("billing-annual-btn");

    if (monthlyBtn) {
      monthlyBtn.addEventListener("click", function () {
        showPrices("monthly");
      });
    }

    if (annualBtn) {
      annualBtn.addEventListener("click", function () {
        showPrices("annual");
      });
    }

    showPrices(getSelectedBillingCycle());
  }

  /**
   * Initialize plan upgrade buttons.
   */
  function initUpgradeButtons() {
    document.querySelectorAll(".upgrade-plan-btn").forEach(function (button) {
      button.addEventListener("click", function () {
        var planId = button.getAttribute("data-plan-id");
        if (!planId) {
          return;
        }
        initiateCheckout(planId, getSelectedBillingCycle());
      });
    });
  }

  /**
   * Start Razorpay checkout by requesting backend order.
   * @param {string} planId
   * @param {"monthly"|"annual"} billingCycle
   */
  async function initiateCheckout(planId, billingCycle) {
    var button = document.querySelector('[data-plan-id="' + planId + '"]');
    var loadingButton = button || null;
    if (loadingButton && window.setButtonLoading) {
      window.setButtonLoading(loadingButton, "Processing payment...");
    }

    try {
      var orderResponse = await fetch("/billing/checkout", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : ""
        },
        body: JSON.stringify({ plan_id: planId, billing_cycle: billingCycle })
      });

      var orderData = await orderResponse.json();
      if (!orderData.success) {
        if (window.showToast) {
          window.showToast(orderData.message || "Failed to initiate checkout", "danger");
        }
        if (window.resetButton && loadingButton) {
          window.resetButton(loadingButton);
        }
        return;
      }

      var options = {
        key: orderData.data.key_id,
        amount: orderData.data.amount,
        currency: "INR",
        name: "AgentFlow Technologies Pvt. Ltd.",
        description: orderData.data.plan_name + " Plan - " + (billingCycle === "monthly" ? "Monthly" : "Annual") + " Subscription",
        order_id: orderData.data.order_id,
        prefill: {
          name: window.currentUserName || "",
          email: window.currentUserEmail || ""
        },
        theme: {
          color: "#1a56db"
        },
        modal: {
          ondismiss: function () {
            if (window.showToast) {
              window.showToast("Payment cancelled", "info");
            }
            if (window.resetButton && loadingButton) {
              window.resetButton(loadingButton);
            }
          }
        },
        handler: async function (response) {
          await verifyPayment(
            response.razorpay_order_id,
            response.razorpay_payment_id,
            response.razorpay_signature,
            orderData.data.plan_name
          );
        }
      };

      var razorpayInstance = new Razorpay(options);
      razorpayInstance.on("payment.failed", function (response) {
        var errorDescription = response && response.error ? response.error.description : "Payment failed";
        if (window.showToast) {
          window.showToast("Payment failed: " + errorDescription, "danger");
        }
        if (window.resetButton && loadingButton) {
          window.resetButton(loadingButton);
        }
      });

      razorpayInstance.open();
    } catch (error) {
      if (window.showToast) {
        window.showToast("Error initiating checkout. Please try again.", "danger");
      }
      if (window.resetButton && loadingButton) {
        window.resetButton(loadingButton);
      }
    }
  }

  /**
   * Verify checkout payment data with backend signature validation.
   * @param {string} orderId
   * @param {string} paymentId
   * @param {string} signature
   * @param {string} planName
   */
  async function verifyPayment(orderId, paymentId, signature, planName) {
    if (window.showToast) {
      window.showToast("Verifying payment...", "info");
    }

    try {
      var verifyResponse = await fetch("/billing/verify", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : ""
        },
        body: JSON.stringify({
          razorpay_order_id: orderId,
          razorpay_payment_id: paymentId,
          razorpay_signature: signature
        })
      });

      var verifyData = await verifyResponse.json();
      if (verifyData.success) {
        if (window.showToast) {
          window.showToast("Successfully upgraded to " + planName + "!", "success");
        }
        setTimeout(function () {
          window.location.href = "/settings/billing";
        }, 2000);
        return;
      }

      if (window.showToast) {
        window.showToast(verifyData.message || "Payment verification failed. Please contact support.", "danger");
      }
    } catch (error) {
      if (window.showToast) {
        window.showToast("Verification error. Please check your billing page or contact support.", "danger");
      }
    }
  }

  /**
   * Request subscription cancellation.
   */
  function cancelSubscription() {
    var triggerButton = document.activeElement && document.activeElement.tagName === "BUTTON"
      ? document.activeElement
      : null;
    var reason = window.prompt("Optional: Why are you cancelling? (helps us improve)") || "";
    var confirmed = window.confirm("Are you sure you want to cancel your subscription? You will lose access at the end of your current billing period.");
    if (!confirmed) {
      return;
    }

    if (triggerButton && window.setButtonLoading) {
      window.setButtonLoading(triggerButton, "Cancelling subscription...");
    }

    fetch("/billing/cancel", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.getCSRFToken ? window.getCSRFToken() : ""
      },
      body: JSON.stringify({ confirmation: "CANCEL", reason: reason })
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        if (!payload.success) {
          if (window.showToast) {
            window.showToast(payload.message || "Unable to cancel subscription", "danger");
          }
          return;
        }

        if (window.showToast) {
          window.showToast(payload.data.message, "warning");
        }
        setTimeout(function () {
          window.location.reload();
        }, 2000);
      })
      .catch(function () {
        if (window.showToast) {
          window.showToast("Unable to cancel subscription right now.", "danger");
        }
      })
      .finally(function () {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
      });
  }

  /**
   * Render billing usage chart for current billing period.
   */
  async function loadUsageChart() {
    var canvas = document.getElementById("billing-usage-chart");
    if (!canvas || typeof Chart === "undefined") {
      return;
    }

    try {
      var response = await fetch("/api/billing/usage-chart", {
        headers: { "X-Requested-With": "XMLHttpRequest" }
      });
      var payload = await response.json();
      if (!payload.success || !payload.data || !Array.isArray(payload.data.daily_usage)) {
        return;
      }

      var labels = payload.data.daily_usage.map(function (item) {
        return item.date;
      });
      var counts = payload.data.daily_usage.map(function (item) {
        return Number(item.count || 0);
      });

      var quotaLimit = Number(payload.data.quota_limit || canvas.getAttribute("data-quota-limit") || 0);
      var datasets = [
        {
          type: "bar",
          label: "Daily Tasks",
          data: counts,
          backgroundColor: "rgba(26, 86, 219, 0.65)",
          borderColor: "rgba(26, 86, 219, 1)",
          borderWidth: 1
        }
      ];

      if (quotaLimit > 0) {
        datasets.push({
          type: "line",
          label: "Quota Limit",
          data: new Array(labels.length).fill(quotaLimit),
          borderColor: "rgba(220, 38, 38, 0.8)",
          borderDash: [6, 4],
          borderWidth: 2,
          pointRadius: 0,
          fill: false
        });
      }

      new Chart(canvas.getContext("2d"), {
        type: "bar",
        data: {
          labels: labels,
          datasets: datasets
        },
        options: {
          maintainAspectRatio: false,
          scales: {
            x: {
              ticks: {
                maxTicksLimit: 10
              }
            },
            y: {
              beginAtZero: true,
              ticks: {
                precision: 0
              }
            }
          },
          plugins: {
            legend: {
              display: true
            }
          }
        }
      });
    } catch (error) {
      if (window.showToast) {
        window.showToast("Could not load usage chart right now.", "warning");
      }
    }
  }

  /**
   * Initialize billing page enhancements.
   */
  function initBillingPage() {
    initPlanToggle();
    initUpgradeButtons();
    loadUsageChart();
  }

  window.getSelectedBillingCycle = getSelectedBillingCycle;
  window.initiateCheckout = initiateCheckout;
  window.verifyPayment = verifyPayment;
  window.cancelSubscription = cancelSubscription;

  document.addEventListener("DOMContentLoaded", initBillingPage);
})();
