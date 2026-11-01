/**
 * WUI-006: клавиатурный поиск.
 *
 * 1. Палитра (`#palette`, _frag/palette_shell.html): Cmd/Ctrl+K с любой
 *    страницы, даже из поля ввода. Запрос — GET /palette?project=&q= с
 *    задержкой; ответ — HTML-фрагмент со ссылками `.cmdk-item`.
 *    ↑/↓ двигают выделение, Enter открывает, Cmd/Ctrl+Enter — в новой
 *    вкладке. Esc закрывает (нативно, <dialog>).
 * 2. Быстрый фильтр списка (`input[data-list-filter]`, макрос sort.filter):
 *    прячет `[data-filter-row]` внутри цели, чей текст не содержит всех слов
 *    запроса, и `[data-filter-group]` без видимых строк. Без запроса к
 *    серверу: это сужение уже отрисованной страницы.
 * 3. `/` вне поля ввода: фокус в быстрый фильтр, если он на странице есть,
 *    иначе — палитра.
 *
 * Пока открыта шторка peek, хоткеи молчат: у неё своя клавиатура и своя
 * защита несохранённой правки.
 */
(function () {
  'use strict';

  const DEBOUNCE_MS = 150;
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

  function isEditable(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  }

  function peekOpen() {
    const peek = document.getElementById('peek');
    return !!(peek && peek.open);
  }

  // ── Быстрый фильтр ───────────────────────────────────────────────────
  function applyFilter(input) {
    const target = document.querySelector(input.dataset.listFilter);
    if (!target) return;
    const words = input.value.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    target.querySelectorAll('[data-filter-row]').forEach((row) => {
      const text = (row.textContent + ' ' + (row.getAttribute('title') || '')).toLocaleLowerCase();
      const hit = words.every((w) => text.includes(w));
      row.style.display = hit ? '' : 'none';
      if (hit) shown += 1;
    });
    target.querySelectorAll('[data-filter-group]').forEach((group) => {
      const rows = group.querySelectorAll('[data-filter-row]');
      const anyVisible = Array.prototype.some.call(rows, (r) => r.style.display !== 'none');
      // Группа без строк вовсе (пустая колонка доски) прячется только под запросом.
      group.style.display = words.length && !anyVisible ? 'none' : '';
    });
    input.classList.toggle('list-filter-empty', words.length > 0 && shown === 0);
  }

  document.addEventListener('input', (ev) => {
    const el = ev.target;
    if (el && el.matches && el.matches('input[data-list-filter]')) applyFilter(el);
  });
  // htmx может перерисовать цель (таблица документов дозапрашивает себя):
  // фильтр с текстом применяется заново к новой разметке.
  document.addEventListener('htmx:afterSettle', () => {
    document.querySelectorAll('input[data-list-filter]').forEach((input) => {
      if (input.value) applyFilter(input);
    });
  });

  // ── Палитра ──────────────────────────────────────────────────────────
  const dialog = document.getElementById('cmdk');
  const input = dialog && dialog.querySelector('.cmdk-input');
  const results = dialog && dialog.querySelector('#cmdk-results');
  const opener = document.getElementById('cmdk-open');
  const project = document.body.dataset.project || '';
  let timer = null;
  let seq = 0;
  let active = -1;

  if (opener) {
    const kbd = opener.querySelector('.cmdk-kbd');
    if (kbd && !isMac) kbd.textContent = 'Ctrl K';
  }

  function items() {
    return results ? Array.from(results.querySelectorAll('.cmdk-item')) : [];
  }

  function select(index) {
    const all = items();
    all.forEach((a) => a.removeAttribute('aria-selected'));
    if (!all.length) { active = -1; return; }
    active = (index + all.length) % all.length;
    const cur = all[active];
    cur.setAttribute('aria-selected', 'true');
    cur.scrollIntoView({ block: 'nearest' });
  }

  async function load(q) {
    const mine = ++seq;
    const url = '/palette?' + new URLSearchParams({ q: q, project: project });
    let html;
    try {
      const resp = await fetch(url, { headers: { 'HX-Request': 'true' } });
      html = resp.ok ? await resp.text() : '<p class="cmdk-note">Search failed: HTTP ' + resp.status + '</p>';
    } catch (e) {
      html = '<p class="cmdk-note">Search is unavailable — the server did not answer.</p>';
    }
    // Ответ на устаревший запрос (пользователь печатал дальше) не рисуем.
    if (mine !== seq) return;
    results.innerHTML = html;
    select(0);
  }

  function open() {
    if (!dialog || dialog.open || peekOpen()) return;
    dialog.showModal();
    input.select();
    load(input.value);
  }

  function go(link, newTab) {
    if (!link) return;
    if (newTab) {
      window.open(link.href, '_blank', 'noopener');
      return;
    }
    dialog.close();
    window.location.assign(link.href);
  }

  if (dialog && input && results) {
    if (opener) opener.addEventListener('click', open);

    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => load(input.value), DEBOUNCE_MS);
    });

    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        select(active + (ev.key === 'ArrowDown' ? 1 : -1));
      } else if (ev.key === 'Enter') {
        ev.preventDefault();
        go(items()[active], ev.metaKey || ev.ctrlKey);
      }
    });

    results.addEventListener('mousemove', (ev) => {
      const link = ev.target.closest && ev.target.closest('.cmdk-item');
      const idx = items().indexOf(link);
      if (idx >= 0 && idx !== active) select(idx);
    });

    // Клик по подложке (вне карточки) закрывает, как у шторки.
    dialog.addEventListener('click', (ev) => {
      if (ev.target === dialog) dialog.close();
    });
  }

  document.addEventListener('keydown', (ev) => {
    if (ev.defaultPrevented || peekOpen()) return;
    const key = (ev.key || '').toLowerCase();
    if (key === 'k' && (ev.metaKey || ev.ctrlKey) && !ev.altKey && !ev.shiftKey) {
      if (!dialog) return;
      ev.preventDefault();
      if (dialog.open) dialog.close();
      else open();
      return;
    }
    if (ev.key === '/' && !ev.metaKey && !ev.ctrlKey && !ev.altKey && !isEditable(ev.target)) {
      const filter = document.querySelector('input[data-list-filter]');
      ev.preventDefault();
      if (filter) {
        filter.focus();
        filter.select();
      } else {
        open();
      }
      return;
    }
    // Esc в быстром фильтре: очистить и вернуть фокус странице.
    if (ev.key === 'Escape' && ev.target && ev.target.matches && ev.target.matches('input[data-list-filter]')) {
      ev.target.value = '';
      applyFilter(ev.target);
      ev.target.blur();
    }
  });
})();
