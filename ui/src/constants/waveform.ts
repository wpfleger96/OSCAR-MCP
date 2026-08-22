// Zoom + cache tuning for the session-detail waveform charts.
export const MIN_ZOOM_WINDOW_SEC = 5 // flat floor for the visible window, all zoom paths
export const BASE_MAX_POINTS = 2000 // in-view density target per fetch
export const WINDOW_EXPANSION = 3 // fetch ±1 window-width around the visible span
export const MIN_INVIEW_POINTS = 1000 // density threshold for serving a window from cache
export const MAX_CACHED_CHUNKS = 8 // per-type LRU depth (excludes the full-night overview)
export const SERVER_MAX_POINTS = 10000 // must match the backend max_points le= bound
export const ZOOM_FETCH_DEBOUNCE_MS = 120
