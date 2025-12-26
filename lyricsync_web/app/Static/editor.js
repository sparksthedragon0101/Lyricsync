/*
  LyricSync — SRT Editor (refactored)
  - Structured for readability
  - Safe initialization (handlers are attached after WaveSurfer/Regions exist)
  - Upgrades:
      • Loop selected region (L) + play selection (P)
      • Snap to 10ms grid + neighbor collision guard + optional ripple
      • Validate (g) and auto-fix micro gaps/overlaps (Shift+G)
      • Undo/Redo (Ctrl+Z / Ctrl+Y) with lightweight history
      • Autosave (debounced) + beforeunload protection + Ctrl+S
      • Minor ergonomics: zoom +/- keys, precise nudge, trim to cursor
*/

// ------------------------------
// Globals (initialized in loadProject)
// ------------------------------
let wavesurfer, regions, timeline;
let project = null;
let currentId = null;
let dirty = false;
let loopSelected = false; // toggle with 'L'
let supportsWordTiming = false;
let editMode = 'lines'; // 'lines' | 'words'
let keyHandler = null;
let regionHistoryTimer = null;
let touchEditEnabled = true;
let copyBuffer = [];
const REGION_HISTORY_DELAY = 200;
// Timeline/region editing stays enabled on mobile; no auto-locking based on coarse pointers.

const MIN_SEG_LEN = 0.05;     // shortest allowable segment (50 ms)
const DEFAULT_SEG_LEN = 2.0;  // default duration for newly inserted lines
const TIME_EPS = 0.001;       // small guard delta for ordering

// ------------------------------
// DOM helpers
// ------------------------------
function $(sel) { return document.querySelector(sel); }
function el(tag, props = {}, ...children) { const e = document.createElement(tag); Object.assign(e, props); for (const c of children) e.append(c); return e; }
async function fetchJSON(url, opts = {}) {
  const r = await fetch(
    url,
    Object.assign({
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    }, opts)
  );
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}
function fmt(sec) { return (Math.max(0, sec) || 0).toFixed(2) + "s"; }
function roundTime(value) { return Math.round(Math.max(0, value) * 1000) / 1000; }
function generateSegmentId() {
  const used = new Set((project?.segments || []).map((s) => s.id));
  let candidate = `seg_${Date.now().toString(36)}`;
  while (used.has(candidate)) {
    candidate = `seg_${Math.random().toString(36).slice(2, 8)}`;
  }
  return candidate;
}

function renumberSegments({ focusId = null, focusIndex = null } = {}) {
  if (!project?.segments?.length) return null;
  let mapped = null;
  project.segments.forEach((seg, idx) => {
    const oldId = seg.id;
    const newId = `L${idx + 1}`;
    seg.id = newId;
    if (focusId && oldId === focusId) mapped = newId;
    if (focusIndex !== null && idx === focusIndex) mapped = newId;
  });
  if (mapped && currentId && (currentId === focusId || (focusIndex !== null && focusIndex < project.segments.length))) {
    currentId = mapped;
  }
  return mapped;
}

// ------------------------------
// State helpers
// ------------------------------
function setDirty(v) { dirty = v; $("#dirty")?.classList.toggle("hidden", !dirty); scheduleAutosave(); }
function findSeg(id) { return project.segments.find(s => s.id === id); }

function mapSegments(raw) {
  return (raw || []).map((s, i) => {
    const words = Array.isArray(s.words)
      ? s.words.map((w, idx) => ({
        text: typeof w.text === "string" ? w.text : `Word ${idx + 1}`,
        start: Number(w.start ?? s.start ?? 0),
        end: Number(w.end ?? s.end ?? 0),
      }))
      : undefined;
    return {
      ...s,
      id: s.id ?? `L${i + 1}`,
      start: Number(s.start ?? 0),
      end: Number(s.end ?? 0),
      text_auto: s.text_auto ?? s.text ?? "",
      text_user: s.text_user ?? "",
      text_source: s.text_user ? "user" : (s.text_source ?? "auto"),
      words,
    };
  });
}

function hasWordData(seg) { return Array.isArray(seg?.words) && seg.words.length > 0; }

// ------------------------------
// History (Undo / Redo)
// ------------------------------
const hist = { stack: [], idx: -1, max: 75 };
function snapshot() { return JSON.parse(JSON.stringify({ segments: project?.segments || [] })); }
function pushHistory() {
  if (!project) return;
  // trim forward revisions
  hist.stack = hist.stack.slice(0, hist.idx + 1);
  hist.stack.push(snapshot());
  if (hist.stack.length > hist.max) hist.stack.shift();
  hist.idx = hist.stack.length - 1;
}
function applyState(state) {
  if (!state) return;
  project.segments = JSON.parse(JSON.stringify(state.segments));
  renumberSegments({ focusId: currentId });
  rebuildRegions();
  refreshList();
  if (project.segments.length) {
    const keep = project.segments.find(s => s.id === currentId) || project.segments[0];
    selectSeg(keep.id);
  }
  setDirty(true);
}
function undo() { if (hist.idx > 0) { hist.idx--; applyState(hist.stack[hist.idx]); } }
function redo() { if (hist.idx < hist.stack.length - 1) { hist.idx++; applyState(hist.stack[hist.idx]); } }
// Record a single history snapshot once the user finishes dragging/resizing a region.
function scheduleRegionHistorySnapshot() {
  if (regionHistoryTimer) clearTimeout(regionHistoryTimer);
  regionHistoryTimer = setTimeout(() => {
    regionHistoryTimer = null;
    if (!dirty) return;
    pushHistory();
  }, REGION_HISTORY_DELAY);
}

// ------------------------------
// Autosave
// ------------------------------
let autosaveT = null;
function scheduleAutosave() {
  if (autosaveT) clearTimeout(autosaveT);
  autosaveT = setTimeout(() => { if (dirty) saveProject().catch(() => { }); }, 1500);
}

// Protect against closing with unsaved edits
window.addEventListener("beforeunload", (e) => { if (!dirty) return; e.preventDefault(); e.returnValue = ""; });

// ------------------------------
// Rendering helpers
// ------------------------------
function refreshList() {
  if (!project?.segments) return;
  const ul = $("#lines"); if (!ul) return;
  ul.innerHTML = "";

  if (editMode === 'words') {
    // WORD MODE LIST
    let count = 0;
    project.segments.forEach((seg, sIdx) => {
      if (!Array.isArray(seg.words)) return;
      seg.words.forEach((w, wIdx) => {
        count++;
        const wordId = `W:${seg.id}:${wIdx}`;
        const li = el("li", { textContent: `${count}: ${w.text || "(empty)"}` });
        li.dataset.id = wordId;
        if (wordId === currentId) li.classList.add("active");
        li.onclick = () => selectSeg(wordId, true);
        ul.append(li);
      });
    });
  } else {
    // LINE MODE LIST
    project.segments.forEach(seg => {
      const li = el("li", { textContent: `${seg.id}: ${displayText(seg)}` });
      li.dataset.id = seg.id;
      if (seg.id === currentId) li.classList.add("active");
      li.onclick = () => selectSeg(seg.id, true);
      ul.append(li);
    });
  }
}

