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
 *    Свёрнутая группа (колонки Done/Cancelled доски) с совпадениями
 *    раскрывается на время запроса, счётчик `[data-filter-count]` показывает
 *    видимые строки; пустой запрос возвращает всё как было.
 * 3. `/` вне поля ввода: фокус в быстрый фильтр, если он на странице есть,
 *    иначе — палитра.
 *
 * Хоткеи сверяются по `ev.code` (физической клавише), а не только по
 * `ev.key`: в русской раскладке Ctrl+K даёт key «л», а клавиша `/` — «.».
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
      const visible = Array.prototype.filter.call(rows, (r) => r.style.display !== 'none').length;
      // Группа без строк вовсе (пустая колонка доски) прячется только под запросом.
      group.style.display = words.length && !visible ? 'none' : '';
      syncDetails(group, words.length > 0 && visible > 0);
      const counter = group.querySelector('[data-filter-count]');
      if (counter) {
        if (counter.dataset.filterTotal === undefined) counter.dataset.filterTotal = counter.textContent;
        counter.textContent = words.length ? String(visible) : counter.dataset.filterTotal;
      }
    });
    input.classList.toggle('list-filter-empty', words.length > 0 && shown === 0);
  }

  // Свёрнутую группу с совпадениями раскрываем, а после очистки запроса
  // закрываем обратно — только ту, что раскрыл фильтр, не человек.
  function syncDetails(group, hasHits) {
    if (group.tagName !== 'DETAILS') return;
    if (hasHits && !group.open) {
      group.open = true;
      group.dataset.filterOpened = '1';
    } else if (!hasHits && group.dataset.filterOpened) {
      group.open = false;
      delete group.dataset.filterOpened;
    }
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

  if (!isMac) {
    const kbd = opener && opener.querySelector('.cmdk-kbd');
    if (kbd) kbd.textContent = 'Ctrl K';
    document.querySelectorAll('.cmdk-mod').forEach((el) => { el.textContent = 'Ctrl'; });
  }

  function items() {
    return results ? Array.from(results.querySelectorAll('.cmdk-item')) : [];
  }

  // Фокус остаётся в поле ввода; выбранный пункт экранная читалка узнаёт
  // из aria-activedescendant (паттерн combobox + listbox).
  function select(index) {
    const all = items();
    all.forEach((a) => a.setAttribute('aria-selected', 'false'));
    if (!all.length) {
      active = -1;
      input.removeAttribute('aria-activedescendant');
      return;
    }
    active = (index + all.length) % all.length;
    const cur = all[active];
    cur.setAttribute('aria-selected', 'true');
    input.setAttribute('aria-activedescendant', cur.id);
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
    items().forEach((a, i) => { a.id = 'cmdk-opt-' + i; });
    select(0);
  }

  function open() {
    if (!dialog || dialog.open || peekOpen()) return;
    dialog.showModal();
    input.setAttribute('aria-expanded', 'true');
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
    // Закрытие отменяет и отложенный запрос (debounce), и уже летящий:
    // `seq++` делает его ответ устаревшим — скрытый диалог не перерисуется.
    dialog.addEventListener('close', () => {
      input.setAttribute('aria-expanded', 'false');
      clearTimeout(timer);
      seq += 1;
    });
  }

  function isPaletteKey(ev) {
    const k = ev.code === 'KeyK' || (ev.key || '').toLowerCase() === 'k';
    return k && (ev.metaKey || ev.ctrlKey) && !ev.altKey && !ev.shiftKey;
  }

  function isSlashKey(ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return false;
    // `/` своей клавишей (US, RU — там она печатает «.») или там, где
    // раскладка даёт `/` другой комбинацией (DE: Shift+7).
    return (ev.code === 'Slash' && !ev.shiftKey) || ev.key === '/';
  }

  document.addEventListener('keydown', (ev) => {
    if (ev.defaultPrevented || peekOpen()) return;
    if (isPaletteKey(ev)) {
      if (!dialog) return;
      ev.preventDefault();
      if (dialog.open) dialog.close();
      else open();
      return;
    }
    if (isSlashKey(ev) && !isEditable(ev.target)) {
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
