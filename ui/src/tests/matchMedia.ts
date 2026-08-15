// Controllable window.matchMedia mock for jsdom, which does not implement it.
// @vueuse's useMediaQuery (behind useIsMobile) calls matchMedia, reads
// .matches, and registers a 'change' listener to keep its ref in sync — so
// this mock tracks per-list listeners and lets tests flip the breakpoint at
// runtime via setMediaMatches().

type ChangeListener = (event: MediaQueryListEvent) => void

interface MockList {
    matches: boolean
    media: string
    listeners: Set<ChangeListener>
}

const lists: MockList[] = []
let currentMatches = false

export function installMatchMediaMock(): void {
    window.matchMedia = (query: string): MediaQueryList => {
        const list: MockList = { matches: currentMatches, media: query, listeners: new Set() }
        lists.push(list)
        return {
            get matches() {
                return list.matches
            },
            media: query,
            onchange: null,
            addEventListener: (type: string, cb: ChangeListener) => {
                if (type === 'change') list.listeners.add(cb)
            },
            removeEventListener: (type: string, cb: ChangeListener) => {
                if (type === 'change') list.listeners.delete(cb)
            },
            // Legacy API, still used as a fallback by some libraries.
            addListener: (cb: ChangeListener) => list.listeners.add(cb),
            removeListener: (cb: ChangeListener) => list.listeners.delete(cb),
            dispatchEvent: () => false,
        } as unknown as MediaQueryList
    }
}

// Flip every media-query list to the given state and notify its listeners,
// mimicking a viewport crossing the breakpoint. Tests that call this with
// true must reset to false afterwards — the useIsMobile ref is a
// module-level singleton shared across tests in a file.
export function setMediaMatches(matches: boolean): void {
    currentMatches = matches
    for (const list of lists) {
        if (list.matches === matches) continue
        list.matches = matches
        const event = { matches, media: list.media } as MediaQueryListEvent
        for (const cb of list.listeners) cb(event)
    }
}
