/* Landing page behaviour: the signature spectrum, and the Pixelsort Me action. */

(function () {
  'use strict';

  /* ---------- Signature: a strip that sorts itself once, on load ---------- */

  function buildSpectrum() {
    const strip = document.getElementById('spectrum');
    if (!strip) return;

    const COUNT = 56;
    const hues = [];
    for (let i = 0; i < COUNT; i++) {
      // A hue ramp from cobalt through violet to warm pink: the accent colour
      // family, pulled apart into its component pixels.
      hues.push(226 + (i / COUNT) * 110);
    }

    const shuffled = hues.slice();
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }

    const frag = document.createDocumentFragment();
    hues.forEach((sortedHue, i) => {
      const bar = document.createElement('span');
      const light = 46 + (i / COUNT) * 26;
      bar.style.setProperty('--from', `hsl(${shuffled[i].toFixed(0)} 62% ${light.toFixed(0)}%)`);
      bar.style.setProperty('--to', `hsl(${sortedHue.toFixed(0)} 68% ${light.toFixed(0)}%)`);
      bar.style.transitionDelay = `${(i / COUNT) * 340 + 220}ms`;
      frag.appendChild(bar);
    });
    strip.appendChild(frag);

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) {
      strip.classList.add('is-sorted');
    } else {
      requestAnimationFrame(() => requestAnimationFrame(() => strip.classList.add('is-sorted')));
    }
  }

  /* ---------- Pixelsort Me! ---------- */

  const el = {
    button:   document.getElementById('pixelsortMe'),
    reset:    document.getElementById('resetPortrait'),
    result:   document.getElementById('portraitResult'),
    veil:     document.getElementById('portraitVeil'),
    status:   document.getElementById('portraitStatus'),
    angle:    document.getElementById('angle'),
    angleOut: document.getElementById('angleOut'),
    sorting:  document.getElementById('sorting'),
    readout:  document.getElementById('portraitReadout'),
    command:  document.getElementById('portraitCommand'),
    timing:   document.getElementById('portraitTiming')
  };

  function syncAngle() {
    if (el.angle && el.angleOut) el.angleOut.textContent = `${el.angle.value}\u00B0`;
  }

  function busy(on, message) {
    if (!el.veil) return;
    el.veil.hidden = !on;
    if (message && el.status) el.status.textContent = message;
    if (el.button) el.button.disabled = on;
  }

  async function runSort() {
    busy(true, 'Sorting\u2026');

    try {
      const response = await fetch('/api/pixelsort-me', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          angle: Number(el.angle.value),
          sorting_function: el.sorting.value
        })
      });

      const data = await response.json();

      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'The sort did not complete.');
      }

      // Decode before revealing so the crossfade never shows a half-painted image.
      await new Promise((resolve, reject) => {
        el.result.onload = resolve;
        el.result.onerror = () => reject(new Error('The result image could not be loaded.'));
        el.result.src = data.url + '?v=' + Date.now();
      });

      el.result.hidden = false;
      el.result.alt = 'The portrait, pixel sorted';
      requestAnimationFrame(() => el.result.classList.add('is-visible'));

      el.command.textContent = data.command;
      el.timing.textContent = data.cached
        ? 'served from cache'
        : `${(data.duration_ms / 1000).toFixed(1)}s`;
      el.readout.hidden = false;
      el.reset.hidden = false;
      el.button.textContent = 'Sort again';
      busy(false);

    } catch (error) {
      busy(true, error.message);
      el.veil.classList.add('is-error');
      if (el.button) el.button.disabled = false;
      setTimeout(() => {
        busy(false);
        el.veil.classList.remove('is-error');
      }, 4000);
    }
  }

  function showOriginal() {
    el.result.classList.remove('is-visible');
    el.reset.hidden = true;
    el.button.textContent = 'Pixelsort Me!';
    setTimeout(() => { el.result.hidden = true; }, 420);
  }

  buildSpectrum();
  syncAngle();
  if (el.angle) el.angle.addEventListener('input', syncAngle);
  if (el.button) el.button.addEventListener('click', runSort);
  if (el.reset) el.reset.addEventListener('click', showOriginal);
})();