function toggleWordTools() {
  const wrap = $("#wordTools");
  if (!wrap) return;
  wrap.classList.toggle("hidden", !supportsWordTiming);
  if (!supportsWordTiming) {
    setWordToolState(false);
  } else {
    setWordToolState(hasWordData(findSeg(currentId)));
  }
}

function setWordToolState(enabled) {
  document.querySelectorAll(".fuzzy-btn").forEach(btn => { btn.disabled = !enabled; });
  const size = $("#fuzzySize");
  if (size) size.disabled = !enabled;
}

function refreshWordPanel(seg) {
  const wrap = $("#wordTools");
  const list = $("#wordList");
  if (!wrap || !list) return;
  if (!supportsWordTiming) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  const hasWords = hasWordData(seg);
  setWordToolState(hasWords);
  if (!hasWords) {
    list.innerHTML = "<p class='muted'>No word timings for this line.</p>";
    return;
  }
  list.innerHTML = "";
  seg.words.forEach((w, idx) => {
    const row = document.createElement("div");
    row.className = "word-row";
    const text = document.createElement("div");
    text.className = "word-text";
    text.textContent = w.text || `Word ${idx + 1}`;
    const startWrap = document.createElement("label");
    startWrap.textContent = "Start";
    const startInput = document.createElement("input");
    startInput.type = "number";
    startInput.step = "0.01";
    startInput.value = Number(w.start ?? seg.start).toFixed(2);
    startInput.dataset.index = idx;
    startInput.dataset.field = "start";
    const endWrap = document.createElement("label");
    endWrap.textContent = "End";
    const endInput = document.createElement("input");
    endInput.type = "number";
    endInput.step = "0.01";
    endInput.value = Number(w.end ?? seg.end).toFixed(2);
    endInput.dataset.index = idx;
    endInput.dataset.field = "end";
    startWrap.appendChild(startInput);
    endWrap.appendChild(endInput);
    row.appendChild(text);
    row.appendChild(startWrap);
    row.appendChild(endWrap);
    list.appendChild(row);
  });
}

function selectSeg(id, scroll = false) {
  // Handle Word Selection
  if (id && id.startsWith("W:")) {
    const parts = id.split(":");
    const segId = parts[1];
    const wIdx = parseInt(parts[2], 10);
    const seg = findSeg(segId);

    if (seg && seg.words && seg.words[wIdx]) {
      currentId = id;
      const w = seg.words[wIdx];

      // Update UI for Word
      $("#selNone")?.classList.add("hidden");
      $("#selPanel")?.classList.remove("hidden");

      // Reuse existing inputs
      $("#selText").value = w.text;
      $("#selStart").value = w.start.toFixed(3);
      $("#selEnd").value = w.end.toFixed(3);
      $("#selDur").value = (w.end - w.start).toFixed(3) + "s";

      // Hide Line-specific tools
      toggleLineTools(false);

      // Highlight region
      const r = regions.getRegions().find(x => x.id === id);
      if (r) {
        regions.getRegions().forEach(x => x.element.classList.remove("selected"));
        r.element.classList.add("selected");
        if (scroll) wavesurfer.setTime(w.start + 0.01);
      }
      return;
    }
  }

  // Handle Line Selection (Legacy)
  currentId = id;
  document.querySelectorAll("#lines li").forEach(li => li.classList.toggle("active", li.dataset.id === id));
  const seg = findSeg(id); if (!seg) return;

  $("#selNone")?.classList.add("hidden");
  $("#selPanel")?.classList.remove("hidden");

  // Show Line tools
  toggleLineTools(true);

  $("#selText").value = displayText(seg);
  refreshWordPanel(seg);
  $("#selStart").value = seg.start.toFixed(3);
  $("#selEnd").value = seg.end.toFixed(3);
  $("#selDur").value = (seg.end - seg.start).toFixed(3) + "s";

  const r = regions.getRegions().find(r => r.id === id);
  if (r) {
    regions.getRegions().forEach(x => x.element.classList.remove("selected"));
    r.element.classList.add("selected");
    if (scroll) wavesurfer.setTime(seg.start + 0.01);
  }
}

function toggleLineTools(show) {
  // Hide/Show tools that don't make sense for Words (Split/Merge/Add/Delete Line)
  const buttons = ["#split", "#merge", "#addLineAfter", "#deleteLine", "#btnCopyLines", "#btnPasteLines", ".trim-row"];
  buttons.forEach(sel => {
    const el = $(sel);
    if (el) el.classList.toggle("hidden", !show);
  });
  // Also hide Word Tools panel if we are IN word mode (since we are editing a word directly)
  const wordTools = $("#wordTools");
  if (wordTools) {
    if (!show) wordTools.classList.add("hidden");
    else toggleWordTools(); // Restore normal state
  }
}

function rebuildRegions() {
  if (!regions) return;

  // Clear existing regions (v7-safe)
  const existing = regions?.getRegions?.() || [];
  for (const r of existing) {
    if (r && typeof r.remove === "function") r.remove();
  }

  const segs = Array.isArray(project?.segments) ? project.segments : [];

  if (editMode === 'words') {
    // WORD MODE
    segs.forEach((seg, sIdx) => {
      if (!Array.isArray(seg.words)) return;
      seg.words.forEach((w, wIdx) => {
        const wordId = `W:${seg.id}:${wIdx}`;
        regions.addRegion({
          id: wordId,
          start: Number(w.start),
          end: Number(w.end),
          content: w.text || "",
          drag: true,
          resize: true,
          color: "rgba(46, 204, 113, 0.25)", // Greenish for words
        });
      });
    });
  } else {
    // LINE MODE (SRT)
    for (const seg of segs) {
      regions.addRegion({
        id: seg.id,
        start: Number(seg.start) || 0,
        end: Number(seg.end) || 0,
        content: displayText(seg) ?? "",
        drag: true,
        resize: true,
        color: "rgba(122,162,247,0.12)",
      });
    }
  }
  applyRegionTouchState();
}

function applyRegionTouchState() {
  if (!regions) return;
  const list = regions.getRegions?.() || [];
  list.forEach((reg) => {
    reg.drag = true;
    reg.resize = true;
    reg.element?.classList?.remove("touch-edit-locked");
    reg.element?.removeAttribute("aria-disabled");
    reg.element?.style?.removeProperty("touch-action");
  });
}

