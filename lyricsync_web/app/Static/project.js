// static/project.js
const projectImagesListeners = [];
let projectImagesCache = null;
let projectImagesPromise = null;
let projectSelectionSet = new Set();
const projectSelectionListeners = [];
let projectSelectionOrder = [];
let storyPipelineHeld = false;
let loraListEl = null;
let loraStatusEl = null;
function ensureLoraElements() {
  if (!loraListEl) {
    loraListEl = document.getElementById('lora-list');
  }
  if (!loraStatusEl) {
    loraStatusEl = document.getElementById('lora-status');
  }
}

const lightboxCtx = {
  root: null,
  img: null,
  closeBtn: null,
  initialized: false,
};

function ensureLightboxElements() {
  if (lightboxCtx.initialized) {
    return;
  }
  lightboxCtx.root = document.getElementById('imageLightbox');
  lightboxCtx.img = lightboxCtx.root ? lightboxCtx.root.querySelector('img') : null;
  lightboxCtx.closeBtn = document.getElementById('lightboxClose');
  if (!lightboxCtx.root || !lightboxCtx.img) {
    return;
  }
  const hide = () => {
    lightboxCtx.root.setAttribute('hidden', 'hidden');
    lightboxCtx.img.src = '';
  };
  if (lightboxCtx.closeBtn) {
    lightboxCtx.closeBtn.addEventListener('click', hide);
  }
  lightboxCtx.root.addEventListener('click', (evt) => {
    if (evt.target === lightboxCtx.root) {
      hide();
    }
  });
  document.addEventListener('keydown', (evt) => {
    if (evt.key === 'Escape' && lightboxCtx.root && !lightboxCtx.root.hasAttribute('hidden')) {
      hide();
    }
  });
  lightboxCtx.initialized = true;
}

function openLightbox(src) {
  ensureLightboxElements();
  if (!lightboxCtx.root || !lightboxCtx.img) return;
  lightboxCtx.img.src = src;
  lightboxCtx.root.removeAttribute('hidden');
}

function closeLightbox() {
  if (!lightboxCtx.root || !lightboxCtx.img) return;
  lightboxCtx.root.setAttribute('hidden', 'hidden');
  lightboxCtx.img.src = '';
}

function getProjectSlug() {
  return window.slug || window.currentProjectSlug || '';
}

function getStorySlotsEndpoint() {
  return `/api/projects/${encodeURIComponent(getProjectSlug())}/image_story_slots`;
}

const STORY_SLOTS_ENDPOINT = getStorySlotsEndpoint();
window.STORY_SLOTS_ENDPOINT = STORY_SLOTS_ENDPOINT;
const DEFAULT_IMAGE_STYLE = 'photorealistic';
const PIPELINE_PRECISION = 'fp16';
const STYLE_HINTS = {
  photorealistic: {
    prompt: 'ultra detailed photorealistic photography, realistic lighting, 35mm, UHD',
    instruction: 'photorealistic, detailed photography with lifelike lighting',
  },
  stylized: {
    prompt: 'stylized concept art, painterly brush strokes, dramatic lighting',
    instruction: 'stylized concept art with painterly strokes',
  },
  anime: {
    prompt: 'anime illustration, cel shading, expressive lighting, vibrant colors',
    instruction: 'anime illustration with cel shading and vibrant colors',
  },
  animated: {
    prompt: 'animated work, 2d hand-drawn aesthetic, vibrant colors, expressive',
    instruction: 'animated work, 2d hand-drawn aesthetic, vibrant colors, expressive',
  },
  landscape: {
    prompt: 'epic wide landscape, sweeping vista, detailed environment, golden hour',
    instruction: 'epic wide environmental landscape shot',
  },
};

const ImageGeneratorAPI = {};

const CLIP_MAX_TOKENS = 9999;
function clipTruncate(text) {
  const raw = typeof text === 'string' ? text.trim() : '';
  if (!raw) {
    return { text: raw, truncated: false };
  }
  const tokens = raw.split(/\s+/).filter(Boolean);
  if (tokens.length <= CLIP_MAX_TOKENS) {
    return { text: raw, truncated: false };
  }
  return {
    text: tokens.slice(0, CLIP_MAX_TOKENS).join(' '),
    truncated: true,
  };
}

function notifyProjectImages(list) {
  const clone = Array.isArray(list) ? list.slice() : [];
  projectImagesCache = clone;
  for (const cb of projectImagesListeners) {
    try {
      cb(clone);
    } catch (err) {
      console.error(err);
    }
  }
}

function notifyProjectSelection(list) {
  const clone = Array.isArray(list) ? list.slice() : [];
  projectSelectionSet = new Set(clone);
  projectSelectionOrder = clone.slice();
  for (const cb of projectSelectionListeners) {
    try {
      cb(clone.slice ? clone.slice() : clone);
    } catch (err) {
      console.error(err);
    }
  }
}

function onProjectImagesUpdated(callback) {
  if (typeof callback === 'function') {
    projectImagesListeners.push(callback);
    if (Array.isArray(projectImagesCache)) {
      try {
        callback(projectImagesCache.slice());
      } catch (err) {
        console.error(err);
      }
    }
  }
}

function onProjectSelectionUpdated(callback) {
  if (typeof callback === 'function') {
    projectSelectionListeners.push(callback);
    if (projectSelectionSet.size) {
      try {
        callback(Array.from(projectSelectionSet));
      } catch (err) {
        console.error(err);
      }
    }
  }
}

async function fetchProjectImages(force = false) {
  if (!force && projectImagesCache !== null) {
    return projectImagesCache.slice();
  }
  if (!force && projectImagesPromise) {
    return projectImagesPromise;
  }
  const slugValue = window.slug || window.currentProjectSlug || '';
  const target = `/api/projects/${encodeURIComponent(slugValue)}/images`;
  const fetcher = (async () => {
    try {
      const res = await fetch(target);
      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      const list = Array.isArray(data?.images) ? data.images : [];
      const selected = Array.isArray(data?.selected) ? data.selected : [];
      notifyProjectImages(list);
      notifyProjectSelection(selected);
      return list;
    } catch (err) {
      console.error(err);
      notifyProjectImages([]);
      notifyProjectSelection([]);
      return [];
    } finally {
      projectImagesPromise = null;
    }
  })();
  if (!force) {
    projectImagesPromise = fetcher;
  }
  return fetcher;
}

function buildProjectDownloadUrl(relPath) {
  if (!relPath) return '';
  const slugValue = window.slug || window.currentProjectSlug || '';
  const cleaned = String(relPath).replace(/^[./\\]+/, '');
  const segments = cleaned.split(/[\\/]/).map((part) => encodeURIComponent(part));
  return `/api/projects/${encodeURIComponent(slugValue)}/download/${segments.join('/')}`;
}

async function deleteProjectImageRequest(relPath) {
  const slugValue = window.slug || window.currentProjectSlug || '';
  const res = await fetch(`/api/projects/${encodeURIComponent(slugValue)}/images`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: relPath }),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok || !data || !data.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
}

