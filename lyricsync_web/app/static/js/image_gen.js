
import { API } from './api.js';
import { State } from './state.js';
import { UI } from './ui.js';

export const ImageGenUI = {
    async init() {
        console.log("[ImageGenUI] Initializing...");
        try {
            const slug = State.slug;
            const form = document.getElementById('img-form');
            // ... restore logic ...

            const styleSelect = document.getElementById('img-style');
            const storyStyleSelect = document.getElementById('story-img-style');

            const submenus = {
                'stylized': document.getElementById('submenu-stylized'),
                'anime': document.getElementById('submenu-anime'),
                'animated': document.getElementById('submenu-animated'),
                'illustration': document.getElementById('submenu-illustration')
            };

            const storySubmenus = {
                'stylized': document.getElementById('story-submenu-stylized'),
                'anime': document.getElementById('story-submenu-anime'),
                'animated': document.getElementById('story-submenu-animated'),
                'illustration': document.getElementById('story-submenu-illustration')
            };

            function updateStyleSubmenus(select, menus) {
                if (!select) return;
                const val = select.value;
                Object.keys(menus).forEach(key => {
                    const el = menus[key];
                    if (el) {
                        if (key === val) el.removeAttribute('hidden');
                        else el.setAttribute('hidden', '');
                    }
                });
            }

            if (styleSelect) {
                styleSelect.addEventListener('change', () => updateStyleSubmenus(styleSelect, submenus));
                updateStyleSubmenus(styleSelect, submenus);
            }
            if (storyStyleSelect) {
                storyStyleSelect.addEventListener('change', () => updateStyleSubmenus(storyStyleSelect, storySubmenus));
                updateStyleSubmenus(storyStyleSelect, storySubmenus);
            }

            this.applyModelSettings = (mode, modelId) => {
                console.log(`[ImageGenUI] applyModelSettings(mode=${mode}, modelId=${modelId})`);
                if (!modelId) return;
                const model = State.models.find(m => m.id === modelId);
                if (!model) {
                    console.warn(`[ImageGenUI] Model metadata not found for ${modelId}`);
                    return;
                }
                console.log(`[ImageGenUI] Applying metadata for ${modelId}:`, model);

                const prefix = mode === 'story' ? 'story-img-' : 'img-';
                const promptEl = document.getElementById(mode === 'story' ? 'story-img-prompt' : 'img-prompt');
                const stepsEl = document.getElementById(`${prefix}steps`);
                const guidanceEl = document.getElementById(`${prefix}guidance`);
                const resEl = document.getElementById(`${prefix}resolution`);
                const aspectEl = document.getElementById(`${prefix}aspect`);

                if (model.steps && stepsEl) {
                    console.log(`[ImageGenUI] Setting steps to ${model.steps}`);
                    stepsEl.value = model.steps;
                    stepsEl.dispatchEvent(new Event('change'));
                    stepsEl.dispatchEvent(new Event('input'));
                }
                if (model.guidance && guidanceEl) {
                    console.log(`[ImageGenUI] Setting guidance to ${model.guidance}`);
                    guidanceEl.value = model.guidance;
                    guidanceEl.dispatchEvent(new Event('change'));
                    guidanceEl.dispatchEvent(new Event('input'));
                }
                
                // Resolution automation removed per user request: 
                // dropdowns should override model defaults.

                // Append trigger words if not present
                if (model.trigger_words && promptEl) {
                    const current = promptEl.value.toLowerCase();
                    const triggers = model.trigger_words.split(',').map(t => t.trim());
                    const missing = triggers.filter(t => !current.includes(t.toLowerCase()));
                    if (missing.length > 0) {
                        if (promptEl.value && !promptEl.value.trim().endsWith(',')) {
                            promptEl.value += ', ';
                        }
                        promptEl.value += missing.join(', ');
                        promptEl.dispatchEvent(new Event('change'));
                        promptEl.dispatchEvent(new Event('input'));
                    }
                }
            };
            const applyModelSettings = this.applyModelSettings; // local ref for listeners

            // Helper to calculate dimensions
            function getDimensions(mode = 'single') {
                const prefix = mode === 'story' ? 'story-img-' : 'img-';
                const aspectSelect = document.getElementById(`${prefix}aspect`);
                const resolutionSelect = document.getElementById(`${prefix}resolution`);

                const ar = aspectSelect ? aspectSelect.value : '16:9';
                const baseRes = resolutionSelect ? parseInt(resolutionSelect.value, 10) : 1080;
                let w = 1024, h = 576;

                if (ar === '1:1') { w = baseRes; h = baseRes; }
                else if (ar === '9:16') { w = baseRes; h = Math.round(w * (16 / 9)); }
                else { h = baseRes; w = Math.round(h * (16 / 9)); }

                return { w: Math.round(w / 8) * 8, h: Math.round(h / 8) * 8 };
            }

            // Bind Generate
            const btnGen = document.getElementById('img-generate-btn');
            const promptEl = document.getElementById('img-prompt');
            const negEl = document.getElementById('img-negative');
            const countInput = document.getElementById('img-count');
            const stepsInput = document.getElementById('img-steps');
            const seedInput = document.getElementById('img-seed');
            const modelSelect = document.getElementById('img-model');

            // Refinement UI logic
            const refineToggle = document.getElementById('img-refine-toggle');
            const refineOptions = document.getElementById('img-refine-options');
            const refineStrengthRaw = document.getElementById('img-refine-strength');
            const refineStrengthVal = document.getElementById('img-refine-strength-val');

            if (refineToggle && refineOptions) {
                refineToggle.addEventListener('change', () => {
                    refineOptions.style.display = refineToggle.checked ? 'block' : 'none';
                });
                if (refineStrengthRaw && refineStrengthVal) {
                    refineStrengthRaw.addEventListener('input', () => {
                        refineStrengthVal.textContent = refineStrengthRaw.value;
                    });
                }
            }

            if (form) form.addEventListener('submit', async (e) => {
                e.preventDefault();
                if (btnGen) btnGen.disabled = true;
                UI.showToast("Generating...", 0, "img-gen-progress");
                try {
                    const dims = getDimensions('single');
                    const payload = {
                        slug: slug,
                        model_id: modelSelect.value,
                        vae_id: document.getElementById('img-vae')?.value || null,
                        text_encoder_id: document.getElementById('img-te')?.value || null,
                        prompt: promptEl.value,
                        negative_prompt: negEl ? negEl.value : '',
                        width: dims.w, height: dims.h,
                        num_images: parseInt(countInput.value || 1),
                        steps: parseInt(stepsInput.value || 30),
                        guidance: parseFloat(document.getElementById('img-guidance')?.value || 6.5),
                        seed: seedInput.value ? parseInt(seedInput.value) : null,
                        style: styleSelect.value,
                        enable_refinement: refineToggle ? refineToggle.checked : false,
                        refinement_strength: refineStrengthRaw ? parseFloat(refineStrengthRaw.value) : 0.75,
                        // Add substyle
                        sub_style: (function () {
                            if (styleSelect.value === 'stylized') return document.getElementById('img-sub-stylized')?.value;
                            if (styleSelect.value === 'anime') return document.getElementById('img-sub-anime')?.value;
                            if (styleSelect.value === 'animated') return document.getElementById('img-sub-animated')?.value;
                            if (styleSelect.value === 'illustration') return document.getElementById('img-sub-illustration')?.value;
                            return null;
                        })(),
                        // Collect LoRAs
                        loras: (function () {
                            const listEl = document.getElementById('lora-list');
                            if (!listEl) return [];
                            const selected = [];
                            // Iterate our generated rows
                            const rows = listEl.querySelectorAll('.lora-item');
                            rows.forEach(row => {
                                const cb = row.querySelector('input[type="checkbox"]');
                                const weightInput = row.querySelector('input[type="number"]');
                                if (cb && cb.checked) {
                                    selected.push({
                                        path: cb.dataset.path,
                                        scale: parseFloat(weightInput.value || 1.0)
                                    });
                                }
                            });
                            return selected;
                        })()
                    };

                    const res = await API.ImageGen.submitJob(payload);
                    const jobId = res.job_id;

                    // Poll
                    while (true) {
                        await new Promise(r => setTimeout(r, 1000));
                        const s = await API.ImageGen.getJobStatus(jobId);
                        console.log(`[ImageGen] Status update for ${jobId}:`, s);
                        
                        if (s.progress) {
                            UI.showToast(s.progress, 0, "img-gen-progress");
                        }

                        if (s.status === 'done') {
                            UI.showToast("Images Generated", 3000, "img-gen-progress");

                            // FIX: Clear story slots so we don't get stuck in story mode
                            // The user just generated a single image, presumably wants to use it.
                            try {
                                if (State.storySlots && State.storySlots.length > 0) {
                                    State.storySlots = [];
                                    // We don't necessarily need to await this save, let it happen in bg
                                    API.saveStorySlots(State.slug, []);
                                    console.log("Cleared story slots on new image generation.");
                                }
                            } catch (e) {
                                console.warn("Failed to auto-clear story slots", e);
                            }

                            // Refresh images in project.js or trigger state update
                            // We need to trigger the refresh in project.js, or API.fetchProjectImages here logic.
                            const data = await API.fetchProjectImages(slug);
                            State.projectImagesCache = data.images || [];
                            State.notifyImagesUpdated();
                            break;
                        } else if (s.status === 'error') {
                            throw new Error(s.error);
                        }
                    }

                } catch (err) {
                    UI.showToast("Error: " + err.message, 5000, "img-gen-progress", "toast-error");
                } finally {
                    if (btnGen) btnGen.disabled = false;
                }
            });

            // Init Logic for Models is handled in project.js or here?
            // Let's rely on State.models populated by project.js for now, or move it here.
            // If we move it here, we need to export a way to update the list.
            // simpler: project.js populates the select.

            // LoRA UI logic (simplified for now as restoring it fully is huge)

            // Populate Ollama models
            const llmSel = document.getElementById('img-llm-model');
            const storyLlmSel = document.getElementById('story-llm-model');

            if (llmSel || storyLlmSel) {
                try {
                    const data = await API.getOllamaModels(); // { models: [...] }
                    const models = data.models || [];
                    if (llmSel) llmSel.innerHTML = '';
                    if (storyLlmSel) storyLlmSel.innerHTML = '';

                    if (models.length === 0) {
                        const opt = document.createElement('option');
                        opt.textContent = "No models found";
                        if (llmSel) llmSel.appendChild(opt.cloneNode(true));
                        if (storyLlmSel) storyLlmSel.appendChild(opt.cloneNode(true));
                    } else {
                        models.forEach(m => {
                            const opt = document.createElement('option');
                            opt.value = m;
                            opt.textContent = m;
                            if (llmSel) llmSel.appendChild(opt.cloneNode(true));
                            if (storyLlmSel) storyLlmSel.appendChild(opt.cloneNode(true));
                        });
                        // Select a default if 'llama3' or 'mistral' exists
                        const pref = models.find(m => m.includes('llama3') || m.includes('mistral'));
                        if (pref) {
                            if (llmSel) llmSel.value = pref;
                            if (storyLlmSel) storyLlmSel.value = pref;
                        }
                    }
                } catch (e) {
                    console.error("Failed to load Ollama models", e);
                    if (llmSel) llmSel.innerHTML = '<option value="">Error loading</option>';
                    if (storyLlmSel) storyLlmSel.innerHTML = '<option value="">Error loading</option>';
                }
            }

            // --- LLM Caller Button Logic ---
            const btnLlm = document.getElementById('img-llm-btn');
            const llmStatus = document.getElementById('img-llm-status');
            const noHumansCb = document.getElementById('img-llm-no-humans');

            if (btnLlm) {
                btnLlm.addEventListener('click', async () => {
                    const model = llmSel ? llmSel.value : '';
                    if (!model) {
                        UI.showToast("Please select an Ollama model first.", 3000, null, "toast-error");
                        return;
                    }

                    btnLlm.disabled = true;
                    if (llmStatus) llmStatus.textContent = "Thinking...";
                    UI.showToast("LLM is crafting your prompt...", 3000);

                    try {
                        const payload = {
                            model: model,
                            prompt: promptEl ? promptEl.value : '',
                            style: styleSelect ? styleSelect.value : null,
                            sub_style: (function () {
                                if (!styleSelect) return null;
                                if (styleSelect.value === 'stylized') return document.getElementById('img-sub-stylized')?.value;
                                if (styleSelect.value === 'anime') return document.getElementById('img-sub-anime')?.value;
                                if (styleSelect.value === 'animated') return document.getElementById('img-sub-animated')?.value;
                                return null;
                            })(),
                            no_humans: noHumansCb ? noHumansCb.checked : false
                        };

                        const res = await API.callImagePromptLLM(slug, payload);
                        if (res.ok) {
                            if (res.positive && promptEl) {
                                promptEl.value = res.positive;
                                promptEl.dispatchEvent(new Event('input'));
                            }
                            if (res.negative && negEl) {
                                negEl.value = res.negative;
                                negEl.dispatchEvent(new Event('input'));
                            }
                            UI.showToast("Prompt generated!", 2000);
                        }
                    } catch (e) {
                        console.error(e);
                        UI.showToast("LLM Error: " + e.message, 4000, null, "toast-error");
                    } finally {
                        btnLlm.disabled = false;
                        if (llmStatus) llmStatus.textContent = "";
                    }
                });
            }

            // --- Story Mode Control UI Logic ---
            const storyRefineToggle = document.getElementById('story-refine-toggle');
            const storyRefineOptions = document.getElementById('story-refine-options');
            const storyRefineStrengthRaw = document.getElementById('story-refine-strength');
            const storyRefineStrengthVal = document.getElementById('story-refine-strength-val');

            if (storyRefineToggle && storyRefineOptions) {
                storyRefineToggle.addEventListener('change', () => {
                    storyRefineOptions.style.display = storyRefineToggle.checked ? 'block' : 'none';
                });
                if (storyRefineStrengthRaw && storyRefineStrengthVal) {
                    storyRefineStrengthRaw.addEventListener('input', () => {
                        storyRefineStrengthVal.textContent = storyRefineStrengthRaw.value;
                    });
                }
            }

            // --- Story Mode Logic ---
            const btnSuggest = document.getElementById('btnSuggestStory');
            const storyPanel = document.getElementById('story-prompts-panel');
            const storyList = document.getElementById('story-prompts-list');
            const storyCount = document.getElementById('story-prompts-count');
            const btnGenAll = document.getElementById('story-run-images');
            const storyStatus = document.getElementById('story-prompts-status');

            async function loadStorySlots() {
                try {
                    const res = await API.fetchStorySlots(slug);
                    if (res.ok && res.slots && res.slots.length > 0) {
                        if (storyPanel) storyPanel.style.display = 'block';
                        renderStoryPrompts(res.slots);
                    }
                } catch (e) {
                    console.error("Failed to load existing story slots", e);
                }
            }

            // Load existing slots on init
            loadStorySlots();

            if (btnSuggest) {
                btnSuggest.addEventListener('click', async () => {
                    const storyLlmSel = document.getElementById('story-llm-model');
                    const model = storyLlmSel ? storyLlmSel.value : '';
                    if (!model) {
                        UI.showToast("Please select an Ollama model first.", 3000, null, "toast-error");
                        return;
                    }

                    btnSuggest.disabled = true;
                    if (storyStatus) storyStatus.textContent = "Generating story prompts... this may take a minute.";
                    UI.showToast("LLM is analyzing story narratives and beats...", 0, "story-llm-toast"); // 0 = persistent until next toast or manual clear
                    try {
                        // Send story style so the LLM makes the prompt fit the style
                        const storyStyle = document.getElementById('story-img-style')?.value || null;
                        const res = await API.generateStoryPrompts(slug, model, storyStyle);
                        if (res.ok && res.prompts) {
                            renderStoryPrompts(res.prompts);
                            if (storyPanel) storyPanel.removeAttribute('hidden');
                            UI.showToast("Story beats identified!", 3000, "story-llm-toast");
                        }
                    } catch (e) {
                        console.error("Story Error", e);
                        UI.showToast("Story Mode Error: " + e.message, 4000, "story-llm-toast", "toast-error");
                    } finally {
                        btnSuggest.disabled = false;
                        if (storyStatus) storyStatus.textContent = "";
                    }
                });
            }

            // --- Assign Images to Story Slots ---
            const btnAssign = document.getElementById('story-assign-images');
            if (btnAssign) {
                btnAssign.addEventListener('click', async () => {
                    const selection = Array.from(State.projectSelectionSet);
                    if (selection.length === 0) {
                        // Empty selection -> Clear Story Slots
                        if (confirm("Clear Story Mode slots? This will switch back to Single Image / Loop mode.")) {
                            State.storySlots = [];
                            await API.saveStorySlots(State.slug, []);
                            UI.showToast("Story Mode Cleared", 2000);
                            // Refresh logic if needed
                        }
                        return;
                    }

                    // Create slots from selection
                    // Default duration: 4s or read from input?
                    // We'll just use 4s default for story slots
                    let currentTime = 0.0;
                    const newSlots = selection.map(path => {
                        const slot = {
                            image_path: path,
                            start: currentTime,
                            end: currentTime + 4.0,
                            prompt: "Story frame"
                        };
                        currentTime += 4.0;
                        return slot;
                    });

                    try {
                        State.storySlots = newSlots;
                        await API.saveStorySlots(State.slug, newSlots);
                        UI.showToast(`Assigned ${newSlots.length} images to Story Mode`, 3000);
                    } catch (e) {
                        UI.showToast("Error saving slots: " + e.message, 4000, null, "toast-error");
                    }
                });
            }

            // Run Images (Render)
            const btnStoryRun = document.getElementById('story-run-images');
            if (btnStoryRun) {
                btnStoryRun.addEventListener('click', async () => {
                    const count = State.storyPrompts ? State.storyPrompts.length : 0;
                    if (count === 0) {
                        UI.showToast("No story prompts to generate.", 3000, null, "toast-error");
                        return;
                    }

                    if (!confirm(`Generate all ${count} images now? This may take a while.`)) return;

                    btnStoryRun.disabled = true;
                    const prevText = btnStoryRun.textContent;
                    btnStoryRun.textContent = "Generating All...";

                    try {
                        for (let i = 0; i < count; i++) {
                            const p = State.storyPrompts[i];
                            if (storyStatus) storyStatus.textContent = `Generating ${i + 1} of ${count}...`;
                            await generateStoryImage(p.prompt, i);
                        }
                        if (storyStatus) storyStatus.textContent = "All images generated!";
                        UI.showToast("Story images complete!", 3000);
                    } catch (e) {
                        UI.showToast("Error generating all: " + e.message, 5000, null, "toast-error");
                        if (storyStatus) storyStatus.textContent = "Error: " + e.message;
                    } finally {
                        btnStoryRun.disabled = false;
                        btnStoryRun.textContent = prevText;
                    }
                });
            }

            async function generateStoryImage(promptText, index) {
                const storyModelSelect = document.getElementById('story-img-model');
                if (!storyModelSelect || !storyModelSelect.value) throw new Error("Please select an Image Model in Story Mode first");

                const dims = getDimensions('story');
                // Build loras array
                const listEl = document.getElementById('lora-list');
                const selectedLoras = [];
                if (listEl) {
                    const rows = listEl.querySelectorAll('.lora-item');
                    rows.forEach(row => {
                        const cb = row.querySelector('input[type="checkbox"]');
                        const weightInput = row.querySelector('input[type="number"]');
                        if (cb && cb.checked) {
                            selectedLoras.push({
                                path: cb.dataset.path,
                                scale: parseFloat(weightInput.value || 1.0)
                            });
                        }
                    });
                }

                // Read from Story Mode specific UI controls
                const storySteps = document.getElementById('story-img-steps');
                const storyStyle = document.getElementById('story-img-style');
                const storyRefineToggle = document.getElementById('story-refine-toggle');
                const storyRefineStrength = document.getElementById('story-refine-strength');
                const storyNegEl = document.getElementById('story-img-negative');
                const storySeedInput = document.getElementById('story-img-seed');

                const selectedStyle = storyStyle ? storyStyle.value : null;

                const payload = {
                    slug: slug,
                    model_id: storyModelSelect.value,
                    vae_id: document.getElementById('story-img-vae')?.value || null,
                    text_encoder_id: document.getElementById('story-img-te')?.value || null,
                    prompt: promptText,
                    negative_prompt: storyNegEl ? storyNegEl.value : '',
                    width: dims.w, height: dims.h,
                    num_images: 1, // Only 1 per prompt usually for story
                    steps: parseInt(storySteps ? storySteps.value : 30) || 30,
                    guidance: parseFloat(document.getElementById('story-img-guidance')?.value || 6.5),
                    seed: storySeedInput && storySeedInput.value ? parseInt(storySeedInput.value) : null,
                    style: selectedStyle,
                    enable_refinement: storyRefineToggle ? storyRefineToggle.checked : false,
                    refinement_strength: storyRefineStrength ? parseFloat(storyRefineStrength.value) : 0.75,
                    sub_style: (function () {
                        if (selectedStyle === 'stylized') return document.getElementById('story-img-sub-stylized')?.value;
                        if (selectedStyle === 'anime') return document.getElementById('story-img-sub-anime')?.value;
                        if (selectedStyle === 'animated') return document.getElementById('story-img-sub-animated')?.value;
                        if (selectedStyle === 'illustration') return document.getElementById('story-img-sub-illustration')?.value;
                        return null;
                    })(),
                    loras: selectedLoras
                };

                const res = await API.ImageGen.submitJob(payload);
                const jobId = res.job_id;

                // Poll
                while (true) {
                    await new Promise(r => setTimeout(r, 1000));
                    const s = await API.ImageGen.getJobStatus(jobId);
                    console.log(`[ImageGen Story] Status update for ${jobId}:`, s);

                    if (s.progress) {
                        UI.showToast(`Story: ${s.progress}`, 0, "img-gen-progress");
                    }

                    if (s.status === 'done') {
                        // Update cache
                        const data = await API.fetchProjectImages(slug);
                        State.projectImagesCache = data.images || [];
                        State.notifyImagesUpdated();
                        return true;
                    } else if (s.status === 'error') {
                        throw new Error(s.error);
                    }
                }
            }

            function renderStoryPrompts(prompts) {
                State.storyPrompts = prompts; // Store for "Generate All"
                if (!storyList) return;
                storyList.innerHTML = '';
                prompts.forEach((p, idx) => {
                    const li = document.createElement('li');
                    li.style.marginBottom = '12px';
                    li.style.display = 'flex';
                    li.style.flexDirection = 'column';
                    li.style.gap = '4px';
                    li.style.padding = '8px';
                    li.style.background = 'rgba(255,255,255,0.03)';
                    li.style.border = '1px solid rgba(255,255,255,0.1)';
                    li.style.borderRadius = '6px';

                    // Show time range if available
                    let timeStr = "";
                    if (p.start !== null && p.start !== undefined) {
                        const s = p.start.toFixed(1);
                        const e = p.end ? p.end.toFixed(1) : "?";
                        timeStr = ` <span class="muted" style="font-size:0.8em">(${s}s - ${e}s)</span>`;
                    }

                    const topRow = document.createElement('div');
                    topRow.innerHTML = `<strong>Scene ${idx + 1}:</strong>${timeStr}`;

                    const promptInput = document.createElement('textarea');
                    promptInput.value = p.prompt;
                    promptInput.rows = 2;
                    promptInput.style.width = '100%';
                    promptInput.style.boxSizing = 'border-box';
                    promptInput.style.resize = 'vertical';
                    promptInput.style.background = 'rgba(0,0,0,0.2)';
                    promptInput.style.color = '#fff';
                    promptInput.style.border = '1px solid rgba(255,255,255,0.2)';
                    promptInput.style.padding = '6px';
                    promptInput.style.borderRadius = '4px';
                    promptInput.addEventListener('change', async () => {
                        State.storyPrompts[idx].prompt = promptInput.value;
                        try {
                            await API.saveStorySlots(slug, State.storyPrompts);
                        } catch (e) {
                            console.error("Failed to auto-save story prompts", e);
                        }
                    });

                    const controls = document.createElement('div');
                    controls.style.display = 'flex';
                    controls.style.justifyContent = 'flex-end';
                    const genBtn = document.createElement('button');
                    genBtn.textContent = 'Generate Image';
                    genBtn.className = 'ghost-btn';
                    genBtn.style.padding = '4px 12px';
                    genBtn.style.fontSize = '0.85em';

                    genBtn.addEventListener('click', async () => {
                        genBtn.disabled = true;
                        genBtn.textContent = 'Generating...';
                        try {
                            await generateStoryImage(promptInput.value, idx);
                            UI.showToast(`Scene ${idx + 1} generated!`, 2000);
                        } catch (e) {
                            UI.showToast("Error: " + e.message, 4000, null, "toast-error");
                        } finally {
                            genBtn.disabled = false;
                            genBtn.textContent = 'Generate Image';
                        }
                    });

                    controls.appendChild(genBtn);

                    li.appendChild(topRow);
                    li.appendChild(promptInput);
                    li.appendChild(controls);

                    storyList.appendChild(li);
                });
                if (storyCount) storyCount.textContent = `${prompts.length} prompts`;
            }

            // --- LoRA UI Logic ---

            function renderLoraList() {
                const listEl = document.getElementById('lora-list');
                if (!listEl) return;
                const loras = State.loras || [];

                if (loras.length === 0) {
                    listEl.innerHTML = '<div class="muted">No LoRAs found in folder.</div>';
                    return;
                }

                listEl.innerHTML = ''; // Clear "Loading..." or old list

                loras.forEach(loraPath => {
                    // loraPath is likely a full path or relative path. We show the filename.
                    // Assuming backend sends a list of paths strings.
                    // registry.py list_loras returns full paths (strings).
                    // Let's just show the filename.
                    const name = loraPath.split(/[\\/]/).pop();

                    const row = document.createElement('div');
                    row.className = 'lora-item';
                    row.style.display = 'flex';
                    row.style.alignItems = 'center';
                    row.style.gap = '8px';
                    row.style.marginBottom = '4px';

                    // Checkbox
                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.dataset.path = loraPath;
                    cb.id = `lora-cb-${name}`;

                    // Label
                    const label = document.createElement('label');
                    label.textContent = name;
                    label.htmlFor = cb.id;
                    label.style.flex = '1';
                    label.style.fontSize = '0.9em';
                    label.style.overflow = 'hidden';
                    label.style.textOverflow = 'ellipsis';
                    label.style.whiteSpace = 'nowrap';
                    label.title = name;

                    // Weight Input
                    const weight = document.createElement('input');
                    weight.type = 'number';
                    weight.min = '-2';
                    weight.max = '2';
                    weight.step = '0.1';
                    weight.value = '1.0';
                    weight.style.width = '60px';
                    weight.style.fontSize = '0.85em';
                    weight.disabled = true; // Disabled until checked

                    cb.addEventListener('change', () => {
                        weight.disabled = !cb.checked;
                        if (cb.checked) row.classList.add('active');
                        else row.classList.remove('active');
                    });

                    row.appendChild(cb);
                    row.appendChild(label);
                    row.appendChild(weight);
                    listEl.appendChild(row);
                });

                // update count/status
                const statusEl = document.getElementById('lora-status');
                if (statusEl) statusEl.textContent = `${loras.length} available`;
            }

            // Expose render function so project.js can call it after fetching models
            // But since ImageGenUI.init is called *after* fetching models in project.js (mostly),
            // we can try rendering now.
            // However, looking at project.js:
            // await ImageGenUI.init();
            // THEN fetchModels().
            // So State.loras is empty here initially.
            // We need to export this or hook into state updates.
            // Quick fix: Attach it to ImageGenUI and call it from project.js, 
            // OR use a polling/listener approach. 
            // Since project.js has: `State.models = ...; State.loras = ...;`
            // We can add a method to ImageGenUI to refresh this list.
            this.renderLoras = renderLoraList;

            // Expose render function for vaes/tes
            this.renderVaesOrTes = function (items, id1, id2) {
                const sel1 = document.getElementById(id1);
                const sel2 = document.getElementById(id2);
                if (!sel1 && !sel2) return;

                const renderSel = (sel) => {
                    if (!sel) return;
                    const oldVal = sel.value;
                    sel.innerHTML = '<option value="">Embedded / Default</option>';
                    items.forEach(path => {
                        // Just show the filename
                        const name = path.split(/[\\/]/).pop();
                        const opt = document.createElement('option');
                        opt.value = path;
                        opt.textContent = name;
                        sel.appendChild(opt);
                    });
                    if (items.includes(oldVal)) sel.value = oldVal;
                };

                renderSel(sel1);
                renderSel(sel2);
            };

            // Initial render (likely empty)
            renderLoraList();

            // --- NEW: Pipeline & Directory Controls using the new API methods ---

            // 1. Pipeline Preload / Release Toggle
            const btnPipeline = document.getElementById('img-pipeline-btn');
            const statusPipeline = document.getElementById('img-pipeline-status');

            async function updatePipelineStatus() {
                if (!modelSelect || !modelSelect.value) return;
                // Don't overwrite if we are in the middle of an action (indicated by disabled button)
                if (btnPipeline && btnPipeline.disabled) return;

                const vaeId = document.getElementById('img-vae')?.value || null;
                const teId = document.getElementById('img-te')?.value || null;

                try {
                    const res = await API.ImageGen.queryPipelineState(modelSelect.value, 'fp16', vaeId, teId);
                    if (res.loaded) {
                        if (btnPipeline) btnPipeline.textContent = "Release Pipeline";
                        if (statusPipeline) statusPipeline.textContent = "Loaded";
                        if (btnPipeline) btnPipeline.dataset.loaded = "true";
                    } else {
                        if (btnPipeline) btnPipeline.textContent = "Preload Pipeline";
                        if (statusPipeline) statusPipeline.textContent = "Not Loaded";
                        if (btnPipeline) btnPipeline.dataset.loaded = "false";
                    }
                } catch (e) {
                    console.error("Failed to check pipeline status", e);
                }
            }

            // Export so project.js can call it
            this.updatePipelineStatus = updatePipelineStatus;

            if (modelSelect) {
                modelSelect.addEventListener('change', () => {
                    applyModelSettings('single', modelSelect.value);
                    updatePipelineStatus();
                });
            }
            const storyModelSelect = document.getElementById('story-img-model');
            if (storyModelSelect) {
                storyModelSelect.addEventListener('change', () => {
                    applyModelSettings('story', storyModelSelect.value);
                    updatePipelineStatus();
                });
            }
            // Also listen to VAE/TE changes since they affect the cache key
            const vaeSelect = document.getElementById('img-vae');
            const teSelect = document.getElementById('img-te');
            if (vaeSelect) vaeSelect.addEventListener('change', updatePipelineStatus);
            if (teSelect) teSelect.addEventListener('change', updatePipelineStatus);

            if (btnPipeline) {
                btnPipeline.addEventListener('click', async () => {
                    const modelId = modelSelect ? modelSelect.value : null;
                    if (!modelId) {
                        UI.showToast("Please select a model first.", 3000, null, "toast-error");
                        return;
                    }

                    const vaeId = document.getElementById('img-vae')?.value || null;
                    const teId = document.getElementById('img-te')?.value || null;

                    const isLoaded = btnPipeline.dataset.loaded === "true";
                    const action = isLoaded ? "Release" : "Preload";
                    const shouldLoad = !isLoaded;

                    btnPipeline.disabled = true;
                    if (statusPipeline) statusPipeline.textContent = isLoaded ? "Releasing..." : "Preloading...";

                    try {
                        await API.ImageGen.setPipelineLoadedState(modelId, 'fp16', shouldLoad, vaeId, teId);
                        UI.showToast(`Pipeline ${action}ed for ${modelId}`, 3000);
                        // Update status - must enable button first so updatePipelineStatus doesn't return early
                        btnPipeline.disabled = false;
                        await updatePipelineStatus();
                    } catch (e) {
                        if (statusPipeline) statusPipeline.textContent = "Error";
                        UI.showToast(`${action} failed: ` + e.message, 4000, null, "toast-error");
                        btnPipeline.disabled = false;
                        // Force re-check to ensure UI is in sync
                        await updatePipelineStatus();
                    } finally {
                        btnPipeline.disabled = false;
                    }
                });
            }

            // 2. Directory Controls - Robust Shared Handler
            const handleDirSetting = async (title, getFn, setFn, onUpdate) => {
                try {
                    const current = await getFn().catch(() => ({ path: '' }));
                    const newPath = prompt(`Enter full path to ${title}:`, current.path || "");
                    if (newPath !== null && newPath !== current.path) {
                        await setFn(newPath);
                        UI.showToast(`${title} updated.`, 3000);
                        
                        // Refresh everything that might have changed
                        const data = await API.fetchModels();
                        State.models = data.models || [];
                        State.loras = data.loras || [];
                        State.vaes = data.vaes || [];
                        State.text_encoders = data.text_encoders || [];

                        // 1. Re-populate model select
                        const mSel = document.getElementById('img-model');
                        const storyMSel = document.getElementById('story-img-model');
                        [mSel, storyMSel].forEach(sel => {
                            if (!sel) return;
                            const oldVal = sel.value;
                            sel.innerHTML = '';
                            State.models.forEach(m => {
                                const opt = document.createElement('option');
                                opt.value = m.id;
                                opt.textContent = m.id;
                                sel.appendChild(opt);
                            });
                            if (State.models.some(m => m.id === oldVal)) sel.value = oldVal;
                        });

                        // 2. Render loras/vaes/tes
                        if (ImageGenUI.renderLoras) ImageGenUI.renderLoras();
                        if (ImageGenUI.renderVaesOrTes) {
                            ImageGenUI.renderVaesOrTes(State.vaes, 'img-vae', 'story-img-vae');
                            ImageGenUI.renderVaesOrTes(State.text_encoders, 'img-te', 'story-img-te');
                        }

                        if (onUpdate) onUpdate();
                    }
                } catch (e) {
                    UI.showToast(`Error setting ${title}: ` + e.message, 4000, null, "toast-error");
                }
            };

            // 3. Directory Buttons Bindings
            const bindDir = (id, title, getFn, setFn) => {
                const btn = document.getElementById(id);
                if (btn) btn.addEventListener('click', () => handleDirSetting(title, getFn, setFn));
            };

            bindDir('img-model-dir-btn', 'Model Folder', API.getModelDirectory, API.setModelDirectory);
            bindDir('story-img-model-dir-btn', 'Model Folder', API.getModelDirectory, API.setModelDirectory);

            bindDir('img-lora-dir-btn', 'LoRA Folder', API.getLoraDirectory, API.setLoraDirectory);
            bindDir('story-img-lora-dir-btn', 'LoRA Folder', API.getLoraDirectory, API.setLoraDirectory);

            bindDir('img-vae-dir-btn', 'VAE Folder', API.getVaeDirectory, API.setVaeDirectory);
            bindDir('story-img-vae-dir-btn', 'VAE Folder', API.getVaeDirectory, API.setVaeDirectory);

            bindDir('img-te-dir-btn', 'Text Encoder Folder', API.getTextEncoderDirectory, API.setTextEncoderDirectory);
            bindDir('story-img-te-dir-btn', 'Text Encoder Folder', API.getTextEncoderDirectory, API.setTextEncoderDirectory);

            console.log("[ImageGenUI] Init complete.");
        } catch (e) {
            console.error("[ImageGenUI] Init failed:", e);
        }
    }
};
