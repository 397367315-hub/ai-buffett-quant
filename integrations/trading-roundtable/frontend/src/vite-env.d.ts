/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly BASE_URL: string;
  readonly VITE_ROUNDTABLE_API_BASE?: string;
  readonly VITE_ROUNDTABLE_ALLOW_DEMO?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
