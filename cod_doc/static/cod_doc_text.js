/* COD-DOC web — регулятор текста (Settings → «Текст и шрифт»).
 *
 * Грузится синхронно в <head> base.html: применяет сохранённый стиль до
 * отрисовки, как bootstrap темы, иначе страница мигает стандартным кеглем.
 * Хранится в localStorage браузера, а не в ~/.cod-doc/config.yaml: это
 * настройка экрана, как тема и палитра, у CLI и MCP её нет.
 *
 * Стиль — набор CSS-переменных на <html>; дефолты (пресет «Стандарт») живут
 * в :root css/_base.css, здесь только то, что отличается от них.
 */
(function () {
  'use strict';

  const KEY = 'cod-doc-text';

  // Только локальные шрифты: веб-UI работает без сети (ADO-233). Чего нет
  // в системе, браузер молча заменяет следующим в стеке.
  const FONTS = {
    sans: {
      system: { label: 'Системный (SF / Segoe)', stack: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", system-ui, sans-serif' },
      inter: { label: 'Inter', stack: '"Inter", "Inter Variable", -apple-system, system-ui, sans-serif' },
      humanist: { label: 'Гуманистический (Avenir / Helvetica)', stack: '"Avenir Next", "Helvetica Neue", "Segoe UI", system-ui, sans-serif' },
      serif: { label: 'С засечками (Charter / Georgia)', stack: 'Charter, "Iowan Old Style", "PT Serif", Georgia, serif' },
    },
    prose: {
      ui: { label: 'Как интерфейс', stack: null },
      serif: { label: 'С засечками (Charter / Georgia)', stack: 'Charter, "Iowan Old Style", "PT Serif", Georgia, serif' },
      humanist: { label: 'Гуманистический (Avenir / Helvetica)', stack: '"Avenir Next", "Helvetica Neue", "Segoe UI", system-ui, sans-serif' },
    },
    mono: {
      system: { label: 'Системный (SF Mono / Menlo)', stack: 'ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace' },
      jetbrains: { label: 'JetBrains Mono', stack: '"JetBrains Mono", ui-monospace, Menlo, monospace' },
      menlo: { label: 'Menlo', stack: 'Menlo, ui-monospace, monospace' },
    },
  };

  // scale — проценты от исходного кегля (тело 14px); measure — ширина строки
  // прозы документов в ch (LIMITS.measure), 0 — без ограничения.
  const PRESETS = {
    standard: {
      label: 'Стандарт',
      note: 'Как было: 14px, плотные таблицы, текст на всю ширину.',
      values: { scale: 100, lineHeight: 1.5, proseLineHeight: 1.5, measure: 0, sans: 'system', prose: 'ui', mono: 'system' },
    },
    board: {
      label: 'Борд роя',
      note: 'Типографика борда ZAIrgRush, откуда взята мягкая палитра: 15px, межстрочный 1.6, проза 1.65 в колонке 84ch.',
      values: { scale: 107, lineHeight: 1.6, proseLineHeight: 1.65, measure: 84, sans: 'system', prose: 'ui', mono: 'system' },
    },
    reading: {
      label: 'Чтение',
      note: 'Для длинных документов: интерфейс как у борда, проза с засечками, 1.7 в колонке 72ch.',
      values: { scale: 107, lineHeight: 1.6, proseLineHeight: 1.7, measure: 72, sans: 'system', prose: 'serif', mono: 'system' },
    },
  };

  const LIMITS = {
    scale: [85, 130],
    lineHeight: [1.3, 1.9],
    proseLineHeight: [1.3, 2.0],
    measure: [50, 120],
  };

  const DEFAULT = PRESETS.standard.values;

  function clamp(v, [lo, hi], fallback) {
    const n = Number(v);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(hi, Math.max(lo, n));
  }

  // Object.hasOwn моложе остального кода страницы (Safari 15.4): скрипт
  // синхронный и стоит в <head>, ему нельзя падать на старом движке.
  const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

  // Чужое или испорченное значение в localStorage не должно ломать вёрстку:
  // каждое поле проверяется и при отказе берётся из «Стандарта».
  function normalize(raw) {
    const src = raw && typeof raw === 'object' ? raw : {};
    const pick = (group, key) => (hasOwn(FONTS[group], src[key]) ? src[key] : DEFAULT[key]);
    return {
      preset: hasOwn(PRESETS, src.preset) ? src.preset : 'custom',
      scale: clamp(src.scale, LIMITS.scale, DEFAULT.scale),
      lineHeight: clamp(src.lineHeight, LIMITS.lineHeight, DEFAULT.lineHeight),
      proseLineHeight: clamp(src.proseLineHeight, LIMITS.proseLineHeight, DEFAULT.proseLineHeight),
      // 0 — «без ограничения»; всё, что ниже нижней границы, тоже оно.
      measure: Number(src.measure) >= LIMITS.measure[0] ? clamp(src.measure, LIMITS.measure, 0) : 0,
      sans: pick('sans', 'sans'),
      prose: pick('prose', 'prose'),
      mono: pick('mono', 'mono'),
    };
  }

  function fromPreset(id) {
    return normalize({ ...PRESETS[id].values, preset: id });
  }

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) return normalize(JSON.parse(raw));
    } catch (e) { /* localStorage закрыт или JSON битый — стандарт */ }
    return fromPreset('standard');
  }

  function save(style) {
    try { localStorage.setItem(KEY, JSON.stringify(style)); } catch (e) { /* ignore */ }
  }

  function apply(raw, el) {
    // Нормализуем и здесь: apply виден на window, и в CSS-переменные не
    // должна попасть строка, которую не пропустил бы normalize.
    const style = normalize(raw);
    const s = (el || document.documentElement).style;
    const set = (name, value) => (value === null ? s.removeProperty(name) : s.setProperty(name, value));
    // Совпадающее с :root не пишем: инлайн тогда пуст, и правка дефолтов в
    // _base.css доезжает до тех, кто регулятор не трогал. Совпадение
    // PRESETS.standard с :root стережёт tests/api/test_web_text_style.py.
    set('--text-scale', style.scale === DEFAULT.scale ? null : String(style.scale / 100));
    set('--line-height', style.lineHeight === DEFAULT.lineHeight ? null : String(style.lineHeight));
    set('--prose-line-height', style.proseLineHeight === DEFAULT.proseLineHeight ? null : String(style.proseLineHeight));
    set('--prose-measure', style.measure ? style.measure + 'ch' : null);
    set('--font-sans', style.sans === DEFAULT.sans ? null : FONTS.sans[style.sans].stack);
    set('--font-prose', FONTS.prose[style.prose].stack);
    set('--font-mono', style.mono === DEFAULT.mono ? null : FONTS.mono[style.mono].stack);
  }

  // Пресет, которому стиль совпадает целиком, — для подсветки: ручная
  // подгонка, вернувшаяся к значениям пресета, снова считается им.
  function matchPreset(style) {
    const keys = Object.keys(DEFAULT);
    for (const [id, preset] of Object.entries(PRESETS)) {
      if (keys.every((k) => preset.values[k] === style[k])) return id;
    }
    return 'custom';
  }

  const CUSTOM = { label: 'Свой', note: 'Ползунки и шрифты ниже; запоминается последняя подгонка.' };

  const FORMAT = {
    scale: (v) => v + '%',
    lineHeight: (v) => v.toFixed(2),
    proseLineHeight: (v) => v.toFixed(2),
    measure: (v) => (v ? v + 'ch' : 'без ограничения'),
  };

  // Контролы секции «Текст и шрифт» (settings.html). Шаблон даёт только
  // каркас — контейнер [data-ts-presets], ползунки и селекты с data-key,
  // <output data-ts-out="key">, кнопку [data-ts-reset]; пресеты, шрифты и
  // границы рисуются отсюда, чтобы форма данных не утекала в шаблон.
  function mount(root) {
    let style = load();
    const presets = root.querySelector('[data-ts-presets]');
    const radios = {};
    for (const [id, preset] of [...Object.entries(PRESETS), ['custom', CUSTOM]]) {
      const label = document.createElement('label');
      label.className = 'settings-preset';
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = 'text_preset';
      input.value = id;
      radios[id] = input;
      const title = document.createElement('span');
      title.className = 'settings-preset-title';
      title.textContent = preset.label;
      const note = document.createElement('span');
      note.className = 'settings-preset-note';
      note.textContent = preset.note;
      label.append(input, title, note);
      presets.append(label);
    }

    const ranges = [...root.querySelectorAll('input[type="range"][data-key]')];
    for (const input of ranges) {
      const [lo, hi] = LIMITS[input.dataset.key];
      // У ширины строки шаг левее нижней границы — «без ограничения» (0).
      input.min = input.dataset.key === 'measure' ? lo - Number(input.step) : lo;
      input.max = hi;
    }
    const selects = [...root.querySelectorAll('select[data-key]')];
    for (const select of selects) {
      for (const [id, font] of Object.entries(FONTS[select.dataset.key])) {
        select.add(new Option(font.label, id));
      }
    }

    function render() {
      for (const input of ranges) {
        const key = input.dataset.key;
        input.value = key === 'measure' && !style[key] ? input.min : style[key];
        const out = root.querySelector(`[data-ts-out="${key}"]`);
        if (out) out.textContent = FORMAT[key](style[key]);
      }
      for (const select of selects) select.value = style[select.dataset.key];
      radios[style.preset].checked = true;
    }

    function commit(next) {
      style = normalize(next);
      apply(style);
      save(style);
      render();
    }

    presets.addEventListener('change', (ev) => {
      const id = ev.target.value;
      // «Свой» без подгонки ничего не меняет: оставляет текущие значения
      // и только снимает привязку к пресету.
      commit(id === 'custom' ? { ...style, preset: 'custom' } : fromPreset(id));
    });
    const onTweak = (ev) => {
      const key = ev.target.dataset.key;
      let value = ev.target.type === 'range' ? Number(ev.target.value) : ev.target.value;
      if (key === 'measure' && value < LIMITS.measure[0]) value = 0;
      const next = { ...style, [key]: value };
      next.preset = matchPreset(next);
      commit(next);
    };
    for (const input of ranges) input.addEventListener('input', onTweak);
    for (const select of selects) select.addEventListener('change', onTweak);
    const reset = root.querySelector('[data-ts-reset]');
    if (reset) reset.addEventListener('click', () => commit(fromPreset('standard')));

    render();
  }

  window.codDocText = { KEY, FONTS, PRESETS, LIMITS, load, save, apply, fromPreset, normalize, matchPreset, mount };
  // Сбой здесь не должен стоить странице отрисовки: без регулятора она
  // просто остаётся со стилем :root.
  try { apply(load()); } catch (e) { console.warn('[cod-doc-text]', e); }
})();
