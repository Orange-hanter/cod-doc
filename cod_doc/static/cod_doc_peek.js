/* COD-DOC web — peek sheet (WUI-001).
 *
 * A link marked `data-peek` opens its task in a <dialog> that slides up over
 * the current page, instead of navigating away. The body is the server's
 * `<canonical url>?peek=1` fragment (cod_doc/api/web/peek.py); the address bar
 * shows the canonical URL, so a reload or a shared link gives the full page.
 *
 * History: every sheet opened by a click pushes one entry. Back steps one
 * sheet back; Esc, ✕ and a backdrop click go back past all of them at once.
 * The `close` event of the dialog is the last word: the browser may close a
 * modal on its own (a repeated Esc, the OS back gesture), and history must
 * follow it.
 */

(function () {
  const dialog = document.getElementById('peek');
  if (!dialog || !window.htmx || typeof dialog.showModal !== 'function') return;

  const htmx = window.htmx;
  const body = dialog.querySelector('#peek-body');
  const titleEl = dialog.querySelector('.peek-title');
  const fullLink = dialog.querySelector('.peek-full');
  const closeBtn = dialog.querySelector('.peek-close');
  const guard = dialog.querySelector('.peek-guard');
  const alertsSlot = dialog.querySelector('.peek-alerts');
  const pageAlerts = document.getElementById('alerts');
  const root = document.documentElement;

  const STATE_KEY = 'codDocPeek';
  const PEEK_PARAM = 'peek';
  // Закрывающая анимация длится --motion-mid (0.2 с); таймер страхует от
  // браузера, который не прислал animationend.
  const CLOSE_FALLBACK_MS = 250;
  const reduceMotion = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let depth = 0;            // history entries pushed by the sheet
  let currentUrl = '';
  let opener = null;        // element focused before the sheet opened
  let pageTitle = document.title;
  let changed = false;      // something was written while the sheet was open
  let guardArmed = false;
  let closing = false;
  let closedByUs = false;
  let finishOnPop = false;  // the browser closed the dialog; finish after history.go
  let alertsObserver = null;
  // URL, который отрисовал этот документ. Записи истории, сделанные
  // шторкой, переживают перезагрузку: после reload на карточке «Назад»
  // приходит в тот же документ с пустым state, и страница должна
  // перерисоваться под новый URL сама.
  const docUrl = location.pathname + location.search;

  // ── Loading ──────────────────────────────────────────────────────────
  function peekUrl(url) {
    const u = new URL(url, location.href);
    u.searchParams.set(PEEK_PARAM, '1');
    return u.pathname + u.search;
  }

  function load(url) {
    hideGuard();
    currentUrl = url;
    fullLink.href = url;
    // source = body: htmx ведёт запрос на нём, и htmx:abort отменяет
    // предыдущую загрузку. hx-sync на теле не ставим — он наследуется, и
    // переход по ссылке оборвал бы POST статуса внутри шторки.
    htmx.trigger(body, 'htmx:abort');
    body.setAttribute('aria-busy', 'true');
    htmx.ajax('GET', peekUrl(url), { source: body, target: body, swap: 'innerHTML' });
  }

  function applyHeader() {
    const card = body.querySelector('.peek-doc');
    const title = card ? card.dataset.peekTitle : '';
    titleEl.textContent = title || '';
    fullLink.href = (card && card.dataset.peekUrl) || currentUrl;
    // Заголовок вкладки — тот же, что у полной страницы карточки.
    document.title = (card && card.dataset.peekPageTitle) || pageTitle;
  }

  function showError(message) {
    const card = document.createElement('article');
    card.className = 'peek-doc peek-error';
    card.dataset.peekTitle = 'Ошибка';
    card.dataset.peekUrl = currentUrl;
    const msg = document.createElement('p');
    msg.className = 'peek-error-msg';
    msg.textContent = message;
    const more = document.createElement('p');
    more.className = 'muted-tiny';
    const link = document.createElement('a');
    link.href = currentUrl;
    link.setAttribute('data-no-peek', '');
    link.textContent = 'Открыть полную страницу';
    more.append(link);
    card.append(msg, more);
    body.replaceChildren(card);
    body.setAttribute('aria-busy', 'false');
    applyHeader();
  }

  body.addEventListener('htmx:afterRequest', (ev) => {
    if (ev.detail.elt === body) body.setAttribute('aria-busy', 'false');
  });
  body.addEventListener('htmx:beforeSwap', (ev) => {
    // 404 шторки — HTML-фрагмент (peek_error.html), его надо показать.
    if (ev.detail.target === body && ev.detail.xhr.status === 404) {
      ev.detail.shouldSwap = true;
      ev.detail.isError = false;
    }
  });
  body.addEventListener('htmx:afterSwap', (ev) => {
    if (ev.detail.target !== body) return;
    applyHeader();
    body.scrollTop = 0;
  });
  // Новая карточка начинается сверху. Повтор после settle: однажды шторка
  // открылась прокрученной на ~140 px, причину поймать не удалось.
  body.addEventListener('htmx:afterSettle', (ev) => {
    if (ev.detail.target === body) body.scrollTop = 0;
  });
  body.addEventListener('htmx:responseError', (ev) => {
    if (ev.detail.elt !== body) return;
    showError('Сервер ответил ' + ev.detail.xhr.status + '.');
  });
  body.addEventListener('htmx:sendError', (ev) => {
    if (ev.detail.elt !== body) return;
    showError('Нет связи с сервером.');
  });

  // ── Opening ──────────────────────────────────────────────────────────
  function open(url, push) {
    if (!dialog.open) {
      opener = document.activeElement;
      pageTitle = document.title;
      changed = false;
      lockScroll();
      watchAlerts(true);
      dialog.classList.remove('is-closing');
      dialog.showModal();
      document.dispatchEvent(new CustomEvent('cod_doc:peek-open', { detail: { url } }));
    }
    if (push) {
      depth += 1;
      history.pushState({ [STATE_KEY]: { url, depth } }, '', url);
    }
    load(url);
  }

  document.addEventListener('click', (ev) => {
    if (ev.defaultPrevented || ev.button !== 0) return;
    if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const link = ev.target.closest('a[data-peek]');
    if (!link || link.hasAttribute('data-no-peek')) return;
    if (link.target && link.target !== '_self') return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin) return;
    ev.preventDefault();
    // Переход из шторки с несохранённой правкой — сначала предупреждение.
    if (dialog.open && guardBlocks()) return;
    if (!dialog.open) link.focus();
    open(url.pathname + url.search, true);
  });

  // ── Closing ──────────────────────────────────────────────────────────
  function requestClose() {
    if (guardBlocks()) return;
    if (depth > 0) {
      history.go(-depth);  // popstate закроет шторку
    } else {
      closeNow();
    }
  }

  function closeNow() {
    if (!dialog.open || closing) return;
    closing = true;
    hideGuard();
    let timer = null;
    const done = (ev) => {
      // animationend всплывает от потомков (заглушка загрузки в теле) и
      // приходит от ::backdrop; закрытие ждёт только peek-out самой шторки.
      if (ev && (ev.target !== dialog || ev.animationName !== 'peek-out')) return;
      if (!closing) return;
      closing = false;
      clearTimeout(timer);
      dialog.removeEventListener('animationend', done);
      closedByUs = true;
      dialog.close();
      finishClose();
    };
    if (reduceMotion) {
      done();
      return;
    }
    dialog.classList.add('is-closing');
    dialog.addEventListener('animationend', done);
    timer = setTimeout(done, CLOSE_FALLBACK_MS);
  }

  function finishClose() {
    dialog.classList.remove('is-closing');
    htmx.trigger(body, 'htmx:abort');
    body.replaceChildren();
    titleEl.textContent = '';
    // Алерты, пришедшие при открытой шторке, возвращаются на страницу: их
    // могли не успеть прочитать, а «Не удалось сохранить» терять нельзя.
    watchAlerts(false);
    if (pageAlerts) pageAlerts.prepend(...alertsSlot.children);
    alertsSlot.replaceChildren();
    unlockScroll();
    document.title = pageTitle;
    depth = 0;
    const wasChanged = changed;
    changed = false;
    document.dispatchEvent(new CustomEvent('cod_doc:peek-close', {
      detail: { url: currentUrl, changed: wasChanged },
    }));
    if (wasChanged) refreshPage();
  }

  dialog.addEventListener('cancel', (ev) => {
    ev.preventDefault();
    requestClose();
  });
  dialog.addEventListener('close', () => {
    if (closedByUs) {
      closedByUs = false;
      return;
    }
    // Диалог закрыл браузер, минуя cancel: история ещё стоит на карточке.
    closing = false;
    if (depth > 0) {
      finishOnPop = true;
      history.go(-depth);
    } else {
      finishClose();
    }
  });
  closeBtn.addEventListener('click', requestClose);
  dialog.addEventListener('click', (ev) => {
    if (ev.target !== dialog) return;
    const r = dialog.getBoundingClientRect();
    const outside = ev.clientX < r.left || ev.clientX > r.right
      || ev.clientY < r.top || ev.clientY > r.bottom;
    if (outside) requestClose();
  });

  window.addEventListener('popstate', (ev) => {
    const st = ev.state && ev.state[STATE_KEY];
    if (finishOnPop) {
      finishOnPop = false;
      finishClose();
      return;
    }
    if (!st && !dialog.open) {
      if (location.pathname + location.search !== docUrl) location.reload();
      return;
    }
    if (dialog.open && !guardArmed && isDirty()) {
      // «Назад» с несохранённой правкой: возвращаем запись и предупреждаем.
      history.pushState({ [STATE_KEY]: { url: currentUrl, depth } }, '', currentUrl);
      showGuard();
      return;
    }
    if (st) {
      depth = st.depth;
      if (dialog.open) {
        load(st.url);
      } else {
        open(st.url, false);
      }
      return;
    }
    depth = 0;
    closeNow();
  });

  // ── Unsaved edits ────────────────────────────────────────────────────
  function isDirty() {
    return [...body.querySelectorAll('.task-field-card.editing textarea')]
      .some((t) => t.value !== t.defaultValue);
  }
  function showGuard() {
    guardArmed = true;
    guard.hidden = false;
  }
  function hideGuard() {
    guardArmed = false;
    guard.hidden = true;
  }
  // Первая попытка уйти с несохранённой правкой — предупреждение, вторая
  // проходит.
  function guardBlocks() {
    if (guardArmed || !isDirty()) return false;
    showGuard();
    return true;
  }

  // ── Page behind the sheet ────────────────────────────────────────────
  function lockScroll() {
    const gap = window.innerWidth - root.clientWidth;
    if (gap > 0) root.style.paddingRight = gap + 'px';
    root.classList.add('peek-open');
  }
  function unlockScroll() {
    root.classList.remove('peek-open');
    root.style.paddingRight = '';
  }

  // #alerts живёт в странице, а страница под модальным окном: алерт,
  // пришедший при открытой шторке, переносим в её собственный слот.
  function watchAlerts(on) {
    if (!pageAlerts) return;
    if (alertsObserver) {
      alertsObserver.disconnect();
      alertsObserver = null;
    }
    if (!on) return;
    alertsObserver = new MutationObserver((records) => {
      const added = records.flatMap((r) => [...r.addedNodes])
        .filter((n) => n.nodeType === Node.ELEMENT_NODE);
      added.reverse().forEach((n) => alertsSlot.prepend(n));
    });
    alertsObserver.observe(pageAlerts, { childList: true });
  }

  document.addEventListener('cod_doc:task-changed', () => {
    if (dialog.open) changed = true;
  });

  // Пока шторка открыта, список под ней не трогаем; после записи
  // перечитываем помеченные области страницы целиком, с состоянием,
  // которое посчитал сервер (какие колонки канбана открыты и т.д.).
  async function refreshPage() {
    const regions = [...document.querySelectorAll('[data-peek-refresh][id]')];
    if (!regions.length) return;
    // Открывший шторку элемент будет заменён свежей копией; фокус вернём на
    // её двойника. У карточки канбана id на самой ссылке, у карточки
    // цепочки — на обёртке вокруг неё.
    const holder = opener && opener.closest && opener.closest('[id]');
    const focusSelector = !holder ? null
      : (holder === opener ? '#' + CSS.escape(holder.id)
        : '#' + CSS.escape(holder.id) + ' a[data-peek]');
    try {
      const r = await fetch(location.href, { credentials: 'same-origin' });
      if (!r.ok) return;
      const fresh = new DOMParser().parseFromString(await r.text(), 'text/html');
      regions.forEach((el) => {
        const next = fresh.getElementById(el.id);
        if (!next) return;
        el.replaceWith(next);
        htmx.process(next);
        if (window.codDocEnhance) window.codDocEnhance(next);
      });
    } catch (e) {
      console.warn('[peek] refresh failed', e);
      return;
    }
    const target = focusSelector && document.querySelector(focusSelector);
    if (target) target.focus();
  }

  // Якоря внутри шторки прокручивают шторку, а не страницу под ней: у той
  // могут быть элементы с теми же id.
  body.addEventListener('click', (ev) => {
    if (ev.defaultPrevented) return;
    const link = ev.target.closest('a[href^="#"]');
    if (!link) return;
    const id = decodeURIComponent(link.getAttribute('href').slice(1));
    const target = id && body.querySelector('[id="' + CSS.escape(id) + '"]');
    if (!target) return;
    ev.preventDefault();
    target.scrollIntoView({ block: 'start', behavior: reduceMotion ? 'auto' : 'smooth' });
  });
})();
