/* COD-062 / COD-065: live-updates client.
 *
 * Connects to /ws/projects/<slug> when the page declares
 *   <body data-project="..."> — auto-reconnects with exponential backoff
 *   (1s, 2s, 4s, …, capped at 30s). Updates the connection dot in the
 *   topbar and dispatches DOM events for each server-pushed message.
 *
 * For task.status_changed we trigger an HTMX out-of-band swap by re-
 * fetching the task list fragment for the current page (cheapest update
 * path for now). Pages can also listen on `cod_doc:event` themselves.
 */

(function () {
  const body = document.body;
  const slug = body && body.dataset ? body.dataset.project : null;
  if (!slug) {
    return; // Not a project page — no-op.
  }

  const dot = document.getElementById('cod-ws-dot');
  function setDot(state) {
    if (!dot) return;
    dot.className = 'cod-ws-dot cod-ws-' + state;
    const label = 'Live updates: ' + state;
    dot.title = label;
    // COD-077 (f): keep aria-label in sync so screen readers
    // announce state changes via the role=status / aria-live region.
    dot.setAttribute('aria-label', label);
  }

  function buildUrl() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws/projects/${encodeURIComponent(slug)}`;
  }

  let attempt = 0;
  let socket = null;

  function connect() {
    setDot('connecting');
    socket = new WebSocket(buildUrl());

    socket.addEventListener('open', () => {
      attempt = 0;
      setDot('connected');
    });

    socket.addEventListener('message', (raw) => {
      let msg;
      try {
        msg = JSON.parse(raw.data);
      } catch (_e) {
        return;
      }
      if (!msg || !msg.kind) return;
      // Re-emit so pages can handle their own reactions.
      document.dispatchEvent(new CustomEvent('cod_doc:event', { detail: msg }));
      handleEvent(msg);
    });

    socket.addEventListener('close', () => {
      setDot('reconnecting');
      const wait = Math.min(30000, 1000 * Math.pow(2, attempt));
      attempt += 1;
      setTimeout(connect, wait);
    });

    socket.addEventListener('error', () => {
      // Let the close handler drive the reconnect cycle.
      try { socket.close(); } catch (_e) { /* noop */ }
    });
  }

  function handleEvent(msg) {
    if (!msg || !msg.kind) return;
    if (msg.kind === 'task.status_changed') {
      // If the page contains the task row, swap just that row via HTMX
      // (server has /p/{slug}/tasks/{id}/status returning the row fragment
      // already — but here we don't want to mutate, just re-render). We
      // achieve that by triggering a custom event the existing inline-edit
      // listener can pick up; default fallback is a soft refresh.
      const row = document.getElementById('task-' + msg.payload.task_id);
      if (row && window.htmx) {
        window.htmx.trigger(row, 'cod_doc:reload-row', msg.payload);
      }
    }
    if (msg.kind === 'task.created') {
      // COD-077 (e): a brand-new task is on the way; the existing list
      // can't render it without a server round-trip, so trigger a soft
      // refresh of the tasks-tab body if we're looking at it.
      const list = document.querySelector('table.grid');
      if (list && window.location.pathname.endsWith('/tasks') && window.htmx) {
        window.htmx.trigger(document.body, 'cod_doc:reload-tasks-list', msg.payload);
      }
    }
    if (msg.kind === 'agent.started') {
      const status = document.getElementById('cod-agent-status');
      if (status) {
        status.hidden = false;
        const step = status.querySelector('.cod-agent-step');
        if (step) step.textContent = 'Started: ' + (msg.payload.title || msg.payload.task_id);
        status.dataset.startedAt = String(Date.now());
      }
    }
    if (msg.kind === 'agent.thinking' || msg.kind === 'agent.tool_call') {
      const step = document.querySelector('#cod-agent-status .cod-agent-step');
      if (step && msg.payload && msg.payload.data) {
        step.textContent = String(msg.payload.data).slice(0, 240);
      }
    }
    if (msg.kind === 'agent.stopped' || msg.kind === 'agent.done' || msg.kind === 'agent.error') {
      const status = document.getElementById('cod-agent-status');
      if (status) {
        const step = status.querySelector('.cod-agent-step');
        if (step) step.textContent = 'Idle';
        // Hide after a short pause so the user sees the final state.
        setTimeout(() => { status.hidden = true; }, 4000);
      }
    }
  }

  connect();
})();
