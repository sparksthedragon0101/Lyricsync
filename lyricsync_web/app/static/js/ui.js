
export const UI = {
    showToast(message, duration = 3000, id = null, extraClass = null) {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            document.body.appendChild(container);
        }

        // If an ID is provided, update existing toast if it exists
        if (id) {
            const existing = document.getElementById(id);
            if (existing) {
                existing.textContent = message;
                existing.classList.add('show');
                // If it was about to fade out, we can't easily reset the setTimeout here
                // without storing the timeout ID. But for progress toasts, duration is 0.
                return;
            }
        }

        const toast = document.createElement('div');
        toast.className = 'toast';
        if (extraClass) toast.classList.add(extraClass);
        if (id) toast.id = id;
        toast.textContent = message;

        container.appendChild(toast);

        // Trigger reflow for transition
        // eslint-disable-next-line no-unused-expressions
        toast.offsetHeight;
        toast.classList.add('show');

        if (duration > 0) {
            setTimeout(() => {
                toast.classList.remove('show');
                const onTransitionEnd = () => {
                    toast.remove();
                    toast.removeEventListener('transitionend', onTransitionEnd);
                };
                toast.addEventListener('transitionend', onTransitionEnd);
            }, duration);
        }
    },

    appendLog(el, text) {
        if (!el) return;
        el.textContent += text;
        el.scrollTop = el.scrollHeight;
    },

    formatTime(seconds) {
        if (typeof seconds !== 'number' || Number.isNaN(seconds)) {
            return "--:--";
        }
        const min = Math.floor(seconds / 60);
        const sec = Math.floor(seconds % 60);
        const ms = Math.round((seconds - Math.floor(seconds)) * 1000);
        return `${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
    },

    // Lightbox logic
    openLightbox(src) {
        const lightbox = document.getElementById('lightbox');
        const img = document.getElementById('lightbox-img');
        if (!lightbox || !img) return;
        img.src = src;
        lightbox.removeAttribute('hidden');
    },

    closeLightbox() {
        const lightbox = document.getElementById('lightbox');
        const img = document.getElementById('lightbox-img');
        if (lightbox) lightbox.setAttribute('hidden', 'hidden');
        if (img) img.src = '';
    }
};
