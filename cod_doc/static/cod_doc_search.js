/* Global search palette — Ctrl/Cmd+K on project pages. */

(function () {
  const i18n = window.COD_DOC_I18N || {};
  function tr(key, fallback) {
    return i18n[key] || fallback || key;
  }

  const backdrop = document.getElementById('search-palette-backdrop');
  const input = document.getElementById('search-palette-input');
  const results = document.getElementById('search-palette-results');
  const openBtn = document.getElementById('search-open');
  if (!backdrop || !input || !results) return;

  const suggestUrl = input.dataset.suggestUrl;
  let items = [];
  let selected = -1;
  let debounceTimer = null;

  function openPalette() {
    backdrop.hidden = false;
    input.value = '';
    items = [];
    selected = -1;
    renderResults();
    input.focus();
  }

  function closePalette() {
    backdrop.hidden = true;
    if (openBtn) openBtn.focus();
  }

  function renderResults() {
    results.innerHTML = '';
    if (!input.value.trim()) {
      const hint = document.createElement('div');
      hint.className = 'search-palette-empty';
      hint.textContent = tr('search.empty_prompt', 'Type to search…');
      results.appendChild(hint);
      return;
    }
    if (items.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'search-palette-empty';
      empty.textContent = tr('search.no_results', 'No results.');
      results.appendChild(empty);
      return;
    }
    items.forEach((item, idx) => {
      const a = document.createElement('a');
      a.className = 'search-palette-item';
      a.href = item.url || '#';
      a.setAttribute('role', 'option');
      if (idx === selected) a.setAttribute('aria-selected', 'true');
      a.innerHTML =
        '<div class="search-palette-item-kind">' + escapeHtml(item.kind) + '</div>' +
        '<div class="search-palette-item-title">' + escapeHtml(item.title) + '</div>';
      a.addEventListener('click', () => closePalette());
      results.appendChild(a);
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fetchSuggestions(q) {
    if (!suggestUrl || !q.trim()) {
      items = [];
      selected = -1;
      renderResults();
      return;
    }
    fetch(suggestUrl + '?q=' + encodeURIComponent(q) + '&limit=12')
      .then((r) => r.json())
      .then((data) => {
        items = data.items || [];
        selected = items.length ? 0 : -1;
        renderResults();
      })
      .catch(() => {
        items = [];
        renderResults();
      });
  }

  function onInput() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => fetchSuggestions(input.value), 180);
  }

  function moveSelection(delta) {
    if (!items.length) return;
    selected = Math.max(0, Math.min(items.length - 1, selected + delta));
    renderResults();
    const el = results.querySelector('[aria-selected="true"]');
    if (el) el.scrollIntoView({ block: 'nearest' });
  }

  function activateSelection() {
    if (selected >= 0 && items[selected] && items[selected].url) {
      window.location.href = items[selected].url;
      closePalette();
    }
  }

  if (openBtn) openBtn.addEventListener('click', openPalette);

  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) closePalette();
  });

  input.addEventListener('input', onInput);
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      ev.preventDefault();
      closePalette();
    } else if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      moveSelection(1);
    } else if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      moveSelection(-1);
    } else if (ev.key === 'Enter') {
      ev.preventDefault();
      activateSelection();
    }
  });

  document.addEventListener('keydown', (ev) => {
    const mod = ev.metaKey || ev.ctrlKey;
    if (mod && ev.key.toLowerCase() === 'k') {
      ev.preventDefault();
      if (backdrop.hidden) openPalette();
      else closePalette();
    }
  });
})();
