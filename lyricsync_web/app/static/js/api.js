
export const API = {
    async fetchProjectImages(slug) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/images`);
        if (!res.ok) throw new Error(`HTTP ${res.status} fetching images`);
        return await res.json();
    },

    async deleteProjectImage(slug, path) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/images`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => null);
            throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async deleteAllProjectImages(slug) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/images/all`, {
            method: 'DELETE',
        });
        if (!res.ok) {
            const data = await res.json().catch(() => null);
            throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async saveProjectImageSelection(slug, paths) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/images/selection`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => null);
            throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async fetchProjectImageSelection(slug) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/images/selection`);
        if (!res.ok) {
            console.warn("Failed to fetch image selection");
            return { selection: [] };
        }
        return await res.json();
    },

    async uploadProjectImages(slug, formData) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/images/upload`, {
            method: 'POST',
            body: formData,
        });
        if (!res.ok) {
            const data = await res.json().catch(() => null);
            throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async fetchStorySlots(slug, cacheBust = true) {
        const endpoint = `/api/projects/${encodeURIComponent(slug)}/image_story_slots`;
        const url = cacheBust ? `${endpoint}?ts=${Date.now()}` : endpoint;
        const res = await fetch(url, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async saveStorySlots(slug, slots) {
        const endpoint = `/api/projects/${encodeURIComponent(slug)}/image_story_slots`;
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slots }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async callAlign(slug, payload) {
        const res = await fetch(`/api/projects/${slug}/align`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const txt = await res.text();
            throw new Error(txt || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async callRender(slug, payload) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/render`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => null);
            throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async getOllamaModels() {
        const res = await fetch('/api/llm/models?ts=' + Date.now());
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async generateStoryPrompts(slug, model, style) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/image_story`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model, style }),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => null);
            throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    async callImagePromptLLM(slug, payload) {
        const res = await fetch(`/api/projects/${encodeURIComponent(slug)}/image_prompt`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const data = await res.json().catch(() => null);
            throw new Error(data?.detail || `HTTP ${res.status}`);
        }
        return await res.json();
    },

    // Image Generation API
    ImageGen: {
        async submitJob(payload) {
            const res = await fetch('/api/image/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => null);
                throw new Error(data?.detail || `HTTP ${res.status}`);
            }
            return await res.json();
        },
        async getJobStatus(jobId) {
            const res = await fetch(`/api/image/status/${encodeURIComponent(jobId)}?ts=${Date.now()}`);
            if (!res.ok) throw new Error('Failed to poll image status');
            return await res.json();
        },
        async queryPipelineState(modelId, precision, vaeId = null, teId = null) {
            let url = `/api/image/pipeline/status?model_id=${encodeURIComponent(modelId)}&precision=${precision}`;
            if (vaeId) url += `&vae_id=${encodeURIComponent(vaeId)}`;
            if (teId) url += `&text_encoder_id=${encodeURIComponent(teId)}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        },
        async setPipelineLoadedState(modelId, precision, shouldLoad, vaeId = null, teId = null) {
            const endpoint = shouldLoad ? '/api/image/pipeline/preload' : '/api/image/pipeline/release';
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    model_id: modelId, 
                    precision,
                    vae_id: vaeId,
                    text_encoder_id: teId
                }),
            });
            if (!res.ok) {
                const data = await res.json().catch(() => null);
                throw new Error(data?.detail || `HTTP ${res.status}`);
            }
            return await res.json();
        }
    },

    async fetchModels() {
        const res = await fetch('/api/models/list');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async getModelDirectory() {
        const res = await fetch('/api/models/directory');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async setModelDirectory(path) {
        const res = await fetch('/api/models/directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async getLoraDirectory() {
        const res = await fetch('/api/models/lora_directory');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async setLoraDirectory(path) {
        const res = await fetch('/api/models/lora_directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async getVaeDirectory() {
        const res = await fetch('/api/models/vae_directory');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async setVaeDirectory(path) {
        const res = await fetch('/api/models/vae_directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async getTextEncoderDirectory() {
        const res = await fetch('/api/models/text_encoder_directory');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    },

    async setTextEncoderDirectory(path) {
        const res = await fetch('/api/models/text_encoder_directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    }
};
