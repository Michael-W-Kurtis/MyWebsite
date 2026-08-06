/* Pixelsort Wrapper.
 *
 * The command preview is built from the same spec object the server uses
 * (app/pixelsort_spec.py, serialised into #pixelsortSpec), applying the same
 * two rules: drop parameters the current interval function ignores, and drop
 * flags whose value still equals the pixelsort default. That is why the preview
 * matches the command the server reports after the run.
 */

(function () {
  'use strict';

  const SPEC = JSON.parse(document.getElementById('pixelsortSpec').textContent);
  const USES = Object.fromEntries(SPEC.intervalFunctions.map(f => [f.value, f.uses]));
  const BLURB_I = Object.fromEntries(SPEC.intervalFunctions.map(f => [f.value, f.blurb]));
  const BLURB_S = Object.fromEntries(SPEC.sortingFunctions.map(f => [f.value, f.blurb]));
  const SLIDERS = ['angle', 'randomness', 'lower_threshold', 'upper_threshold', 'clength'];

  const el = {
    dropzone:  document.getElementById('dropzone'),
    fileInput: document.getElementById('fileInput'),
    pick:      document.getElementById('pickButton'),
    fileName:  document.getElementById('fileName'),
    intervalFile: document.getElementById('intervalFile'),

    interval:  document.getElementById('intervalFunction'),
    sorting:   document.getElementById('sortingFunction'),
    iBlurb:    document.getElementById('intervalBlurb'),
    sBlurb:    document.getElementById('sortingBlurb'),
    hiddenNote: document.getElementById('hiddenNote'),

    originalImg: document.getElementById('originalImg'),
    originalBox: document.getElementById('originalBox'),
    resultImg:   document.getElementById('resultImg'),
    resultBox:   document.getElementById('resultBox'),
    working:     document.getElementById('working'),
    workingText: document.getElementById('workingText'),
    download:    document.getElementById('downloadLink'),

    commandLine: document.getElementById('commandLine'),
    sortButton:  document.getElementById('sortButton'),
    alert:       document.getElementById('alert'),

    helpOpen:  document.getElementById('helpOpen'),
    helpModal: document.getElementById('helpModal')
  };

  let selectedFile = null;

  /* ---------------- Parameter relevance ---------------- */

  function relevant(intervalFn) {
    return new Set(SPEC.alwaysRelevant.concat(USES[intervalFn] || []));
  }

  function readValue(key) {
    const input = document.getElementById(key);
    if (!input) return SPEC.defaults[key];
    return key === 'clength' ? parseInt(input.value, 10) : parseFloat(input.value);
  }

  function applyRelevance() {
    const keep = relevant(el.interval.value);
    const dropped = [];

    for (const key of SLIDERS) {
      const block = document.querySelector(`.param[data-param="${key}"]`);
      if (!block) continue;
      const show = keep.has(key);
      block.hidden = !show;
      if (!show) dropped.push(SPEC.controls[key].label);
    }

    const fileBlock = document.querySelector('.param[data-param="interval_file"]');
    if (fileBlock) fileBlock.hidden = !keep.has('interval_file');

    if (dropped.length) {
      el.hiddenNote.textContent =
        `${dropped.join(', ')} ${dropped.length === 1 ? 'is' : 'are'} hidden because ` +
        `the ${el.interval.value} interval function ignores ${dropped.length === 1 ? 'it' : 'them'}.`;
      el.hiddenNote.hidden = false;
    } else {
      el.hiddenNote.hidden = true;
    }

    el.iBlurb.textContent = BLURB_I[el.interval.value] || '';
    el.sBlurb.textContent = BLURB_S[el.sorting.value] || '';
  }

  function syncOutputs() {
    for (const key of SLIDERS) {
      const input = document.getElementById(key);
      const out = document.getElementById(key + 'Out');
      if (input && out) out.textContent = input.value + (input.dataset.unit || '');
    }
  }

  /* ---------------- Command preview ---------------- */

  function fmt(n) {
    return Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(4)));
  }

  function buildCommand() {
    const inName = selectedFile
      ? './UploadedImages/' + selectedFile.name.replace(/\.[^.]+$/, '') + '.png'
      : './UploadedImages/<your-file>.png';

    const parts = ['python', '-m', 'pixelsort', inName, '-o', './results/<out>.png'];

    if (el.interval.value !== SPEC.defaults.interval_function) parts.push('-i', el.interval.value);
    if (el.sorting.value !== SPEC.defaults.sorting_function) parts.push('-s', el.sorting.value);

    const keep = relevant(el.interval.value);
    const flagOrder = [
      ['lower_threshold', '-t'], ['upper_threshold', '-u'],
      ['clength', '-c'], ['angle', '-a'], ['randomness', '-r']
    ];
    for (const [key, flag] of flagOrder) {
      if (!keep.has(key)) continue;
      const value = readValue(key);
      if (value !== SPEC.defaults[key]) parts.push(flag, fmt(value));
    }

    if (keep.has('interval_file')) {
      const chosen = el.intervalFile && el.intervalFile.files[0];
      parts.push('-f', chosen ? './UploadedImages/' + chosen.name : '<interval-image>');
    }

    el.commandLine.textContent = parts.join(' ');
  }

  function refresh() {
    applyRelevance();
    syncOutputs();
    buildCommand();
  }

  /* ---------------- File intake ---------------- */

  function acceptFile(file) {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      warn('That file is not an image. Pick a jpg, png, webp, bmp, gif or tiff.');
      return;
    }
    selectedFile = file;
    clearWarning();

    el.fileName.textContent = `${file.name} — ${(file.size / 1024).toFixed(0)} KB`;
    el.fileName.hidden = false;

    const reader = new FileReader();
    reader.onload = e => {
      el.originalImg.src = e.target.result;
      el.originalImg.hidden = false;
      const empty = el.originalBox.querySelector('.viewport__empty');
      if (empty) empty.remove();
    };
    reader.readAsDataURL(file);

    el.sortButton.disabled = false;
    el.sortButton.querySelector('.btn__sub').textContent = 'ready';
    buildCommand();
  }

  function warn(message) {
    el.alert.textContent = message;
    el.alert.hidden = false;
  }
  function clearWarning() { el.alert.hidden = true; }

  /* ---------------- Run ---------------- */

  async function runSort() {
    if (!selectedFile) return;
    clearWarning();

    const form = new FormData();
    form.append('image', selectedFile);
    form.append('interval_function', el.interval.value);
    form.append('sorting_function', el.sorting.value);
    for (const key of SLIDERS) form.append(key, String(readValue(key)));

    const chosenInterval = el.intervalFile && el.intervalFile.files[0];
    if (chosenInterval) form.append('interval_file', chosenInterval);

    el.working.hidden = false;
    el.workingText.textContent = 'SORTING…';
    el.sortButton.disabled = true;
    el.sortButton.querySelector('.btn__sub').textContent = 'working';

    try {
      const response = await fetch('/api/sort', { method: 'POST', body: form });
      const data = await response.json();

      if (!response.ok || !data.ok) throw new Error(data.error || 'The sort did not complete.');

      el.resultImg.src = data.url + '?v=' + Date.now();
      el.resultImg.hidden = false;
      const empty = el.resultBox.querySelector('.viewport__empty');
      if (empty) empty.remove();

      el.download.href = data.url;
      el.download.hidden = false;

      // Replace the preview with the command the server actually executed.
      el.commandLine.textContent = data.command;
      el.sortButton.querySelector('.btn__sub').textContent =
        `done in ${(data.duration_ms / 1000).toFixed(1)}s — sort again?`;

    } catch (error) {
      warn(error.message);
      el.sortButton.querySelector('.btn__sub').textContent = 'ready';
    } finally {
      el.working.hidden = true;
      el.sortButton.disabled = false;
    }
  }

  /* ---------------- Modal ---------------- */

  function openHelp() {
    el.helpModal.hidden = false;
    el.helpModal.querySelector('.btn--x').focus();
  }
  function closeHelp() {
    el.helpModal.hidden = true;
    el.helpOpen.focus();
  }

  /* ---------------- Wiring ---------------- */

  el.pick.addEventListener('click', e => { e.stopPropagation(); el.fileInput.click(); });
  el.dropzone.addEventListener('click', () => el.fileInput.click());
  el.dropzone.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.fileInput.click(); }
  });
  el.fileInput.addEventListener('change', e => acceptFile(e.target.files[0]));

  ['dragenter', 'dragover'].forEach(type =>
    el.dropzone.addEventListener(type, e => {
      e.preventDefault();
      el.dropzone.classList.add('is-over');
    })
  );
  ['dragleave', 'drop'].forEach(type =>
    el.dropzone.addEventListener(type, e => {
      e.preventDefault();
      el.dropzone.classList.remove('is-over');
    })
  );
  el.dropzone.addEventListener('drop', e => {
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    acceptFile(file);
  });
  // Stop the browser from navigating away if a drop misses the target.
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => e.preventDefault());

  el.interval.addEventListener('change', refresh);
  el.sorting.addEventListener('change', refresh);
  for (const key of SLIDERS) {
    const input = document.getElementById(key);
    if (input) input.addEventListener('input', () => { syncOutputs(); buildCommand(); });
  }
  if (el.intervalFile) el.intervalFile.addEventListener('change', buildCommand);

  el.sortButton.addEventListener('click', runSort);
  el.helpOpen.addEventListener('click', openHelp);
  el.helpModal.querySelectorAll('[data-close]').forEach(node =>
    node.addEventListener('click', closeHelp)
  );
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !el.helpModal.hidden) closeHelp();
  });

  refresh();
})();
