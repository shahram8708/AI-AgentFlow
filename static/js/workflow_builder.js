/**
 * Workflow builder page controller.
 */
(function () {
  'use strict';

  /**
   * Safely parse JSON values.
   * @param {string} value
   * @param {any} fallback
   * @returns {any}
   */
  function parseJSON(value, fallback) {
    try {
      return JSON.parse(value);
    } catch (error) {
      return fallback;
    }
  }

  /**
   * Escape basic HTML characters.
   * @param {string} value
   * @returns {string}
   */
  function escapeHTML(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /**
   * Determine short input summary for a step.
   * @param {object} inputJson
   * @returns {string}
   */
  function formatInputPreview(inputJson) {
    if (!inputJson || typeof inputJson !== 'object') {
      return 'No input configured yet';
    }

    var entries = Object.entries(inputJson).filter(function (entry) {
      var value = entry[1];
      if (Array.isArray(value)) {
        return value.length > 0;
      }
      return value !== null && value !== undefined && String(value).trim() !== '';
    });

    if (!entries.length) {
      return 'No input configured yet';
    }

    return entries.slice(0, 2).map(function (entry) {
      var key = entry[0];
      var value = entry[1];
      var serialized;
      if (Array.isArray(value)) {
        serialized = value.join(', ');
      } else if (typeof value === 'object') {
        serialized = JSON.stringify(value);
      } else {
        serialized = String(value);
      }
      if (serialized.length > 36) {
        serialized = serialized.slice(0, 33) + '...';
      }
      return key + ': ' + serialized;
    }).join(' | ');
  }

  /**
   * Create an input element for dynamic step configuration.
   * @param {object} field
   * @param {any} value
   * @param {number} stepIndex
   * @returns {HTMLElement}
   */
  function createFieldElement(field, value, stepIndex) {
    var wrapper = document.createElement('div');
    wrapper.className = 'mb-3';

    var label = document.createElement('label');
    label.className = 'form-label fw-semibold small';
    label.textContent = (field.label || field.name || 'Field') + (field.required ? ' *' : '');
    wrapper.appendChild(label);

    var fieldType = String(field.type || 'text').toLowerCase();
    var fieldName = String(field.name || '');
    var normalizedValue = value;

    var input;

    if (fieldType === 'textarea') {
      input = document.createElement('textarea');
      input.className = 'form-control form-control-sm';
      input.rows = 3;
      input.value = normalizedValue ? String(normalizedValue) : '';
      input.placeholder = field.placeholder || '';
      if (field.max_length) {
        input.maxLength = Number(field.max_length);
      }
      input.addEventListener('input', function () {
        WorkflowBuilder.updateStep(stepIndex, fieldName, input.value);
      });
      wrapper.appendChild(input);
    } else if (fieldType === 'select') {
      input = document.createElement('select');
      input.className = 'form-select form-select-sm';
      var options = Array.isArray(field.options) ? field.options : [];
      options.forEach(function (option) {
        var opt = document.createElement('option');
        opt.value = String(option.value);
        opt.textContent = String(option.label || option.value);
        if (String(normalizedValue || '') === String(option.value)) {
          opt.selected = true;
        }
        input.appendChild(opt);
      });
      input.addEventListener('change', function () {
        WorkflowBuilder.updateStep(stepIndex, fieldName, input.value);
      });
      wrapper.appendChild(input);
    } else if (fieldType === 'checkboxes') {
      var selectedValues = [];
      if (Array.isArray(normalizedValue)) {
        selectedValues = normalizedValue.map(function (item) { return String(item); });
      }

      var checkboxWrap = document.createElement('div');
      checkboxWrap.className = 'd-flex flex-wrap gap-2';
      (field.options || []).forEach(function (option, optionIndex) {
        var optionValue = String(option.value);
        var id = 'step_' + stepIndex + '_field_' + fieldName + '_' + optionIndex;

        var checkContainer = document.createElement('div');
        checkContainer.className = 'form-check';

        var checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'form-check-input';
        checkbox.id = id;
        checkbox.value = optionValue;
        checkbox.checked = selectedValues.indexOf(optionValue) !== -1;

        checkbox.addEventListener('change', function () {
          var checkedValues = Array.from(checkboxWrap.querySelectorAll('input[type="checkbox"]:checked')).map(function (node) {
            return node.value;
          });
          WorkflowBuilder.updateStep(stepIndex, fieldName, checkedValues);
        });

        var checkboxLabel = document.createElement('label');
        checkboxLabel.className = 'form-check-label small';
        checkboxLabel.setAttribute('for', id);
        checkboxLabel.textContent = String(option.label || optionValue);

        checkContainer.appendChild(checkbox);
        checkContainer.appendChild(checkboxLabel);
        checkboxWrap.appendChild(checkContainer);
      });

      wrapper.appendChild(checkboxWrap);
    } else {
      input = document.createElement('input');
      input.type = (fieldType === 'number' || fieldType === 'url' || fieldType === 'email') ? fieldType : 'text';
      input.className = 'form-control form-control-sm';
      input.value = normalizedValue !== undefined && normalizedValue !== null ? String(normalizedValue) : '';
      input.placeholder = field.placeholder || '';
      if (field.max_length) {
        input.maxLength = Number(field.max_length);
      }
      input.addEventListener('input', function () {
        var nextValue = input.type === 'number' ? Number(input.value || 0) : input.value;
        WorkflowBuilder.updateStep(stepIndex, fieldName, nextValue);
      });
      wrapper.appendChild(input);
    }

    if (field.help_text) {
      var help = document.createElement('div');
      help.className = 'form-text';
      help.textContent = String(field.help_text);
      wrapper.appendChild(help);
    }

    return wrapper;
  }

  var WorkflowBuilder = {
    workflowId: null,
    steps: [],
    hasUnsavedChanges: false,
    isSaving: false,
    taskRegistry: {},
    insertAfterIndex: -1,
    activeStepIndex: null,

    /**
     * Initialize workflow builder page.
     */
    init: function () {
      var dataElement = document.getElementById('workflow-data');
      if (!dataElement) {
        return;
      }

      this.workflowId = dataElement.dataset.workflowId || null;
      this.steps = parseJSON(dataElement.dataset.initialSteps || '[]', []);
      this.taskRegistry = parseJSON(dataElement.dataset.taskRegistry || '{}', {});
      this.steps = Array.isArray(this.steps) ? this.steps : [];

      this.renderSteps();
      this.bindToolbarEvents();
      this.bindBuilderEvents();
      this.bindStepTypeSelectorEvents();
      this.updateEstimatedTime();
      this.markUnsaved();

      var self = this;
      window.addEventListener('beforeunload', function (event) {
        if (!self.hasUnsavedChanges) {
          return;
        }
        event.preventDefault();
        event.returnValue = '';
      });

      var nameInput = document.getElementById('workflow-name');
      if (nameInput) {
        nameInput.addEventListener('blur', function () {
          if (self.hasUnsavedChanges) {
            self.saveWorkflow();
          }
        });
      }
    },

    /**
     * Add a new workflow step.
     * @param {string} taskType
     * @param {number} insertAfterIndex
     */
    addStep: function (taskType, insertAfterIndex) {
      var indexToInsert = typeof insertAfterIndex === 'number' ? insertAfterIndex : -1;
      var config = this.taskRegistry[taskType];
      if (!config) {
        if (window.showToast) {
          window.showToast('Unknown task type selected', 'warning');
        }
        return;
      }

      var step = {
        task_type: taskType,
        task_name: config.name,
        category: config.category,
        input_json: {},
        description: config.description
      };

      var newIndex;
      if (indexToInsert === -1 || indexToInsert >= this.steps.length - 1) {
        this.steps.push(step);
        newIndex = this.steps.length - 1;
      } else {
        this.steps.splice(indexToInsert + 1, 0, step);
        newIndex = indexToInsert + 1;
      }

      this.activeStepIndex = newIndex;
      this.renderSteps();
      this.markUnsaved();
      this.openStepConfig(newIndex);

      var newNode = document.getElementById('step-' + newIndex);
      if (newNode) {
        newNode.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    },

    /**
     * Remove a step by index.
     * @param {number} stepIndex
     */
    removeStep: function (stepIndex) {
      if (stepIndex < 0 || stepIndex >= this.steps.length) {
        return;
      }
      this.steps.splice(stepIndex, 1);
      this.activeStepIndex = null;
      this.renderSteps();
      this.markUnsaved();
    },

    /**
     * Update one field in a step input payload.
     * @param {number} stepIndex
     * @param {string} fieldName
     * @param {any} value
     */
    updateStep: function (stepIndex, fieldName, value) {
      if (!this.steps[stepIndex]) {
        return;
      }
      if (!this.steps[stepIndex].input_json || typeof this.steps[stepIndex].input_json !== 'object') {
        this.steps[stepIndex].input_json = {};
      }
      this.steps[stepIndex].input_json[fieldName] = value;
      this.markUnsaved();
      this.updateEstimatedTime();

      var preview = document.querySelector('#step-' + stepIndex + ' .step-input-preview');
      if (preview) {
        preview.textContent = formatInputPreview(this.steps[stepIndex].input_json);
      }
    },

    /**
     * Reorder steps array.
     * @param {number} fromIndex
     * @param {number} toIndex
     */
    reorderSteps: function (fromIndex, toIndex) {
      if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) {
        return;
      }
      var moved = this.steps.splice(fromIndex, 1)[0];
      this.steps.splice(toIndex, 0, moved);
      this.activeStepIndex = toIndex;
      this.renderSteps();
      this.markUnsaved();
    },

    /**
     * Render entire steps list.
     */
    renderSteps: function () {
      var list = document.getElementById('steps-list');
      var emptyState = document.getElementById('steps-empty-state');
      if (!list || !emptyState) {
        return;
      }

      list.innerHTML = '';

      if (!this.steps.length) {
        emptyState.classList.remove('d-none');
        list.classList.add('d-none');
        this.updateEstimatedTime();
        return;
      }

      emptyState.classList.add('d-none');
      list.classList.remove('d-none');

      var self = this;
      this.steps.forEach(function (step, index) {
        list.appendChild(self.renderSingleStep(step, index));
      });

      this.initDragAndDrop();
      this.initTouchDragAndDrop();
      this.updateEstimatedTime();

      if (this.activeStepIndex !== null && this.steps[this.activeStepIndex]) {
        this.openStepConfig(this.activeStepIndex);
      }
    },

    /**
     * Render one step card.
     * @param {object} step
     * @param {number} index
     * @returns {HTMLElement}
     */
    renderSingleStep: function (step, index) {
      var taskConfig = this.taskRegistry[step.task_type] || {};
      var wrapper = document.createElement('div');
      wrapper.className = 'step-card-wrap';

      var card = document.createElement('div');
      card.className = 'step-card';
      card.id = 'step-' + index;
      card.draggable = true;
      card.dataset.stepIndex = String(index);

      var taskIcon = taskConfig.icon || 'bi-stars';
      var taskName = step.task_name || taskConfig.name || step.task_type || 'Task';
      var taskCategory = taskConfig.category_display || step.category || taskConfig.category || 'General';
      var description = step.description || taskConfig.description || 'No description available.';
      var preview = formatInputPreview(step.input_json || {});

      card.innerHTML = '' +
        '<div class="p-3 d-flex gap-2 align-items-start">' +
          '<div class="drag-handle"><i class="bi bi-grip-vertical"></i></div>' +
          '<div class="step-number">' + (index + 1) + '</div>' +
          '<div class="flex-grow-1">' +
            '<div class="d-flex justify-content-between align-items-start gap-2">' +
              '<div>' +
                '<div class="fw-semibold"><i class="bi ' + escapeHTML(taskIcon) + ' me-1"></i>' + escapeHTML(taskName) + '</div>' +
                '<div class="small text-muted">' + escapeHTML(description) + '</div>' +
                '<span class="badge text-bg-light border mt-1">' + escapeHTML(taskCategory) + '</span>' +
              '</div>' +
              '<div class="d-flex gap-1">' +
                '<button type="button" class="btn btn-sm btn-outline-secondary step-edit-btn" data-step-index="' + index + '"><i class="bi bi-pencil"></i></button>' +
                '<button type="button" class="btn btn-sm btn-outline-danger step-delete-btn" data-step-index="' + index + '"><i class="bi bi-x-lg"></i></button>' +
              '</div>' +
            '</div>' +
            '<div class="step-input-preview mt-2">' + escapeHTML(preview) + '</div>' +
          '</div>' +
        '</div>';

      var configPanel = document.createElement('div');
      configPanel.className = 'step-config';
      configPanel.id = 'step-config-' + index;

      var fieldList = Array.isArray(taskConfig.input_fields) ? taskConfig.input_fields : [];
      if (!fieldList.length) {
        var emptyLabel = document.createElement('div');
        emptyLabel.className = 'small text-muted';
        emptyLabel.textContent = 'No configurable fields for this task type.';
        configPanel.appendChild(emptyLabel);
      } else {
        var self = this;
        fieldList.forEach(function (field) {
          if (!field || !field.name) {
            return;
          }
          var value = step.input_json && Object.prototype.hasOwnProperty.call(step.input_json, field.name)
            ? step.input_json[field.name]
            : '';
          configPanel.appendChild(createFieldElement(field, value, index));
        });
      }

      card.appendChild(configPanel);

      var insertRow = document.createElement('div');
      insertRow.className = 'insert-step-row';
      insertRow.innerHTML = '<button type="button" class="btn btn-link btn-sm insert-step-btn" data-insert-index="' + index + '"><i class="bi bi-plus-circle me-1"></i>Add Step Here</button>';

      wrapper.appendChild(card);
      wrapper.appendChild(insertRow);

      return wrapper;
    },

    /**
     * Open step config accordion for selected index.
     * @param {number} stepIndex
     */
    openStepConfig: function (stepIndex) {
      this.activeStepIndex = stepIndex;
      document.querySelectorAll('.step-config').forEach(function (panel) {
        panel.classList.remove('open');
      });
      var selected = document.getElementById('step-config-' + stepIndex);
      if (selected) {
        selected.classList.add('open');
      }
    },

    /**
     * Close all open step configurations.
     */
    closeStepConfig: function () {
      this.activeStepIndex = null;
      document.querySelectorAll('.step-config').forEach(function (panel) {
        panel.classList.remove('open');
      });
    },

    /**
     * Save workflow to backend.
     * @param {object=} options
     * @returns {Promise<any>}
     */
    saveWorkflow: async function (options) {
      var opts = options || {};
      var triggerButton = opts.triggerButton || null;
      if (this.isSaving) {
        return null;
      }
      this.isSaving = true;

      if (triggerButton && window.setButtonLoading) {
        window.setButtonLoading(triggerButton, opts.runAfterSave ? 'Saving & launching...' : 'Saving workflow...');
      }

      var nameInput = document.getElementById('workflow-name');
      var name = nameInput ? String(nameInput.value || '').trim() : '';
      if (!name) {
        if (window.showToast) {
          window.showToast('Please enter a workflow name', 'warning');
        }
        this.isSaving = false;
        return null;
      }

      var payload = {
        name: name,
        description: document.getElementById('wf-description') ? document.getElementById('wf-description').value : '',
        trigger_type: document.getElementById('wf-trigger-type') ? document.getElementById('wf-trigger-type').value : 'manual',
        project_id: document.getElementById('wf-project-id') ? document.getElementById('wf-project-id').value : '',
        is_public: document.getElementById('wf-is-public') ? document.getElementById('wf-is-public').checked : false,
        tags: document.getElementById('wf-tags') ? document.getElementById('wf-tags').value : '',
        steps_json: this.steps
      };

      if (opts.saveAsTemplate) {
        payload.save_as_template = true;
        payload.template_name = opts.templateName || name;
        payload.template_category = opts.templateCategory || '';
        payload.template_difficulty = opts.templateDifficulty || '';
      }

      var url = this.workflowId ? '/workflows/' + encodeURIComponent(this.workflowId) : '/workflows';
      var method = this.workflowId ? 'PUT' : 'POST';

      try {
        var response = await fetch(url, {
          method: method,
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': window.getCSRFToken ? window.getCSRFToken() : ''
          },
          credentials: 'same-origin',
          body: JSON.stringify(payload)
        });

        var data = await response.json();
        if (!data || !data.success) {
          if (window.showToast) {
            window.showToast((data && data.message) || 'Save failed', 'danger');
          }
          return data;
        }

        if (!this.workflowId && data.data && data.data.workflow_id) {
          this.workflowId = data.data.workflow_id;
          history.replaceState(null, '', '/workflows/' + this.workflowId + '?mode=edit');
        }

        this.markSaved();
        if (window.showToast) {
          window.showToast(opts.saveAsTemplate ? 'Workflow and template saved' : 'Workflow saved', 'success');
        }

        if (opts.runAfterSave) {
          await this.runWorkflow();
        }

        return data;
      } catch (error) {
        if (window.showToast) {
          window.showToast('Error saving workflow', 'danger');
        }
        return null;
      } finally {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
        this.isSaving = false;
      }
    },

    /**
     * Trigger workflow run endpoint.
     * @returns {Promise<void>}
     */
    runWorkflow: async function (triggerButton) {
      if (!this.workflowId) {
        var saveResult = await this.saveWorkflow({ triggerButton: triggerButton });
        if (!saveResult || !this.workflowId) {
          return;
        }
      }

      if (triggerButton && window.setButtonLoading) {
        window.setButtonLoading(triggerButton, 'Running...');
      }

      try {
        var response = await fetch('/workflows/' + encodeURIComponent(this.workflowId) + '/run', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': window.getCSRFToken ? window.getCSRFToken() : ''
          },
          credentials: 'same-origin',
          body: JSON.stringify({ priority: 'default' })
        });
        var data = await response.json();
        if (data && data.success && data.data && data.data.monitor_url) {
          window.location.href = data.data.monitor_url;
          return;
        }
        if (window.showToast) {
          window.showToast((data && data.message) || 'Could not run workflow', 'danger');
        }
      } catch (error) {
        if (window.showToast) {
          window.showToast('Could not run workflow', 'danger');
        }
      } finally {
        if (triggerButton && window.resetButton) {
          window.resetButton(triggerButton);
        }
      }
    },

    /**
     * Run first step quickly as test using run endpoint.
     */
    testFirstStep: function (triggerButton) {
      this.runWorkflow(triggerButton);
    },

    /**
     * Mark builder as dirty and update save status indicator.
     */
    markUnsaved: function () {
      this.hasUnsavedChanges = true;
      var status = document.getElementById('save-status');
      var saveButton = document.getElementById('save-workflow-btn');
      if (status) {
        status.textContent = 'Unsaved changes';
        status.classList.remove('text-success');
        status.classList.add('text-warning');
      }
      if (saveButton) {
        saveButton.classList.add('save-btn-pulse');
      }
    },

    /**
     * Mark builder as clean and update save status indicator.
     */
    markSaved: function () {
      this.hasUnsavedChanges = false;
      var status = document.getElementById('save-status');
      var saveButton = document.getElementById('save-workflow-btn');
      if (status) {
        status.textContent = 'Saved OK';
        status.classList.remove('text-warning');
        status.classList.add('text-success');

        window.setTimeout(function () {
          status.textContent = 'All changes saved';
          status.classList.remove('text-success');
          status.classList.add('text-muted');
        }, 3000);
      }
      if (saveButton) {
        saveButton.classList.remove('save-btn-pulse');
      }
    },

    /**
     * Update estimated runtime card.
     */
    updateEstimatedTime: function () {
      var totalSeconds = 0;
      var breakdownRows = [];
      var self = this;

      this.steps.forEach(function (step, index) {
        var config = self.taskRegistry[step.task_type] || {};
        var estimate = Number(config.estimated_seconds || 60);
        totalSeconds += estimate;
        breakdownRows.push((index + 1) + '. ' + (step.task_name || config.name || step.task_type) + ' ~' + estimate + 's');
      });

      var target = document.getElementById('estimated-time-display');
      if (target) {
        target.textContent = '~' + Math.floor(totalSeconds / 60) + ' min ' + (totalSeconds % 60) + ' sec';
      }

      var breakdown = document.getElementById('estimated-time-breakdown');
      if (breakdown) {
        breakdown.innerHTML = breakdownRows.map(function (row) {
          return '<div>' + escapeHTML(row) + '</div>';
        }).join('');
      }
    },

    /**
     * Initialize desktop HTML5 drag and drop handlers.
     */
    initDragAndDrop: function () {
      var list = document.getElementById('steps-list');
      if (!list) {
        return;
      }

      var draggedIndex = null;
      var self = this;

      list.querySelectorAll('.step-card').forEach(function (card, index) {
        card.addEventListener('dragstart', function (event) {
          draggedIndex = index;
          card.classList.add('dragging');
          event.dataTransfer.effectAllowed = 'move';
        });

        card.addEventListener('dragend', function () {
          card.classList.remove('dragging');
          draggedIndex = null;
        });

        card.addEventListener('dragover', function (event) {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
          card.classList.add('drag-over');
        });

        card.addEventListener('dragleave', function () {
          card.classList.remove('drag-over');
        });

        card.addEventListener('drop', function (event) {
          event.preventDefault();
          card.classList.remove('drag-over');
          if (draggedIndex !== null && draggedIndex !== index) {
            self.reorderSteps(draggedIndex, index);
          }
        });
      });
    },

    /**
     * Initialize touch drag and drop support for mobile devices.
     */
    initTouchDragAndDrop: function () {
      var list = document.getElementById('steps-list');
      if (!list) {
        return;
      }

      var self = this;
      var draggedIndex = null;
      var currentHoverCard = null;

      list.querySelectorAll('.step-card').forEach(function (card) {
        card.addEventListener('touchstart', function () {
          draggedIndex = Number(card.dataset.stepIndex || '-1');
          card.classList.add('touch-dragging');
        }, { passive: true });

        card.addEventListener('touchmove', function (event) {
          if (draggedIndex === null || draggedIndex < 0) {
            return;
          }

          var touch = event.touches[0];
          if (!touch) {
            return;
          }

          var target = document.elementFromPoint(touch.clientX, touch.clientY);
          var hoverCard = target ? target.closest('.step-card') : null;

          if (currentHoverCard && currentHoverCard !== hoverCard) {
            currentHoverCard.classList.remove('drag-over');
          }

          if (hoverCard) {
            hoverCard.classList.add('drag-over');
          }

          currentHoverCard = hoverCard;
          event.preventDefault();
        }, { passive: false });

        card.addEventListener('touchend', function (event) {
          card.classList.remove('touch-dragging');

          if (currentHoverCard) {
            var toIndex = Number(currentHoverCard.dataset.stepIndex || '-1');
            currentHoverCard.classList.remove('drag-over');
            if (draggedIndex >= 0 && toIndex >= 0 && draggedIndex !== toIndex) {
              self.reorderSteps(draggedIndex, toIndex);
            }
          }

          currentHoverCard = null;
          draggedIndex = null;
          event.preventDefault();
        }, { passive: false });
      });
    },

    /**
     * Attach toolbar action handlers.
     */
    bindToolbarEvents: function () {
      var self = this;

      var saveButton = document.getElementById('save-workflow-btn');
      if (saveButton) {
        saveButton.addEventListener('click', function () {
          self.saveWorkflow({ triggerButton: saveButton });
        });
      }

      var saveRunButton = document.getElementById('save-run-workflow-btn');
      if (saveRunButton) {
        saveRunButton.addEventListener('click', function () {
          self.saveWorkflow({ runAfterSave: true, triggerButton: saveRunButton });
        });
      }

      var testRunButton = document.getElementById('test-run-btn');
      if (testRunButton) {
        testRunButton.addEventListener('click', function () {
          self.testFirstStep(testRunButton);
        });
      }

      var saveTemplateAction = document.getElementById('save-template-action');
      if (saveTemplateAction) {
        saveTemplateAction.addEventListener('click', function () {
          var templateName = window.prompt('Template name', (document.getElementById('workflow-name') || {}).value || 'My Template');
          if (!templateName) {
            return;
          }
          var templateCategory = window.prompt('Template category', 'custom');
          if (templateCategory === null) {
            return;
          }
          var templateDifficulty = window.prompt('Template difficulty (beginner, intermediate, advanced)', 'beginner');
          if (templateDifficulty === null) {
            return;
          }

          self.saveWorkflow({
            saveAsTemplate: true,
            templateName: templateName,
            templateCategory: templateCategory,
            templateDifficulty: templateDifficulty
          });
        });
      }

      var duplicateAction = document.getElementById('duplicate-workflow-action');
      if (duplicateAction) {
        duplicateAction.addEventListener('click', async function () {
          if (!self.workflowId) {
            if (window.showToast) {
              window.showToast('Save this workflow before duplicating', 'warning');
            }
            return;
          }

          try {
            if (window.setButtonLoading) {
              window.setButtonLoading(duplicateAction, 'Duplicating...');
            }
            var response = await fetch('/workflows/' + encodeURIComponent(self.workflowId) + '/duplicate', {
              method: 'POST',
              headers: {
                'X-CSRFToken': window.getCSRFToken ? window.getCSRFToken() : ''
              },
              credentials: 'same-origin'
            });
            var data = await response.json();
            if (data && data.success && data.data && data.data.redirect) {
              window.location.href = data.data.redirect;
              return;
            }
            if (window.showToast) {
              window.showToast((data && data.message) || 'Could not duplicate workflow', 'danger');
            }
          } catch (error) {
            if (window.showToast) {
              window.showToast('Could not duplicate workflow', 'danger');
            }
          } finally {
            if (window.resetButton) {
              window.resetButton(duplicateAction);
            }
          }
        });
      }

      var shareAction = document.getElementById('share-workflow-action');
      if (shareAction) {
        shareAction.addEventListener('click', function () {
          if (window.copyToClipboard) {
            window.copyToClipboard(window.location.href).then(function () {
              if (window.showToast) {
                window.showToast('Workflow link copied', 'success');
              }
            }).catch(function () {
              if (window.showToast) {
                window.showToast('Could not copy link', 'danger');
              }
            });
          }
        });
      }

      var deleteAction = document.getElementById('delete-workflow-action');
      if (deleteAction) {
        deleteAction.addEventListener('click', async function () {
          if (!self.workflowId) {
            if (window.showToast) {
              window.showToast('Workflow is not saved yet', 'warning');
            }
            return;
          }

          if (!window.confirm('Delete this workflow? This action cannot be undone.')) {
            return;
          }

          try {
            if (window.setButtonLoading) {
              window.setButtonLoading(deleteAction, 'Deleting...');
            }
            var response = await fetch('/workflows/' + encodeURIComponent(self.workflowId), {
              method: 'DELETE',
              headers: {
                'X-CSRFToken': window.getCSRFToken ? window.getCSRFToken() : ''
              },
              credentials: 'same-origin'
            });
            var data = await response.json();
            if (data && data.success) {
              window.location.href = '/workflows';
              return;
            }
            if (window.showToast) {
              window.showToast((data && data.message) || 'Could not delete workflow', 'danger');
            }
          } catch (error) {
            if (window.showToast) {
              window.showToast('Could not delete workflow', 'danger');
            }
          } finally {
            if (window.resetButton) {
              window.resetButton(deleteAction);
            }
          }
        });
      }
    },

    /**
     * Attach events for builder inputs and step actions.
     */
    bindBuilderEvents: function () {
      var self = this;

      ['wf-description', 'wf-trigger-type', 'wf-project-id', 'wf-is-public', 'wf-tags', 'workflow-name'].forEach(function (id) {
        var node = document.getElementById(id);
        if (!node) {
          return;
        }

        var eventName = (node.tagName === 'SELECT' || node.type === 'checkbox') ? 'change' : 'input';
        node.addEventListener(eventName, function () {
          self.markUnsaved();
        });
      });

      var addStepButton = document.getElementById('add-step-btn');
      if (addStepButton) {
        addStepButton.addEventListener('click', function () {
          self.openStepTypeSelector(-1);
        });
      }

      var emptyAddStepButton = document.getElementById('empty-add-step-btn');
      if (emptyAddStepButton) {
        emptyAddStepButton.addEventListener('click', function () {
          self.openStepTypeSelector(-1);
        });
      }

      document.addEventListener('click', function (event) {
        var editBtn = event.target.closest('.step-edit-btn');
        if (editBtn) {
          var editIndex = Number(editBtn.getAttribute('data-step-index') || '-1');
          if (editIndex >= 0) {
            self.openStepConfig(editIndex);
          }
          return;
        }

        var deleteBtn = event.target.closest('.step-delete-btn');
        if (deleteBtn) {
          var deleteIndex = Number(deleteBtn.getAttribute('data-step-index') || '-1');
          if (deleteIndex >= 0) {
            self.removeStep(deleteIndex);
          }
          return;
        }

        var insertBtn = event.target.closest('.insert-step-btn');
        if (insertBtn) {
          var insertIndex = Number(insertBtn.getAttribute('data-insert-index') || '-1');
          self.openStepTypeSelector(insertIndex);
          return;
        }

        var stepCard = event.target.closest('.step-card');
        if (stepCard && !event.target.closest('.step-config') && !event.target.closest('button')) {
          var stepIndex = Number(stepCard.dataset.stepIndex || '-1');
          if (stepIndex >= 0) {
            if (self.activeStepIndex === stepIndex) {
              self.closeStepConfig();
            } else {
              self.openStepConfig(stepIndex);
            }
          }
        }
      });
    },

    /**
     * Open step type selector panel.
     * @param {number} insertAfterIndex
     */
    openStepTypeSelector: function (insertAfterIndex) {
      this.insertAfterIndex = insertAfterIndex;
      var panel = document.getElementById('step-type-selector');
      if (panel) {
        panel.style.display = 'block';
      }

      var searchInput = document.getElementById('step-type-search');
      if (searchInput) {
        searchInput.focus();
      }
    },

    /**
     * Close step type selector panel.
     */
    closeStepTypeSelector: function () {
      var panel = document.getElementById('step-type-selector');
      if (panel) {
        panel.style.display = 'none';
      }
      this.insertAfterIndex = -1;
    },

    /**
     * Bind step selector panel events.
     */
    bindStepTypeSelectorEvents: function () {
      var self = this;
      var closeBtn = document.getElementById('close-step-selector-btn');
      if (closeBtn) {
        closeBtn.addEventListener('click', function () {
          self.closeStepTypeSelector();
        });
      }

      var searchInput = document.getElementById('step-type-search');
      if (searchInput) {
        searchInput.addEventListener('input', function () {
          var query = String(searchInput.value || '').trim().toLowerCase();
          var activeCategoryPill = document.querySelector('.category-pill.active');
          var activeCategory = activeCategoryPill ? String(activeCategoryPill.dataset.category || 'all') : 'all';

          document.querySelectorAll('.task-type-option-wrap').forEach(function (item) {
            var name = String(item.dataset.name || '').toLowerCase();
            var category = String(item.dataset.category || '').toLowerCase();
            var matchesQuery = name.indexOf(query) !== -1;
            var matchesCategory = activeCategory === 'all' || category === activeCategory;
            item.style.display = (matchesQuery && matchesCategory) ? '' : 'none';
          });
        });
      }

      document.querySelectorAll('.category-pill').forEach(function (pill) {
        pill.addEventListener('click', function () {
          document.querySelectorAll('.category-pill').forEach(function (item) {
            item.classList.remove('active');
          });
          pill.classList.add('active');
          if (searchInput) {
            searchInput.dispatchEvent(new Event('input'));
          }
        });
      });

      document.querySelectorAll('.task-type-option').forEach(function (option) {
        option.addEventListener('click', function () {
          var taskType = option.getAttribute('data-task-type');
          if (!taskType) {
            return;
          }
          self.addStep(taskType, self.insertAfterIndex);
          self.closeStepTypeSelector();
        });
      });
    }
  };

  document.addEventListener('DOMContentLoaded', function () {
    WorkflowBuilder.init();
  });

  window.WorkflowBuilder = WorkflowBuilder;
})();