// ------------------------------
// Validation & micro-fixes
// ------------------------------
function validateSegments(threshold = 0.05) {
  const issues = [];
  const segs = [...project.segments].sort((a, b) => a.start - b.start);
  for (let i = 0; i < segs.length - 1; i++) {
    const a = segs[i], b = segs[i + 1];
    const gap = b.start - a.end;
    if (gap < -threshold) issues.push({ type: "overlap", a: a.id, b: b.id, amount: gap });
    else if (gap > threshold) issues.push({ type: "gap", a: a.id, b: b.id, amount: gap });
  }
  return issues;
}
function autoFixMicro(threshold = 0.05) {
  const segs = [...project.segments].sort((a, b) => a.start - b.start);
  let changed = false;
  for (let i = 0; i < segs.length - 1; i++) {
    const a = segs[i], b = segs[i + 1];
    const gap = b.start - a.end;
    if (gap < 0 && Math.abs(gap) <= threshold) {
      const mid = (a.end + b.start) / 2; a.end = mid; b.start = mid; changed = true;
    } else if (gap > 0 && gap <= threshold) {
      const mid = (a.end + b.start) / 2; a.end = mid; b.start = mid; changed = true;
    }
  }
  if (changed) { rebuildRegions(); setDirty(true); refreshList(); if (currentId) selectSeg(currentId); }
  return changed;
}

// ------------------------------
// Project I/O
// ------------------------------
async function saveProject() {
  // persist both lanes + chosen output
  const payload = {
    segments: project.segments.map(s => ({
      id: s.id, start: s.start, end: s.end,
      text_auto: s.text_auto ?? "",
      text_user: s.text_user ?? "",
      text_source: s.text_source ?? "auto",
      // server can also read 'text' if it expects a single field
      text: displayText(s),
      ...(Array.isArray(s.words) && s.words.length
        ? { words: s.words.map(w => ({ text: w.text ?? "", start: w.start, end: w.end })) }
        : {})
    }))
  };
  await fetchJSON(`/api/projects/${SLUG}/timing`, { method: "POST", body: JSON.stringify(payload) });
  setDirty(false);
  const st = $("#status"); if (st) { st.textContent = "Saved."; setTimeout(() => st.textContent = "", 1200); }
}
async function exportSrt() {
  await saveProject();
  const r = await fetchJSON(`/api/projects/${SLUG}/export_srt`, { method: "POST" });
  const st = $("#status"); if (st) st.textContent = "Exported SRT → " + r.path;
}
async function importSrt() {
  await fetchJSON(`/api/projects/${SLUG}/import_srt`, { method: "POST" }); project = await fetchJSON(`/api/projects/${SLUG}/timing`); project = await fetchJSON(`/api/projects/${SLUG}/timing`);
  project.segments = mapSegments(project.segments);
  supportsWordTiming = project.segments.some(hasWordData);
  toggleWordTools();
  renumberSegments();
  rebuildRegions(); refreshList();
  if (project.segments.length) selectSeg(project.segments[0].id);
  const st = $("#status"); if (st) st.textContent = "Imported SRT.";
}
function cleanSrtText(txt) {
  return txt.replace(/^\uFEFF/, '')        // BOM
    .replace(/^WEBVTT.*\n+/i, '')  // VTT header
    .replace(/\r\n?/g, '\n');      // normalize
}

// ------------------------------
// Editing helpers
// ------------------------------
function shiftFollowingSegments(startIndex, delta, durationLimit) {
  if (!project?.segments?.length || !delta || delta <= 0) return;
  for (let i = startIndex; i < project.segments.length; i++) {
    const seg = project.segments[i];
    if (!seg) continue;
    const prev = i > 0 ? project.segments[i - 1] : null;

    seg.start = roundTime(seg.start + delta);
    seg.end = roundTime(seg.end + delta);

    if (prev && seg.start < prev.end + TIME_EPS) {
      seg.start = roundTime(prev.end + TIME_EPS);
    }

    if (seg.end <= seg.start + MIN_SEG_LEN) {
      seg.end = roundTime(seg.start + MIN_SEG_LEN);
    }

    if (Number.isFinite(durationLimit)) {
      if (seg.end > durationLimit) {
        seg.end = roundTime(durationLimit);
      }
      if (seg.start > durationLimit - MIN_SEG_LEN) {
        seg.start = roundTime(Math.max(0, durationLimit - MIN_SEG_LEN));
        if (prev && seg.start < prev.end + TIME_EPS) {
          seg.start = roundTime(prev.end + TIME_EPS);
        }
        if (seg.end <= seg.start) {
          seg.end = roundTime(seg.start + TIME_EPS);
        }
      }
    }
  }
}

function addLineAfterCurrent() {
  if (!project) return;
  project.segments = Array.isArray(project.segments) ? project.segments : [];
  const segs = project.segments;
  const idx = currentId ? segs.findIndex((s) => s.id === currentId) : segs.length - 1;
  const insertPos = idx >= 0 ? idx + 1 : segs.length;
  const prev = insertPos > 0 ? segs[insertPos - 1] : null;
  const next = insertPos < segs.length ? segs[insertPos] : null;

  const duration = wavesurfer?.getDuration?.();
  const durationFinite = Number.isFinite(duration);
  const playhead = wavesurfer?.getCurrentTime?.();

  const guardStart = prev ? prev.end + TIME_EPS : 0;
  const guardEnd = (() => {
    if (next) {
      return Math.max(guardStart + MIN_SEG_LEN, next.start - TIME_EPS);
    }
    if (durationFinite) {
      return Math.max(guardStart + MIN_SEG_LEN, duration);
    }
    return guardStart + DEFAULT_SEG_LEN;
  })();

  let start = Number.isFinite(playhead) ? playhead : guardStart;
  if (!Number.isFinite(start)) start = guardStart;
  start = Math.max(guardStart, Math.min(start, guardEnd - MIN_SEG_LEN));
  start = roundTime(start);

  let end = start + DEFAULT_SEG_LEN;
  end = Math.min(end, guardEnd);
  if (end <= start) {
    end = start + MIN_SEG_LEN;
  }
  if (durationFinite) {
    end = Math.min(end, duration);
  }
  end = roundTime(Math.max(start + MIN_SEG_LEN, end));

  const placeholderId = generateSegmentId();
  const newSeg = {
    id: placeholderId,
    start,
    end,
    text_auto: "",
    text_user: "",
    text_source: "user",
  };

  segs.splice(insertPos, 0, newSeg);
  const assignedId = renumberSegments({ focusId: placeholderId, focusIndex: insertPos }) || (project.segments[insertPos]?.id);
  rebuildRegions();
  refreshList();
  if (assignedId) {
    selectSeg(assignedId, true);
  } else {
    selectSeg(newSeg.id, true);
  }
  setDirty(true);
  pushHistory();
}

function copySelectedLines() {
  if (!project?.segments?.length || !currentId) return;
  const startIdx = project.segments.findIndex((s) => s.id === currentId);
  if (startIdx === -1) return;
  let count = 1;
  try {
    const response = window.prompt("How many lines would you like to copy?", "1");
    if (response !== null) {
      const parsed = parseInt(response, 10);
      if (Number.isFinite(parsed) && parsed > 0) {
        count = parsed;
      }
    }
  } catch { }
  copyBuffer = [];
  for (let i = 0; i < count; i++) {
    const seg = project.segments[startIdx + i];
    if (!seg) break;
    const origin = seg.start;
    const duration = Math.max(MIN_SEG_LEN, (seg.end - seg.start) || MIN_SEG_LEN);
    const relWords = Array.isArray(seg.words)
      ? seg.words.map((w) => ({
        text: w.text || "",
        startOffset: Number(w.start ?? origin) - origin,
        endOffset: Number(w.end ?? origin) - origin,
      }))
      : null;
    copyBuffer.push({
      duration,
      text_auto: seg.text_auto ?? "",
      text_user: seg.text_user ?? "",
      text_source: seg.text_source ?? "auto",
      words: relWords,
    });
  }
  const st = $("#status");
  if (st) {
    const n = copyBuffer.length;
    st.textContent = n ? `Copied ${n} line${n === 1 ? "" : "s"}.` : "Nothing copied.";
    setTimeout(() => { if (st.textContent?.startsWith("Copied") || st.textContent === "Nothing copied.") st.textContent = ""; }, 1400);
  }
}

