/* Pixelsort Batch client.
 *
 * Orchestration lives here rather than on the server on purpose: every finished
 * cell is already on disk under a deterministic content-hash name, so "resume"
 * is just re-requesting the cells and letting the cache answer. That removes the
 * need for a job table, a worker service, or any server-side batch state beyond
 * the plan file.
 */

(function () {
  'use strict';

  const SPEC  = JSON.parse(document.getElementById('pixelsortSpec').textContent);
  const BATCH = JSON.parse(document.getElementById('batchSpec').textContent);
  const CONSTS = ['angle', 'randomness', 'lower_threshold', 'upper_threshold', 'clength'];

  const el = id => document.getElementById(id);
  const dom = {
    dropzone: el('dropzone'), fileInput: el('fileInput'), pick: el('pickButton'),
    fileName: el('fileName'), gridSize: el('gridSize'),
    interval: el('intervalFunction'), sorting: el('sortingFunction'),
    sweepParam: el('sweepParam'), axisPreview: el('axisPreview'),
    preflightText: el('preflightText'), alert: el('alert'),
    start: el('startButton'), runbar: el('runbar'), runbarFill: el('runbarFill'),
    runbarText: el('runbarText'), cancel: el('cancelButton'),
    done: el('done'), zipLink: el('zipLink'), doneNote: el('doneNote'),
    gridPanel: el('gridPanel'), gridTitle: el('gridTitle'), gridWrap: el('gridWrap'),
    constBar: el('constBar'), disclose: el('disclose'),
    lightbox: el('lightbox'), lightboxImg: el('lightboxImg'),
    lightboxCmd: el('lightboxCmd'), lightboxTitle: el('lightboxTitle'),
    lightboxDl: el('lightboxDl'), copyCmd: el('copyCmd'),
    helpOpen: el('helpOpen'), helpModal: el('helpModal')
  };

  let mode = 'matrix';
  let file = null;
  let imageSize = null;      // { w, h } read client-side for the estimate
  let batch = null;          // the server plan
  let running = false;
  let abort = false;

  /* ---------------- helpers ---------------- */

  const fmt = n => Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(4)));

  function warn(message) { dom.alert.textContent = message; dom.alert.hidden = false; }
  function clearWarn() { dom.alert.hidden = true; }

  function readConstants() {
    const out = {};
    for (const key of CONSTS) {
      const input = el('k_' + key);
      if (input) out[key] = key === 'clength' ? parseInt(input.value, 10) : parseFloat(input.value);
    }
    return out;
  }

  function syncConstOutputs() {
    for (const key of CONSTS) {
      const input = el('k_' + key), out = el('k_' + key + 'Out');
      if (input && out) out.textContent = input.value + (input.dataset.unit || '');
    }
  }

  function humanTime(seconds) {
    if (seconds < 90) return `${Math.round(seconds)} seconds`;
    const m = seconds / 60;
    return m < 10 ? `${m.toFixed(1)} minutes` : `${Math.round(m)} minutes`;
  }

  /* ---------------- mode + axis preview ---------------- */

  function applyMode() {
    for (const node of document.querySelectorAll('[data-when]')) {
      node.hidden = !node.dataset.when.split(' ').includes(mode);
    }
    for (const button of document.querySelectorAll('.mode')) {
      const on = button.dataset.mode === mode;
      button.classList.toggle('is-on', on);
      button.setAttribute('aria-checked', String(on));
    }
    renderAxisPreview();
    updatePreflight();
  }

  function ladder(name, n) {
    const rung = BATCH.ladders[name];
    return rung ? (rung[n] || rung[String(n)]) : null;
  }

  function renderAxisPreview() {
    const n = parseInt(dom.gridSize.value, 10);
    let html = '';

    if (mode === 'matrix') {
      const rows = BATCH.matrix.rowValues[n] || BATCH.matrix.rowValues[String(n)];
      const cols = BATCH.matrix.colValues[n] || BATCH.matrix.colValues[String(n)];
      html = `rows &nbsp;<b>interval fn</b> &nbsp;${rows.join(' · ')}<br>`
           + `cols &nbsp;<b>sorting fn</b> &nbsp;${cols.join(' · ')}`;

    } else if (mode === 'intensity') {
      const axes = BATCH.intensityAxes[dom.interval.value];
      if (!axes) { dom.axisPreview.innerHTML = ''; return; }
      const rows = ladder(axes.row.ladder, n) || [];
      const cols = ladder(axes.col.ladder, n) || [];
      html = `rows &nbsp;<b>${axes.row.param}</b> &nbsp;${rows.join(' → ')}<br>`
           + `cols &nbsp;<b>${axes.col.param}</b> &nbsp;${cols.join(' → ')}`;
      html += axes.col.monotonic
        ? `<br><em>effect grows toward the bottom right</em>`
        : `<br><em>${dom.interval.value} reads no second intensity parameter — `
          + `the column axis rotates rather than intensifies</em>`;

    } else {
      const cfg = BATCH.sweeps[dom.sweepParam.value];
      html = `<b>${cfg.label}</b> across ${n * n} cells, `
           + `${cfg.min}${cfg.unit} → ${cfg.max}${cfg.unit}`
           + (cfg.space === 'log' ? ' <em>(log spaced)</em>' : '');
    }
    dom.axisPreview.innerHTML = html;
  }

  function updatePreflight() {
    const n = parseInt(dom.gridSize.value, 10);
    const cells = n * n;
    if (!imageSize) {
      dom.preflightText.textContent = 'Load an image to see the estimate.';
      return;
    }
    const cap = BATCH.maxEdge;
    let { w, h } = imageSize;
    if (cap && Math.max(w, h) > cap) {
      const s = cap / Math.max(w, h);
      w = Math.round(w * s); h = Math.round(h * s);
    }
    const mp = (w * h) / 1e6;
    const seconds = (cells * mp * BATCH.secondsPerMegapixel) / Math.max(1, BATCH.concurrency);
    dom.preflightText.innerHTML =
      `<b>${cells} cells</b> · ${w} × ${h} · about <b>${humanTime(seconds)}</b>`;
  }

  /* ---------------- file intake ---------------- */

  function acceptFile(chosen) {
    if (!chosen) return;
    if (!chosen.type.startsWith('image/')) {
      warn('That file is not an image. Pick a jpg, png, webp, bmp, gif or tiff.');
      return;
    }
    file = chosen;
    clearWarn();
    dom.fileName.textContent = `${chosen.name} — ${(chosen.size / 1024).toFixed(0)} KB`;
    dom.fileName.hidden = false;

    const url = URL.createObjectURL(chosen);
    const probe = new Image();
    probe.onload = () => {
      imageSize = { w: probe.naturalWidth, h: probe.naturalHeight };
      URL.revokeObjectURL(url);
      updatePreflight();
    };
    probe.src = url;

    dom.start.disabled = false;
    dom.start.querySelector('.btn__sub').textContent = 'ready';
  }

  /* ---------------- grid rendering ---------------- */

  function buildGrid() {
    const plan = batch.plan;
    const n = plan.grid_size;
    dom.gridWrap.style.setProperty('--n', n);
    // Cells take the source's aspect ratio so nothing is cropped.
    dom.gridWrap.style.setProperty('--ar', `${batch.width} / ${batch.height}`);
    dom.gridWrap.classList.toggle('is-sweep', plan.mode === 'sweep');
    dom.gridWrap.innerHTML = '';

    if (plan.mode !== 'sweep') {
      const corner = document.createElement('div');
      corner.className = 'axishead axishead--corner';
      corner.innerHTML = `<b>rows</b>${plan.row_axis.label}<b>cols</b>${plan.col_axis.label}`;
      dom.gridWrap.appendChild(corner);
      for (const label of plan.col_labels) {
        const head = document.createElement('div');
        head.className = 'axishead';
        head.textContent = label;
        dom.gridWrap.appendChild(head);
      }
    }

    plan.cells.forEach((cell, i) => {
      if (plan.mode !== 'sweep' && cell.col === 0) {
        const head = document.createElement('div');
        head.className = 'axishead axishead--row';
        head.textContent = plan.row_labels[cell.row];
        dom.gridWrap.appendChild(head);
      }
      const node = document.createElement('div');
      node.className = 'cell';
      node.id = 'cell-' + i;
      node.tabIndex = 0;
      node.innerHTML = plan.mode === 'sweep'
        ? `<div class="cell__state">queued</div><span class="cell__sweep">${cell.col_label}</span>`
        : `<div class="cell__state">queued</div>`;
      dom.gridWrap.appendChild(node);
      if (cell.url) paintCell(i, cell);
    });

    // Constants bar: everything held fixed, stated once.
    const varying = new Set([plan.row_axis.param, plan.col_axis.param]);
    const held = Object.entries(batch.constants)
      .filter(([k]) => !varying.has(k) && CONSTS.includes(k))
      .map(([k, v]) => `${SPEC.controls[k].flag} ${fmt(v)}`);
    if (plan.mode !== 'matrix') {
      if (!varying.has('interval_function')) held.unshift(`-i ${batch.constants.interval_function}`);
      if (!varying.has('sorting_function')) held.unshift(`-s ${batch.constants.sorting_function}`);
    }
    dom.constBar.textContent =
      `all cells · ${batch.width} × ${batch.height} · ${held.join('  ') || 'defaults'}`;

    // Non-determinism, disclosed rather than hidden.
    const stochastic = plan.cells.some(c =>
      ['random', 'waves'].includes(c.params.interval_function));
    dom.disclose.hidden = !stochastic;
    if (stochastic) {
      dom.disclose.textContent =
        'pixelsort seeds nothing, so random and waves build a fresh interval layout '
        + 'on every run. Cells using them are not a perfectly controlled comparison, '
        + 'and re-rendering this grid will produce different output.';
    }

    dom.gridTitle.textContent = `4 · Results — ${plan.mode}, ${n} × ${n}`;
    dom.gridPanel.hidden = false;
  }

  function setCellState(i, state, html) {
    const node = el('cell-' + i);
    if (!node) return;
    node.classList.remove('is-running', 'is-error');
    if (state) node.classList.add(state);
    const slot = node.querySelector('.cell__state');
    if (slot) slot.innerHTML = html;
  }

  function paintCell(i, data) {
    const node = el('cell-' + i);
    const cell = batch.plan.cells[i];
    const sweepTag = node.querySelector('.cell__sweep');
    node.innerHTML = '';
    const img = document.createElement('img');
    img.src = data.view_url;
    img.alt = cell.command || `cell ${i}`;
    img.loading = 'lazy';
    node.appendChild(img);
    if (sweepTag) node.appendChild(sweepTag);
    const zoom = document.createElement('span');
    zoom.className = 'cell__zoom';
    zoom.textContent = `${data.width || batch.width} × ${data.height || batch.height} — click for full size`;
    node.appendChild(zoom);
    node.classList.remove('is-running', 'is-error');
    node.classList.add('is-done');
    node.dataset.url = data.url;
    node.dataset.command = data.command || cell.command;
  }

  /* ---------------- the run loop ---------------- */

  async function renderCell(i) {
    const started = Date.now();
    setCellState(i, 'is-running', '<div class="spin"></div><span id="t' + i + '">0s</span>');
    const ticker = setInterval(() => {
      const t = el('t' + i);
      if (t) t.textContent = Math.round((Date.now() - started) / 1000) + 's';
    }, 1000);

    try {
      const response = await fetch(`/api/batch/${batch.batch_id}/cell/${i}`);
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'render failed');
      paintCell(i, data);
      return true;
    } catch (error) {
      // A cell killed by STOP is not a failure, and styling it as one makes the
      // user think something broke rather than that they stopped it.
      if (abort) setCellState(i, null, 'stopped');
      else setCellState(i, 'is-error', `<b>failed</b>${error.message}`);
      return false;
    } finally {
      clearInterval(ticker);
    }
  }

  async function runBatch() {
    running = true;
    abort = false;
    dom.start.hidden = true;
    dom.runbar.hidden = false;
    dom.done.hidden = true;

    const order = batch.fill_order.filter(i => !batch.plan.cells[i].url);
    const alreadyDone = batch.plan.cells.length - order.length;
    let finished = alreadyDone;
    const total = batch.plan.cells.length;

    const tick = () => {
      dom.runbarFill.style.width = `${(finished / total) * 100}%`;
      const left = total - finished;
      const perCell = (batch.estimate_seconds * BATCH.concurrency) / total;
      const eta = Math.ceil((left * perCell) / Math.max(1, BATCH.concurrency));
      dom.runbarText.textContent = left
        ? `${finished} / ${total} · about ${humanTime(eta)} left`
        : `${finished} / ${total}`;
    };
    tick();

    // Client-side limiter matched to the server semaphore. Firing all sixteen at
    // once would just hold sixteen sockets open waiting on the same queue.
    const queue = order.slice();
    const workers = Array.from({ length: Math.max(1, BATCH.concurrency) }, async () => {
      while (queue.length && !abort) {
        const i = queue.shift();
        await renderCell(i);
        finished += 1;
        tick();
      }
    });
    await Promise.all(workers);

    running = false;
    dom.runbar.hidden = true;
    dom.start.hidden = false;
    dom.start.querySelector('.btn__label').textContent = abort ? 'RESUME GRID' : 'RENDER AGAIN';
    dom.start.querySelector('.btn__sub').textContent =
      abort ? 'stopped — finished cells are kept' : 'finished cells are reused';
    dom.start.disabled = false;

    const anyDone = batch.plan.cells.some((_, i) => el('cell-' + i)?.classList.contains('is-done'));
    if (anyDone) {
      dom.zipLink.href = `/api/batch/${batch.batch_id}/download.zip`;
      dom.doneNote.textContent = 'full-resolution PNGs plus a commands.txt';
      dom.done.hidden = false;
    }
  }

  /* ---------------- submit / resume ---------------- */

  async function submit() {
    if (running) return;
    if (batch) {
      // Re-entering an existing grid. Re-fetching refreshes which cells are
      // already cached and clears any cancel flag left by a STOP.
      try {
        const response = await fetch(`/api/batch/${batch.batch_id}`);
        const data = await response.json();
        if (response.ok && data.ok) { batch = data; buildGrid(); }
      } catch (_) { /* keep the plan we already have */ }
      return runBatch();
    }
    if (!file) { warn('Load an image first.'); return; }
    clearWarn();
    dom.start.disabled = true;
    dom.start.querySelector('.btn__sub').textContent = 'preparing…';

    const form = new FormData();
    form.append('image', file);
    form.append('mode', mode);
    form.append('grid_size', dom.gridSize.value);
    form.append('interval_function', dom.interval.value);
    form.append('sorting_function', dom.sorting.value);
    form.append('sweep_param', dom.sweepParam.value);
    const constants = readConstants();
    for (const [k, v] of Object.entries(constants)) form.append(k, String(v));

    try {
      const response = await fetch('/api/batch', { method: 'POST', body: form });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'could not start the batch');
      batch = data;
      history.replaceState(null, '', `?batch=${data.batch_id}`);
      buildGrid();
      dom.gridPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
      await runBatch();
    } catch (error) {
      warn(error.message);
      dom.start.disabled = false;
      dom.start.querySelector('.btn__sub').textContent = 'ready';
    }
  }

  async function resume(batchId) {
    try {
      const response = await fetch(`/api/batch/${batchId}`);
      const data = await response.json();
      if (!response.ok || !data.ok) return;
      batch = data;
      imageSize = { w: data.width, h: data.height };
      mode = data.plan.mode;
      dom.gridSize.value = String(data.plan.grid_size);
      applyMode();
      buildGrid();
      const remaining = data.plan.cells.filter(c => !c.url).length;
      dom.start.hidden = false;
      dom.start.disabled = false;
      dom.start.querySelector('.btn__label').textContent = remaining ? 'RESUME GRID' : 'RENDER AGAIN';
      dom.start.querySelector('.btn__sub').textContent =
        remaining ? `${remaining} cell${remaining === 1 ? '' : 's'} left` : 'all cells cached';
      if (data.plan.cells.some(c => c.url)) {
        dom.zipLink.href = `/api/batch/${batchId}/download.zip`;
        dom.doneNote.textContent = 'full-resolution PNGs plus a commands.txt';
        dom.done.hidden = false;
      }
    } catch (_) { /* stale link, fall through to a fresh start */ }
  }

  async function cancel() {
    abort = true;
    dom.runbarText.textContent = 'stopping…';
    try { await fetch(`/api/batch/${batch.batch_id}/cancel`, { method: 'POST' }); } catch (_) {}
  }

  /* ---------------- lightbox ---------------- */

  function openLightbox(i) {
    const node = el('cell-' + i);
    if (!node || !node.dataset.url) return;
    const cell = batch.plan.cells[i];
    dom.lightboxImg.src = node.dataset.url;
    dom.lightboxImg.alt = node.dataset.command;
    dom.lightboxCmd.textContent = node.dataset.command;
    dom.lightboxDl.href = node.dataset.url;
    dom.lightboxTitle.textContent =
      [cell.row_label, cell.col_label].filter(Boolean).join('  ·  ') || `cell ${i}`;
    dom.lightbox.hidden = false;
    dom.lightbox.querySelector('.btn--x').focus();
  }
  function closeLightbox() { dom.lightbox.hidden = true; }

  /* ---------------- wiring ---------------- */

  dom.pick.addEventListener('click', e => { e.stopPropagation(); dom.fileInput.click(); });
  dom.dropzone.addEventListener('click', () => dom.fileInput.click());
  dom.dropzone.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); dom.fileInput.click(); }
  });
  dom.fileInput.addEventListener('change', e => acceptFile(e.target.files[0]));
  ['dragenter', 'dragover'].forEach(t => dom.dropzone.addEventListener(t, e => {
    e.preventDefault(); dom.dropzone.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach(t => dom.dropzone.addEventListener(t, e => {
    e.preventDefault(); dom.dropzone.classList.remove('is-over');
  }));
  dom.dropzone.addEventListener('drop', e => acceptFile(e.dataTransfer.files[0]));
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => e.preventDefault());

  for (const button of document.querySelectorAll('.mode')) {
    button.addEventListener('click', () => { mode = button.dataset.mode; batch = null; applyMode(); });
  }
  dom.gridSize.addEventListener('change', () => { batch = null; renderAxisPreview(); updatePreflight(); });
  dom.interval.addEventListener('change', () => { batch = null; renderAxisPreview(); });
  dom.sorting.addEventListener('change', () => { batch = null; });
  dom.sweepParam.addEventListener('change', () => { batch = null; renderAxisPreview(); });
  for (const key of CONSTS) {
    const input = el('k_' + key);
    if (input) input.addEventListener('input', () => { syncConstOutputs(); batch = null; });
  }

  dom.start.addEventListener('click', submit);
  dom.cancel.addEventListener('click', cancel);

  dom.gridWrap.addEventListener('click', e => {
    const node = e.target.closest('.cell.is-done');
    if (node) openLightbox(parseInt(node.id.slice(5), 10));
  });
  dom.gridWrap.addEventListener('keydown', e => {
    const node = e.target.closest('.cell');
    if (!node) return;
    const n = batch ? batch.plan.grid_size : 4;
    const i = parseInt(node.id.slice(5), 10);
    const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: n, ArrowUp: -n };
    if (e.key === 'Enter' && node.classList.contains('is-done')) { e.preventDefault(); openLightbox(i); }
    else if (moves[e.key] !== undefined) {
      e.preventDefault();
      const next = el('cell-' + (i + moves[e.key]));
      if (next) next.focus();
    }
  });

  dom.copyCmd.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(dom.lightboxCmd.textContent);
      dom.copyCmd.textContent = 'COPIED';
      setTimeout(() => { dom.copyCmd.textContent = 'COPY COMMAND'; }, 1600);
    } catch (_) { dom.copyCmd.textContent = 'COPY FAILED'; }
  });

  dom.lightbox.querySelectorAll('[data-close]').forEach(n => n.addEventListener('click', closeLightbox));
  dom.helpOpen.addEventListener('click', () => {
    dom.helpModal.hidden = false;
    dom.helpModal.querySelector('.btn--x').focus();
  });
  dom.helpModal.querySelectorAll('[data-close]').forEach(n =>
    n.addEventListener('click', () => { dom.helpModal.hidden = true; dom.helpOpen.focus(); }));
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (!dom.lightbox.hidden) closeLightbox();
    else if (!dom.helpModal.hidden) { dom.helpModal.hidden = true; dom.helpOpen.focus(); }
  });

  window.addEventListener('beforeunload', e => {
    if (running) { e.preventDefault(); e.returnValue = ''; }
  });

  syncConstOutputs();
  applyMode();

  const existing = new URLSearchParams(location.search).get('batch');
  if (existing) resume(existing);
})();
