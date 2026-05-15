(function () {
  'use strict';

  /**
   * Return ordinal suffix label.
   * @param {number} value
   * @returns {string}
   */
  function ordinal(value) {
    var number = Number(value || 0);
    if (number % 100 >= 11 && number % 100 <= 13) {
      return number + 'th';
    }
    if (number % 10 === 1) {
      return number + 'st';
    }
    if (number % 10 === 2) {
      return number + 'nd';
    }
    if (number % 10 === 3) {
      return number + 'rd';
    }
    return number + 'th';
  }

  /**
   * Convert 24h values to 12h label.
   * @param {number} hour
   * @param {number} minute
   * @returns {string}
   */
  function formatTime(hour, minute) {
    var h = Number(hour || 0);
    var m = Number(minute || 0);
    var period = h < 12 ? 'AM' : 'PM';
    var displayHour = h % 12;
    if (displayHour === 0) {
      displayHour = 12;
    }
    return displayHour + ':' + String(m).padStart(2, '0') + ' ' + period;
  }

  /**
   * Validate 5 field cron expression format.
   * @param {string} expression
   * @returns {boolean}
   */
  function validateCronExpression(expression) {
    var expr = String(expression || '').trim();
    var parts = expr.split(/\s+/);
    if (parts.length !== 5) {
      return false;
    }

    var tokenRegex = /^(\*|\d+|\d+-\d+|\*\/\d+|\d+(,\d+)+)$/;
    return parts.every(function (part) {
      return tokenRegex.test(part);
    });
  }

  var CronBuilder = {
    scheduleType: 'daily',
    hour: 9,
    minute: 0,
    dayOfWeek: [1],
    dayOfMonth: 1,
    customExpression: '',

    /**
     * Build cron expression from current state.
     * @returns {string}
     */
    getCronExpression: function () {
      switch (this.scheduleType) {
        case 'daily':
          return this.minute + ' ' + this.hour + ' * * *';
        case 'weekly': {
          var days = this.dayOfWeek.length ? this.dayOfWeek.join(',') : '1';
          return this.minute + ' ' + this.hour + ' * * ' + days;
        }
        case 'monthly':
          return this.minute + ' ' + this.hour + ' ' + this.dayOfMonth + ' * *';
        case 'custom':
          return String(this.customExpression || '').trim();
        default:
          return '0 9 * * *';
      }
    },

    /**
     * Build human description from current state.
     * @returns {string}
     */
    getDescription: function () {
      if (this.scheduleType === 'daily') {
        return 'At ' + formatTime(this.hour, this.minute) + ' every day';
      }

      if (this.scheduleType === 'weekly') {
        var names = {
          0: 'Sunday',
          1: 'Monday',
          2: 'Tuesday',
          3: 'Wednesday',
          4: 'Thursday',
          5: 'Friday',
          6: 'Saturday'
        };
        var dayLabels = this.dayOfWeek.slice().sort().map(function (day) {
          return names[day];
        });
        return 'At ' + formatTime(this.hour, this.minute) + ' every ' + dayLabels.join(', ');
      }

      if (this.scheduleType === 'monthly') {
        return 'At ' + formatTime(this.hour, this.minute) + ' on the ' + ordinal(this.dayOfMonth) + ' of every month';
      }

      return 'Custom: ' + String(this.customExpression || '').trim();
    },

    /**
     * Update preview text and hidden expression fields.
     */
    updatePreview: function () {
      var expression = this.getCronExpression();
      var hiddenExpression = document.getElementById('cron_expression');
      var hiddenScheduleType = document.getElementById('schedule_type');
      var previewText = document.getElementById('schedule-preview-text');
      var nextRunPreview = document.getElementById('next-run-preview');
      var customError = document.getElementById('custom-cron-error');

      if (hiddenExpression) {
        hiddenExpression.value = expression;
      }
      if (hiddenScheduleType) {
        hiddenScheduleType.value = this.scheduleType;
      }
      if (previewText) {
        previewText.textContent = this.getDescription();
      }

      if (customError) {
        customError.classList.add('d-none');
      }

      if (this.scheduleType === 'custom' && expression && !validateCronExpression(expression)) {
        if (customError) {
          customError.textContent = 'Invalid cron expression. Use 5 fields (minute hour day month day_of_week).';
          customError.classList.remove('d-none');
        }
        if (nextRunPreview) {
          nextRunPreview.textContent = 'Fix cron expression to preview next run';
        }
        return;
      }

      if (nextRunPreview) {
        nextRunPreview.textContent = getNextRunIST(expression, this.scheduleType, this);
      }
    }
  };

  /**
   * Show selected schedule option panel.
   * @param {string} type
   */
  function showScheduleOptions(type) {
    ['daily', 'weekly', 'monthly', 'custom'].forEach(function (name) {
      var section = document.querySelector('.schedule-' + name + '-options');
      if (!section) {
        return;
      }
      section.classList.toggle('d-none', name !== type);
    });
  }

  /**
   * Basic client side next run preview for simple patterns.
   * @param {string} cronExpr
   * @param {string} scheduleType
   * @param {object} state
   * @returns {string}
   */
  function getNextRunIST(cronExpr, scheduleType, state) {
    if (!cronExpr) {
      return 'Calculated on save';
    }

    if (scheduleType === 'custom') {
      return 'Calculated on save';
    }

    var now = new Date();
    var next = new Date(now.getTime());

    if (scheduleType === 'daily') {
      next.setHours(state.hour, state.minute, 0, 0);
      if (next <= now) {
        next.setDate(next.getDate() + 1);
      }
    } else if (scheduleType === 'weekly') {
      var sortedDays = state.dayOfWeek.slice().sort(function (a, b) { return a - b; });
      if (!sortedDays.length) {
        sortedDays = [1];
      }
      var currentDay = now.getDay();
      var targetDay = sortedDays[0];
      var minDiff = 8;

      sortedDays.forEach(function (day) {
        var diff = day - currentDay;
        if (diff < 0) {
          diff += 7;
        }
        if (diff < minDiff) {
          minDiff = diff;
          targetDay = day;
        }
      });

      next.setDate(now.getDate() + minDiff);
      next.setHours(state.hour, state.minute, 0, 0);

      if (next <= now) {
        next.setDate(next.getDate() + 7);
        while (next.getDay() !== targetDay) {
          next.setDate(next.getDate() + 1);
        }
      }
    } else if (scheduleType === 'monthly') {
      next.setDate(state.dayOfMonth);
      next.setHours(state.hour, state.minute, 0, 0);
      if (next <= now) {
        next.setMonth(next.getMonth() + 1);
        next.setDate(state.dayOfMonth);
      }
    }

    return next.toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    }) + ' IST';
  }

  /**
   * Fill hour and minute select inputs.
   */
  function initializeTimeInputs() {
    var hourSelectors = document.querySelectorAll('.schedule-hour-input');
    var minuteSelectors = document.querySelectorAll('.schedule-minute-input');

    hourSelectors.forEach(function (select) {
      if (select.options.length) {
        return;
      }
      for (var h = 0; h < 24; h += 1) {
        var hourOption = document.createElement('option');
        hourOption.value = String(h);
        hourOption.textContent = String(h).padStart(2, '0');
        if (h === 9) {
          hourOption.selected = true;
        }
        select.appendChild(hourOption);
      }
    });

    minuteSelectors.forEach(function (select) {
      if (select.options.length) {
        return;
      }
      for (var m = 0; m < 60; m += 1) {
        var minuteOption = document.createElement('option');
        minuteOption.value = String(m);
        minuteOption.textContent = String(m).padStart(2, '0');
        if (m === 0) {
          minuteOption.selected = true;
        }
        select.appendChild(minuteOption);
      }
    });
  }

  /**
   * Keep all time selector groups in sync.
   */
  function syncTimeSelectors() {
    document.querySelectorAll('.schedule-hour-input').forEach(function (select) {
      select.value = String(CronBuilder.hour);
    });
    document.querySelectorAll('.schedule-minute-input').forEach(function (select) {
      select.value = String(CronBuilder.minute);
    });
  }

  /**
   * Initialize countdown badges for next run cells.
   */
  function initCountdowns() {
    var countdownEls = document.querySelectorAll('.countdown[data-next-run]');
    if (!countdownEls.length) {
      return;
    }

    function parseNextRun(value) {
      var normalized = String(value || '').trim();
      if (!normalized) {
        return null;
      }

      if (!normalized.endsWith('Z') && normalized.indexOf('+') === -1) {
        normalized += 'Z';
      }

      var parsed = new Date(normalized);
      if (Number.isNaN(parsed.getTime())) {
        return null;
      }
      return parsed;
    }

    function updateCountdown(el) {
      var nextRun = parseNextRun(el.dataset.nextRun);
      if (!nextRun) {
        el.textContent = 'Unknown';
        return;
      }

      var now = new Date();
      var diff = nextRun.getTime() - now.getTime();

      if (diff <= 0) {
        el.textContent = 'Overdue';
        el.className = 'countdown badge bg-warning';
        return;
      }

      var days = Math.floor(diff / 86400000);
      var hours = Math.floor((diff % 86400000) / 3600000);
      var mins = Math.floor((diff % 3600000) / 60000);

      if (days > 0) {
        el.textContent = 'in ' + days + 'd ' + hours + 'h';
        el.className = 'countdown badge text-bg-light border';
      } else if (hours > 0) {
        el.textContent = 'in ' + hours + 'h ' + mins + 'm';
        el.className = 'countdown badge text-bg-light border';
      } else {
        el.textContent = 'in ' + mins + 'm';
        el.className = 'countdown badge bg-warning';
      }
    }

    countdownEls.forEach(function (el) { updateCountdown(el); });
    window.setInterval(function () {
      countdownEls.forEach(function (el) { updateCountdown(el); });
    }, 60000);
  }

  /**
   * Bind schedule toggle handlers.
   */
  function attachToggleHandlers() {
    document.querySelectorAll('.schedule-toggle').forEach(function (toggle) {
      toggle.addEventListener('change', async function () {
        var jobId = this.dataset.jobId;
        var expectedState = this.checked;
        if (!jobId) {
          return;
        }

        if (window.setToggleLoading) {
          window.setToggleLoading(this);
        }

        try {
          var response = await fetch('/schedules/' + encodeURIComponent(jobId) + '/toggle', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': window.getCSRFToken ? window.getCSRFToken() : ''
            },
            credentials: 'same-origin'
          });
          var data = await response.json();
          if (data && data.success) {
            var isActive = data.data ? Boolean(data.data.is_active) : expectedState;
            this.checked = isActive;

            var row = this.closest('tr');
            if (row && data.data && data.data.next_run_at) {
              var countdown = row.querySelector('.countdown');
              if (countdown) {
                countdown.dataset.nextRun = data.data.next_run_at;
              }
            }

            if (window.showToast) {
              window.showToast('Schedule ' + (isActive ? 'active' : 'paused'), 'success');
            }
            if (window.resetToggleLoading) {
              window.resetToggleLoading(this, false);
            }
          } else {
            if (window.resetToggleLoading) {
              window.resetToggleLoading(this, true);
            } else {
              this.checked = !expectedState;
            }
            if (window.showToast) {
              window.showToast((data && data.message) || 'Failed to update schedule', 'danger');
            }
          }
        } catch (error) {
          if (window.resetToggleLoading) {
            window.resetToggleLoading(this, true);
          } else {
            this.checked = !expectedState;
          }
          if (window.showToast) {
            window.showToast('Error updating schedule', 'danger');
          }
        } finally {
          if (window.resetToggleLoading && this.disabled) {
            window.resetToggleLoading(this, false);
          }
        }
      });
    });
  }

  /**
   * Bind edit schedule button handlers.
   */
  function attachEditModalHandlers() {
    document.querySelectorAll('.edit-schedule-btn').forEach(function (button) {
      button.addEventListener('click', function () {
        var jobId = button.dataset.jobId;
        var jobName = button.dataset.jobName;
        var cronExpr = button.dataset.cron;
        var timezone = button.dataset.timezone;
        var workflowId = button.dataset.workflowId;
        var isActive = button.dataset.isActive === '1';

        var idInput = document.getElementById('edit-schedule-id');
        var nameInput = document.getElementById('edit-schedule-name');
        var cronInput = document.getElementById('edit-cron-expression');
        var timezoneInput = document.getElementById('edit-timezone');
        var workflowInput = document.getElementById('edit-workflow-id');
        var activeInput = document.getElementById('edit-is-active');

        if (idInput) { idInput.value = jobId || ''; }
        if (nameInput) { nameInput.value = jobName || ''; }
        if (cronInput) { cronInput.value = cronExpr || ''; }
        if (timezoneInput) { timezoneInput.value = timezone || 'Asia/Kolkata'; }
        if (workflowInput) { workflowInput.value = workflowId || ''; }
        if (activeInput) { activeInput.checked = isActive; }

        var modalElement = document.getElementById('edit-schedule-modal');
        if (modalElement) {
          bootstrap.Modal.getOrCreateInstance(modalElement).show();
        }
      });
    });

    var editForm = document.getElementById('edit-schedule-form');
    if (editForm) {
      editForm.addEventListener('submit', async function (event) {
        event.preventDefault();
        var submitButton = editForm.querySelector('button[type="submit"], input[type="submit"]');

        var scheduleId = document.getElementById('edit-schedule-id') ? document.getElementById('edit-schedule-id').value : '';
        if (!scheduleId) {
          return;
        }

        var payload = {
          name: document.getElementById('edit-schedule-name') ? document.getElementById('edit-schedule-name').value : '',
          cron_expression: document.getElementById('edit-cron-expression') ? document.getElementById('edit-cron-expression').value : '',
          timezone: document.getElementById('edit-timezone') ? document.getElementById('edit-timezone').value : 'Asia/Kolkata',
          is_active: document.getElementById('edit-is-active') ? document.getElementById('edit-is-active').checked : true,
          notify_on_completion: document.getElementById('edit-notify-on-completion') ? document.getElementById('edit-notify-on-completion').checked : true,
          schedule_type: 'custom'
        };

        if (!validateCronExpression(payload.cron_expression)) {
          if (window.showToast) {
            window.showToast('Invalid cron expression', 'danger');
          }
          return;
        }

        try {
          if (submitButton && window.setButtonLoading) {
            window.setButtonLoading(submitButton, 'Saving...');
          }
          var response = await fetch('/schedules/' + encodeURIComponent(scheduleId), {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': window.getCSRFToken ? window.getCSRFToken() : ''
            },
            credentials: 'same-origin',
            body: JSON.stringify(payload)
          });
          var data = await response.json();
          if (data && data.success) {
            window.location.reload();
            return;
          }
          if (window.showToast) {
            window.showToast((data && data.message) || 'Could not update schedule', 'danger');
          }
        } catch (error) {
          if (window.showToast) {
            window.showToast('Could not update schedule', 'danger');
          }
        } finally {
          if (submitButton && window.resetButton) {
            window.resetButton(submitButton);
          }
        }
      });
    }
  }

  /**
   * Bind delete schedule handlers.
   */
  function attachDeleteHandlers() {
    document.querySelectorAll('.delete-schedule-btn').forEach(function (button) {
      button.addEventListener('click', async function () {
        var jobId = button.dataset.jobId;
        if (!jobId) {
          return;
        }

        if (!window.confirm('Delete this schedule?')) {
          return;
        }

        if (window.setButtonLoading) {
          window.setButtonLoading(button, 'Deleting...');
        }

        try {
          var response = await fetch('/schedules/' + encodeURIComponent(jobId), {
            method: 'DELETE',
            headers: {
              'X-CSRFToken': window.getCSRFToken ? window.getCSRFToken() : ''
            },
            credentials: 'same-origin'
          });
          var data = await response.json();
          if (data && data.success) {
            var row = button.closest('tr');
            if (row) {
              row.remove();
            }
            if (window.showToast) {
              window.showToast('Schedule deleted', 'success');
            }
            return;
          }
          if (window.showToast) {
            window.showToast((data && data.message) || 'Could not delete schedule', 'danger');
          }
        } catch (error) {
          if (window.showToast) {
            window.showToast('Could not delete schedule', 'danger');
          }
        } finally {
          if (window.resetButton) {
            window.resetButton(button);
          }
        }
      });
    });
  }

  /**
   * Bind schedule builder UI events.
   */
  function attachBuilderHandlers() {
    initializeTimeInputs();
    syncTimeSelectors();

    document.querySelectorAll('.schedule-type-card').forEach(function (card) {
      card.addEventListener('click', function () {
        document.querySelectorAll('.schedule-type-card').forEach(function (node) {
          node.classList.remove('selected');
        });
        card.classList.add('selected');

        CronBuilder.scheduleType = card.dataset.type || 'daily';
        showScheduleOptions(CronBuilder.scheduleType);
        CronBuilder.updatePreview();
      });
    });

    document.querySelectorAll('.schedule-hour-input').forEach(function (select) {
      select.addEventListener('change', function () {
        CronBuilder.hour = Number(select.value || 0);
        syncTimeSelectors();
        CronBuilder.updatePreview();
      });
    });

    document.querySelectorAll('.schedule-minute-input').forEach(function (select) {
      select.addEventListener('change', function () {
        CronBuilder.minute = Number(select.value || 0);
        syncTimeSelectors();
        CronBuilder.updatePreview();
      });
    });

    document.querySelectorAll('.day-pill-btn').forEach(function (button) {
      button.addEventListener('click', function () {
        var day = Number(button.dataset.day);
        var index = CronBuilder.dayOfWeek.indexOf(day);
        if (index >= 0) {
          CronBuilder.dayOfWeek.splice(index, 1);
        } else {
          CronBuilder.dayOfWeek.push(day);
        }

        if (!CronBuilder.dayOfWeek.length) {
          CronBuilder.dayOfWeek = [1];
        }

        document.querySelectorAll('.day-pill-btn').forEach(function (node) {
          var currentDay = Number(node.dataset.day);
          node.classList.toggle('active', CronBuilder.dayOfWeek.indexOf(currentDay) !== -1);
        });

        CronBuilder.updatePreview();
      });
    });

    document.querySelectorAll('.month-day-btn').forEach(function (button) {
      button.addEventListener('click', function () {
        CronBuilder.dayOfMonth = Number(button.dataset.day || 1);
        document.querySelectorAll('.month-day-btn').forEach(function (node) {
          node.classList.toggle('active', Number(node.dataset.day || 0) === CronBuilder.dayOfMonth);
        });
        CronBuilder.updatePreview();
      });
    });

    var customInput = document.getElementById('custom-cron-input');
    if (customInput) {
      customInput.addEventListener('input', function () {
        CronBuilder.customExpression = customInput.value;
        CronBuilder.updatePreview();
      });
    }

    var timezoneSelect = document.getElementById('schedule-timezone');
    if (timezoneSelect) {
      timezoneSelect.addEventListener('change', function () {
        CronBuilder.updatePreview();
      });
    }

    var createForm = document.getElementById('create-schedule-form');
    if (createForm) {
      createForm.addEventListener('submit', function (event) {
        var submitButton = createForm.querySelector('button[type="submit"], input[type="submit"]');
        CronBuilder.updatePreview();
        var expression = CronBuilder.getCronExpression();
        if (!validateCronExpression(expression)) {
          event.preventDefault();
          if (window.showToast) {
            window.showToast('Invalid cron expression', 'danger');
          }
          if (submitButton && window.resetButton) {
            window.resetButton(submitButton);
          }
          return;
        }

        if (submitButton && window.setButtonLoading) {
          window.setButtonLoading(submitButton, 'Creating...');
        }
      });
    }

    showScheduleOptions('daily');
    CronBuilder.updatePreview();
  }

  document.addEventListener('DOMContentLoaded', function () {
    attachBuilderHandlers();
    initCountdowns();
    attachToggleHandlers();
    attachEditModalHandlers();
    attachDeleteHandlers();
  });
})();