function pasteCopiedLines() {
  if (!project?.segments || !copyBuffer.length) {
    const st = $("#status");
    if (st) {
      st.textContent = "Copy lines first.";
      setTimeout(() => { if (st.textContent === "Copy lines first.") st.textContent = ""; }, 1200);
    }
    return;
  }
  const segs = project.segments;
  const idx = currentId ? segs.findIndex((s) => s.id === currentId) : segs.length - 1;
  const insertPos = idx >= 0 ? idx + 1 : segs.length;
  const prev = insertPos > 0 ? segs[insertPos - 1] : null;
  const next = insertPos < segs.length ? segs[insertPos] : null;
  let startCursor = prev ? prev.end + TIME_EPS : 0;
  const duration = wavesurfer?.getDuration?.();
  const durationFinite = Number.isFinite(duration);
  if (durationFinite) {
    startCursor = Math.min(startCursor, Math.max(0, duration - MIN_SEG_LEN));
  }
  const newSegments = [];
  let cursor = roundTime(Math.max(0, startCursor));
  for (const template of copyBuffer) {
    if (durationFinite && cursor >= duration) break;
    const segDuration = Math.max(MIN_SEG_LEN, Number(template.duration) || MIN_SEG_LEN);
    let end = cursor + segDuration;
    if (durationFinite) end = Math.min(end, duration);
    const newSeg = {
      id: generateSegmentId(),
      start: roundTime(cursor),
      end: roundTime(Math.max(cursor + MIN_SEG_LEN, end)),
      text_auto: template.text_auto || "",
      text_user: template.text_user || "",
      text_source: template.text_user ? "user" : (template.text_source || "auto"),
    };
    if (Array.isArray(template.words)) {
      const origin = newSeg.start;
      newSeg.words = template.words.map((w) => {
        const start = origin + Math.max(0, Number(w.startOffset) || 0);
        const end = origin + Math.max(0, Number(w.endOffset) || 0.005);
        return {
          text: w.text || "",
          start: roundTime(Math.min(end, start)),
          end: roundTime(Math.max(end, start + 0.005)),
        };
      });
    }
    newSegments.push(newSeg);
    cursor = newSeg.end + TIME_EPS;
  }
  if (!newSegments.length) return;
  const blockEnd = newSegments[newSegments.length - 1].end;
  const overlapDelta = next ? Math.max(0, (blockEnd + TIME_EPS) - next.start) : 0;
  segs.splice(insertPos, 0, ...newSegments);
  if (overlapDelta > 0) {
    shiftFollowingSegments(insertPos + newSegments.length, overlapDelta, durationFinite ? duration : undefined);
  }
  rebuildRegions();
  refreshList();
  selectSeg(newSegments[0].id, true);
  setDirty(true);
  pushHistory();
  const st = $("#status");
  if (st) {
    st.textContent = `Pasted ${newSegments.length} line${newSegments.length === 1 ? "" : "s"}.`;
    setTimeout(() => { if (st.textContent?.startsWith("Pasted")) st.textContent = ""; }, 1400);
  }
}

function deleteCurrentLine({ confirm: shouldConfirm = true } = {}) {
  if (!project?.segments?.length || !currentId) return;
  if (shouldConfirm && !window.confirm("Delete selected line?")) return;
  const idx = project.segments.findIndex((s) => s.id === currentId);
  if (idx === -1) return;

  project.segments.splice(idx, 1);
  const fallbackIndex = Math.min(idx, project.segments.length - 1);
  const fallbackId = fallbackIndex >= 0 ? renumberSegments({ focusIndex: fallbackIndex }) : null;
  rebuildRegions();
  refreshList();

  if (fallbackId) {
    selectSeg(fallbackId, true);
  } else {
    currentId = null;
    $("#selPanel")?.classList.add("hidden");
    $("#selNone")?.classList.remove("hidden");
  }

  setDirty(true);
  pushHistory();
}

function nudgeSelected(delta) { if (!currentId) return; const s = findSeg(currentId); if (!s) return; s.start = Math.max(0, s.start + delta); s.end = Math.max(s.start + 0.01, s.end + delta); rebuildRegions(); selectSeg(currentId); setDirty(true); pushHistory(); }
function trimToCursor(which) { if (!currentId) return; const s = findSeg(currentId); const t = wavesurfer.getCurrentTime(); if (which === 'start') s.start = Math.min(t, s.end - 0.01); else s.end = Math.max(t, s.start + 0.01); rebuildRegions(); selectSeg(currentId); setDirty(true); pushHistory(); }
function splitAtCursor() {
  if (!currentId) return;
  const s = findSeg(currentId); if (!s) return;
  const t = wavesurfer.getCurrentTime();
  if (t <= s.start + 0.05 || t >= s.end - 0.05) return;

  const idx = project.segments.findIndex(x => x.id === s.id);

  const left = {
    id: s.id,
    start: s.start, end: t,
    text_auto: s.text_auto, text_user: s.text_user, text_source: s.text_source
  };
  const right = {
    id: "L" + (project.segments.length + 1),
    start: t, end: s.end,
    text_auto: s.text_auto, text_user: s.text_user, text_source: s.text_source
  };

  project.segments.splice(idx, 1, left, right);
  const rightId = renumberSegments({ focusIndex: idx + 1 }) || right.id;
  rebuildRegions(); refreshList(); selectSeg(rightId);
  setDirty(true); pushHistory();
}

function mergeWithNext() {
  if (!currentId) return;
  const idx = project.segments.findIndex(x => x.id === currentId);
  if (idx < 0 || idx >= project.segments.length - 1) return;
  const a = project.segments[idx], b = project.segments[idx + 1];

  a.end = b.end;

  // merge texts: prefer user text if any; fall back to auto.
  const aTxt = displayText(a).trim();
  const bTxt = displayText(b).trim();
  const merged = (aTxt && bTxt) ? (aTxt + " " + bTxt) : (aTxt || bTxt);

  // keep auto and user lanes in sync:
  // - if either had user text, merged result becomes user text
  if ((a.text_source === "user" && a.text_user) || (b.text_source === "user" && b.text_user)) {
    a.text_user = merged;
    a.text_source = "user";
  } else {
    a.text_auto = merged;
    a.text_source = "auto";
  }

  project.segments.splice(idx + 1, 1);
  const mergedId = renumberSegments({ focusIndex: idx }) || a.id;
  rebuildRegions(); selectSeg(mergedId, true); refreshList(); selectSeg(mergedId);
  setDirty(true); pushHistory();
}

