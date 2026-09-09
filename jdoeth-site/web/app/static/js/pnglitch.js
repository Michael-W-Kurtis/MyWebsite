/* PNGlitch Wrapper.
 *
 * The Ruby preview is built here from the same operation catalogue the server
 * generates from (app/pnglitch_spec.py, serialised into #glitchSpec), so what
 * you read before pressing GLITCH is what the server writes to disk and runs.
 * After a run the preview is replaced by the server's own copy of the script,
 * which is the authoritative one.
 */

(function () {
  'use strict';

  const SPEC = JSON.parse(document.getElementById('glitchSpec').textContent);
  const OPS = Object.fromEntries(SPEC.operations.map(o => [o.id, o]));
  const FILTER_BLURB = Object.fromEntries(SPEC.filterTypes.map(f => [f.value, f.blurb]));

  const el = id => document.getElementById(id);
  const dom = {
    dropzone: el('dropzone'), fileInput: el('fileInput'), pick: el('pickButton'),
    fileName: el('fileName'), converted: el('convertedNote'),
    filterType: el('filterType'), filterHint: el('filterHint'),
    opParams: el('opParams'), seed: el('seed'), reroll: el('rerollButton'),
    originalImg: el('originalImg'), originalBox: el('originalBox'),
    resultImg: el('resultImg'), resultBox: el('resultBox'), working: el('working'),
    resultBar: el('resultBar'), statusBadge: el('statusBadge'),
    downloadLink: el('downloadLink'), blankNote: el('blankNote'),
    rescue: el('rescueButton'), rescueNote: el('rescueNote'),
    scriptView: el('scriptView'), alert: el('alert'), run: el('runButton'),
    helpOpen: el('helpOpen'), helpModal: el('helpModal')
  };

  let file = null;
  let opId = SPEC.operations[0].id;
  let lastResultName = null;

  /* ---------------- parameter widgets ---------------- */

  function renderParams() {
    const op = OPS[opId];
    dom.opParams.innerHTML = '';

    for (const spec of op.params) {
      const wrap = document.createElement('div');
      wrap.className = 'param';

      if (spec.type === 'select') {
        wrap.innerHTML =
          `<label class="param__label" for="p_${spec.name}">${spec.label}</label>
           <div class="selectwrap"><select id="p_${spec.name}">` +
          spec.options.map(o =>
            `<option value="${o.value}"${o.value === spec.default ? ' selected' : ''}>${o.label}</option>`
          ).join('') + `</select></div>`;
      } else {
        wrap.innerHTML =
          `<label class="param__label" for="p_${spec.name}">${spec.label}
             <output class="param__value" id="p_${spec.name}Out"></output></label>
           <input class="dial" type="range" id="p_${spec.name}"
                  min="${spec.min}" max="${spec.max}" step="${spec.step || 1}"
                  value="${spec.default}" data-unit="${spec.unit || ''}">`;
      }
      dom.opParams.appendChild(wrap);
    }

    // Filter type is meaningless for the operation that *is* filter manipulation.
    document.querySelector('.param[data-role="filter"]').hidden = !op.filterable;
    document.querySelector('.param[data-role="seed"]').hidden = !op.seeded;

    for (const spec of op.params) {
      const input = el('p_' + spec.name);
      if (input) input.addEventListener('input', () => { syncOutputs(); preview(); });
      if (input && input.tagName === 'SELECT') input.addEventListener('change', preview);
    }
    syncOutputs();
  }

  function syncOutputs() {
    for (const spec of OPS[opId].params) {
      const input = el('p_' + spec.name), out = el('p_' + spec.name + 'Out');
      if (input && out) out.textContent = input.value + (input.dataset.unit || '');
    }
  }

  function readParams() {
    const out = {};
    for (const spec of OPS[opId].params) {
      const input = el('p_' + spec.name);
      if (!input) continue;
      out[spec.name] = spec.type === 'select' ? input.value : parseInt(input.value, 10);
    }
    return out;
  }

  /* ---------------- Ruby preview ---------------- */

  const rb = s => "'" + String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";

  function body(op, p, seed) {
    switch (op) {
      case 'substitute':
        return `  png.glitch do |data|\n    data.gsub(/${SPEC.patterns[p.pattern].regex}/, ${rb(p.replacement)})\n  end`;
      case 'random_bytes':
        return `  png.glitch do |data|\n    rng = Random.new(${seed})\n    ${p.count}.times do\n      data.setbyte(rng.rand(data.size), rng.rand(256))\n    end\n    data\n  end`;
      case 'chunk_swap':
        return `  png.glitch do |data|\n    rng = Random.new(${seed})\n    block = ${p.block}\n    blocks = data.size / block\n    ${p.swaps}.times do\n      a = rng.rand(blocks) * block\n      b = rng.rand(blocks) * block\n      held = data[a, block]\n      data[a, block] = data[b, block]\n      data[b, block] = held\n    end\n    data\n  end`;
      case 'scanline_repeat':
        return `  start_row = (png.height * ${p.start_pct} / 100.0).to_i\n  held = nil\n  row = 0\n  png.each_scanline do |line|\n    held = line.data if row == start_row\n    if !held.nil? && row > start_row && row <= start_row + ${p.span}\n      line.replace_data held\n    end\n    row += 1\n  end`;
      case 'scanline_shuffle':
        return `  rows = []\n  png.each_scanline { |line| rows << line.data }\n  rng = Random.new(${seed})\n  order = (0...rows.size).to_a\n  ${p.swaps}.times do\n    a = rng.rand(rows.size)\n    b = rng.rand(rows.size)\n    order[a], order[b] = order[b], order[a]\n  end\n  row = 0\n  png.each_scanline do |line|\n    line.replace_data rows[order[row]]\n    row += 1\n  end`;
      case 'filter_band':
        return `  types = [0, 1, 2, 3, 4]\n  row = 0\n  png.each_scanline do |line|\n    line.graft types[(row / ${p.period}) % types.size]\n    row += 1\n  end`;
      case 'after_compress':
        return `  png.glitch_after_compress do |data|\n    rng = Random.new(${seed})\n    ${p.count}.times do\n      data.setbyte(rng.rand(data.size), rng.rand(256))\n    end\n    data\n  end`;
      default:
        return '  # ?';
    }
  }

  function preview() {
    const op = OPS[opId];
    const lines = ["require 'pnglitch'", '', "PNGlitch.open('./input.png') do |png|"];
    if (op.filterable && dom.filterType.value !== 'keep') {
      lines.push(`  png.change_all_filters PNGlitch::Filter::${dom.filterType.value.toUpperCase()}`);
    }
    lines.push(body(opId, readParams(), parseInt(dom.seed.value, 10) || 0));
    lines.push("  png.save './glitched.png'", 'end');
    dom.scriptView.textContent = lines.join('\n');
    dom.filterHint.textContent = FILTER_BLURB[dom.filterType.value] || '';
  }

  /* ---------------- file intake ---------------- */

  function accept(chosen) {
    if (!chosen) return;
    if (!chosen.type.startsWith('image/')) {
      warn('That file is not an image.');
      return;
    }
    file = chosen;
    clearWarn();
    dom.fileName.textContent = `${chosen.name} — ${(chosen.size / 1024).toFixed(0)} KB`;
    dom.fileName.hidden = false;

    const reader = new FileReader();
    reader.onload = e => {
      dom.originalImg.src = e.target.result;
      dom.originalImg.hidden = false;
      const empty = dom.originalBox.querySelector('.viewport__empty');
      if (empty) empty.remove();
    };
    reader.readAsDataURL(chosen);

    dom.run.disabled = false;
    dom.run.querySelector('.btn__sub').textContent = 'ready';
  }

  function warn(m) { dom.alert.textContent = m; dom.alert.hidden = false; }
  function clearWarn() { dom.alert.hidden = true; }

  /* ---------------- run ---------------- */

  async function run() {
    if (!file) return;
    clearWarn();
    dom.rescueNote.hidden = true;
    dom.rescue.hidden = true;

    const form = new FormData();
    form.append('image', file);
    form.append('operation', opId);
    form.append('filter_type', OPS[opId].filterable ? dom.filterType.value : 'keep');
    form.append('seed', dom.seed.value || '1');
    for (const [k, v] of Object.entries(readParams())) form.append(k, String(v));

    dom.working.hidden = false;
    dom.run.disabled = true;
    dom.run.querySelector('.btn__sub').textContent = 'working';

    try {
      const response = await fetch('/api/pnglitch', { method: 'POST', body: form });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'The glitch failed.');

      dom.resultImg.src = data.url + '?v=' + Date.now();
      dom.resultImg.hidden = false;
      const empty = dom.resultBox.querySelector('.viewport__empty');
      if (empty) empty.remove();

      if (data.original_url) dom.originalImg.src = data.original_url;

      lastResultName = data.url.split('/').pop();
      dom.downloadLink.href = data.url;
      dom.statusBadge.textContent = ({
        valid: 'valid png',
        decodable: 'malformed but decodable',
        undecodable: 'undecodable'
      })[data.status] || data.status;
      dom.statusBadge.dataset.status = data.status;
      dom.resultBar.hidden = false;
      dom.blankNote.hidden = false;
      dom.rescue.hidden = false;
      dom.converted.hidden = !data.converted;
      dom.scriptView.textContent = data.script;

      dom.run.querySelector('.btn__sub').textContent = data.cached
        ? 'served from cache — change something or re-roll'
        : `done in ${(data.duration_ms / 1000).toFixed(1)}s`;

    } catch (error) {
      warn(error.message);
      dom.run.querySelector('.btn__sub').textContent = 'ready';
    } finally {
      dom.working.hidden = true;
      dom.run.disabled = false;
    }
  }

  async function rescue() {
    if (!lastResultName) return;
    dom.rescue.disabled = true;
    dom.rescueNote.hidden = true;
    try {
      const response = await fetch('/api/pnglitch/rescue/' + encodeURIComponent(lastResultName));
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Could not decode it.');
      dom.resultImg.src = data.url + '?v=' + Date.now();
      dom.rescueNote.textContent =
        'Showing a re-encoded copy. The download button still gives you the raw glitched file.';
      dom.rescueNote.hidden = false;
    } catch (error) {
      dom.rescueNote.textContent = error.message;
      dom.rescueNote.hidden = false;
    } finally {
      dom.rescue.disabled = false;
    }
  }

  /* ---------------- wiring ---------------- */

  dom.pick.addEventListener('click', e => { e.stopPropagation(); dom.fileInput.click(); });
  dom.dropzone.addEventListener('click', () => dom.fileInput.click());
  dom.dropzone.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); dom.fileInput.click(); }
  });
  dom.fileInput.addEventListener('change', e => accept(e.target.files[0]));
  ['dragenter', 'dragover'].forEach(t => dom.dropzone.addEventListener(t, e => {
    e.preventDefault(); dom.dropzone.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach(t => dom.dropzone.addEventListener(t, e => {
    e.preventDefault(); dom.dropzone.classList.remove('is-over');
  }));
  dom.dropzone.addEventListener('drop', e => accept(e.dataTransfer.files[0]));
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => e.preventDefault());

  for (const button of document.querySelectorAll('.op')) {
    button.addEventListener('click', () => {
      opId = button.dataset.op;
      for (const other of document.querySelectorAll('.op')) {
        const on = other === button;
        other.classList.toggle('is-on', on);
        other.setAttribute('aria-checked', String(on));
      }
      renderParams();
      preview();
    });
  }

  dom.filterType.addEventListener('change', preview);
  dom.seed.addEventListener('input', preview);
  dom.reroll.addEventListener('click', () => {
    dom.seed.value = String(Math.floor(Math.random() * 999999) + 1);
    preview();
    if (file) run();
  });
  dom.run.addEventListener('click', run);
  dom.rescue.addEventListener('click', rescue);

  dom.helpOpen.addEventListener('click', () => {
    dom.helpModal.hidden = false;
    dom.helpModal.querySelector('.btn--x').focus();
  });
  dom.helpModal.querySelectorAll('[data-close]').forEach(n =>
    n.addEventListener('click', () => { dom.helpModal.hidden = true; dom.helpOpen.focus(); }));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !dom.helpModal.hidden) {
      dom.helpModal.hidden = true; dom.helpOpen.focus();
    }
  });

  renderParams();
  preview();
})();
