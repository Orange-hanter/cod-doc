/* COD-DOC web — small client utilities (theme toggle, agent console).
 *
 * Loaded on every page (after htmx). Keep this single-file: anything
 * heavier than a small handler belongs in its own module behind a feature
 * detection (`querySelector('.foo')`).
 */

(function () {
  // ── Theme toggle ─────────────────────────────────────────────────────
  const root = document.documentElement;
  const KEY = 'cod-doc-theme';
  const toggle = document.getElementById('theme-toggle');

  function setTheme(t) {
    root.setAttribute('data-theme', t);
    try { localStorage.setItem(KEY, t); } catch (e) { /* ignore */ }
  }

  if (toggle) {
    toggle.addEventListener('click', () => {
      const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      setTheme(next);
    });
  }

  // Sync to system preference if user hasn't explicitly chosen.
  try {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener('change', (ev) => {
      const stored = localStorage.getItem(KEY);
      if (!stored) {
        root.setAttribute('data-theme', ev.matches ? 'dark' : 'light');
      }
    });
  } catch (e) { /* matchMedia may not exist in some test envs */ }

  // ── Agent console ────────────────────────────────────────────────────
  // Only wires up if the page has the console container. Listens for
  // cod_doc:event events emitted by cod_doc_ws.js and renders a live
  // timeline; also drives a runtime timer for the active run.
  const consoleEl = document.getElementById('agent-console');
  if (consoleEl) initAgentConsole(consoleEl);

  function initAgentConsole(root) {
    const stream = root.querySelector('.agent-stream-body');
    const empty = root.querySelector('.agent-stream-empty');
    const statusCard = root.querySelector('.agent-status-card');
    const statusValue = root.querySelector('.agent-status-meta .value');
    const statusSub = root.querySelector('.agent-status-meta .sub');
    const timerEl = root.querySelector('.agent-status-runtime .timer');
    const filtersEl = root.querySelector('.agent-stream-filters');

    let activeStartTs = null;
    let timerHandle = null;
    let activeFilter = 'all';
    const eventCount = { thinking: 0, tool_call: 0, tool_result: 0, message: 0, error: 0, blocked: 0 };

    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    function fmtTime(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()); }
    function fmtDuration(ms) {
      const s = Math.floor(ms / 1000);
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      const sec = s % 60;
      if (h > 0) return h + ':' + pad(m) + ':' + pad(sec);
      return pad(m) + ':' + pad(sec);
    }

    function updateTimer() {
      if (!timerEl) return;
      if (activeStartTs == null) { timerEl.textContent = '—'; return; }
      timerEl.textContent = fmtDuration(Date.now() - activeStartTs);
    }

    function setRunning(payload) {
      if (statusCard) {
        statusCard.classList.add('is-running');
        statusCard.classList.remove('is-idle');
      }
      if (statusValue) {
        statusValue.textContent = (payload && payload.title) || (payload && payload.task_id) || 'Working…';
      }
      if (statusSub) {
        statusSub.textContent = (payload && payload.task_id) ? ('Task: ' + payload.task_id) : '';
      }
      activeStartTs = Date.now();
      if (timerHandle) clearInterval(timerHandle);
      timerHandle = setInterval(updateTimer, 1000);
      updateTimer();
    }

    function setIdle(reason) {
      if (statusCard) {
        statusCard.classList.add('is-idle');
        statusCard.classList.remove('is-running');
      }
      if (statusValue) statusValue.textContent = 'Idle';
      if (statusSub) statusSub.textContent = reason ? ('Last: ' + reason) : '';
      activeStartTs = null;
      if (timerHandle) { clearInterval(timerHandle); timerHandle = null; }
      if (timerEl) timerEl.textContent = '—';
    }

    function trimTo(text, max) {
      if (typeof text !== 'string') text = JSON.stringify(text);
      if (!text) return '';
      return text.length > max ? text.slice(0, max) + '…' : text;
    }

    function appendEvent(kind, payload, ts) {
      if (empty) empty.remove();
      const wrap = document.createElement('div');
      wrap.className = 'agent-event kind-' + kind;
      wrap.dataset.kind = kind;

      const tsEl = document.createElement('span');
      tsEl.className = 'ts';
      tsEl.textContent = fmtTime(new Date(ts ? ts * 1000 : Date.now()));

      const kindEl = document.createElement('span');
      kindEl.className = 'kind';
      kindEl.textContent = kind.replace('_', ' ');

      const bodyEl = document.createElement('span');
      bodyEl.className = 'body';

      if (kind === 'tool_call' && payload && payload.data) {
        const data = payload.data;
        const name = (data && data.name) || '';
        const args = (data && data.args) || '';
        const nameEl = document.createElement('span');
        nameEl.className = 'tool-name';
        nameEl.textContent = name;
        bodyEl.appendChild(nameEl);
        if (args) {
          const pre = document.createElement('div');
          pre.className = 'args';
          pre.textContent = trimTo(args, 800);
          bodyEl.appendChild(pre);
        }
      } else if (kind === 'tool_result' && payload && payload.data) {
        const data = payload.data;
        const name = (data && data.name) || '';
        const result = (data && data.result) || '';
        const nameEl = document.createElement('span');
        nameEl.className = 'tool-name';
        nameEl.textContent = name + ' →';
        bodyEl.appendChild(nameEl);
        if (result) {
          const pre = document.createElement('div');
          pre.className = 'result';
          pre.textContent = trimTo(result, 800);
          bodyEl.appendChild(pre);
        }
      } else {
        bodyEl.textContent = trimTo(payload && payload.data ? payload.data : (payload && payload.title) || '', 1200);
      }

      wrap.appendChild(tsEl);
      wrap.appendChild(kindEl);
      wrap.appendChild(bodyEl);
      if (activeFilter !== 'all' && activeFilter !== kind) {
        wrap.style.display = 'none';
      }
      stream.appendChild(wrap);

      // Update counts
      if (kind in eventCount) {
        eventCount[kind] += 1;
        const counter = filtersEl && filtersEl.querySelector('[data-kind="' + kind + '"] .count');
        if (counter) counter.textContent = eventCount[kind];
      }

      // Auto-scroll if user is near bottom
      if (stream.scrollHeight - stream.scrollTop - stream.clientHeight < 120) {
        stream.scrollTop = stream.scrollHeight;
      }
    }

    if (filtersEl) {
      filtersEl.addEventListener('click', (ev) => {
        const btn = ev.target.closest('.agent-stream-filter');
        if (!btn) return;
        filtersEl.querySelectorAll('.agent-stream-filter').forEach((b) => {
          b.classList.remove('active');
          b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
        activeFilter = btn.dataset.kind || 'all';
        stream.querySelectorAll('.agent-event').forEach((row) => {
          row.style.display = activeFilter === 'all' || row.dataset.kind === activeFilter ? '' : 'none';
        });
      });
    }

    document.addEventListener('cod_doc:event', (ev) => {
      const msg = ev.detail;
      if (!msg || !msg.kind) return;
      const k = msg.kind;
      if (k === 'agent.started') {
        setRunning(msg.payload);
        appendEvent('started', msg.payload, msg.ts);
      } else if (k === 'agent.thinking') {
        appendEvent('thinking', msg.payload, msg.ts);
      } else if (k === 'agent.tool_call') {
        appendEvent('tool_call', msg.payload, msg.ts);
      } else if (k === 'agent.tool_result') {
        appendEvent('tool_result', msg.payload, msg.ts);
      } else if (k === 'agent.message') {
        appendEvent('message', msg.payload, msg.ts);
      } else if (k === 'agent.error') {
        appendEvent('error', msg.payload, msg.ts);
        setIdle('error');
      } else if (k === 'agent.blocked') {
        appendEvent('blocked', msg.payload, msg.ts);
      } else if (k === 'agent.stopped' || k === 'agent.done') {
        const reason = (msg.payload && msg.payload.reason) || k.split('.')[1];
        appendEvent('stopped', msg.payload, msg.ts);
        setIdle(reason);
      }
    });
  }
})();