function getFuzzySize() {
  const size = parseFloat($("#fuzzySize")?.value ?? "0.02");
  return Number.isFinite(size) ? size : 0.02;
}

function triggerFuzzyNudge(direction) {
  if (!supportsWordTiming || !currentId) return;
  const seg = findSeg(currentId);
  if (!seg || !hasWordData(seg)) return;
  const idx = project.segments.findIndex(s => s.id === seg.id);
  if (idx < 0) return;
  const amount = getFuzzySize() * Number(direction || 0);
  if (!amount) return;
  const prev = project.segments[idx - 1];
  const next = project.segments[idx + 1];
  const duration = wavesurfer?.getDuration?.();
  let startCandidate = seg.start + amount;
  let endCandidate = seg.end + amount;

  if (prev && startCandidate < prev.end + TIME_EPS) {
    const correction = (prev.end + TIME_EPS) - startCandidate;
    startCandidate += correction;
    endCandidate += correction;
  }
  if (startCandidate < 0) {
    endCandidate -= startCandidate;
    startCandidate = 0;
  }
  if (next && endCandidate > next.start - TIME_EPS) {
    const correction = endCandidate - (next.start - TIME_EPS);
    startCandidate -= correction;
    endCandidate -= correction;
  }
  if (duration && endCandidate > duration) {
    const correction = endCandidate - duration;
    startCandidate -= correction;
    endCandidate -= correction;
  }
  if (endCandidate <= startCandidate + MIN_SEG_LEN) return;
  const appliedDelta = startCandidate - seg.start;
  if (!appliedDelta) return;
  seg.start = roundTime(startCandidate);
  seg.end = roundTime(endCandidate);
  if (Array.isArray(seg.words)) {
    seg.words.forEach((w) => {
      w.start = roundTime(Math.max(seg.start, Math.min(seg.end, w.start + appliedDelta)));
      w.end = roundTime(Math.max(w.start + 0.005, Math.min(seg.end, w.end + appliedDelta)));
    });
  }
  const reg = regions?.getRegions().find(r => r.id === seg.id);
  reg?.setOptions({ start: seg.start, end: seg.end });
  refreshWordPanel(seg);
  setDirty(true);
  pushHistory();
}

function handleWordInputChange(evt) {
  const input = evt.target;
  if (!(input instanceof HTMLInputElement)) return;
  const idx = Number(input.dataset.index);
  const field = input.dataset.field;
  if (!Number.isFinite(idx) || !field) return;
  const seg = findSeg(currentId);
  if (!seg || !Array.isArray(seg.words) || !seg.words[idx]) return;
  let value = parseFloat(input.value);
  if (!Number.isFinite(value)) return;
  if (field === "start") {
    value = Math.max(seg.start, Math.min(value, seg.words[idx].end - 0.005));
    seg.words[idx].start = roundTime(value);
  } else {
    value = Math.min(seg.end, Math.max(value, seg.words[idx].start + 0.005));
    seg.words[idx].end = roundTime(value);
  }
  input.value = seg.words[idx][field].toFixed(2);
  setDirty(true);
}

function displayText(seg) {
  return (seg?.text_source === "user" ? seg.text_user : seg.text_auto) || "";
}
function setUserText(seg, txt) {
  if (!seg) return;
  const clean = txt.replace(/\r\n?/g, "\n").trimEnd();
  seg.text_user = clean;
  seg.text_source = "user";
}
function setAutoText(seg, txt) {
  if (!seg) return;
  seg.text_auto = txt;
  // do not flip text_source if user has taken over
}

function handleManualTimeInput(kind, rawValue) {
  if (!project || !currentId) return;
  const seg = findSeg(currentId);
  if (!seg) return;

  const idx = project.segments.findIndex((s) => s.id === seg.id);
  if (idx === -1) return;

  let value = Number(rawValue);
  if (!Number.isFinite(value)) {
    selectSeg(seg.id);
    return;
  }

  value = roundTime(value);

  const prev = idx > 0 ? project.segments[idx - 1] : null;
  const next = idx < project.segments.length - 1 ? project.segments[idx + 1] : null;
  const duration = wavesurfer?.getDuration?.();
  const durationFinite = Number.isFinite(duration);

  let newStart = seg.start;
  let newEnd = seg.end;

  if (kind === "start") {
    newStart = value;
    if (!Number.isFinite(newStart)) newStart = seg.start;
    if (durationFinite) newStart = Math.min(newStart, roundTime(Math.max(0, duration - MIN_SEG_LEN)));
    if (prev) newStart = Math.max(newStart, roundTime(prev.end + TIME_EPS));
    if (next) newStart = Math.min(newStart, roundTime(next.start - MIN_SEG_LEN));
    newStart = Math.max(0, newStart);
    if (newStart > newEnd - MIN_SEG_LEN) {
      newStart = roundTime(Math.max(newEnd - MIN_SEG_LEN, prev ? prev.end + TIME_EPS : 0));
    }
  } else if (kind === "end") {
    newEnd = value;
    if (!Number.isFinite(newEnd)) newEnd = seg.end;
    newEnd = Math.max(newEnd, roundTime(seg.start + MIN_SEG_LEN));
    if (next) newEnd = Math.min(newEnd, roundTime(next.start - TIME_EPS));
    if (durationFinite) newEnd = Math.min(newEnd, roundTime(duration));
  }

  if (newEnd <= newStart + MIN_SEG_LEN) {
    if (kind === "start") {
      newStart = roundTime(Math.max(0, newEnd - MIN_SEG_LEN));
      if (prev) newStart = Math.max(newStart, roundTime(prev.end + TIME_EPS));
    } else {
      newEnd = roundTime(newStart + MIN_SEG_LEN);
      if (durationFinite) newEnd = Math.min(newEnd, roundTime(duration));
      if (next) newEnd = Math.min(newEnd, roundTime(next.start - TIME_EPS));
    }
  }

  if (durationFinite) {
    if (newStart >= duration) {
      newStart = roundTime(Math.max(0, duration - MIN_SEG_LEN));
    }
    if (newEnd > duration) {
      newEnd = roundTime(duration);
    }
  }

  newStart = roundTime(newStart);
  newEnd = roundTime(newEnd);

  if (next && newStart >= next.start - MIN_SEG_LEN) {
    newStart = roundTime(Math.max(prev ? prev.end + TIME_EPS : 0, next.start - MIN_SEG_LEN));
  }
  if (prev && newEnd <= prev.end + MIN_SEG_LEN) {
    newEnd = roundTime(Math.max(newStart + MIN_SEG_LEN, prev.end + MIN_SEG_LEN));
  }

  const changed = (Math.abs(newStart - seg.start) > TIME_EPS) || (Math.abs(newEnd - seg.end) > TIME_EPS);
  if (!changed) {
    selectSeg(seg.id);
    return;
  }

  const oldEnd = seg.end;
  seg.start = newStart;
  seg.end = newEnd;

  if (kind === "end" && $("#ripple")?.checked) {
    const delta = seg.end - oldEnd;
    if (Math.abs(delta) > TIME_EPS) {
      shiftFollowingSegments(idx + 1, delta, durationFinite ? duration : undefined);
    }
  }

  rebuildRegions();
  refreshList();
  selectSeg(seg.id);
  setDirty(true);
  pushHistory();
}


