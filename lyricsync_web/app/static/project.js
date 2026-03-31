
import { API } from './js/api.js';
import { State } from './js/state.js';
import { UI } from './js/ui.js';
import { ImageGenUI } from './js/image_gen.js?v=60';

window.API = API;
window.State = State;
window.UI = UI;

(async function main() {
  State.init(window.slug || '');
  const slug = State.slug;

  // DOM Elements
  const logAlignEl = document.getElementById('logAlign');
  const logRenderEl = document.getElementById('logRender');
  const btnAlign = document.getElementById('btnAlign');
  const btnRender = document.getElementById('btnRender');

  // Initialize video if present
  const vidEl = document.getElementById('ui-video');
  if (vidEl && vidEl.dataset.hasPreview === 'true') {
    const proto = window.location.protocol;
    const host = window.location.host;
    const newSrc = `${proto}//${host}/api/projects/${slug}/download/preview.mp4?t=${Date.now()}`;
    vidEl.src = newSrc;
  }

  // Helpers
  function getById(id) { return document.getElementById(id); }
  function readValue(id, def) {
    const el = document.getElementById(id);
    return el && el.value ? el.value : def;
  }
  function readCheckbox(id, def) {
    const el = document.getElementById(id);
    return el ? el.checked : def;
  }
  function readInt(id, def) {
    const val = parseInt(readValue(id, def), 10);
    return isNaN(val) ? def : val;
  }
  function readFloat(id, def) {
    const val = parseFloat(readValue(id, def));
    return isNaN(val) ? def : val;
  }

  // Persistence
  const SETTINGS_KEY = `lyricsync_settings_${slug}`;
  function readSettings() {
    try {
      const data = localStorage.getItem(SETTINGS_KEY);
      if (data) {
        console.log(`[Project] Loaded settings for ${slug}:`, JSON.parse(data));
        return JSON.parse(data);
      }
      return {};
    } catch (e) { 
      console.warn("[Project] Read settings failed:", e);
      return {}; 
    }
  }
  function saveSettings(obj) {
    try {
      const cur = readSettings();
      const updated = { ...cur, ...obj };
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(updated));
      console.log(`[Project] Saved settings to ${SETTINGS_KEY}:`, obj);
    } catch (e) { console.error("[Project] Save settings failed:", e); }
  }

  function applySettingsToUI(settings) {
    if (!settings) return;
    console.log(`[Project] Applying saved settings for ${slug}:`, settings);
    Object.entries(settings).forEach(([k, v]) => {
      const el = document.getElementById(k);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!v;
      else el.value = v;
      
      // Trigger change for fields that might have logic attached (like UI toggles)
      if (k.startsWith('ui-') || k.startsWith('img-') || k.startsWith('story-')) {
          el.dispatchEvent(new Event('change'));
      }
    });
  }

  function attachPersistenceHandlers() {
    const selector = '#render-form input, #render-form select, #img-form input, #img-form select, #img-form textarea';
    const inputs = document.querySelectorAll(selector);
    
    inputs.forEach(el => {
      const isTracked = el.id && (
        el.id.startsWith('ui-') || 
        el.id.startsWith('img-') || 
        el.id.startsWith('story-')
      );
      
      if (isTracked) {
        const eventType = (el.tagName === 'SELECT' || el.type === 'checkbox') ? 'change' : 'input';
        
        el.addEventListener(eventType, () => {
          const val = el.type === 'checkbox' ? el.checked : el.value;
          saveSettings({ [el.id]: val });
          
          if (el.id === 'ui-theme') applyThemeToUI(val);
          if (el.id.startsWith('ui-')) updatePreviewMeta();
        });
      }
    });
  }

  function updatePreviewMeta() {
    // Logic to update review tab stats
    const setText = (id, val) => {
      const el = getById(id);
      if (el) el.textContent = val;
    };
    setText('rev-style', readValue('ui-style', 'burn-srt'));
    setText('rev-theme', getEffectiveThemeSelection());
    setText('rev-font', readValue('ui-font-family', 'Arial'));
    setText('rev-size', readValue('ui-font-size', '20') + 'px');
    setText('rev-playback', readValue('ui-img-playback', 'story'));
  }

  // --- Themes ---
  let themeCache = {};
  async function loadThemes() {
    // simplified
    const sel = getById('ui-theme');
    if (!sel) return;
    try {
      const res = await fetch('/api/themes');
      const data = await res.json();
      themeCache = data.themes || {};
      // populate select
      const saved = readSettings()['ui-theme'] || sel.dataset.default || 'default';
      sel.innerHTML = '';
      Object.keys(themeCache).sort().forEach(key => {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = key;
        sel.appendChild(opt);
      });
      sel.value = Object.keys(themeCache).includes(saved) ? saved : 'default';
      applyThemeToUI(sel.value);
    } catch (e) { console.error(e); }
  }

  function getEffectiveThemeSelection() {
    const el = getById('ui-theme');
    return el ? el.value : 'default';
  }

  function findThemeByKey(key) { return themeCache[key]; }

  function applyThemeToUI(key) {
    const t = themeCache[key];
    if (!t) return;
    // map theme props to UI IDs
    const map = {
      font: 'ui-font-family',
      font_size: 'ui-font-size',
      outline: 'ui-outline',
      font_color: 'ui-font-color',
      outline_color: 'ui-outline-color',
      endcard_color: 'ui-endcard-color',
      endcard_border_color: 'ui-endcard-border'
    };
    Object.entries(map).forEach(([tKey, uiId]) => {
      if (t[tKey] !== undefined) {
        const el = getById(uiId);
        if (el) el.value = t[tKey];
      }
    });
    // Handle font file separately if needed
    if (t.font_file_name) {
      const fs = getById('ui-font-file');
      if (fs) fs.value = t.font_file_name;
    }
    updatePreviewMeta();
  }

  function attachThemeActions() {
    // Save/Delete theme handlers
    const btnSave = getById('btnSaveTheme');
    const btnDel = getById('btnDeleteTheme');
    if (btnSave) btnSave.addEventListener('click', async () => {
      const name = prompt("Theme name:");
      if (!name) return;
      // Construct payload from UI
      const payload = {
        font: readValue('ui-font-family'),
        font_size: readInt('ui-font-size'),
        outline: readInt('ui-outline'),
        font_color: readValue('ui-font-color'),
        outline_color: readValue('ui-outline-color'),
        endcard_color: readValue('ui-endcard-color'),
        endcard_border_color: readValue('ui-endcard-border'),
        font_file_name: readValue('ui-font-file')
      };
      try {
        await fetch(`/api/themes/${name}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        await loadThemes();
        getById('ui-theme').value = name;
      } catch (e) { alert(e.message); }
    });
    if (btnDel) btnDel.addEventListener('click', async () => {
      const name = getEffectiveThemeSelection();
      if (!confirm(`Delete theme ${name}?`)) return;
      await fetch(`/api/themes/${name}`, { method: 'DELETE' });
      await loadThemes();
    });
  }

  // --- Fonts ---
  async function loadFonts() {
    const sel = getById('ui-font-file');
    if (!sel) return;
    try {
      const res = await fetch('/api/fonts');
      const data = await res.json();
      sel.innerHTML = '<option value="">— none —</option>';
      (data.fonts || []).forEach(f => {
        const opt = document.createElement('option');
        opt.value = f;
        opt.textContent = f;
        sel.appendChild(opt);
      });
      const saved = readSettings()['ui-font-file'];
      if (saved) sel.value = saved;
    } catch (e) { console.error(e); }
  }

  // --- Transcribe Logic ---

  function streamLogs(jobName, el) {
    if (!el) return;
    el.textContent = ''; // clear
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws/logs/${slug}/${jobName}`;
    UI.appendLog(el, `Connecting to log stream: ${jobName}...\n`);

    const isAlign = jobName === 'align';
    const toastId = isAlign ? 'align-toast' : 'render-toast';

    const socket = new WebSocket(wsUrl);
    socket.onmessage = (ev) => {
      UI.appendLog(el, ev.data);
      if (ev.data.includes("[Complete]")) {
        if (isAlign) {
          UI.showToast("Transcription Complete!", 5000, "align-complete");
          const btnAlign = document.getElementById('btnAlign');
          if (btnAlign) btnAlign.disabled = false;
        } else {
          UI.showToast("Render Complete!", 5000, "render-complete");
          const btnRender = document.getElementById('btnRender');
          if (btnRender) btnRender.disabled = false;

          // Force reload the preview video
          const vid = document.getElementById('ui-video');
          if (vid) {
            // Always reconstruct the source to ensure we aren't reloading a "missing" placeholder or empty src
            const proto = window.location.protocol;
            const host = window.location.host;
            const newSrc = `${proto}//${host}/api/projects/${slug}/download/preview.mp4?t=${Date.now()}`;
            console.log("[Project] Reloading video preview:", newSrc);
            vid.src = newSrc;
            vid.load();
          }
        }
      }

      // Stage-based updates
      const msg = ev.data;
      if (msg.includes("Separating vocals")) UI.showToast("Separating vocals...", 10000, toastId);
      else if (msg.includes("Transcribing") || msg.includes("Preprocessing audio")) UI.showToast("Transcribing audio...", 20000, toastId);
      else if (msg.includes("Aligning lyrics")) UI.showToast("Aligning lyrics...", 10000, toastId);
      else if (msg.includes("Generating Karaoke")) UI.showToast("Generating Karaoke...", 5000, toastId);
      else if (msg.includes("Writing SRT")) UI.showToast("Generating SRT...", 5000, toastId);

      // Attempt to parse progress percentage
      const match = ev.data.match(/(?:(?:Alignment|Transcription).*?|Encoding preview.*?)\s*(\d+)%/);
      if (match) {
        const pct = match[1];
        UI.showToast(`${isAlign ? 'Transcription' : 'Render'} Progress: ${pct}%`, 30000, toastId);
      }
    };
    socket.onclose = () => {
      UI.appendLog(el, '\n[Stream closed]');
    };
    socket.onerror = () => {
      UI.appendLog(el, '\n[Stream error]');
    };
  }

  async function callAlign() {
    if (btnAlign) btnAlign.disabled = true;
    UI.appendLog(logAlignEl, `\n=== Align request @ ${new Date().toLocaleTimeString()} ===\n`);

    try {
      const payload = {
        model_size: readValue('ui-tx-model', 'large-v2'),
        language: readValue('ui-tx-language', 'auto'),
        device: readValue('ui-tx-device', 'auto'),
        compute_type: readValue('ui-tx-compute', 'float16'),
        enable_word_highlight: true,
        engine: readValue('ui-tx-engine', 'whisperx')
      };

      const data = await API.callAlign(slug, payload);
      const pid = data.pid || 'n/a';

      UI.appendLog(logAlignEl, `Started align (PID: ${pid})\n`);
      UI.showToast("Transcription Started...", 3000, "align-toast");
      streamLogs('align', logAlignEl);

    } catch (e) {
      UI.appendLog(logAlignEl, `Error: ${e.message}\n`);
      if (btnAlign) btnAlign.disabled = false;
    }

    setTimeout(() => { if (btnAlign) btnAlign.disabled = false; }, 5000);
  }

  async function callRender() {
    if (btnRender) btnRender.disabled = true;
    UI.appendLog(logRenderEl, `\n=== Render request @ ${new Date().toLocaleTimeString()} ===\n`);

    try {
      const payload = buildRenderPayload();
      const data = await API.callRender(slug, payload);
      const jobId = data.job_id;

      UI.appendLog(logRenderEl, `Render started (job: ${jobId})\n`);
      UI.showToast("Render Started...", 2000, "render-toast");

      streamLogs(`${jobId}`, logRenderEl);

    } catch (e) {
      UI.appendLog(logRenderEl, `Error: ${e.message}\n`);
      UI.showToast(`Error: ${e.message}`, 5000, "render-toast", "toast-error");
      if (btnRender) btnRender.disabled = false;
    }
  }

  function buildRenderPayload() {
    const stored = readSettings();
    return {
      style: readValue('ui-style', 'burn-srt'),
      text_theme: getEffectiveThemeSelection(),
      font: readValue('ui-font-family', 'Arial'),
      font_size: readInt('ui-font-size', 20),
      outline: readInt('ui-outline', 2),
      ass_align: 2,
      margin_v: 20,
      force_res: '1920:1080',
      srt_name: 'edited.srt',
      no_burn: false,
      show_title: readCheckbox('ui-show-title', false),
      title_from_mp3: readCheckbox('ui-use-mp3-title', false),
      show_end_card: readCheckbox('ui-show-end-card', true),
      end_card_text: readValue('ui-end-card-text', 'Thank You'),
      end_card_seconds: readFloat('ui-end-card-seconds', 5),
      font_color: readValue('ui-font-color', '#FFFFFF'),
      outline_color: readValue('ui-outline-color', '#000000'),
      endcard_color: readValue('ui-endcard-color', '#FFFFFF'),
      endcard_border_color: readValue('ui-endcard-border', '#000000'),
      font_file_name: readValue('ui-font-file') || null,
      effect: readValue('ui-effect', 'none'),
      effect_strength: readFloat('ui-effect-strength', 0.08),
      effect_cycle: readFloat('ui-effect-cycle', 12),
      effect_zoom: readFloat('ui-kenburns-zoom', 0.12),
      effect_pan: readFloat('ui-kenburns-pan', 0.35),
      fps: readInt('ui-fps', 30),
      image: {
        clip_seconds: readFloat('ui-img-duration', 6),
        fade_seconds: readFloat('ui-img-fade', 1),
        playback: readValue('ui-img-playback', 'story'),
        story_slots: State.storySlots // Use state
      }
    };
  }

  // --- Image Logic ---
  async function refreshImages(force) {
    try {
      const data = await API.fetchProjectImages(slug);
      State.projectImagesCache = data.images || [];
      State.notifyImagesUpdated();

      const dataSel = await API.fetchProjectImageSelection(slug);
      State.projectSelectionSet.clear();
      if (dataSel && dataSel.selection) {
        dataSel.selection.forEach(path => State.projectSelectionSet.add(path));
      }
      State.notifySelectionUpdated();

      renderReviewImages();
    } catch (e) { console.error(e); }
  }

  function renderReviewImages() {
    const el = getById('rev-img-grid');
    const countEl = getById('rev-img-count');
    if (!el) return;

    const list = Array.from(State.projectSelectionSet);
    if (countEl) countEl.textContent = list.length;
    
    el.innerHTML = '';
    list.slice(0, 15).forEach(path => {
      const div = document.createElement('div');
      div.className = 'gallery-item';
      div.dataset.path = path;

      const img = document.createElement('img');
      img.src = `/api/projects/${slug}/download/${path}?t=${Date.now()}`;
      img.onclick = () => UI.openLightbox(img.src);

      div.appendChild(img);
      el.appendChild(div);
    });

    if (list.length === 0) {
      el.innerHTML = '<div class="muted" style="padding:10px;font-size:0.9em;">No images selected</div>';
    }
  }

  // --- Init ---

  // Wire Buttons
  if (btnAlign) btnAlign.onclick = (e) => { e.preventDefault(); callAlign(); };
  if (btnRender) btnRender.onclick = (e) => { e.preventDefault(); callRender(); };

  // Initial Loads
  await loadFonts();
  await loadThemes();
  applySettingsToUI(readSettings());
  attachPersistenceHandlers();

  refreshImages();
  State.projectImagesListeners.push(renderReviewImages);
  State.projectSelectionListeners.push(renderReviewImages);

  // --- Gallery & Selection Logic ---

  function renderGallery() {
    const el = getById('img-gallery');
    if (!el) return;
    el.innerHTML = '';

    if (State.projectImagesCache.length === 0) {
      el.innerHTML = '<div class="muted" style="padding:20px;text-align:center">No images yet. Generate or upload some!</div>';
      return;
    }

    State.projectImagesCache.forEach(path => {
      const div = document.createElement('div');
      div.className = 'gallery-item';
      div.dataset.path = path;

      // Thumbnail Image
      const img = document.createElement('img');
      const src = `/api/projects/${slug}/download/${path}?t=${Date.now()}`;
      img.src = src;
      img.loading = 'lazy';
      // Click image -> Open Lightbox
      img.onclick = (e) => {
        e.stopPropagation();
        UI.openLightbox(src);
      };

      // Selection Toggle (Checkbox-like)
      const toggle = document.createElement('div');
      toggle.className = 'selection-toggle';
      toggle.onclick = (e) => {
        e.stopPropagation();
        if (State.projectSelectionSet.has(path)) {
          State.projectSelectionSet.delete(path);
        } else {
          State.projectSelectionSet.add(path);
        }
        State.notifySelectionUpdated();
      };

      // Single Delete Toggle (X button)
      const delToggle = document.createElement('div');
      delToggle.className = 'delete-toggle';
      delToggle.innerHTML = '&times;';
      delToggle.title = 'Delete this image';
      delToggle.onclick = async (e) => {
        e.stopPropagation();
        // Since it's a permanent action, maybe a small confirmation? 
        // User said they want to "delete single images"
        try {
          await API.deleteProjectImage(slug, path);
          // Remove from local cache
          State.projectImagesCache = State.projectImagesCache.filter(p => p !== path);
          // Remove from selection if present
          if (State.projectSelectionSet.has(path)) {
            State.projectSelectionSet.delete(path);
            State.notifySelectionUpdated();
          }
          State.notifyImagesUpdated();
        } catch (err) {
          UI.showToast("Delete failed: " + err.message, 4000, null, "toast-error");
        }
      };

      div.appendChild(img);
      div.appendChild(toggle);
      div.appendChild(delToggle);
      el.appendChild(div);
    });

    // Applying initial selection state
    renderGallerySelection();
  }

  function renderGallerySelection() {
    // 1. Update visual classes
    const el = getById('img-gallery');
    if (el) {
      Array.from(el.children).forEach(child => {
        const path = child.dataset.path;
        if (State.projectSelectionSet.has(path)) {
          child.classList.add('selected');
        } else {
          child.classList.remove('selected');
        }
      });
    }

    // 2. Update stats / buttons
    const count = State.projectSelectionSet.size;
    const statsEl = getById('img-selection-status');
    if (statsEl) statsEl.textContent = `${count} selected`;

    // Enable/disable "Save Selection"
    const btnSave = getById('img-selection-save');
    if (btnSave) btnSave.disabled = (count === 0);
  }

  // Hook up listeners
  State.projectImagesListeners.push(renderGallery);
  State.projectSelectionListeners.push(renderGallerySelection);

  // Wire up Lightbox Close
  const lightbox = document.getElementById('lightbox');
  if (lightbox) {
    lightbox.addEventListener('click', () => UI.closeLightbox());
  }

  // Wire up Gallery Actions
  const btnImgGenSave = getById('img-selection-save');
  const btnImgDelAll = getById('img-delete-all');
  const btnImgUpload = getById('img-batch-upload');
  const fileInputBatch = getById('img-batch-files');

  if (btnImgGenSave) btnImgGenSave.onclick = async () => {
    if (State.projectSelectionSet.size === 0) return;
    btnImgGenSave.disabled = true;
    try {
      const paths = Array.from(State.projectSelectionSet);
      // We might want to preserve order if the user did reordering, 
      // but proper reordering UI is complex. 
      // For now, let's just save the set.
      await API.saveProjectImageSelection(slug, paths);
      UI.showToast(`Saved ${paths.length} images for render.`, 3000);
      refreshImages(); // ensure everything is in sync
    } catch (e) {
      UI.showToast("Error saving selection: " + e.message, 4000, null, "toast-error");
    } finally {
      if (State.projectSelectionSet.size > 0) btnImgGenSave.disabled = false;
    }
  };

  if (btnImgDelAll) btnImgDelAll.onclick = async () => {
    if (!confirm("Are you sure you want to delete ALL images? This cannot be undone.")) return;
    try {
      await API.deleteAllProjectImages(slug);
      UI.showToast("All images deleted.", 3000);
      State.projectSelectionSet.clear();
      State.notifySelectionUpdated();
      refreshImages();
    } catch (e) {
      UI.showToast("Error: " + e.message, 4000, null, "toast-error");
    }
  };

  if (btnImgUpload && fileInputBatch) {
    btnImgUpload.onclick = () => fileInputBatch.click();
    fileInputBatch.onchange = async () => {
      if (!fileInputBatch.files.length) return;
      const fd = new FormData();
      Array.from(fileInputBatch.files).forEach(f => fd.append('files', f));

      const statusEl = getById('img-batch-status');
      if (statusEl) statusEl.textContent = "Uploading...";

      try {
        await API.uploadProjectImages(slug, fd);
        UI.showToast(`Uploaded ${fileInputBatch.files.length} images.`, 3000);
        fileInputBatch.value = ''; // reset
        refreshImages();
      } catch (e) {
        UI.showToast("Upload failed: " + e.message, 4000, null, "toast-error");
      } finally {
        if (statusEl) statusEl.textContent = "";
      }
    };
  }

  // Initialize Image Gen UI from module
  await ImageGenUI.init();

  // Populate models for UI (since ImageGenUI expects them loosely or bound to state)
  try {
    const data = await API.fetchModels();
    State.models = data.models || [];
    State.loras = data.loras || [];
    State.vaes = data.vaes || [];
    State.text_encoders = data.text_encoders || [];

    const mSel = getById('img-model');
    const storyMSel = getById('story-img-model');

    if (mSel || storyMSel) {
      if (mSel) mSel.innerHTML = '';
      if (storyMSel) storyMSel.innerHTML = '';

      State.models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.id;
        if (mSel) mSel.appendChild(opt.cloneNode(true));
        if (storyMSel) storyMSel.appendChild(opt.cloneNode(true));
      });
    }
    // Render LoRAs now that we have them
    if (ImageGenUI.renderLoras) ImageGenUI.renderLoras();

    // Render VAEs and TEs
    if (ImageGenUI.renderVaesOrTes) {
      ImageGenUI.renderVaesOrTes(State.vaes, 'img-vae', 'story-img-vae');
      ImageGenUI.renderVaesOrTes(State.text_encoders, 'img-te', 'story-img-te');
    }

    // Update pipeline status and apply metadata for the initially selected model
    if (ImageGenUI.applyModelSettings) {
      if (mSel) ImageGenUI.applyModelSettings('single', mSel.value);
      if (storyMSel) ImageGenUI.applyModelSettings('story', storyMSel.value);
    }
    if (ImageGenUI.updatePipelineStatus) ImageGenUI.updatePipelineStatus();
  } catch (e) {
    console.error("Failed to post-init models", e);
  }

})();
