
export const State = {
    slug: window.slug || '', // Fallback, usually set by template

    // Images
    projectImagesCache: [],
    projectImagesListeners: [], // callbacks

    // Selection
    projectSelectionSet: new Set(),
    projectSelectionOrder: [],
    projectSelectionListeners: [],

    // Story
    storySlots: [],
    storyPrompts: [],

    // Models
    models: [],
    loras: [],
    vaes: [],
    text_encoders: [],
    loraSelections: {},

    // Init
    init(slug) {
        this.slug = slug || window.slug;
    },

    notifyImagesUpdated() {
        this.projectImagesListeners.forEach(cb => cb(this.projectImagesCache));
    },

    notifySelectionUpdated() {
        const list = Array.from(this.projectSelectionSet);
        this.projectSelectionListeners.forEach(cb => cb(list));
    }
};
