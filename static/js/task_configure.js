(function () {
  'use strict';

  function initCharacterCounters() {
    document.querySelectorAll('textarea[maxlength]').forEach(function (textarea) {
      const maxLen = Number(textarea.getAttribute('maxlength') || '0');
      const counter = document.createElement('div');
      counter.className = 'text-end small text-muted mt-1';
      counter.id = 'counter_' + textarea.name;
      textarea.parentNode.appendChild(counter);

      function updateCounter() {
        const currentLength = textarea.value.length;
        const remaining = maxLen - currentLength;
        counter.textContent = currentLength + ' / ' + maxLen + ' characters';
        counter.classList.remove('text-warning', 'text-danger');
        if (remaining < 50) {
          counter.classList.add('text-warning');
        }
        if (remaining < 0) {
          counter.classList.add('text-danger');
        }
      }

      textarea.addEventListener('input', updateCounter);
      updateCounter();
    });
  }

  function removeFieldError(field) {
    field.classList.remove('is-invalid');
    const container = field.closest('[data-field-container]') || field.parentNode;
    const existing = container.querySelector('.invalid-feedback.field-feedback');
    if (existing) {
      existing.remove();
    }
  }

  function showFieldError(field, message) {
    field.classList.add('is-invalid');
    const container = field.closest('[data-field-container]') || field.parentNode;
    let feedback = container.querySelector('.invalid-feedback.field-feedback');
    if (!feedback) {
      feedback = document.createElement('div');
      feedback.className = 'invalid-feedback field-feedback d-block';
      container.appendChild(feedback);
    }
    feedback.textContent = message;
  }

  function validateRequiredCheckboxGroups() {
    const errors = [];
    const processedGroups = new Set();

    document.querySelectorAll('input[data-required-group]').forEach(function (checkbox) {
      const groupName = checkbox.getAttribute('data-required-group');
      if (!groupName || processedGroups.has(groupName)) {
        return;
      }

      processedGroups.add(groupName);
      const groupCheckboxes = Array.from(document.querySelectorAll('input[name="' + groupName + '"]'));
      const hasChecked = groupCheckboxes.some(function (item) { return item.checked; });

      if (!hasChecked) {
        errors.push({ field: groupCheckboxes[0], message: 'Please select at least one option.' });
      }
    });

    return errors;
  }

  function initFormValidation() {
    const form = document.getElementById('task-config-form');
    if (!form) {
      return;
    }

    form.addEventListener('submit', function (event) {
      const validationErrors = [];
      let firstInvalid = null;

      form.querySelectorAll('.is-invalid').forEach(function (node) {
        node.classList.remove('is-invalid');
      });
      form.querySelectorAll('.invalid-feedback.field-feedback').forEach(function (node) {
        node.remove();
      });

      form.querySelectorAll('input[required], textarea[required], select[required]').forEach(function (field) {
        removeFieldError(field);

        if (field.tagName === 'SELECT') {
          if (!field.value || !field.value.trim()) {
            validationErrors.push({ field: field, message: 'Please select a value.' });
            return;
          }
        }

        if (field.tagName === 'INPUT' || field.tagName === 'TEXTAREA') {
          if (!String(field.value || '').trim()) {
            validationErrors.push({ field: field, message: 'This field is required.' });
            return;
          }
        }

        const minLength = Number(field.getAttribute('data-min-length') || field.getAttribute('minlength') || '0');
        if (minLength > 0 && String(field.value || '').trim().length < minLength) {
          validationErrors.push({
            field: field,
            message: 'Please enter at least ' + minLength + ' characters.'
          });
        }
      });

      validateRequiredCheckboxGroups().forEach(function (error) {
        validationErrors.push(error);
      });

      const saveWorkflowToggle = document.getElementById('save-workflow-toggle');
      const workflowName = document.getElementById('workflow_name');
      if (saveWorkflowToggle && saveWorkflowToggle.checked && workflowName) {
        if (!String(workflowName.value || '').trim()) {
          validationErrors.push({
            field: workflowName,
            message: 'Workflow name is required when Save as workflow is enabled.'
          });
        }
      }

      if (validationErrors.length > 0) {
        event.preventDefault();
        validationErrors.forEach(function (error, index) {
          showFieldError(error.field, error.message);
          if (index === 0) {
            firstInvalid = error.field;
          }
        });

        if (firstInvalid) {
          firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
          firstInvalid.focus({ preventScroll: true });
        }
      }
    });
  }

  function initTemplateLoader() {
    document.querySelectorAll('.load-template-btn').forEach(function (button) {
      button.addEventListener('click', async function () {
        const templateId = button.getAttribute('data-template-id');
        if (!templateId) {
          return;
        }

        button.disabled = true;
        try {
          const response = await fetch('/tasks/template/' + encodeURIComponent(templateId) + '/data', {
            headers: {
              'X-Requested-With': 'XMLHttpRequest'
            },
            credentials: 'same-origin'
          });

          if (!response.ok) {
            throw new Error('Template request failed');
          }

          const payload = await response.json();
          if (!payload || !payload.success || !payload.data) {
            throw new Error('Invalid template payload');
          }

          const template = payload.data;
          const stepsText = Array.isArray(template.steps_json)
            ? template.steps_json.map(function (step, index) {
                return (index + 1) + '. ' + (step.step_name || step.task_type || 'Step');
              }).join('\n')
            : '';

          const textareas = Array.from(document.querySelectorAll('#task-config-form textarea'));
          if (textareas.length) {
            const firstTextarea = textareas[0];
            const prefill = [
              'Template: ' + (template.name || ''),
              template.description || '',
              stepsText
            ].filter(Boolean).join('\n\n');
            firstTextarea.value = prefill;
            firstTextarea.dispatchEvent(new Event('input', { bubbles: true }));
          }

          if (window.showToast) {
            window.showToast('Template loaded. Review and customize the fields.', 'success');
          }
        } catch (error) {
          if (window.showToast) {
            window.showToast('Could not load template.', 'error');
          }
        } finally {
          button.disabled = false;
        }
      });
    });
  }

  function initEstimatedTimeUpdater() {
    const depthSelect = document.querySelector('select[name="depth_level"]');
    const estimatedNode = document.getElementById('estimated-time-dynamic');
    if (!depthSelect || !estimatedNode) {
      return;
    }

    const depthMap = {
      quick: '~2 minutes',
      standard: '~5 minutes',
      deep: '~12 minutes'
    };

    const usesWebSearch = estimatedNode.textContent.toLowerCase().includes('uses web search');

    function updateEstimatedText() {
      const selected = depthSelect.value || 'standard';
      const estimate = depthMap[selected] || '~5 minutes';
      estimatedNode.textContent = 'Estimated time: ' + estimate + ' • ' + (usesWebSearch ? 'Uses web search' : 'No web search required');
    }

    depthSelect.addEventListener('change', updateEstimatedText);
    updateEstimatedText();
  }

  function initSaveWorkflowToggle() {
    const toggle = document.getElementById('save-workflow-toggle');
    const wrapper = document.getElementById('workflow-name-wrap');
    const input = document.getElementById('workflow_name');

    if (!toggle || !wrapper || !input) {
      return;
    }

    function syncState() {
      const isChecked = toggle.checked;
      wrapper.classList.toggle('open', isChecked);
      input.required = isChecked;
      if (!isChecked) {
        input.value = '';
      }
    }

    toggle.addEventListener('change', syncState);
    syncState();
  }

  document.addEventListener('DOMContentLoaded', function () {
    initCharacterCounters();
    initFormValidation();
    initTemplateLoader();
    initEstimatedTimeUpdater();
    initSaveWorkflowToggle();
  });
})();
