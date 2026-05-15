(function () {
  'use strict';

  let activeCategory = 'all';
  let activeSearchQuery = '';
  let searchDebounceTimer = null;
  let focusedCardIndex = -1;

  function getCards() {
    return Array.from(document.querySelectorAll('.task-card'));
  }

  function getVisibleCards() {
    return getCards().filter(function (card) {
      const wrapper = card.closest('.col');
      return wrapper && wrapper.style.display !== 'none';
    });
  }

  function cardMatchesCategory(card, category) {
    return category === 'all' || card.dataset.category === category;
  }

  function cardMatchesSearch(card, query) {
    const q = query.toLowerCase().trim();
    const name = card.dataset.name || '';
    const category = card.dataset.category || '';
    const id = card.dataset.id || '';
    return q === '' || name.includes(q) || category.includes(q) || id.includes(q);
  }

  function applyCombinedFilters() {
    getCards().forEach(function (card) {
      const wrapper = card.closest('.col');
      if (!wrapper) {
        return;
      }
      const visible = cardMatchesCategory(card, activeCategory) && cardMatchesSearch(card, activeSearchQuery);
      wrapper.style.display = visible ? '' : 'none';
    });
    updateResultCount();
  }

  function updateActivePill(category) {
    document.querySelectorAll('[data-category-pill]').forEach(function (pill) {
      pill.classList.remove('active');
      pill.style.background = '#f1f5f9';
      pill.style.color = 'var(--text-primary)';
      pill.style.borderColor = 'var(--border-color)';
    });

    const activePill = document.querySelector('[data-category-pill="' + category + '"]');
    if (!activePill) {
      return;
    }

    activePill.classList.add('active');
    activePill.style.background = activePill.dataset.pillColor || '#1a56db';
    activePill.style.color = '#fff';
    activePill.style.borderColor = 'transparent';
  }

  function filterByCategory(category) {
    activeCategory = category;
    applyCombinedFilters();
    updateActivePill(category);
    window.location.hash = '#category=' + encodeURIComponent(category);
  }

  function filterBySearch(query) {
    activeSearchQuery = query.toLowerCase().trim();
    applyCombinedFilters();
  }

  function updateResultCount() {
    const visibleCount = getVisibleCards().length;
    const resultCounter = document.getElementById('search-result-count');
    const gridWrapper = document.getElementById('task-grid-wrapper');
    const emptyState = document.getElementById('task-empty-state');
    const taskGrid = document.getElementById('task-grid');
    const total = taskGrid ? Number(taskGrid.dataset.total || getCards().length) : getCards().length;

    if (resultCounter) {
      resultCounter.textContent = 'Showing ' + visibleCount + ' of ' + total + ' tasks';
    }

    if (visibleCount === 0) {
      if (emptyState) {
        emptyState.classList.remove('d-none');
      }
      if (gridWrapper) {
        gridWrapper.classList.add('d-none');
      }
    } else {
      if (emptyState) {
        emptyState.classList.add('d-none');
      }
      if (gridWrapper) {
        gridWrapper.classList.remove('d-none');
      }
    }

    focusedCardIndex = -1;
  }

  function applyHashCategory() {
    const hash = window.location.hash || '';
    if (!hash.startsWith('#category=')) {
      return;
    }
    const category = decodeURIComponent(hash.replace('#category=', '')).trim();
    if (!category) {
      return;
    }
    if (!document.querySelector('[data-category-pill="' + category + '"]')) {
      return;
    }
    filterByCategory(category);
  }

  function focusCardByIndex(index) {
    const visibleCards = getVisibleCards();
    if (!visibleCards.length) {
      focusedCardIndex = -1;
      return;
    }

    if (index < 0) {
      index = visibleCards.length - 1;
    }
    if (index >= visibleCards.length) {
      index = 0;
    }

    focusedCardIndex = index;
    visibleCards[index].focus();
  }

  function setupKeyboardNavigation() {
    const searchInput = document.getElementById('task-search-input');
    if (!searchInput) {
      return;
    }

    searchInput.addEventListener('keydown', function (event) {
      if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Enter'].includes(event.key)) {
        return;
      }

      const visibleCards = getVisibleCards();
      if (!visibleCards.length) {
        return;
      }

      if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
        event.preventDefault();
        focusCardByIndex(focusedCardIndex + 1);
      }

      if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
        event.preventDefault();
        focusCardByIndex(focusedCardIndex - 1);
      }

      if (event.key === 'Enter') {
        event.preventDefault();
        const targetCard = focusedCardIndex >= 0 ? visibleCards[focusedCardIndex] : visibleCards[0];
        const runButton = targetCard.querySelector('a.btn');
        if (runButton) {
          runButton.click();
        }
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-category-pill]').forEach(function (pill) {
      pill.addEventListener('click', function () {
        filterByCategory(pill.dataset.categoryPill || 'all');
      });
    });

    const searchInput = document.getElementById('task-search-input');
    const clearButton = document.getElementById('task-search-clear');

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        const query = searchInput.value || '';

        if (clearButton) {
          clearButton.classList.toggle('d-none', query.trim().length === 0);
        }

        if (searchDebounceTimer) {
          clearTimeout(searchDebounceTimer);
        }

        searchDebounceTimer = setTimeout(function () {
          filterBySearch(query);
        }, 300);
      });
    }

    if (clearButton && searchInput) {
      clearButton.addEventListener('click', function () {
        searchInput.value = '';
        clearButton.classList.add('d-none');
        filterBySearch('');
        searchInput.focus();
      });
    }

    applyHashCategory();
    if (activeCategory === 'all') {
      updateActivePill('all');
      applyCombinedFilters();
    }

    if (window.bootstrap) {
      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (node) {
        window.bootstrap.Tooltip.getOrCreateInstance(node);
      });
    }

    setupKeyboardNavigation();
    updateResultCount();
  });

  window.filterByCategory = filterByCategory;
  window.filterBySearch = filterBySearch;
  window.updateResultCount = updateResultCount;
})();
