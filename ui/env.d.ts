/// <reference types="vite/client" />

interface ImportMetaEnv {
    readonly VITE_CHUNK_SIZE_BYTES?: string
}

interface ImportMeta {
    readonly env: ImportMetaEnv
}