// ------------------------------
// Main loader (creates WaveSurfer + plugins, then attaches handlers)
// ------------------------------
async function loadProject() {
  if (wavesurfer) destroyWave();
  project = await fetchJSON(`/api/projects/${SLUG}/timing`);
  // Normalize segments: keep auto + user text separately
  project.segments = mapSegments(project.segments);
  supportsWordTiming = project.segments.some(hasWordData);
  toggleWordTools();
  renumberSegments();


  wavesurfer = WaveSurfer.create({
    container: '#wave',
    waveColor: '#7aa2f7',
    progressColor: '#9ece6a',
    height: 200,
    url: `/api/projects/${SLUG}/audio`,
    minPxPerSec: 60
  });

  timeline = wavesurfer.registerPlugin(WaveSurfer.Timeline.create({ container: '#timeline' }));
  regions = wavesurfer.registerPlugin(WaveSurfer.Regions.create());

  // unified timeupdate (clock + loop current segment)
  wavesurfer.on('timeupdate', (t) => {
    $("#time").textContent = fmt(t);
    if (loopSelected && currentId) {
      const s = findSeg(currentId);
      if (s && t >= s.end) wavesurfer.setTime(Math.max(0, s.start + 0.005));
    }
  });
  const MIN_LEN = 0.05; // 50ms
  function clampRegion(seg, prev, next, dur) {
    seg.start = Math.max(0, seg.start);
    seg.end = Math.min(dur, Math.max(seg.start + MIN_LEN, seg.end));
    if (prev) seg.start = Math.max(prev.end + 0.001, seg.start);
    if (next) seg.end = Math.min(next.start - 0.001, seg.end);
  }

  // Enhanced region-updated: snap to 10ms, guard overlaps, optional ripple
  regions.on('region-updated', (reg) => {
    // Handle Word Updates
    if (reg.id.startsWith("W:")) {
      const parts = reg.id.split(":");
      const segId = parts[1];
      const wIdx = parseInt(parts[2], 10);
      const seg = findSeg(segId);
      if (seg && seg.words && seg.words[wIdx]) {
        seg.words[wIdx].start = Math.round(reg.start * 1000) / 1000;
        seg.words[wIdx].end = Math.round(reg.end * 1000) / 1000;
        setDirty(true);
        // Update sidebar input if this word is selected
        if (currentId === reg.id) {
          $("#selStart").value = seg.words[wIdx].start.toFixed(3);
          $("#selEnd").value = seg.words[wIdx].end.toFixed(3);
          $("#selDur").value = (seg.words[wIdx].end - seg.words[wIdx].start).toFixed(3) + "s";
        }
      }
      return;
    }

    // Handle Segment Updates (Legacy)
    const seg = findSeg(reg.id); if (!seg) return;
    const q10 = (x) => Math.max(0, Math.round(x * 100) / 100);

    const oldStart = seg.start;
    let newStart = q10(reg.start);
    let newEnd = q10(reg.end);

    if (newEnd < newStart + 0.01) newEnd = newStart + 0.01;

    const idx = project.segments.findIndex(s => s.id === seg.id);
    const prev = project.segments[idx - 1];
    const next = project.segments[idx + 1];
    if (prev) newStart = Math.max(newStart, q10(prev.end));
    if (next) newEnd = Math.min(newEnd, q10(next.start));

    seg.start = newStart; seg.end = newEnd;

    if ($("#ripple")?.checked) {
      const delta = newStart - oldStart; let hit = false;
      for (const s of project.segments) {
        if (s.id === seg.id) { hit = true; continue; }
        if (hit) { s.start = q10(s.start + delta); s.end = q10(s.end + delta); }
      }
      rebuildRegions();
    }

    setDirty(true); selectSeg(seg.id); refreshList();
    scheduleRegionHistorySnapshot();
    // If you want each drag to be an undo step, uncomment:
    // pushHistory();
  });

  // Click inside a region sets playhead EXACTLY where you click (not just at start) 
  regions.on('region-clicked', (r, e) => {
    e.stopPropagation();
    // compute click position within the region element
    const rect = r.element.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, (e.clientX ?? 0) - rect.left));
    const frac = rect.width ? (x / rect.width) : 0;
    const t = r.start + frac * (r.end - r.start);

    // select the segment but DON'T auto-jump to its start
    selectSeg(r.id, false);
    // place the playhead at the clicked time
    if (Number.isFinite(t)) wavesurfer.setTime(Math.max(0, t));
    // if already playing, continue playback from the clicked position
    if (wavesurfer.isPlaying && wavesurfer.isPlaying()) wavesurfer.play();
  });

  //All fetches should set headers and handle non-200s uniformly so failures surface in the UI.
  async function jsonFetch(url, opts = {}) {
    const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json();
  }

  //Fix memory leaks on navigation
  function teardown() {
    destroyWave();
    window.removeEventListener('keydown', keyHandler);
    // remove any intervals/observers
  }

  //Avoid rebuilding the entire <ul id="lines"> on tiny changes. Patch the one row or batch DOM updates in a fragment.
  function toSrtTime(sec) {
    const ms = Math.max(0, Math.round(sec * 1000));
    const h = Math.floor(ms / 3600000);
    const m = Math.floor(ms % 3600000 / 60000);
    const s = Math.floor(ms % 60000 / 1000);
    const mm = String(m).padStart(2, '0');
    const ss = String(s).padStart(2, '0');
    const mmm = String(ms % 1000).padStart(3, '0');
    return `${String(h).padStart(2, '0')}:${mm}:${ss},${mmm}`;
  }
  //Reset the input after importing srt
  const fileInput = document.getElementById('importFile');
  fileInput?.addEventListener('change', async (e) => {
    // ...process file...
    e.target.value = ''; // allow picking same file again later
  });

  function rebuildLinesFast(list) {
    const frag = document.createDocumentFragment();
    for (const li of list) frag.append(li);
    const UL = document.getElementById('lines');
    UL.replaceChildren(frag);
  }

  wavesurfer.on('ready', () => {

    rebuildRegions();
    ensureValidSelection();
    refreshList();
    if (project.segments.length) selectSeg(project.segments[0].id);
    // seed history so you can undo future edits
    pushHistory();
    wireButtonsOnce();
  });
}

// ------------------------------
// Keyboard + UI wiring
// ------------------------------

