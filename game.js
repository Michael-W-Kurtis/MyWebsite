/* Unity WebGL bootstrap.
 *
 * Loading is behind a Play button on purpose. A Unity build is typically tens of
 * megabytes; starting that download the instant someone opens the page is rude,
 * and on a phone it is expensive.
 *
 * Unity changed its WebGL API in 2020 and the two are not interchangeable:
 *   up to 2019.x  UnityLoader.js  -> UnityLoader.instantiate(container, json)
 *   2020 onward   *.loader.js     -> createUnityInstance(canvas, config)
 * The server detects which generation the build is; this branches on it.
 */

(function () {
  'use strict';

  const CONFIG = JSON.parse(document.getElementById('gameConfig').textContent);
  const BASE = window.GAME_BASE;

  const el = id => document.getElementById(id);
  const overlay = el('overlay');
  const loading = el('loading');
  const errorBox = el('loadError');
  const errorText = el('errorText');
  const barFill = el('barFill');
  const loadPct = el('loadPct');
  const playButton = el('playButton');
  const fullscreenButton = el('fullscreenButton');
  const container = el('unityContainer');

  let instance = null;

  function progress(value) {
    const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
    barFill.style.width = pct + '%';
    loadPct.textContent = pct + '%';
  }

  function fail(message) {
    loading.hidden = true;
    errorBox.hidden = false;
    errorText.textContent = message;
    console.error('[game]', message);
  }

  function ready(unityInstance) {
    instance = unityInstance;
    loading.hidden = true;
    fullscreenButton.disabled = false;
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const tag = document.createElement('script');
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = () => reject(new Error(`Could not load ${src}`));
      document.body.appendChild(tag);
    });
  }

  async function startLegacy() {
    await loadScript(`${BASE}/${CONFIG.loader}`);
    if (typeof UnityLoader === 'undefined') {
      throw new Error('UnityLoader.js loaded but did not define UnityLoader.');
    }
    // instantiate() builds its own canvas inside the container element.
    UnityLoader.instantiate('unityContainer', `${BASE}/${CONFIG.manifest}`, {
      onProgress: (unityInstance, value) => {
        progress(value);
        if (value === 1) ready(unityInstance);
      },
      Module: {
        onRuntimeInitialized: () => { loading.hidden = true; }
      }
    });
  }

  async function startModern() {
    const canvas = document.createElement('canvas');
    canvas.id = 'unity-canvas';
    canvas.className = 'stage__canvas';
    container.appendChild(canvas);

    await loadScript(`${BASE}/${CONFIG.loader}`);
    if (typeof createUnityInstance === 'undefined') {
      throw new Error('loader.js loaded but did not define createUnityInstance.');
    }
    const unityInstance = await createUnityInstance(canvas, {
      dataUrl: `${BASE}/${CONFIG.data}`,
      frameworkUrl: `${BASE}/${CONFIG.framework}`,
      codeUrl: `${BASE}/${CONFIG.code}`,
      streamingAssetsUrl: `${BASE}/StreamingAssets`,
      companyName: 'Unknown',
      productName: document.title,
      productVersion: '1.0'
    }, progress);
    ready(unityInstance);
  }

  async function start() {
    overlay.hidden = true;
    loading.hidden = false;
    try {
      if (CONFIG.generation === 'modern') await startModern();
      else await startLegacy();
    } catch (error) {
      fail(error.message + ' — check the browser console; a stalled progress bar '
         + 'usually means the build files are served without the right '
         + 'Content-Encoding header.');
    }
  }

  playButton.addEventListener('click', start);

  fullscreenButton.addEventListener('click', () => {
    if (instance && typeof instance.SetFullscreen === 'function') {
      instance.SetFullscreen(1);
    } else {
      const frame = el('stageFrame');
      if (frame.requestFullscreen) frame.requestFullscreen();
    }
  });
})();
