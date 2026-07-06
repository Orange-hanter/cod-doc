/* COD-062 / COD-065: live-updates client.
 *
 * Connects to /ws/projects/<slug> when the page declares
 *   <body data-project="..."> — auto-reconnects with exponential backoff
 *   (1s, 2s, 4s, …, capped at 30s). Updates the connection dot in the
 *   topbar and dispatches DOM events for each server-pushed message.
 *
 * Task board refresh: when #tasks-live-region is present, HTMX-swaps the
 * stats strip + kanban from GET /p/{slug}/frag/tasks/board.
 */

(function () {
  const body = document.body;
  const slug = body && body.dataset ? body.dataset.project : null;
  const i18n = window.COD_DOC_I18N || {};
  function tr(key, fallback) {
    return i18n[key] || fallback || key;
  }
  function wsLabel(state) {
    const stateKey = 'ws.state.' + state;
    const stateText = tr(stateKey, state);
    const tpl = tr('ws.live_updates', 'Live updates: {state}');
    return tpl.replace('{state}', stateText);
  }
  if (!slug) {
    return;
  }

  const dot = document.getElementById('cod-ws-dot');
  function setDot(state) {
    if (!dot) return;
    dot.className = 'cod-ws-dot cod-ws-' + state;
    const label = wsLabel(state);
    dot.title = label;
    dot.setAttribute('aria-label', label);
    dot.textContent = tr('ws.state.' + state, state);
  }

  function buildUrl() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws/projects/${encodeURIComponent(slug)}`;
  }

  function tasksLiveRefreshUrl() {
    const region = document.getElementById('tasks-live-region');
    return region ? region.dataset.refreshUrl : null;
  }

  function refreshTasksLiveRegion() {
    const url = tasksLiveRefreshUrl();
    const region = document.getElementById('tasks-live-region');
    if (!url || !region || !window.htmx) return;
    window.htmx.ajax('GET', url, { target: '#tasks-live-region', swap: 'outerHTML' });
  }

  function removeReadyRow(taskId) {
    const row = document.getElementById('task-' + taskId);
    if (row && row.closest('.ready-list')) {
      row.remove();
      const list = document.querySelector('.ready-list');
      if (list && list.children.length === 0) {
        const block = list.closest('.overview-block');
        if (block) {
          const empty = document.createElement('p');
          empty.className = 'muted';
          empty.textContent = tr('overview.no_ready_tasks', 'No tasks ready to start.');
          list.replaceWith(empty);
        }
      }
    }
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
      try { socket.close(); } catch (_e) { /* noop */ }
    });
  }

  function handleEvent(msg) {
    if (!msg || !msg.kind) return;
    const payload = msg.payload || {};
    const taskId = payload.task_id;

    if (msg.kind === 'task.status_changed' || msg.kind === 'task.created') {
      if (tasksLiveRefreshUrl()) {
        refreshTasksLiveRegion();
        return;
      }
    }

    if (msg.kind === 'task.status_changed' && taskId) {
      removeReadyRow(taskId);
      const row = document.getElementById('task-' + taskId);
      if (row && window.htmx) {
        window.htmx.trigger(row, 'cod_doc:reload-row', payload);
      }
    }

    if (msg.kind === 'task.created') {
      const list = document.querySelector('table.grid');
      if (list && window.location.pathname.endsWith('/tasks') && window.htmx) {
        window.htmx.trigger(document.body, 'cod_doc:reload-tasks-list', payload);
      }
    }

    if (msg.kind === 'agent.started') {
      const status = document.getElementById('cod-agent-status');
      if (status) {
        status.hidden = false;
        const step = status.querySelector('.cod-agent-step');
        if (step) step.textContent = 'Started: ' + (payload.title || payload.task_id);
        status.dataset.startedAt = String(Date.now());
      }
    }
    if (msg.kind === 'agent.thinking' || msg.kind === 'agent.tool_call') {
      const step = document.querySelector('#cod-agent-status .cod-agent-step');
      if (step && payload.data) {
        step.textContent = String(payload.data).slice(0, 240);
      }
    }
    if (msg.kind === 'agent.stopped' || msg.kind === 'agent.done' || msg.kind === 'agent.error') {
      const status = document.getElementById('cod-agent-status');
      if (status) {
        const step = status.querySelector('.cod-agent-step');
        if (step) step.textContent = 'Idle';
        setTimeout(() => { status.hidden = true; }, 4000);
      }
    }
  }

  connect();
})();
