(() => {
  if (window.__pasteLyricsInit) return;
  window.__pasteLyricsInit = true;

  function ensurePost(url, data) {
    if (typeof window.post === "function") {
      return window.post(url, data);
    }
    return fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify(data || {})
    }).then((res) => {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  function localClean(raw) {
    if (!raw) return "";
    let txt = String(raw).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    txt = txt.replace(/\[?\b\d{1,2}:\d{2}(?:\.\d{1,3})?\b\]?/g, "");
    txt = txt
      .split("\n")
      .filter(l => !/^\s*\[(?:verse|chorus|bridge|pre-chorus|post-chorus|intro|outro|hook|refrain|break|solo)[^\]]*\]\s*$/i.test(l))
      .filter(l => !/^(you might also like|embed|see lyrics)/i.test(l.trim()))
      .map(s => s.trim())
      .join("\n");
    txt = txt
      .replace(/[\u2018\u2019]/g, "'")
      .replace(/[\u201c\u201d]/g, '"')
      .replace(/[\u2013\u2014]/g, "-")
      .replace(/\u00a0/g, " ");
    return txt.replace(/\n{3,}/g, "\n\n").trim();
  }

  window.clientCleanLyrics = window.clientCleanLyrics || localClean;

  function initModal() {
    const modal          = document.getElementById("paste-lyrics-modal");
    const btnClose       = document.getElementById("pl-close");
    const btnPreview     = document.getElementById("pl-preview");
    const btnSave        = document.getElementById("pl-save");
    const rawEl          = document.getElementById("pl-raw");
    const cleanEl        = document.getElementById("pl-clean");
    const alsoOfficialEl = document.getElementById("pl-also-official");
    const statusEl       = document.getElementById("pl-status");

    if (!modal) return;

    const projectTrigger   = document.getElementById("btnPasteLyrics");
    const dashboardButtons = Array.from(document.querySelectorAll(".btnPasteLyrics[data-slug]"));
    if (!projectTrigger && dashboardButtons.length === 0) return;

    const pageSlug = (typeof window !== "undefined")
      ? (window.slug || window.currentProjectSlug || null)
      : null;

    let currentSlug = null;

    function show(slug) {
      currentSlug = slug || pageSlug || null;
      modal.style.display = "flex";
      if (statusEl) {
        statusEl.textContent = currentSlug
          ? `Target: ${currentSlug}`
          : "Paste target not set";
      }
      if (rawEl && rawEl.value == null) rawEl.value = "";
      if (cleanEl && cleanEl.value == null) cleanEl.value = "";
    }

    function hide() {
      modal.style.display = "none";
      currentSlug = null;
    }

    projectTrigger?.addEventListener("click", (e) => {
      e?.preventDefault?.();
      show(pageSlug);
    });

    dashboardButtons.forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e?.preventDefault?.();
        const slug = btn.getAttribute("data-slug") || btn.dataset.slug || null;
        show(slug);
      });
    });

    btnClose?.addEventListener("click", (e) => {
      e?.preventDefault?.();
      hide();
    });

    modal.addEventListener("click", (e) => {
      if (e.target === modal) hide();
    });

    function doPreview() {
      if (cleanEl && rawEl) {
        cleanEl.value = window.clientCleanLyrics(rawEl.value);
      }
    }

    rawEl?.addEventListener("input", doPreview);
    btnPreview?.addEventListener("click", (e) => {
      e?.preventDefault?.();
      doPreview();
    });

    btnSave?.addEventListener("click", async (e) => {
      e?.preventDefault?.();
      if (!currentSlug) {
        if (statusEl) statusEl.textContent = "Missing project slug.";
        return;
      }
      if (statusEl) statusEl.textContent = "Saving.";
      try {
        const payload = {
          text: rawEl?.value || "",
          also_official: !!alsoOfficialEl?.checked,
        };
        const res = await ensurePost(`/api/projects/${currentSlug}/paste_lyrics`, payload);
        if (res?.ok) {
          if (statusEl) {
            const extra = res.also_official ? " + official_lyrics.txt" : "";
            statusEl.textContent = `Saved ${res.saved} (${res.bytes} bytes)${extra}`;
          }
          try {
            localStorage.setItem(`lastEditedTxt:${currentSlug}`, window.clientCleanLyrics(payload.text));
          } catch (err) {
            console.warn("Unable to cache lyrics:", err);
          }
        } else if (statusEl) {
          statusEl.textContent = "Save failed.";
        }
      } catch (err) {
        console.error(err);
        if (statusEl) statusEl.textContent = "Error saving lyrics.";
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initModal, { once: true });
  } else {
    initModal();
  }
})();