// Allow tapping/clicking on the waveform/timeline to place the playhead anywhere
(() => {
  const getClientX = (evt) => {
    if (typeof evt.clientX === "number") return evt.clientX;
    if (evt.touches && evt.touches.length) return evt.touches[0].clientX;
    if (evt.changedTouches && evt.changedTouches.length) return evt.changedTouches[0].clientX;
    return 0;
  };

  const bindPointer = (el, handler) => {
    if (!el) return;
    const wrapped = (evt) => {
      if (evt.type === "mousedown" && evt.button !== 0) return;
      if (evt.pointerType === "mouse" && evt.button !== undefined && evt.button !== 0) return;
      if (evt.cancelable) evt.preventDefault();
      handler(evt, getClientX(evt));
    };
    if (window.PointerEvent) {
      el.addEventListener("pointerdown", wrapped, { passive: false });
    } else {
      el.addEventListener("mousedown", wrapped);
      el.addEventListener("touchstart", wrapped, { passive: false });
    }
  };

  const timelineEl = document.getElementById("timeline");
  if (timelineEl && !timelineEl.querySelector(".timeline-hitbox")) {
    const box = document.createElement("div");
    box.className = "timeline-hitbox";
    box.setAttribute("aria-hidden", "true");
    timelineEl.appendChild(box);
  }
  const seekFromTimeline = (clientX = 0) => {
    if (!timelineEl) return;
    const rect = timelineEl.getBoundingClientRect();
    const frac = rect.width ? Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) : 0;
    if (Number.isFinite(frac) && wavesurfer?.seekTo) wavesurfer.seekTo(frac);
  };
  bindPointer(timelineEl, (evt, clientX) => {
    const isRegionTap = !!evt.target?.closest?.('.region');
    if (isRegionTap && touchEditEnabled) return;
    seekFromTimeline(clientX);
  });
  timelineEl?.addEventListener("click", (evt) => {
    if (evt.defaultPrevented) return;
    seekFromTimeline(evt.clientX ?? 0);
  });

  const waveEl = document.getElementById("wave");
  bindPointer(waveEl, (evt, clientX) => {
    const isRegionTap = !!evt.target?.closest?.('.region');
    if (isRegionTap && touchEditEnabled) return;
    if (!wavesurfer?.getCurrentTime || !wavesurfer?.setTime) return;
    const cur = wavesurfer.getCurrentTime();
    const dur = wavesurfer.getDuration?.() ?? Infinity;
    const step = evt.ctrlKey ? 1.0 : (evt.shiftKey ? 0.05 : 0.01);
    const rect = waveEl.getBoundingClientRect();
    const rel = rect.width ? ((clientX - rect.left) / rect.width) : 0.5;
    if (rel < 1 / 3) {
      wavesurfer.setTime(Math.max(0, cur - step));
    } else if (rel > 2 / 3) {
      wavesurfer.setTime(Math.min(dur, cur + step));
    } else {
      const microStep = evt.altKey ? 0.001 : 0.005;
      wavesurfer.setTime(Math.min(dur, Math.max(0, cur + microStep)));
    }
  });

  waveEl?.addEventListener("dblclick", (e) => {
    if (e.target?.closest?.('.region')) return;
    const rect = waveEl.getBoundingClientRect();
    const frac = rect.width ? Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) : 0;
    if (Number.isFinite(frac) && wavesurfer?.seekTo) wavesurfer.seekTo(frac);
  });
})();


// Helper for zoom value across WS versions
const getZoom = () => (wavesurfer?.getOptions?.().minPxPerSec ?? wavesurfer?.params?.minPxPerSec ?? 60);