async function deleteAllProjectImages() {
  const slugValue = window.slug || window.currentProjectSlug || '';
  const res = await fetch(`/api/projects/${encodeURIComponent(slugValue)}/images/all`, {
    method: 'DELETE',
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok || !data || !data.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  notifyProjectSelection([]);
  return data;
}

async function saveProjectImageSelection(paths) {
  const slugValue = window.slug || window.currentProjectSlug || '';
  const res = await fetch(`/api/projects/${encodeURIComponent(slugValue)}/images/selection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok || !data || !data.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  notifyProjectSelection(Array.isArray(data.selected) ? data.selected : paths);
}

(() => {
  const $ = (sel) => document.querySelector(sel);

  const logAlignEl = $('#logAlign');
  const logRenderEl = $('#logRender');
  const btnAlign = $('#btnAlign');
  const btnRender = $('#btnRender');

  const logOffsets = {
    alignOffset: 0,
    renderOffset: 0,
  };
  let alignTimer = null;
  let renderTimer = null;
  let renderCompleteSeen = false;
  let renderIdleMisses = 0;
  let renderForceCheck = false;
  const renderStatusEl = document.getElementById('render-status');

  // ---------- persistence ----------
  const LS_KEY = (slug) => `proj:${slug}:settings`;
  const LORA_KEY = (slug) => `proj:${slug}:loras`;
  const STORY_STYLE_KEY = (slug) => `proj:${slug}:story-style`;
  const STORY_PROMPTS_KEY = (slug) => `proj:${slug}:story-prompts`;
  const LLM_MODEL_KEY = (slug) => `proj:${slug}:ollama-model`;

  // All controls we persist (add here if you introduce more)
  const CONTROL_IDS = [
    // checkboxes
    'ui-show-title', 'ui-use-mp3-title', 'ui-show-end-card',
    // selects
    'ui-style', 'ui-theme', 'ui-font-file',
    // text / number inputs
    'ui-font-family', 'ui-font-size', 'ui-outline',
    'ui-end-card-text', 'ui-end-card-seconds',
    // color inputs
    'ui-font-color', 'ui-outline-color', 'ui-endcard-color', 'ui-endcard-border',
    // effect controls
    'ui-effect', 'ui-effect-strength', 'ui-effect-cycle', 'ui-fps',
    'ui-kenburns-zoom', 'ui-kenburns-pan',
    // image playback controls
    'ui-img-duration', 'ui-img-fade', 'ui-img-playback',
  ];
  const TX_CONTROL_IDS = [
    'ui-tx-model',
    'ui-tx-language',
    'ui-tx-device',
    'ui-tx-compute',
    'ui-tx-highlight',
  ];
  CONTROL_IDS.push(...TX_CONTROL_IDS);

  const THEME_DEFAULT_SLUG = 'default';
  let themeCache = [];
  let suppressThemeChange = false;

  function getById(id) {
    return document.getElementById(id);
  }
  window.getById = getById;

  function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function readValue(id, fallback) {
    const el = getById(id);
    if (!el || typeof el.value === "undefined" || el.value === null) return fallback;
    return el.value;
  }

  function readInt(id, fallback) {
    const parsed = parseInt(readValue(id, ''), 10);
    return isFiniteNumber(parsed) ? parsed : fallback;
  }

  function readFloat(id, fallback) {
    const parsed = parseFloat(readValue(id, ''));
    return isFiniteNumber(parsed) ? parsed : fallback;
  }

  function readCheckbox(id, fallback) {
    const el = getById(id);
    if (!el || typeof el.checked === "undefined") return fallback;
    return !!el.checked;
  }
  function readSettings() {
    try {
      const raw = localStorage.getItem(LS_KEY(slug));
      return raw ? JSON.parse(raw) : {};
    } catch { return {}; }
  }
  window.readSettings = readSettings;
  function writeSettings(s) {
    try { localStorage.setItem(LS_KEY(slug), JSON.stringify(s)); } catch { }
  }
  function gatherSettingsFromUI() {
    const s = {};
    for (const id of CONTROL_IDS) {
      const el = document.getElementById(id);
      if (!el) continue;
      if (el.type === 'checkbox') s[id] = !!el.checked;
      else s[id] = el.value;
    }
    return s;
  }
  function applySettingsToUI(s) {
    for (const id of CONTROL_IDS) {
      const el = document.getElementById(id);
      if (!el || !(id in s)) continue;
      if (el.type === 'checkbox') el.checked = !!s[id];
      else el.value = s[id];
    }
  }
  function attachPersistenceHandlers() {
    const save = () => writeSettings(gatherSettingsFromUI());
    for (const id of CONTROL_IDS) {
      const el = document.getElementById(id);
      if (!el) continue;
      const evt = (el.tagName === 'SELECT' || el.type === 'color' || el.type === 'checkbox') ? 'change' : 'input';
      el.addEventListener(evt, save);
    }
  }

  function setControlValue(id, value) {
    const el = getById(id);
    if (!el) return;
    const eventName = (el.tagName === 'SELECT' || el.type === 'color') ? 'change' : 'input';
    const assigned = value == null ? '' : String(value);
    if (el.value === assigned) {
      el.dispatchEvent(new Event(eventName, { bubbles: true }));
    } else {
      el.value = assigned;
      el.dispatchEvent(new Event(eventName, { bubbles: true }));
    }
  }

  function normalizeThemeKey(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  }

  function getThemeSelect() {
    return document.getElementById('ui-theme');
  }

  function getThemeStatusEl() {
    return document.getElementById('theme-status');
  }

  function setThemeStatus(message, isError = false) {
    const el = getThemeStatusEl();
    if (!el) return;
    el.textContent = message || '';
    el.style.color = isError ? '#ff9b9b' : '';
  }

  function getDefaultThemeSlug() {
    const select = getThemeSelect();
    const metaDefault = select?.dataset?.default;
    return metaDefault ? normalizeThemeKey(metaDefault) : THEME_DEFAULT_SLUG;
  }

  function getEffectiveThemeSelection() {
    const raw = readValue('ui-theme', '');
    const normalized = normalizeThemeKey(raw);
    if (!normalized) return '';
    const defaultSlug = getDefaultThemeSlug();
    if (normalized === defaultSlug) return '';
    return raw;
  }

  function findThemeByKey(key) {
    const normalized = normalizeThemeKey(key);
    return themeCache.find((theme) => normalizeThemeKey(theme.slug || theme.name) === normalized);
  }

  function populateThemeOptions() {
    const select = getThemeSelect();
    if (!select) return;
    const previous = select.value;
    select.innerHTML = '';
    if (!themeCache.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No themes';
      select.appendChild(opt);
      select.value = '';
      return;
    }
    themeCache.forEach((theme) => {
      const opt = document.createElement('option');
      opt.value = theme.slug || normalizeThemeKey(theme.name);
      opt.textContent = theme.name || opt.value;
      select.appendChild(opt);
    });
    let desired = previous;
    if (!desired) {
      desired = select.dataset?.default || THEME_DEFAULT_SLUG;
    }
    if (!findThemeByKey(desired)) {
      desired = themeCache[0]?.slug || '';
    }
    suppressThemeChange = true;
    select.value = desired || '';
    suppressThemeChange = false;
  }

  async function loadThemes() {
    setThemeStatus('');
    try {
      const res = await fetch('/api/themes');
      const data = await res.json();
      themeCache = Array.isArray(data?.themes) ? data.themes : [];
    } catch (err) {
      console.error('loadThemes failed:', err);
      themeCache = [];
      setThemeStatus('Failed to load themes', true);
    }
    populateThemeOptions();
    return themeCache.slice();
  }

  function setFontFileSelection(value) {
    const select = document.getElementById('ui-font-file');
    if (!select) return;
    const target = value || '';
    if (target && !Array.from(select.options).some((opt) => opt.value === target)) {
      select.value = '';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      setThemeStatus(`Font file "${target}" missing`, true);
      return;
    }
    select.value = target;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function applyThemeToUI(theme, options = {}) {
    if (!theme) return;
    const select = getThemeSelect();
    if (select) {
      const desired = theme.slug || normalizeThemeKey(theme.name);
      if (select.value !== desired) {
        suppressThemeChange = true;
        select.value = desired;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        suppressThemeChange = false;
      }
    }
    setControlValue('ui-font-family', theme.font || '');
    setFontFileSelection(theme.font_file_name || '');
    setControlValue('ui-font-size', theme.font_size);
    setControlValue('ui-outline', theme.outline);
    setControlValue('ui-font-color', theme.font_color);
    setControlValue('ui-outline-color', theme.outline_color);
    setControlValue('ui-endcard-color', theme.thanks_color);
    setControlValue('ui-endcard-border', theme.thanks_border_color);
    if (typeof window.updateFontPreview === 'function') {
      window.updateFontPreview();
    }
    if (!options.silent) {
      setThemeStatus(`Applied theme "${theme.name}"`);
    }
  }

  function gatherThemeFromUI(name) {
    return {
      name,
      font: readValue('ui-font-family', 'Arial'),
      font_file_name: readValue('ui-font-file', '') || null,
      font_size: readInt('ui-font-size', 20),
      outline: readInt('ui-outline', 2),
      font_color: readValue('ui-font-color', '#FFFFFF'),
      outline_color: readValue('ui-outline-color', '#000000'),
      thanks_color: readValue('ui-endcard-color', '#FFFFFF'),
      thanks_border_color: readValue('ui-endcard-border', '#000000'),
    };
  }

  async function saveThemeFromUI() {
    const select = getThemeSelect();
    const current = select && select.value ? findThemeByKey(select.value) : null;
    const suggestion = current ? current.name : 'New Theme';
    const name = (prompt('Theme name', suggestion) || '').trim();
    if (!name) return;
    setThemeStatus('Saving theme…');
    try {
      const res = await fetch('/api/themes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(gatherThemeFromUI(name)),
      });
      const data = await res.json();
      if (!res.ok || !data?.ok) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      const saved = data.theme;
      await loadThemes();
      if (saved?.slug) {
        applyThemeToUI(saved, { silent: true });
      }
      setThemeStatus(`Saved theme "${saved?.name || name}"`);
    } catch (err) {
      console.error('saveThemeFromUI failed:', err);
      setThemeStatus(err.message || 'Failed to save theme', true);
    }
  }

  async function deleteTheme(slug) {
    if (!slug) return;
    if (normalizeThemeKey(slug) === THEME_DEFAULT_SLUG) {
      setThemeStatus('Default theme cannot be deleted.', true);
      return;
    }
    if (!window.confirm('Delete this theme?')) return;
    setThemeStatus('Deleting theme…');
    try {
      const res = await fetch(`/api/themes/${encodeURIComponent(slug)}`, { method: 'DELETE' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      await loadThemes();
      const select = getThemeSelect();
      if (select) {
        suppressThemeChange = true;
        select.value = THEME_DEFAULT_SLUG;
        suppressThemeChange = false;
        const fallback = findThemeByKey(select.value);
        if (fallback) {
          setThemeStatus('Deleted theme. Default applied.');
          applyThemeToUI(fallback, { silent: true });
        }
      }
    } catch (err) {
      console.error('deleteTheme failed:', err);
      setThemeStatus(err.message || 'Failed to delete theme', true);
    }
  }

  function attachThemeActions() {
    const select = getThemeSelect();
    if (select) {
      select.addEventListener('change', () => {
        if (suppressThemeChange) return;
        const theme = findThemeByKey(select.value);
        if (theme) {
          applyThemeToUI(theme);
        }
      });
    }
    const saveBtn = document.getElementById('btnSaveTheme');
    if (saveBtn) {
      saveBtn.addEventListener('click', (e) => {
        e.preventDefault();
        saveThemeFromUI();
      });
    }
    const deleteBtn = document.getElementById('btnDeleteTheme');
    if (deleteBtn) {
      deleteBtn.addEventListener('click', (e) => {
        e.preventDefault();
        const selectEl = getThemeSelect();
        deleteTheme(selectEl ? selectEl.value : '');
      });
    }
  }

  // ---------- logging & polling ----------
  function appendLog(el, text) {
    if (!el) return;
    el.textContent += text;
    el.scrollTop = el.scrollHeight;
  }

  async function pollLog(job, el, offsetRefName) {
    try {
      const offsetParam = Number(logOffsets[offsetRefName] || 0);
      const res = await fetch(`/api/projects/${slug}/logs/${job}?offset=${offsetParam}`);
      if (!res.ok) return; // do not spam errors
      const data = await res.json();
      const { offset, chunk } = data;
      let detectedComplete = false;
      if (chunk) {
        appendLog(el, chunk);
        if (job === 'render') {
          const match = chunk.match(/Encoding preview:.*?(\d+)%/i);
          if (match && match[1]) {
            if (renderStatusEl) renderStatusEl.textContent = `Rendering... ${match[1]}%`;
            if (typeof showToast === "function") showToast(`Rendering Preview... ${match[1]}%`, 0, "render-toast");
          }
        }
        // react to milestones
        if (job === 'align' && /\u005bComplete\u005d/.test(chunk)) {
          // enable Render button immediately after align completes
          if (btnRender) btnRender.disabled = false;
          // optionally: bump srt_name setting to 'aligned.srt' now that we have it
          const s = readSettings();
          s['srt_name'] = 'aligned.srt'; // harmless if your server ignores; your default is edited.srt
          writeSettings(s);
        }
        if (job === 'render') {
          renderIdleMisses = 0;
          detectedComplete = /\u005bComplete\u005d/.test(chunk)
            || /Files ready/i.test(chunk)
            || /Encoding preview:\s+100%/.test(chunk);
        }
      }
      logOffsets[offsetRefName] = offset;

      // If render log stops changing, stop polling (and refresh if we already saw Complete)
      if (job === 'render') {
        if (detectedComplete) {
          renderCompleteSeen = true;
          // Toast: Render Complete
          if (typeof showToast === 'function') showToast("Render Complete!", 4000, "render-toast", "toast-success");
        }
        if (!chunk) {
          renderIdleMisses += 1;
        }
        // After several idle polls, force-read the whole log once to look for [Complete]
        if (!renderForceCheck && renderIdleMisses >= 6 && offset > 0) {
          renderForceCheck = true;
          try {
            const full = await fetch(`/api/projects/${slug}/logs/${job}?offset=0`).then(r => r.json()).catch(() => null);
            const fullChunk = full && full.chunk ? full.chunk : '';
            if (/\u005bComplete\u005d/.test(fullChunk)) {
              renderCompleteSeen = true;
              if (typeof showToast === 'function') showToast("Render Complete!", 4000, "render-toast", "toast-success");
            }
          } catch { }
        }
        if (renderCompleteSeen || renderIdleMisses >= 10) {
          stopRenderPolling();
          if (renderStatusEl) {
            renderStatusEl.textContent = renderCompleteSeen ? 'Render complete. Refreshing preview...' : 'Render finished.';
          }
          setPreview(`/api/projects/${slug}/download/preview.mp4`);
          try { window.location.reload(); } catch { }
        }
      }
    } catch { } // ignore errors during polling
  }

  // Toast Utility
  window.showToast = function (msg, duration = 3000, id = null, extraClass = "") {
    let container = document.getElementById('toast-container');
    if (!container) return; // base.html should have this

    // If ID provided, try to update existing
    let toast = id ? document.getElementById(id) : null;

    if (!toast) {
      toast = document.createElement('div');
      toast.className = `toast ${extraClass}`;
      if (id) toast.id = id;
      container.appendChild(toast);
      // Force reflow
      void toast.offsetWidth;
      toast.classList.add('show');
    } else {
      // Update existing
      toast.className = `toast show ${extraClass}`;
    }

    toast.textContent = msg;

    // Clear any existing timeout if we are updating
    if (toast.dataset.timer) {
      clearTimeout(parseInt(toast.dataset.timer, 10));
    }

    if (duration > 0) {
      const timer = setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => {
          if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 300);
      }, duration);
      toast.dataset.timer = timer;
    }
  };

  function startAlignPolling() {
    stopAlignPolling();
    alignTimer = setInterval(() => pollLog('align', logAlignEl, 'alignOffset'), 750);
  }
  function stopAlignPolling() { if (alignTimer) { clearInterval(alignTimer); alignTimer = null; } }

  function startRenderPolling() {
    stopRenderPolling();
    renderTimer = setInterval(() => pollLog('render', logRenderEl, 'renderOffset'), 750);
  }
  function stopRenderPolling() { if (renderTimer) { clearInterval(renderTimer); renderTimer = null; } }

  // ---------- API calls ----------
  async function loadFonts() {
    try {
      const r = await fetch('/api/fonts');
      if (!r.ok) {
        console.error("Fonts API error:", r.status, await r.text());
        return;
      }
      const j = await r.json();
      const sel = document.getElementById("ui-font-file");
      if (!sel) { console.warn("No #ui-font-file element on page"); return; }

      // reset list (keep “— none —” at index 0 if your HTML has one)
      for (let i = sel.options.length - 1; i >= 1; i--) sel.remove(i);

      if (Array.isArray(j.fonts) && j.fonts.length) {
        j.fonts.forEach(name => {
          const opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          sel.appendChild(opt);
        });
      } else {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "(no fonts found in app/fonts)";
        sel.appendChild(opt);
      }

      // apply saved value after we’ve populated options
      const saved = readSettings()['ui-font-file'];
      if (saved != null) sel.value = saved;
      if (typeof window.updateFontPreview === "function") {
        window.updateFontPreview();
      }

    } catch (e) {
      console.error("loadFonts failed:", e);
    }
  }


  function toggleTxDrawer(open) {
    const drawer = document.getElementById('txDrawer');
    const backdrop = document.getElementById('txDrawerBackdrop');
    if (!drawer || !backdrop) return;
    const shouldOpen = open !== undefined ? open : !drawer.classList.contains('open');
    drawer.classList.toggle('open', shouldOpen);
    backdrop.classList.toggle('active', shouldOpen);
    drawer.setAttribute('aria-hidden', shouldOpen ? 'false' : 'true');
  }

  function initTxDrawer() {
    const openBtn = document.getElementById('btnTxSettings');
    const closeBtn = document.getElementById('btnTxClose');
    const backdrop = document.getElementById('txDrawerBackdrop');
    if (openBtn) openBtn.addEventListener('click', () => toggleTxDrawer(true));
    if (closeBtn) closeBtn.addEventListener('click', () => toggleTxDrawer(false));
    if (backdrop) backdrop.addEventListener('click', () => toggleTxDrawer(false));
    document.addEventListener('keydown', (evt) => {
      if (evt.key === 'Escape') toggleTxDrawer(false);
    });
  }

  function buildTranscriptionPayload() {
    return {
      model_size: readValue('ui-tx-model', 'large-v2'),
      language: readValue('ui-tx-language', 'auto'),
      device: readValue('ui-tx-device', 'auto'),
      compute_type: readValue('ui-tx-compute', 'float16'),
      enable_word_highlight: readCheckbox('ui-tx-highlight', false),
    };
  }


  async function callAlign() {
    if (btnAlign) btnAlign.setAttribute('disabled', '');
    appendLog(logAlignEl, `\n=== Align request @ ${new Date().toLocaleTimeString()} ===\n`);
    logOffsets.alignOffset = 0;

    try {
      const res = await fetch(`/api/projects/${slug}/align`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildTranscriptionPayload()),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        appendLog(logAlignEl, `ERROR ${res.status}: ${txt || 'request failed'}\n`);
        if (btnAlign) btnAlign.removeAttribute('disabled');
        return;
      }
      const data = await res.json().catch(() => ({}));
      const pid = data && data.pid !== undefined && data.pid !== null ? data.pid : 'n/a';
      appendLog(logAlignEl, `Started align (pid: ${pid})\n`);
      startAlignPolling();
    } catch (e) {
      appendLog(logAlignEl, `JS error: ${e}\n`);
      if (btnAlign) btnAlign.removeAttribute('disabled');
    }
  }
  // ==========================================================
  // One canonical payload builder
  // ==========================================================
  function getStorySlotsForRender() {
    const source = Array.isArray(window.storySlots) ? window.storySlots : [];
    return source
      .map((slot) => normalizeStorySlot(slot))
      .filter(Boolean)
      .map((slot) => {
        const start = typeof slot.start === 'number' && Number.isFinite(slot.start) ? slot.start : null;
        const end = typeof slot.end === 'number' && Number.isFinite(slot.end) ? slot.end : null;
        const imagePath = typeof slot.image_path === 'string' && slot.image_path.trim()
          ? slot.image_path.trim()
          : null;
        return {
          prompt: slot.prompt,
          start,
          end,
          image_path: imagePath,
        };
      });
  }

  const THEME_TO_PAYLOAD_FIELDS = [
    ['font', 'font'],
    ['font_file_name', 'font_file_name'],
    ['font_size', 'font_size'],
    ['outline', 'outline'],
    ['font_color', 'font_color'],
    ['outline_color', 'outline_color'],
    ['thanks_color', 'endcard_color'],
    ['thanks_border_color', 'endcard_border_color'],
  ];

  function normalizeValue(val) {
    if (val == null) return '';
    if (typeof val === 'number') return Number(val);
    return String(val).trim().toLowerCase();
  }

  function themeOverridesPayload(theme, payload) {
    if (!theme || !payload) return false;
    return THEME_TO_PAYLOAD_FIELDS.some(([themeKey, payloadKey]) => {
      if (!(themeKey in theme)) return false;
      const themeVal = theme[themeKey];
      const payloadVal = payload[payloadKey];
      const themeIsNumber = typeof themeVal === 'number' || typeof payloadVal === 'number';
      if (themeIsNumber) {
        const a = Number(themeVal);
        const b = Number(payloadVal);
        if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
        return Math.abs(a - b) > 1e-9;
      }
      return normalizeValue(themeVal) !== normalizeValue(payloadVal);
    });
  }

  function buildRenderPayload() {
    const stored = readSettings();
    const imagePayload = {
      clip_seconds: readFloat('ui-img-duration', 6),
      fade_seconds: readFloat('ui-img-fade', 1),
      playback: readValue('ui-img-playback', 'story'),
    };
    const storySlots = getStorySlotsForRender();
    if (imagePayload.playback === 'story' && storySlots.length) {
      imagePayload.story_slots = storySlots;
    }
    const payload = {
      style: readValue('ui-style', 'burn-srt'),
      text_theme: getEffectiveThemeSelection(),
      font: readValue('ui-font-family', 'Arial'),
      font_size: readInt('ui-font-size', 20),
      outline: readInt('ui-outline', 2),
      ass_align: 2,
      margin_v: 20,
      force_res: '1920:1080',
      srt_name: stored.srt_name || 'edited.srt',
      no_burn: false,
      show_title: readCheckbox('ui-show-title', false),
      title_from_mp3: readCheckbox('ui-use-mp3-title', false),
      show_end_card: readCheckbox('ui-show-end-card', true),
      end_card_text: readValue('ui-end-card-text', 'Thank You for Watching'),
      end_card_seconds: readFloat('ui-end-card-seconds', 5),
      font_color: readValue('ui-font-color', '#FFFFFF'),
      outline_color: readValue('ui-outline-color', '#000000'),
      endcard_color: readValue('ui-endcard-color', '#FFFFFF'),
      endcard_border_color: readValue('ui-endcard-border', '#000000'),
      font_file_name: (function () {
        const el = getById('ui-font-file');
        if (!el || typeof el.value !== "string") return null;
        const trimmed = el.value.trim();
        return trimmed ? trimmed : null;
      })(),
      // --- effects ---
      effect: readValue('ui-effect', 'none'),
      effect_strength: readFloat('ui-effect-strength', 0.08),
      effect_cycle: readFloat('ui-effect-cycle', 12),
      effect_zoom: readFloat('ui-kenburns-zoom', 0.12),
      effect_pan: readFloat('ui-kenburns-pan', 0.35),
      fps: readInt('ui-fps', 30),
      image: imagePayload,
    };

    if (payload.text_theme) {
      const theme = findThemeByKey(payload.text_theme);
      if (theme && themeOverridesPayload(theme, payload)) {
        payload.text_theme = '';
      }
    }
    return payload;
  }
  window.buildRenderPayload = buildRenderPayload;

  // In project.js, replace callRender() with this version
  async function fetchStorySlotsInner() {
    const endpoint = getStorySlotsEndpoint();
    try {
      const res = await fetch(`${endpoint}?ts=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json().catch(() => null);
      const slots = Array.isArray(data?.slots) ? data.slots : [];
      window.storySlots = slots
        .map(normalizeStorySlot)
        .filter(Boolean);
      return window.storySlots;
    } catch (err) {
      console.warn('Failed to load story slots for render:', err);
      return window.storySlots || [];
    }
  }
  window.__projectFetchStorySlots = fetchStorySlotsInner;

  async function callRender() {
    const btnRender = document.getElementById('btnRender');

    try {
      if (btnRender) btnRender.setAttribute('disabled', '');
      // reset the render log + offset so we tail from the top
      if (logRenderEl) logRenderEl.textContent = '';
      appendLog(logRenderEl, `\n=== Render request @ ${new Date().toLocaleTimeString()} ===\n`);
      logOffsets.renderOffset = 0;

      // Build payload
      await fetchStorySlots();
      const payload = window.buildRenderPayload();

      // Clamp/sanitize numeric effect fields
      const clamp = (n, min, max) => {
        const v = Number(n);
        return isFiniteNumber(v) ? Math.min(max, Math.max(min, v)) : min;
      };
      payload.effect_strength = Math.round(clamp(payload.effect_strength, 0, 0.5) * 100) / 100;
      payload.effect_cycle = clamp(payload.effect_cycle, 1, 9999);
      payload.effect_zoom = Math.round(clamp(payload.effect_zoom, 0.01, 0.6) * 100) / 100;
      payload.effect_pan = Math.round(clamp(payload.effect_pan, 0, 1) * 100) / 100;
      payload.fps = Math.round(clamp(payload.fps, 1, 60));

      // For backend compatibility: also send nested "effects"
      payload.effects = {
        effect: payload.effect,
        strength: payload.effect_strength,
        cycle: payload.effect_cycle,
        zoom: payload.effect_zoom,
        pan: payload.effect_pan,
        fps: payload.fps,
      };

      console.log('[Render payload]', payload);

      const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/render`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !data || !data.ok) {
        const detail = data && data.detail ? data.detail : 'Render failed';
        throw new Error(detail);
      }
      const jobId = data.job_id !== undefined && data.job_id !== null ? data.job_id : 'n/a';
      appendLog(logRenderEl, `Render started (job: ${jobId})\n`);
      if (typeof showToast === 'function') showToast("Render Started...", 2000, "render-toast");
      startRenderPolling();
      // Start tailing render logs; preview refresh happens on completion
      // Do not call setPreview() here; pollLog handles the update
      // when it sees "[Complete]" in the render log.
    } catch (err) {
      console.error(err);
      appendLog(logRenderEl, `Error: ${err.message}\n`);
      if (typeof showToast === 'function') showToast(`Error: ${err.message}`, 5000, "render-toast", "toast-error");
    } finally {
      if (btnRender) btnRender.removeAttribute('disabled');
    }
  }


  window.normalizeStorySlot = function (entry) {
    if (!entry || typeof entry !== 'object') return null;
    const prompt = String(entry.prompt || '').trim();
    if (!prompt) return null;
    const toNumber = (value) => {
      if (value === null || value === undefined) return null;
      const num = Number(value);
      return Number.isFinite(num) ? num : null;
    };
    return {
      prompt,
      start: toNumber(entry.start),
      end: toNumber(entry.end),
      image_path: typeof entry.image_path === 'string' && entry.image_path.trim()
        ? entry.image_path.trim()
        : null,
    };
  };

  function normalizeStorySlot(entry) {
    return window.normalizeStorySlot(entry);
  }

  // ---------- init ----------
  (async function init() {
    // 1) populate font + theme lists so saved selections can be applied
    await loadFonts();
    await loadThemes();

    // 2) hydrate from localStorage and start persisting changes
    applySettingsToUI(readSettings());
    if (typeof window.updateFontPreview === "function") {
      window.updateFontPreview();
    }
    attachPersistenceHandlers();
    attachThemeActions();

    // 3) wire buttons
    if (btnAlign) {
      btnAlign.addEventListener('click', (e) => { e.preventDefault(); callAlign(); });
    }
    const btnPasteLyricsTop = document.getElementById('btnPasteLyricsTop');
    if (btnPasteLyricsTop) {
      btnPasteLyricsTop.addEventListener('click', (e) => {
        e.preventDefault();
        document.getElementById('btnPasteLyrics')?.click();
      });
    }
    if (btnRender) {
      btnRender.addEventListener('click', (e) => { e.preventDefault(); callRender(); });
    }
    initTxDrawer();
    initCoverUploader();
    initImageGenerator();
    initBatchImageUpload();
    initImageLLM();

    // 4) try to show existing preview immediately (will no-op if missing)
    const link = document.querySelector('#ui-video-open');
    if (link && /preview\.mp4/.test(link.href || '')) {
      setPreview(link.href);
    }
  })();

  function initBatchImageUpload() {
    const fileInput = document.getElementById('img-batch-files');
    const uploadBtn = document.getElementById('img-batch-upload');
    const statusEl = document.getElementById('img-batch-status');
    if (!fileInput || !uploadBtn) return;

    const setStatus = (msg, isError) => {
      if (!statusEl) return;
      statusEl.textContent = msg || '';
      statusEl.style.color = isError ? '#ff8f8f' : '#9cd7ff';
    };

    fileInput.addEventListener('change', () => {
      if (!fileInput.files || !fileInput.files.length) {
        setStatus('', false);
        return;
      }
      const names = Array.from(fileInput.files).map((f) => f.name).slice(0, 3);
      const more = fileInput.files.length - names.length;
      setStatus(names.join(', ') + (more > 0 ? ` (+${more} more)` : ''), false);
    });

    uploadBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      if (!fileInput.files || !fileInput.files.length) {
        setStatus('Choose at least one image.', true);
        return;
      }
      const data = new FormData();
      Array.from(fileInput.files).forEach((file) => data.append('files', file));
      uploadBtn.disabled = true;
      setStatus('Uploading...', false);
      try {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/images/upload`, {
          method: 'POST',
          body: data,
        });
        const json = await res.json().catch(() => null);
        if (!res.ok || !json?.ok) {
          throw new Error(json?.detail || json?.errors?.join('; ') || `HTTP ${res.status}`);
        }
        const count = Array.isArray(json.saved) ? json.saved.length : 0;
        setStatus(count ? `Uploaded ${count} image${count === 1 ? '' : 's'}.` : 'No images saved.', false);
        fileInput.value = '';
        await fetchProjectImages(true);
      } catch (err) {
        setStatus(err?.message || 'Upload failed.', true);
      } finally {
        uploadBtn.disabled = false;
      }
    });
  }

  function initCoverUploader() {
    const fileInput = document.getElementById('cover-file');
    const uploadBtn = document.getElementById('btnCoverUpload');
    const statusEl = document.getElementById('cover-status');
    const coverContainer = document.getElementById('cover-generated-container');
    const coverGallery = document.getElementById('cover-generated');
    if (!fileInput || !uploadBtn) {
      return;
    }

    function setStatus(message, isError) {
      if (!statusEl) return;
      statusEl.textContent = message || '';
      statusEl.style.color = isError ? '#ff8f8f' : '#9cd7ff';
    }

    fileInput.addEventListener('change', function () {
      if (!fileInput.files || !fileInput.files.length) {
        setStatus('', false);
        return;
      }
      setStatus(fileInput.files[0].name, false);
    });

    uploadBtn.addEventListener('click', async function (e) {
      e.preventDefault();
      const files = fileInput.files;
      if (!files || !files.length) {
        setStatus('Choose an image first.', true);
        return;
      }

      const data = new FormData();
      data.append('cover', files[0]);
      uploadBtn.disabled = true;
      setStatus('Uploading...', false);

      try {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/cover`, {
          method: 'POST',
          body: data,
        });
        let payload = null;
        try {
          payload = await res.json();
        } catch {
          payload = null;
        }

        if (!res.ok || !payload || !payload.ok) {
          const detail = payload && payload.detail ? payload.detail : `HTTP ${res.status}`;
          throw new Error(detail);
        }

        setStatus('Background updated. Re-render to see it in the preview.', false);
        window.projectHasCover = true;
        if (coverContainer) {
          coverContainer.setAttribute('hidden', 'hidden');
        }
        fileInput.value = '';
      } catch (err) {
        setStatus(`Upload failed: ${err.message}`, true);
      } finally {
        uploadBtn.disabled = false;
      }
    });

    const setCoverFromImage = async (relPath) => {
      if (!relPath) return;
      setStatus('Applying generated image.', false);
      try {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/cover/from_image`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: relPath }),
        });
        let data = null;
        try {
          data = await res.json();
        } catch {
          data = null;
        }
        if (!res.ok || !data || !data.ok) {
          const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
          throw new Error(detail);
        }
        setStatus('Background updated from generated image.', false);
        window.projectHasCover = true;
        if (coverContainer) {
          coverContainer.setAttribute('hidden', 'hidden');
        }
      } catch (err) {
        setStatus(`Failed to apply image: ${err.message}`, true);
      }
    };

    const renderCoverChoices = (paths) => {
      if (!coverGallery || !coverContainer) return;
      if (window.projectHasCover) {
        coverContainer.setAttribute('hidden', 'hidden');
        return;
      }
      coverGallery.innerHTML = '';
      if (!paths || !paths.length) {
        coverContainer.setAttribute('hidden', 'hidden');
        return;
      }
      coverContainer.removeAttribute('hidden');
      const frag = document.createDocumentFragment();
      paths.forEach((relPath) => {
        const url = buildProjectDownloadUrl(relPath);
        if (!url) return;
        const card = document.createElement('div');
        card.className = 'image-thumb';
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.src = `${url}?ts=${Date.now()}`;
        img.alt = relPath;
        img.addEventListener('click', () => openLightbox(url));
        img.addEventListener('click', () => openLightbox(url));
        const actions = document.createElement('div');
        actions.className = 'image-actions';
        const useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.textContent = 'Use as Background';
        useBtn.addEventListener('click', () => setCoverFromImage(relPath));
        const selectWrap = document.createElement('label');
        selectWrap.className = 'select-toggle';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = projectSelectionSet.has(relPath);
        checkbox.addEventListener('change', () => {
          if (checkbox.checked) {
            projectSelectionSet.add(relPath);
            if (!projectSelectionOrder.includes(relPath)) {
              projectSelectionOrder.push(relPath);
            }
          } else {
            projectSelectionSet.delete(relPath);
            projectSelectionOrder = projectSelectionOrder.filter((p) => p !== relPath);
          }
          notifyProjectSelection(Array.from(projectSelectionSet));
        });
        selectWrap.appendChild(checkbox);
        selectWrap.appendChild(document.createTextNode('Use in render'));
        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'image-delete';
        delBtn.title = 'Delete image';
        delBtn.textContent = '🗑';
        delBtn.addEventListener('click', async () => {
          const confirmDelete = window.confirm('Delete this generated image?');
          if (!confirmDelete) return;
          setStatus('Deleting image...');
          try {
            await deleteProjectImageRequest(relPath);
            setStatus('Image deleted.');
            await fetchProjectImages(true);
          } catch (err) {
            setStatus(`Failed to delete image: ${err.message}`, true);
          }
        });
        actions.appendChild(useBtn);
        actions.appendChild(selectWrap);
        actions.appendChild(delBtn);
        card.appendChild(img);
        card.appendChild(actions);
        frag.appendChild(card);
      });
      coverGallery.appendChild(frag);
      updateSelectionActions();
    };

    if (coverGallery && !window.projectHasCover) {
      onProjectImagesUpdated(renderCoverChoices);
      onProjectSelectionUpdated(updateSelectionActions);
      fetchProjectImages();
    }

    const selectionSaveBtn = document.getElementById('cover-selection-save');
    const selectionStatus = document.getElementById('cover-selection-status');
    function updateSelectionActions() {
      const container = document.getElementById('cover-selection-actions');
      if (!container) return;
      if (window.projectHasCover) {
        container.setAttribute('hidden', 'hidden');
        return;
      }
      container.removeAttribute('hidden');
      if (selectionStatus) {
        selectionStatus.textContent = projectSelectionSet.size
          ? `${projectSelectionSet.size} image${projectSelectionSet.size === 1 ? '' : 's'} selected.`
          : 'Select images to include in renders.';
      }
    }

    if (selectionSaveBtn) {
      selectionSaveBtn.addEventListener('click', async () => {
        const paths = Array.from(projectSelectionSet);
        if (!paths.length) {
          if (selectionStatus) selectionStatus.textContent = 'Select at least one image first.';
          return;
        }
        if (selectionStatus) selectionStatus.textContent = 'Saving selection...';
        try {
          await saveProjectImageSelection(paths);
          if (selectionStatus) selectionStatus.textContent = 'Selection saved.';
        } catch (err) {
          if (selectionStatus) selectionStatus.textContent = `Save failed: ${err.message}`;
        }
      });
    }
  }

  function initImageGenerator() {
    const form = document.getElementById('img-form');
    const promptEl = document.getElementById('img-prompt');
    const negativeEl = document.getElementById('img-negative');
    const styleSelect = document.getElementById('img-style');
    const modelSelect = document.getElementById('img-model');
    const countInput = document.getElementById('img-count');
    const seedInput = document.getElementById('img-seed');
    const widthInput = null; // Removed
    const heightInput = null; // Removed
    const aspectSelect = document.getElementById('img-aspect');
    const resolutionSelect = document.getElementById('img-resolution');
    const stepsInput = document.getElementById('img-steps');
    const statusEl = document.getElementById('img-status');


    // Helper to calculate dimensions
    function getDimensions() {
      const ar = aspectSelect ? aspectSelect.value : '16:9';
      const baseRes = resolutionSelect ? parseInt(resolutionSelect.value, 10) : 1080;
      let w = 1024, h = 576;

      if (ar === '1:1') {
        w = baseRes;
        h = baseRes;
      } else if (ar === '9:16') {
        w = Math.round(baseRes * (9 / 16)); // This logic is tricky if baseRes is height.
        // Wait, "1080p" usually means 1080 vertical.
        // For 16:9 1080p -> 1920x1080
        // For 9:16 1080p -> 1080x1920? Or 608x1080?
        // Let's assume the user means the "major" dimension or vertical lines.
        // Standard convention: "1080p" = 1080px HEIGHT (for landscape).

        // Let's implement strict logic based on user request:
        // 480p -> 480 lines
        // 720p -> 720 lines
        // 1080p -> 1080 lines

        if (ar === '16:9') {
          h = baseRes;
          w = Math.round(h * (16 / 9));
        } else if (ar === '9:16') {
          // For vertical "1080p" video usually means 1080x1920 canvas? 
          // Or does it mean same pixel density?
          // Usually "1080p Shorts" means 1080x1920.
          // If baseRes is 1080 (the short side or the standard naming convention container):
          // Let's interpret "1080p" as the SHORTEST side for square/landscape, and WIDTH for portrait? 
          // No, "1080p" is almost always the vertical count (scan lines).

          // If I select 1080p (Quality) and 9:16:
          // Should it be 1080 wide x 1920 high? Yes that is "Full HD Portrait".
          // If I select 480p and 9:16 -> 480x854.

          w = baseRes; // width is the "p" count? No, usually p is height.
          // BUT for 9:16, usually we want high quality.
          // Let's stick to: "Resolution" = The smallest dimension (approx).
          // 1:1 1080p -> 1080x1080
          // 16:9 1080p -> 1920x1080
          // 9:16 1080p -> 1080x1920

          w = baseRes;
          h = Math.round(w * (16 / 9));
        }
      } else {
        // Default 16:9
        h = baseRes;
        w = Math.round(h * (16 / 9));
      }

      // Snap to multiples of 8 for SD
      w = Math.round(w / 8) * 8;
      h = Math.round(h / 8) * 8;
      return { w, h };
    }

    // ...

    const generateBtn = document.getElementById('img-generate-btn');
    const pipelineBtn = document.getElementById('img-pipeline-btn');
    const pipelineStatusEl = document.getElementById('img-pipeline-status');
    const galleryEl = document.getElementById('img-gallery');
    const selectionActions = document.getElementById('img-selection-actions');
    const selectionSaveBtn = document.getElementById('img-selection-save');
    const selectionStatus = document.getElementById('img-selection-status');
    const deleteAllBtn = document.getElementById('img-delete-all');
    const modelDirBtn = document.getElementById('img-model-dir-btn');
    const loraDirBtn = document.getElementById('img-lora-dir-btn');

    // Sub-menu logic
    const submenus = {
      'stylized': document.getElementById('submenu-stylized'),
      'anime': document.getElementById('submenu-anime'),
      'animated': document.getElementById('submenu-animated')
    };

    function updateStyleSubmenus() {
      if (!styleSelect) return;
      const val = styleSelect.value;
      Object.keys(submenus).forEach(key => {
        const el = submenus[key];
        if (el) {
          if (key === val) el.removeAttribute('hidden');
          else el.setAttribute('hidden', '');
        }
      });
    }

    if (styleSelect) {
      styleSelect.addEventListener('change', updateStyleSubmenus);
      // init
      updateStyleSubmenus();
    }
    const reorderBtn = document.getElementById('img-reorder-btn');
    const reorderModal = document.getElementById('reorderModal');
    const reorderList = document.getElementById('reorderList');
    const reorderSave = document.getElementById('reorderSave');
    const reorderCancel = document.getElementById('reorderCancel');
    const reorderStatus = document.getElementById('reorderStatus');

    if (!form || !modelSelect || !promptEl) {
      ImageGeneratorAPI.buildPayload = () => {
        throw new Error('Image generator UI unavailable.');
      };
      ImageGeneratorAPI.submitJob = async () => {
        throw new Error('Image generator UI unavailable.');
      };
      ImageGeneratorAPI.queryPipelineState = async () => {
        throw new Error('Image generator UI unavailable.');
      };
      ImageGeneratorAPI.setPipelineLoadedState = async () => {
        throw new Error('Image generator UI unavailable.');
      };
      return;
    }

    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const clampInt = (value, min, max) => {
      const num = Number(value);
      if (!Number.isFinite(num)) {
        return min;
      }
      return Math.max(min, Math.min(max, Math.round(num)));
    };

    const state = {
      models: [],
      loras: [],
      loraSelections: loadSavedLoras(),
    };

    function loadSavedLoras() {
      try {
        const raw = localStorage.getItem(LORA_KEY(slug));
        return raw ? JSON.parse(raw) : {};
      } catch {
        return {};
      }
    }

    function persistLoras() {
      try {
        localStorage.setItem(LORA_KEY(slug), JSON.stringify(state.loraSelections));
      } catch {
        /* ignore */
      }
    }

    function ensureLoraConfig(path) {
      if (!state.loraSelections[path]) {
        state.loraSelections[path] = { enabled: false, weight: 0.8 };
      }
      return state.loraSelections[path];
    }

    function getActiveLoras() {
      return Object.entries(state.loraSelections || {})
        .filter(([, cfg]) => cfg && cfg.enabled && Number.isFinite(Number(cfg.weight)))
        .map(([path, cfg]) => ({
          path,
          weight: Number(cfg.weight) || 0.8,
        }));
    }

    const setStatus = (message, isError = false) => {
      if (!statusEl) return;
      statusEl.textContent = message || '';
      statusEl.style.color = isError ? '#ff8f8f' : '#9cd7ff';
    };

    const setPipelineStatus = (message, isError = false) => {
      if (!pipelineStatusEl) return;
      pipelineStatusEl.textContent = message || '';
      pipelineStatusEl.style.color = isError ? '#ff8f8f' : '#9cd7ff';
    };

    const updateSelectionSummary = () => {
      if (!selectionStatus) return;
      const count = projectSelectionSet.size;
      selectionStatus.textContent = count
        ? `${count} image${count === 1 ? '' : 's'} selected.`
        : 'Select images to include when rendering.';
      selectionStatus.style.color = count ? '#9cd7ff' : '';
    };

    const updateSelectionVisibility = (paths) => {
      if (!selectionActions) return;
      if (Array.isArray(paths) && paths.length) {
        selectionActions.removeAttribute('hidden');
      } else {
        selectionActions.setAttribute('hidden', 'hidden');
      }
    };

    function getSelectedModelId() {
      return modelSelect.value || '';
    }

    function renderModelOptions(list) {
      state.models = Array.isArray(list) ? list.slice() : [];
      const previous = modelSelect.value;
      modelSelect.innerHTML = '';
      if (!state.models.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No models found';
        modelSelect.appendChild(opt);
        return;
      }
      state.models.forEach((entry) => {
        const opt = document.createElement('option');
        opt.value = entry.id;
        const label = entry.tags && entry.tags.length
          ? `${entry.id} (${entry.tags.join(', ')})`
          : entry.id;
        opt.textContent = label;
        opt.dataset.path = entry.path || '';
        modelSelect.appendChild(opt);
      });
      if (previous && state.models.some((m) => m.id === previous)) {
        modelSelect.value = previous;
      }
    }

    function renderLoraList(list) {
      ensureLoraElements();
      state.loras = Array.isArray(list) ? list.slice() : [];
      if (!loraListEl) {
        return;
      }
      loraListEl.innerHTML = '';
      if (!state.loras.length) {
        loraListEl.textContent = 'No LoRAs detected.';
        loraListEl.classList.add('muted');
        if (loraStatusEl) {
          loraStatusEl.textContent = 'Set the LoRA folder to enable adapters.';
          loraStatusEl.style.color = '#ffdf9c';
        }
        return;
      }
      loraListEl.classList.remove('muted');
      state.loras.forEach((path) => {
        const row = document.createElement('div');
        row.className = 'lora-row';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        const cfg = ensureLoraConfig(path);
        checkbox.checked = !!cfg.enabled;
        checkbox.addEventListener('change', () => {
          cfg.enabled = checkbox.checked;
          persistLoras();
        });
        const label = document.createElement('span');
        label.textContent = path.split(/[\\/]/).pop() || path;
        label.style.flex = '1';
        const weight = document.createElement('input');
        weight.type = 'number';
        weight.step = '0.1';
        weight.min = '0';
        weight.max = '2';
        weight.className = 'lora-weight';
        weight.value = cfg.weight ?? 0.8;
        weight.addEventListener('change', () => {
          cfg.weight = Number(weight.value) || 0.8;
          persistLoras();
        });
        row.appendChild(checkbox);
        row.appendChild(label);
        row.appendChild(weight);
        loraListEl.appendChild(row);
      });
      if (loraStatusEl) {
        loraStatusEl.textContent = `${state.loras.length} adapter${state.loras.length === 1 ? '' : 's'} available.`;
        loraStatusEl.style.color = '';
      }
    }

    async function loadModelData() {
      try {
        setStatus('Loading models...');
        const res = await fetch('/api/models/list');
        const data = await res.json().catch(() => null);
        if (!res.ok || !data) {
          throw new Error(`HTTP ${res.status}`);
        }
        renderModelOptions(data.models || []);
        renderLoraList(data.loras || []);
        setStatus('');
      } catch (err) {
        console.error('Failed to load models', err);
        setStatus(err.message || 'Failed to load models', true);
        renderModelOptions([]);
        renderLoraList([]);
      }
    }

    const renderImageGallery = (paths) => {
      if (!galleryEl) return;
      galleryEl.innerHTML = '';
      if (!Array.isArray(paths) || !paths.length) {
        galleryEl.innerHTML = '<p class="muted">No generated images yet.</p>';
        updateSelectionVisibility([]);
        updateSelectionSummary();
        return;
      }
      updateSelectionVisibility(paths);
      const frag = document.createDocumentFragment();
      paths.forEach((relPath) => {
        const url = buildProjectDownloadUrl(relPath);
        const card = document.createElement('div');
        card.className = 'image-thumb';
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.src = `${url}?ts=${Date.now()}`;
        img.alt = relPath;
        img.addEventListener('click', () => openLightbox(url));
        card.appendChild(img);
        const actions = document.createElement('div');
        actions.className = 'image-actions';
        const selectWrap = document.createElement('label');
        selectWrap.className = 'select-toggle';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.dataset.path = relPath;
        checkbox.checked = projectSelectionSet.has(relPath);
        checkbox.addEventListener('change', () => {
          if (checkbox.checked) {
            projectSelectionSet.add(relPath);
          } else {
            projectSelectionSet.delete(relPath);
          }
          notifyProjectSelection(Array.from(projectSelectionSet));
        });
        selectWrap.appendChild(checkbox);
        selectWrap.appendChild(document.createTextNode('Use in render'));
        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'image-delete';
        delBtn.textContent = 'Delete';
        delBtn.addEventListener('click', async () => {
          if (!window.confirm('Delete this generated image?')) return;
          try {
            setStatus('Deleting image...');
            await deleteProjectImageRequest(relPath);
            await fetchProjectImages(true);
            setStatus('Image deleted.');
          } catch (err) {
            setStatus(err.message || 'Delete failed', true);
          }
        });
        actions.appendChild(selectWrap);
        actions.appendChild(delBtn);
        card.appendChild(actions);
        frag.appendChild(card);
      });
      galleryEl.appendChild(frag);
      updateSelectionSummary();
    };

    onProjectImagesUpdated(renderImageGallery);
    onProjectSelectionUpdated(() => {
      updateSelectionSummary();
      if (!galleryEl) return;
      const boxes = galleryEl.querySelectorAll('input[type="checkbox"][data-path]');
      boxes.forEach((checkbox) => {
        const rel = checkbox.dataset?.path;
        if (rel) {
          checkbox.checked = projectSelectionSet.has(rel);
        }
      });
    });
    fetchProjectImages();
    updateSelectionSummary();

    if (selectionSaveBtn) {
      selectionSaveBtn.addEventListener('click', async () => {
        const selections = projectSelectionOrder.filter((p) => projectSelectionSet.has(p));
        if (!selections.length) {
          if (selectionStatus) {
            selectionStatus.textContent = 'Select at least one image first.';
            selectionStatus.style.color = '#ff8f8f';
          }
          return;
        }
        if (selectionStatus) {
          selectionStatus.textContent = 'Saving selection...';
          selectionStatus.style.color = '';
        }
        try {
          await saveProjectImageSelection(selections);
          if (selectionStatus) {
            selectionStatus.textContent = 'Selection saved.';
            selectionStatus.style.color = '#9cd7ff';
          }
        } catch (err) {
          if (selectionStatus) {
            selectionStatus.textContent = err.message || 'Failed to save selection';
            selectionStatus.style.color = '#ff8f8f';
          }
        }
      });
    }

    function renderReorderList() {
      if (!reorderList) return;
      reorderList.innerHTML = '';
      const items = projectSelectionOrder.filter((p) => projectSelectionSet.has(p));
      if (!items.length) {
        reorderList.innerHTML = '<li class="muted">Select images first.</li>';
        return;
      }
      items.forEach((path) => {
        const li = document.createElement('li');
        li.className = 'reorder-item';
        li.draggable = true;
        li.dataset.path = path;
        li.textContent = path.split(/[\\\\/]/).pop() || path;
        li.addEventListener('dragstart', (e) => {
          e.dataTransfer.effectAllowed = 'move';
          e.dataTransfer.setData('text/plain', path);
          li.classList.add('dragging');
        });
        li.addEventListener('dragend', () => {
          li.classList.remove('dragging');
        });
        li.addEventListener('dragover', (e) => {
          e.preventDefault();
          const dragging = reorderList.querySelector('.dragging');
          if (!dragging || dragging === li) return;
          const rect = li.getBoundingClientRect();
          const before = (e.clientY - rect.top) < rect.height / 2;
          reorderList.insertBefore(dragging, before ? li : li.nextSibling);
        });
        reorderList.appendChild(li);
      });
    }

    function openReorderModal() {
      if (!reorderModal) return;
      renderReorderList();
      if (reorderStatus) reorderStatus.textContent = '';
      reorderModal.removeAttribute('hidden');
    }
    function closeReorderModal() {
      if (reorderModal) reorderModal.setAttribute('hidden', 'hidden');
    }

    if (reorderBtn && reorderModal) {
      reorderBtn.addEventListener('click', () => {
        if (!projectSelectionSet.size) {
          if (selectionStatus) {
            selectionStatus.textContent = 'Select images first, then reorder.';
            selectionStatus.style.color = '#ff8f8f';
          }
          return;
        }
        openReorderModal();
      });
    }
    if (reorderCancel) {
      reorderCancel.addEventListener('click', () => closeReorderModal());
    }
    if (reorderModal) {
      reorderModal.addEventListener('click', (e) => {
        if (e.target === reorderModal) closeReorderModal();
      });
    }
    if (reorderSave && reorderList) {
      reorderSave.addEventListener('click', async () => {
        const newOrder = Array.from(reorderList.querySelectorAll('.reorder-item'))
          .map((li) => li.dataset.path)
          .filter(Boolean);
        projectSelectionOrder = newOrder.slice();
        projectSelectionSet = new Set(newOrder);
        notifyProjectSelection(newOrder);
        try {
          await saveProjectImageSelection(newOrder);
          await applySelectedImagesToStoryPrompts({ silent: true });
          if (reorderStatus) {
            reorderStatus.textContent = 'Order saved.';
            reorderStatus.style.color = '#9cd7ff';
          }
          closeReorderModal();
          updateSelectionSummary();
        } catch (err) {
          if (reorderStatus) {
            reorderStatus.textContent = err.message || 'Failed to save order';
            reorderStatus.style.color = '#ff8f8f';
          }
        }
      });
    }

    if (deleteAllBtn) {
      deleteAllBtn.addEventListener('click', async () => {
        if (!window.confirm('Delete all generated images?')) return;
        deleteAllBtn.disabled = true;
        try {
          setStatus('Deleting all images...');
          await deleteAllProjectImages();
          await fetchProjectImages(true);
          setStatus('Deleted all images.');
        } catch (err) {
          setStatus(err.message || 'Failed to delete images', true);
        } finally {
          deleteAllBtn.disabled = false;
        }
      });
    }

    // Apply selected images to story slots in order
    async function applySelectedImagesToStoryPrompts(options = {}) {
      const { silent = false } = options;
      const ordered = projectSelectionOrder.filter((p) => projectSelectionSet.has(p));
      if (!storyPrompts.length) {
        if (!silent) {
          setStoryStatus('Generate story prompts first.', true);
        }
        return false;
      }
      if (!ordered.length) {
        if (!silent) {
          setStoryStatus('Select images first, then click apply.', true);
        }
        return false;
      }
      storyPrompts.forEach((entry, idx) => {
        entry.image_path = ordered[idx % ordered.length];
        entry.status = 'done';
      });
      renderStoryPrompts();
      if (!silent) {
        setStoryStatus('Images applied in selected order.', false);
      }
      return true;
    }

    const storyAssignBtn = document.getElementById('story-assign-images');
    if (storyAssignBtn) {
      storyAssignBtn.addEventListener('click', async () => {
        await applySelectedImagesToStoryPrompts();
      });
    }

    function updatePipelineButton() {
      if (!pipelineBtn) return;
      pipelineBtn.textContent = ImageGeneratorAPI.pipelineActive ? 'Release pipeline' : 'Preload pipeline';
    }

    ImageGeneratorAPI.buildPayload = (overrides = {}, options = {}) => {
      const modelId = overrides.model_id || getSelectedModelId();
      if (!modelId) {
        throw new Error('Select a model before generating images.');
      }
      const styleKey = overrides.style || (styleSelect ? styleSelect.value : DEFAULT_IMAGE_STYLE);
      const styleHint = STYLE_HINTS[styleKey] || STYLE_HINTS[DEFAULT_IMAGE_STYLE];
      const basePrompt = typeof overrides.prompt === 'string'
        ? overrides.prompt
        : (promptEl.value || '');
      const trimmedPrompt = basePrompt.trim();
      if (!trimmedPrompt) {
        throw new Error('Enter a prompt before generating images.');
      }
      let compositePrompt = trimmedPrompt;
      if (!options?.skipStyleHint && styleHint?.prompt) {
        compositePrompt = `${styleHint.prompt}\n\n${compositePrompt}`.trim();
      }
      const clipResult = clipTruncate(compositePrompt);
      const negative = typeof overrides.negative === 'string'
        ? overrides.negative
        : (negativeEl ? negativeEl.value : '');
      // Calculate dims
      const { w, h } = getDimensions();

      const payload = {
        slug,
        model_id: modelId,
        prompt: clipResult.text,
        negative_prompt: negative,
        width: clampInt(overrides.width ?? w, 256, 2048),
        height: clampInt(overrides.height ?? h, 256, 2048),
        steps: clampInt(overrides.steps ?? (stepsInput ? stepsInput.value : 28), 1, 80),
        guidance: typeof overrides.guidance === 'number' ? overrides.guidance : 6.5,
        seed: overrides.seed ?? (seedInput && seedInput.value ? Number(seedInput.value) : null),
        num_images: clampInt(overrides.count ?? (countInput ? countInput.value : 1), 1, 4),
        loras: Array.isArray(overrides.loras) ? overrides.loras : getActiveLoras(),
        precision: overrides.precision || PIPELINE_PRECISION,
        style: styleKey,
      };
      if (payload.seed !== null && !Number.isFinite(payload.seed)) {
        payload.seed = null;
      }
      return { payload, truncated: clipResult.truncated };
    };

    ImageGeneratorAPI.submitJob = async (payload, label = 'Image job', options = {}) => {
      const res = await fetch('/api/image/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok || !data?.job_id) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      const jobId = data.job_id;
      const notify = (msg) => {
        if (typeof options.onStatus === 'function') {
          options.onStatus(msg);
        }
      };
      notify(`${label} queued...`);
      while (true) {
        await sleep(1250);
        const statusRes = await fetch(`/api/image/status/${encodeURIComponent(jobId)}?ts=${Date.now()}`);
        const statusData = await statusRes.json().catch(() => null);
        if (!statusRes.ok || !statusData) {
          throw new Error('Failed to poll image status');
        }
        if (statusData.progress) {
          notify(`${label}: ${statusData.progress}`);
        }
        if (statusData.status === 'done') {
          notify(`${label}: completed.`);
          await fetchProjectImages(true);
          return statusData;
        }
        if (statusData.status === 'error' || statusData.status === 'unknown') {
          throw new Error(statusData.error || 'Image generation failed.');
        }
      }
    };

    ImageGeneratorAPI.queryPipelineState = async () => {
      const modelId = getSelectedModelId();
      if (!modelId) {
        throw new Error('Select a model first.');
      }
      const res = await fetch(`/api/image/pipeline/status?model_id=${encodeURIComponent(modelId)}&precision=${PIPELINE_PRECISION}`);
      const data = await res.json().catch(() => null);
      if (!res.ok || !data) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      ImageGeneratorAPI.pipelineActive = !!data.loaded;
      updatePipelineButton();
      return data;
    };

    ImageGeneratorAPI.setPipelineLoadedState = async (shouldLoad = true) => {
      const modelId = getSelectedModelId();
      if (!modelId) {
        throw new Error('Select a model first.');
      }
      const endpoint = shouldLoad ? '/api/image/pipeline/preload' : '/api/image/pipeline/release';
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId, precision: PIPELINE_PRECISION }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok || data?.ok === false) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      ImageGeneratorAPI.pipelineActive = shouldLoad && (!!data.ok || data.ok === undefined);
      updatePipelineButton();
      return data;
    };

    if (pipelineBtn) {
      pipelineBtn.addEventListener('click', async () => {
        if (!getSelectedModelId()) {
          setPipelineStatus('Select a model first.', true);
          return;
        }
        pipelineBtn.disabled = true;
        try {
          if (ImageGeneratorAPI.pipelineActive) {
            setPipelineStatus('Releasing pipeline...');
            await ImageGeneratorAPI.setPipelineLoadedState(false);
            setPipelineStatus('Pipeline released.');
          } else {
            setPipelineStatus('Preloading pipeline...');
            await ImageGeneratorAPI.setPipelineLoadedState(true);
            setPipelineStatus('Pipeline ready.');
          }
        } catch (err) {
          setPipelineStatus(err.message || 'Pipeline request failed', true);
        } finally {
          pipelineBtn.disabled = false;
        }
      });
    }

    form.addEventListener('submit', async (evt) => {
      evt.preventDefault();
      if (generateBtn) {
        generateBtn.disabled = true;
      }
      try {
        const payloadInfo = ImageGeneratorAPI.buildPayload();
        setStatus(payloadInfo.truncated ? 'Prompt truncated for CLIP, sending job...' : 'Submitting image job...');
        const result = await ImageGeneratorAPI.submitJob(payloadInfo.payload, 'Image job', {
          truncated: payloadInfo.truncated,
          onStatus: (msg) => setStatus(msg),
        });
        const count = result?.result?.images?.length || payloadInfo.payload.num_images || 0;
        setStatus(`Saved ${count} image${count === 1 ? '' : 's'}.`);
      } catch (err) {
        setStatus(err.message || 'Generation failed.', true);
      } finally {
        if (generateBtn) {
          generateBtn.disabled = false;
        }
      }
    });

    function initDirectoryModal() {
      const modal = document.getElementById('modelDirModal');
      const input = document.getElementById('model-dir-input');
      const saveBtn = document.getElementById('model-dir-save');
      const cancelBtn = document.getElementById('model-dir-cancel');
      const status = document.getElementById('model-dir-status');
      const titleEl = modal ? modal.querySelector('h3') : null;
      const descEl = modal ? modal.querySelector('p') : null;

      // Fallback if modal missing
      if (!modal || !input || !saveBtn || !cancelBtn || !status) {
        const setupFallback = (btn, type) => {
          btn?.addEventListener('click', async () => {
            const path = window.prompt(`Enter ${type.toUpperCase()} directory path`);
            if (!path) return;
            try {
              await saveDirectoryPath(type, path);
            } catch (err) {
              window.alert(err.message || 'Failed to save directory.');
            }
          });
        };
        setupFallback(modelDirBtn, 'model');
        setupFallback(loraDirBtn, 'lora');
        return;
      }

      let activeType = null;

      const closeModal = () => {
        modal.setAttribute('hidden', 'hidden');
        status.textContent = '';
        activeType = null;
      };

      cancelBtn.addEventListener('click', closeModal);
      modal.addEventListener('click', (evt) => {
        if (evt.target === modal) closeModal();
      });

      saveBtn.addEventListener('click', async () => {
        if (!activeType) return;
        const path = input.value.trim();
        if (!path) {
          status.textContent = 'Enter a directory path.';
          status.style.color = '#ff8f8f';
          return;
        }
        status.textContent = 'Saving...';
        status.style.color = '';
        try {
          await saveDirectoryPath(activeType, path);
          status.textContent = 'Saved.';
          status.style.color = '#9cd7ff';
          setTimeout(closeModal, 600);
        } catch (err) {
          status.textContent = err.message || 'Failed to save directory.';
          status.style.color = '#ff8f8f';
        }
      });

      const setupTrigger = (btn, type) => {
        btn?.addEventListener('click', async () => {
          activeType = type;
          modal.removeAttribute('hidden');
          status.textContent = '';
          if (titleEl) {
            titleEl.textContent = type === 'model' ? 'Set Model Directory' : 'Set LoRA Directory';
          }
          if (descEl) {
            descEl.textContent = type === 'model'
              ? 'Enter the folder path that contains your diffusion checkpoints.'
              : 'Enter the folder path that contains your LoRA adapters.';
          }
          try {
            const existing = await fetchDirectoryPath(type);
            input.value = existing || '';
          } catch {
            input.value = '';
          }
          input.focus();
        });
      };

      setupTrigger(modelDirBtn, 'model');
      setupTrigger(loraDirBtn, 'lora');
    }

    async function fetchDirectoryPath(type) {
      const endpoint = type === 'lora'
        ? '/api/models/lora_directory'
        : '/api/models/directory';
      const res = await fetch(endpoint);
      const data = await res.json().catch(() => null);
      if (!res.ok || !data) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      return data.path || '';
    }

    async function saveDirectoryPath(type, path) {
      const endpoint = type === 'lora'
        ? '/api/models/lora_directory'
        : '/api/models/directory';
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok || data?.ok === false) {
        throw new Error(data?.detail || `HTTP ${res.status}`);
      }
      await loadModelData();
    }

    initDirectoryModal();

    modelSelect.addEventListener('change', () => {
      ImageGeneratorAPI.pipelineActive = false;
      updatePipelineButton();
      ImageGeneratorAPI.queryPipelineState().catch(() => { });
    });

    loadModelData();
    ImageGeneratorAPI.pipelineActive = false;
    updatePipelineButton();
    ImageGeneratorAPI.queryPipelineState().catch(() => { });
  }

  window.addEventListener('beforeunload', () => {
    if (typeof persistStoryPrompts === 'function') {
      persistStoryPrompts();
    }
  });

  // Global helper to load story slots (uses inner helper if present, else fetches directly)
  async function fetchStorySlots(options = {}) {
    const useCacheBust = options.cacheBust !== false;
    if (typeof window.__projectFetchStorySlots === 'function') {
      try {
        return await window.__projectFetchStorySlots();
      } catch (err) {
        console.warn('Inner story slot fetch failed, falling back to direct fetch.', err);
      }
    }
    const endpoint = getStorySlotsEndpoint();
    try {
      const url = useCacheBust ? `${endpoint}?ts=${Date.now()}` : endpoint;
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json().catch(() => null);
      const slots = Array.isArray(data?.slots) ? data.slots : [];
      const normalized = slots.map((slot) => normalizeStorySlot(slot)).filter(Boolean);
      window.storySlots = normalized;
      return normalized;
    } catch (err) {
      console.warn('Failed to fetch story slots.', err);
      return Array.isArray(window.storySlots) ? window.storySlots : [];
    }
  }
  window.fetchStorySlots = fetchStorySlots;

  function initImageLLM() {
    const btn = document.getElementById('img-llm-btn');
    const select = document.getElementById('img-llm-model');
    const statusEl = document.getElementById('img-llm-status');
    const promptEl = document.getElementById('img-prompt');
    const negativeEl = document.getElementById('img-negative');
    const styleEl = document.getElementById('img-style');
    const noHumansEl = document.getElementById('img-llm-no-humans');
    if (!btn || !select) return;

    const storySuggestBtn = document.getElementById('btnSuggestStory');

    const readSavedModel = () => {
      try {
        return localStorage.getItem(LLM_MODEL_KEY(slug)) || '';
      } catch {
        return '';
      }
    };

    const persistModelChoice = (value) => {
      try {
        if (value) {
          localStorage.setItem(LLM_MODEL_KEY(slug), value);
        } else {
          localStorage.removeItem(LLM_MODEL_KEY(slug));
        }
      } catch {
        /* ignore */
      }
    };

    const setStatus = (message, isError) => {
      if (!statusEl) return;
      statusEl.textContent = message || '';
      statusEl.style.color = isError ? '#ff8f8f' : '#9cd7ff';
    };

    const updateInputValue = (el, value) => {
      if (!el) return;
      el.value = value || '';
      el.dispatchEvent(new Event('input', { bubbles: true }));
    };

    async function loadOllamaModels() {
      select.disabled = true;
      let data = null;
      try {
        const res = await fetch('/api/ollama/models?ts=' + Date.now());
        data = await res.json().catch(() => null);
        if (!res.ok || !data) {
          throw new Error(data?.detail || `HTTP ${res.status}`);
        }
      } catch (err) {
        select.innerHTML = '';
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'Failed to load models';
        select.appendChild(opt);
        setStatus(err?.message || 'Failed to load Ollama models.', true);
        select.disabled = false;
        return;
      }
      const models = Array.isArray(data?.models) ? data.models : [];
      select.innerHTML = '';
      if (!models.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No Ollama models found';
        select.appendChild(opt);
        setStatus('No Ollama models detected. Start Ollama and refresh.', true);
      } else {
        models.forEach((model) => {
          const opt = document.createElement('option');
          opt.value = model;
          opt.textContent = model;
          select.appendChild(opt);
        });
        const saved = readSavedModel();
        if (saved && models.includes(saved)) {
          select.value = saved;
        } else {
          select.value = models[0];
          persistModelChoice(select.value);
        }
      }
      select.disabled = false;
    }

    async function callImagePromptLLM() {
      const model = select.value.trim();
      if (!model) {
        setStatus('Select an Ollama model first.', true);
        select.focus();
        return;
      }
      btn.disabled = true;
      setStatus(`Calling ${model}...`, false);
      try {
        const payload = {
          model,
          style: styleEl ? styleEl.value : null,
          sub_style: (function () {
            if (!styleEl) return null;
            if (styleEl.value === 'stylized') return document.getElementById('img-sub-stylized')?.value || null;
            if (styleEl.value === 'anime') return document.getElementById('img-sub-anime')?.value || null;
            if (styleEl.value === 'animated') return document.getElementById('img-sub-animated')?.value || null;
            return null;
          })(),
          no_humans: !!(noHumansEl && noHumansEl.checked),
        };
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/image_prompt`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => null);
        if (!res.ok || !data?.ok) {
          throw new Error(data?.detail || 'LLM request failed.');
        }
        updateInputValue(promptEl, data.positive || '');
        updateInputValue(negativeEl, data.negative || '');
        persistModelChoice(model);
        const provider = data.model || model;
        setStatus(`Prompt updated via ${provider}.`, false);
      } catch (err) {
        setStatus(err?.message || 'LLM call failed.', true);
      } finally {
        btn.disabled = false;
      }
    }

    const storyPanel = document.getElementById('story-prompts-panel');
    const storyList = document.getElementById('story-prompts-list');
    const storyStatusEl = document.getElementById('story-prompts-status');
    const storyCountEl = document.getElementById('story-prompts-count');
    const storyRunBtn = document.getElementById('story-run-images');
    let storyPrompts = [];
    let storyLocalDirty = false;
    let storyBusy = false;
    let storyBatchActive = false;
    let storyStylePreset = null;
    try {
      const savedPreset = localStorage.getItem(STORY_STYLE_KEY(slug));
      if (savedPreset) {
        storyStylePreset = JSON.parse(savedPreset);
      }
    } catch {
      storyStylePreset = null;
    }

    const loadStoryCache = () => {
      try {
        const raw = localStorage.getItem(STORY_PROMPTS_KEY(slug));
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed)
          ? parsed.map((slot) => normalizeStorySlot(slot)).filter(Boolean)
          : [];
      } catch {
        return [];
      }
    };

    const saveStoryCache = (slots) => {
      try {
        localStorage.setItem(STORY_PROMPTS_KEY(slug), JSON.stringify(slots || []));
      } catch {
        /* ignore */
      }
    };

    const setStoryStatus = (message, isError = false) => {
      if (!storyStatusEl) return;
      storyStatusEl.textContent = message || '';
      storyStatusEl.style.color = isError ? '#ff8f8f' : '#9cd7ff';
    };

    const persistStoryStylePreset = () => {
      if (!storyStylePreset) return;
      try {
        localStorage.setItem(STORY_STYLE_KEY(slug), JSON.stringify(storyStylePreset));
      } catch {
        /* ignore */
      }
    };

    const clearStoryStylePreset = () => {
      storyStylePreset = null;
      try {
        localStorage.removeItem(STORY_STYLE_KEY(slug));
      } catch {
        /* ignore */
      }
    };

    const ensureStorySeed = (payload) => {
      if (payload.seed == null || !Number.isFinite(Number(payload.seed))) {
        payload.seed = Math.floor(Math.random() * 1_000_000_000);
      } else {
        payload.seed = Number(payload.seed);
      }
    };

    const applyStoryStyleHint = (payload) => {
      if (!storyStylePreset) return;
      payload.seed = storyStylePreset.seed;
      payload.width = storyStylePreset.width;
      payload.height = storyStylePreset.height;
      payload.style = storyStylePreset.style;
      if (Array.isArray(storyStylePreset.loras) && storyStylePreset.loras.length) {
        payload.loras = storyStylePreset.loras.slice();
      }
      if (!/base story image/i.test(payload.prompt)) {
        payload.prompt = `${payload.prompt}\n\n(Keep palette and composition consistent with the base story image.)`;
      }
    };

    const captureStoryStyleFromPayload = (payload) => {
      storyStylePreset = {
        seed: payload.seed,
        width: payload.width,
        height: payload.height,
        style: payload.style,
        loras: Array.isArray(payload.loras) ? payload.loras.slice() : [],
      };
      persistStoryStylePreset();
    };

    const formatTime = (seconds) => {
      if (typeof seconds !== 'number' || Number.isNaN(seconds)) {
        return "--:--";
      }
      const min = Math.floor(seconds / 60);
      const sec = Math.floor(seconds % 60);
      const ms = Math.round((seconds - Math.floor(seconds)) * 1000);
      return `${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
    };

    const applyStorySlotPayload = (slots) => {
      const normalized = Array.isArray(slots)
        ? slots.map((slot) => normalizeStorySlot(slot)).filter(Boolean)
        : [];
      storyPrompts = normalized.map((slot) => ({
        prompt: slot.prompt,
        start: slot.start,
        end: slot.end,
        image_path: slot.image_path || null,
        status: slot.image_path ? 'done' : 'pending',
        error: '',
      }));
      storyLocalDirty = false;
      renderStoryPrompts();
    };

    async function hydrateStoryPromptsFromServer(options = {}) {
      const { silent = false } = options;
      try {
        if (storyLocalDirty) {
          if (!silent) {
            setStoryStatus('Unsaved story changes present; skipping refresh.', false);
          }
          return;
        }
        const slots = await fetchStorySlots();
        applyStorySlotPayload(slots || []);
        if (!silent) {
          const count = storyPrompts.length;
          if (count) {
            setStoryStatus(`Loaded ${count} saved prompt${count === 1 ? '' : 's'}.`, false);
          } else {
            setStoryStatus('No saved story prompts yet.', false);
          }
        }
      } catch (err) {
        if (!silent) {
          setStoryStatus(err?.message || 'Failed to load story prompts.', true);
        }
      }
    }

    async function handleStorySuggestion() {
      if (!storySuggestBtn) {
        return;
      }
      const model = select.value.trim();
      if (!model) {
        setStoryStatus('Select an Ollama model first.', true);
        select.focus();
        return;
      }
      storySuggestBtn.disabled = true;
      setStoryStatus('Requesting story prompts from LLM...', false);
      try {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/image_story`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model }),
        });
        const data = await res.json().catch(() => null);
        if (!res.ok || !data?.ok) {
          throw new Error(data?.detail || 'Failed to generate story prompts.');
        }
        const preservedImages = storyPrompts
          .map((entry) => entry.image_path)
          .filter(Boolean);
        applyStorySlotPayload(Array.isArray(data.prompts) ? data.prompts : []);
        const selectionOrder = projectSelectionOrder.filter((p) => projectSelectionSet.has(p));
        const preferredImages = preservedImages.length ? preservedImages : selectionOrder;
        if (preferredImages.length && storyPrompts.length) {
          storyPrompts.forEach((entry, idx) => {
            entry.image_path = preferredImages[idx % preferredImages.length];
            if (entry.image_path) {
              entry.status = 'done';
            }
          });
          renderStoryPrompts();
        }
        const count = storyPrompts.length;
        setStoryStatus(`Received ${count} prompt${count === 1 ? '' : 's'} from ${model}.`, false);
      } catch (err) {
        setStoryStatus(err?.message || 'Failed to fetch story prompts.', true);
      } finally {
        storySuggestBtn.disabled = false;
      }
    }

    async function runStoryBatch() {
      if (!storyPrompts.length) {
        setStoryStatus('Generate story prompts first.', true);
        return;
      }
      if (storyBatchActive || storyBusy) {
        setStoryStatus('Finish the current story job first.', true);
        return;
      }
      storyBatchActive = true;
      if (storyRunBtn) storyRunBtn.disabled = true;
      setStoryStatus(`Generating ${storyPrompts.length} story image${storyPrompts.length === 1 ? '' : 's'}...`, false);
      try {
        for (let i = 0; i < storyPrompts.length; i += 1) {
          await regenerateStoryPrompt(i);
        }
        setStoryStatus('Story image batch complete.', false);
      } catch (err) {
        setStoryStatus(err?.message || 'Story batch failed.', true);
      } finally {
        storyBatchActive = false;
        if (storyRunBtn) {
          storyRunBtn.disabled = storyBusy || !storyPrompts.length;
        }
      }
    }

    btn.addEventListener('click', (evt) => {
      evt.preventDefault();
      callImagePromptLLM();
    });

    select.addEventListener('change', () => {
      persistModelChoice(select.value.trim());
    });

    if (storySuggestBtn) {
      storySuggestBtn.addEventListener('click', (evt) => {
        evt.preventDefault();
        handleStorySuggestion();
      });
    }

    if (storyRunBtn) {
      storyRunBtn.addEventListener('click', (evt) => {
        evt.preventDefault();
        runStoryBatch();
      });
    }

    const cachedStorySlots = loadStoryCache();
    const initialStorySlots = Array.isArray(window.storySlots) ? window.storySlots : [];
    const seedSlots = cachedStorySlots.length ? cachedStorySlots : initialStorySlots;
    if (seedSlots.length) {
      applyStorySlotPayload(seedSlots);
    } else {
      renderStoryPrompts();
    }
    setStoryStatus('Loading saved prompts...');
    hydrateStoryPromptsFromServer({ silent: false });
    loadOllamaModels();

    async function regenerateStoryPrompt(index) {
      const entry = storyPrompts[index];
      if (!entry) return;
      if (storyBusy) {
        setStoryStatus('Finish the current story job first.', true);
        return;
      }
      storyBusy = true;
      if (storyRunBtn) storyRunBtn.disabled = true;
      setStoryStatus(`Regenerating prompt ${index + 1}...`, false);
      let payloadInfo = null;
      try {
        await ensurePipelineLoadedForStory();
        if (entry.image_path) {
          try {
            await deleteProjectImageRequest(entry.image_path);
          } catch (err) {
            console.warn('Failed to delete previous story image:', err);
          }
          entry.image_path = null;
          await persistStoryPrompts();
          await fetchProjectImages(true);
        }
        entry.status = 'running';
        entry.error = '';
        renderStoryPrompts();
        const payloadInfo = ImageGeneratorAPI.buildPayload(
          { prompt: entry.prompt, negative: '', count: 1 },
          { skipStyleHint: true, label: `story ${index + 1}` }
        );
        ensureStorySeed(payloadInfo.payload);
        if (storyStylePreset) {
          applyStoryStyleHint(payloadInfo.payload);
        }
        const jobStatus = await ImageGeneratorAPI.submitJob(
          payloadInfo.payload,
          `story ${index + 1} (regen)`,
          { truncated: payloadInfo.truncated }
        );
        const savedImages = jobStatus && jobStatus.result && Array.isArray(jobStatus.result.images)
          ? jobStatus.result.images
          : [];
        if (savedImages.length) {
          entry.image_path = savedImages[0];
          await persistStoryPrompts();
          await fetchProjectImages(true);
        }
        entry.status = 'done';
        entry.error = '';
        setStoryStatus(`Prompt ${index + 1} regenerated.`, false);
      } catch (err) {
        entry.status = 'failed';
        entry.error = err?.message || 'generation failed';
        setStoryStatus(`Regeneration failed: ${entry.error}`, true);
      } finally {
        storyBusy = false;
        if (storyRunBtn) storyRunBtn.disabled = storyBatchActive || !storyPrompts.length;
        await releaseStoryPipelineIfHeld();
        if (!storyStylePreset && payloadInfo && payloadInfo.payload) {
          captureStoryStyleFromPayload(payloadInfo.payload);
        }
        renderStoryPrompts();
      }
    }

    function getImageOptions() {
      if (!Array.isArray(projectImagesCache)) return [];
      const ordered = [];
      const seen = new Set();
      projectSelectionOrder.forEach((p) => {
        if (projectSelectionSet.has(p) && projectImagesCache.includes(p) && !seen.has(p)) {
          seen.add(p);
          ordered.push(p);
        }
      });
      projectImagesCache.forEach((p) => {
        if (!seen.has(p)) {
          seen.add(p);
          ordered.push(p);
        }
      });
      return ordered;
    }

    function renderStoryPrompts() {
      if (storyList) {
        storyList.innerHTML = '';
        if (!storyPrompts.length) {
          const li = document.createElement('li');
          li.className = 'muted';
          li.textContent = 'No saved story prompts yet.';
          storyList.appendChild(li);
        }
        storyPrompts.forEach((entry, idx) => {
          const li = document.createElement('li');

          const text = document.createElement('span');
          text.textContent = entry.prompt;

          const time = document.createElement('span');
          time.className = 'story-time';
          time.textContent = `${formatTime(entry.start)} - ${formatTime(entry.end)}`;

          const status = document.createElement('span');
          status.className = `story-status story-status-${entry.status || 'pending'}`;
          status.textContent = entry.status || 'pending';
          if (entry.error) {
            status.title = entry.error;
          }

          const container = document.createElement('div');
          container.className = 'story-prompt-item';
          container.appendChild(text);
          container.appendChild(time);
          container.appendChild(status);
          if (entry.image_path) {
            const badge = document.createElement('span');
            badge.className = 'story-image-badge';
            badge.textContent = 'image linked';
            container.appendChild(badge);
          }

          const controls = document.createElement('div');
          controls.className = 'story-inline-actions';
          const imageSelect = document.createElement('select');
          imageSelect.className = 'story-image-select';
          const blank = document.createElement('option');
          blank.value = '';
          blank.textContent = 'Link an existing image...';
          imageSelect.appendChild(blank);
          getImageOptions().forEach((path) => {
            const opt = document.createElement('option');
            opt.value = path;
            opt.textContent = path.split(/[\\/]/).pop() || path;
            if (entry.image_path && entry.image_path === path) {
              opt.selected = true;
            }
            imageSelect.appendChild(opt);
          });
          imageSelect.addEventListener('change', async () => {
            const chosen = imageSelect.value.trim();
            entry.image_path = chosen || null;
            if (entry.image_path) {
              entry.status = 'done';
            }
            storyLocalDirty = true;
            await persistStoryPrompts();
            renderStoryPrompts();
          });
          controls.appendChild(imageSelect);

          const regenBtn = document.createElement('button');
          regenBtn.type = 'button';
          regenBtn.className = 'ghost-btn small';
          regenBtn.textContent = entry.image_path ? 'Regenerate' : 'Generate';
          regenBtn.disabled = storyBusy || entry.status === 'running';
          regenBtn.addEventListener('click', () => regenerateStoryPrompt(idx));
          controls.appendChild(regenBtn);
          container.appendChild(controls);

          li.appendChild(container);
          storyList.appendChild(li);
        });
      }
      if (storyCountEl) {
        storyCountEl.textContent = `${storyPrompts.length} prompt${storyPrompts.length === 1 ? '' : 's'}`;
      }
      if (storyPanel) {
        storyPanel.removeAttribute('hidden');
      }
      if (storyRunBtn) {
        storyRunBtn.disabled = storyBusy || !storyPrompts.length;
      }
      window.storySlots = storyPrompts
        .map(normalizeStorySlot)
        .filter(Boolean);
      saveStoryCache(window.storySlots);
      persistStoryPrompts();
    }

    async function persistStoryPrompts() {
      if (!storyPrompts.length) return;
      const payload = storyPrompts
        .map((entry) => ({
          prompt: entry.prompt,
          start: entry.start,
          end: entry.end,
          image_path: entry.image_path || null,
        }));
      try {
        const endpoint = getStorySlotsEndpoint();
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slots: payload }),
        });
        const data = await res.json().catch(() => null);
        if (res.ok && data && Array.isArray(data.slots)) {
          storyPrompts = data.slots.map((slot) => ({
            prompt: slot.prompt,
            start: slot.start,
            end: slot.end,
            image_path: slot.image_path || null,
            status: slot.image_path ? 'done' : 'pending',
            error: '',
          }));
          window.storySlots = storyPrompts.map(normalizeStorySlot).filter(Boolean);
          storyLocalDirty = false;
        } else {
          window.storySlots = payload.map(normalizeStorySlot).filter(Boolean);
        }
        storyLocalDirty = false;
      } catch (err) {
        console.warn('Failed to save story slots', err);
        storyLocalDirty = true;
      }
    }

    async function ensurePipelineLoadedForStory() {
      if (!ImageGeneratorAPI.queryPipelineState || !ImageGeneratorAPI.setPipelineLoadedState) {
        throw new Error('Image generator pipeline helpers unavailable.');
      }
      await ImageGeneratorAPI.queryPipelineState();
      if (ImageGeneratorAPI.pipelineActive) {
        return;
      }
      await ImageGeneratorAPI.setPipelineLoadedState(true);
      storyPipelineHeld = true;
    }

    async function releaseStoryPipelineIfHeld() {
      if (!storyPipelineHeld) {
        return;
      }
      if (!ImageGeneratorAPI.setPipelineLoadedState) {
        storyPipelineHeld = false;
        return;
      }
      try {
        await ImageGeneratorAPI.setPipelineLoadedState(false);
      } catch (err) {
        console.warn('Failed to release story pipeline', err);
      } finally {
        storyPipelineHeld = false;
      }
    }
  }
})();