keyHandler = (e) => {
  const activeTag = (document.activeElement?.tagName || "").toLowerCase();
  const isTyping = activeTag === "input" || activeTag === "textarea";
  // transport (Space only when focus is within waveform/timeline to avoid page scroll)
  if (e.code === "Space") {
    if (isTyping) {
      return;
    }
    const withinWave = document.activeElement?.closest?.('#wave, #timeline');
    if (withinWave) e.preventDefault();
    wavesurfer?.playPause?.();
  }
  if (e.code === "Home") {
    wavesurfer?.setTime?.(0);
  }

  // selection nav
  if (e.key === "j") {
    const idx = project.segments.findIndex(s => s.id === currentId);
    if (idx > 0) selectSeg(project.segments[idx - 1].id, true);
  }
  if (e.key === "k") {
    const idx = project.segments.findIndex(s => s.id === currentId);
    if (idx >= 0 && idx < project.segments.length - 1)
      selectSeg(project.segments[idx + 1].id, true);
  }

  if (!isTyping && (e.key === "Insert" || (e.shiftKey && e.key.toLowerCase() === "a"))) {
    e.preventDefault();
    addLineAfterCurrent();
  }
  if (!isTyping && e.key === "Delete") {
    e.preventDefault();
    deleteCurrentLine({ confirm: true });
  }

  // loop + play selection
  if (e.key.toLowerCase() === "l") {
    loopSelected = !loopSelected;
    const st = $("#status");
    if (st) { st.textContent = loopSelected ? "Loop: ON" : "Loop: OFF"; setTimeout(() => st.textContent = "", 800); }
  }
  if (e.key.toLowerCase() === "p" && currentId) {
    const s = findSeg(currentId);
    if (s) { wavesurfer.setTime?.(Math.max(0, s.start + 0.005)); wavesurfer.play?.(s.start, s.end); }
  }

  // zoom (note: "+" often reports as "=", so handle both)
  if (e.key === "+" || (e.key === "=" && e.shiftKey)) wavesurfer?.zoom?.(getZoom() + 20);
  if (e.key === "-") wavesurfer?.zoom?.(Math.max(20, getZoom() - 20));

  // precise nudges
  if (e.key === "ArrowLeft") nudgeSelected(e.ctrlKey ? -0.001 : (e.shiftKey ? -0.05 : -0.01));
  if (e.key === "ArrowRight") nudgeSelected(e.ctrlKey ? 0.001 : (e.shiftKey ? 0.05 : 0.01));

  // trim / split / merge
  if (e.key === "[") trimToCursor('start');
  if (e.key === "]") trimToCursor('end');
  if (e.key.toLowerCase() === "s") splitAtCursor();
  if (e.key.toLowerCase() === "m") mergeWithNext();

  // validate / autofix
  if (e.key.toLowerCase() === "g" && !e.shiftKey) {
    const n = validateSegments(0.05).length;
    const st = $("#status"); if (st) { st.textContent = n ? `Found ${n} issues` : "No timing issues"; setTimeout(() => st.textContent = "", 1200); }
  }
  if (e.key.toLowerCase() === "g" && e.shiftKey) {
    const ok = autoFixMicro(0.05);
    const st = $("#status"); if (st) { st.textContent = ok ? "Micro gaps/overlaps fixed" : "Nothing to fix"; setTimeout(() => st.textContent = "", 1200); }
  }

  // save / undo / redo
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveProject(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
};


let uiWired = false;
function wireButtonsOnce() {
  if (uiWired) return;
  uiWired = true;

  const handlePlayPause = (e) => {
    e.preventDefault();
    wavesurfer?.playPause();
  };
  const handlePrev = (e) => {
    e.preventDefault();
    const idx = project?.segments.findIndex((s) => s.id === currentId);
    if (idx > 0) selectSeg(project.segments[idx - 1].id, true);
  };
  const handleNext = (e) => {
    e.preventDefault();
    const idx = project?.segments.findIndex((s) => s.id === currentId);
    if (idx >= 0 && idx < project.segments.length - 1)
      selectSeg(project.segments[idx + 1].id, true);
  };

  // Transport
  $("#play")?.addEventListener("click", handlePlayPause);
  $("#mobilePlay")?.addEventListener("click", handlePlayPause);

  document.addEventListener("keydown", keyHandler);
  $("#stop")?.addEventListener("click", (e) => {
    e.preventDefault();
    if (!wavesurfer) return;
    wavesurfer.pause();
    wavesurfer.setTime(0);
  });

  // Selection nav
  $("#prev")?.addEventListener("click", handlePrev);
  $("#next")?.addEventListener("click", handleNext);
  $("#mobilePrev")?.addEventListener("click", handlePrev);
  $("#mobileNext")?.addEventListener("click", handleNext);

  // Zoom
  const getZoom = () => (wavesurfer?.params?.minPxPerSec ?? 60);
  $("#zoomIn")?.addEventListener("click", (e) => {
    e.preventDefault();
    if (wavesurfer) wavesurfer.zoom(getZoom() + 20);
  });
  $("#zoomOut")?.addEventListener("click", (e) => {
    e.preventDefault();
    if (wavesurfer) wavesurfer.zoom(Math.max(20, getZoom() - 20));
  });



  // File I/O
  $("#save")?.addEventListener("click", async (e) => {
    e.preventDefault();
    try { await saveProject(); } catch (err) { console.error(err); }
  });
  $("#mobileSave")?.addEventListener("click", async (e) => {
    e.preventDefault();
    try { await saveProject(); } catch (err) { console.error(err); }
  });
  $("#exportSrt")?.addEventListener("click", async (e) => {
    e.preventDefault();
    try { await exportSrt(); } catch (err) { console.error(err); }
  });
  $("#importSrt")?.addEventListener("click", async (e) => {
    e.preventDefault();
    try { await importSrt(); } catch (err) { console.error(err); }
  });
  // Split/Merge/Add/Delete button wiring
  $("#split")?.addEventListener("click", (e) => { e.preventDefault(); splitAtCursor(); });
  $("#merge")?.addEventListener("click", (e) => { e.preventDefault(); mergeWithNext(); });
  $("#mobileSplit")?.addEventListener("click", (e) => { e.preventDefault(); splitAtCursor(); });
  $("#addLineAfter")?.addEventListener("click", (e) => { e.preventDefault(); addLineAfterCurrent(); });
  $("#deleteLine")?.addEventListener("click", (e) => {
    e.preventDefault();
    deleteCurrentLine({ confirm: true });
  });
  $("#btnCopyLines")?.addEventListener("click", (e) => { e.preventDefault(); copySelectedLines(); });
  $("#btnPasteLines")?.addEventListener("click", (e) => { e.preventDefault(); pasteCopiedLines(); });
  $("#btnSetStart")?.addEventListener("click", (e) => {
    e.preventDefault();
    trimToCursor('start');
  });
  $("#btnSetEnd")?.addEventListener("click", (e) => {
    e.preventDefault();
    trimToCursor('end');
  });
  document.querySelectorAll(".fuzzy-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const dir = parseFloat(btn.dataset.dir || "0");
      triggerFuzzyNudge(dir);
    });
  });
  $("#wordList")?.addEventListener("input", handleWordInputChange);

  // Wire Mode Toggle
  $("#btnModeLines")?.addEventListener("click", () => {
    if (editMode === 'lines') return;
    editMode = 'lines';
    $("#btnModeWords")?.classList.remove("active");
    $("#btnModeLines")?.classList.add("active");
    rebuildRegions();
    refreshList();
    // Reset selection to first line
    if (project?.segments?.length) selectSeg(project.segments[0].id);
  });
  $("#btnModeWords")?.addEventListener("click", () => {
    if (editMode === 'words') return;
    editMode = 'words';
    $("#btnModeLines")?.classList.remove("active");
    $("#btnModeWords")?.classList.add("active");

    rebuildRegions();
    refreshList();
    // Reset selection to first word of first line?
    // Or just clear selection.
    $("#selNone")?.classList.remove("hidden");
    $("#selPanel")?.classList.add("hidden");
    currentId = null;
  });
}

// debounce helper (top-level if you like)
function debounce(fn, ms = 200) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// Bind text box -> user text (never clobbered by timing changes)
$("#selText")?.addEventListener("input", debounce((e) => {
  const seg = findSeg(currentId); if (!seg) return;
  // optional: strip leading time tags on edit
  const TIME_TAG = /^\s*(?:\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]|\{\d+(?:\.\d+)?\})\s*/;
  const clean = String(e.target.value).replace(TIME_TAG, "");
  setUserText(seg, clean);
  setDirty(true);

  // live-update the visible region label
  const r = regions.getRegions().find(r => r.id === seg.id);
  if (r) r.setContent(clean);
}, 120));

function bindManualTimeInput(selector, kind) {
  const input = $(selector);
  if (!input) return;
  let skipNextChange = false;
  const commit = () => {
    handleManualTimeInput(kind, input.value);
  };
  input.addEventListener("change", () => {
    if (skipNextChange) { skipNextChange = false; return; }
    commit();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      skipNextChange = true;
      commit();
      input.blur();
    }
  });
}

bindManualTimeInput("#selStart", "start");
bindManualTimeInput("#selEnd", "end");


function ensureValidSelection() {
  if (!project?.segments?.length) { currentId = null; return; }
  if (!project.segments.some(s => s.id === currentId)) {
    // pick next, else prev, else first
    currentId = project.segments[0].id;
  }
}

function destroyWave() {
  try { wavesurfer?.destroy(); } catch (_) { }
  wavesurfer = null;
  if (regionHistoryTimer) { clearTimeout(regionHistoryTimer); regionHistoryTimer = null; }
  const timelineHost = document.getElementById('timeline');
  if (timelineHost) {
    Array.from(timelineHost.children).forEach((child) => {
      if (!child.classList?.contains?.('timeline-hitbox')) child.remove();
    });
  }
}

// ------------------------------
// Boot
// ------------------------------
loadProject().catch(err => { console.error(err); const st = $("#status"); if (st) st.textContent = "Failed to load project: " + err.message; });

// Metadata Logic
if (document.getElementById("btnMetadata")) {
  document.getElementById("btnMetadata").addEventListener("click", editMetadata);
}

async function editMetadata() {
  const btn = document.getElementById("btnMetadata");
  const origText = btn.textContent;
  btn.textContent = "Loading...";
  try {
    const r = await fetchJSON(`/api/projects/${SLUG}/metadata`);
    const currentTitle = r.metadata?.title || "";
    // Simple prompt for now
    const newTitle = window.prompt("Edit MP3 Title Metadata:", currentTitle);
    if (newTitle !== null && newTitle !== currentTitle) {
      await fetchJSON(`/api/projects/${SLUG}/metadata`, {
        method: "POST",
        body: JSON.stringify({ title: newTitle }),
      });
      alert("Metadata saved to audio file.");
    }
  } catch (err) {
    alert("Failed to update metadata: " + err.message);
  } finally {
    btn.textContent = origText;
  }
}
