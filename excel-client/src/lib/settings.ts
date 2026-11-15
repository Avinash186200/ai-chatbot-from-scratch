/**
 * Base URL and token, set in the task pane.
 *
 * Stored in OfficeRuntime.storage (shared by the task pane and the custom
 * functions in the shared runtime, and kept per add-in), falling back to
 * localStorage where OfficeRuntime is unavailable or fails, and to memory as a
 * last resort.
 */

export interface Settings {
  baseUrl: string;
  token: string;
}

export interface KeyValueStore {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
}

export const KEYS = { baseUrl: "aa.baseUrl", token: "aa.token" } as const;
export const DEFAULT_SETTINGS: Settings = { baseUrl: "http://localhost:8000", token: "" };

interface SyncStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function fromSync(s: SyncStorage): KeyValueStore {
  return {
    getItem: async (k) => s.getItem(k),
    setItem: async (k, v) => s.setItem(k, v),
  };
}

export function memoryStore(): KeyValueStore {
  const m = new Map<string, string>();
  return { getItem: async (k) => m.get(k) ?? null, setItem: async (k, v) => void m.set(k, v) };
}

/** OfficeRuntime.storage first, then localStorage, then memory; per operation. */
export function defaultStore(scope: Record<string, unknown> = globalThis as Record<string, unknown>): KeyValueStore {
  const candidates: KeyValueStore[] = [];
  const officeRuntime = scope.OfficeRuntime as { storage?: KeyValueStore } | undefined;
  if (officeRuntime?.storage) candidates.push(officeRuntime.storage);
  try {
    const ls = scope.localStorage as SyncStorage | undefined;
    if (ls) candidates.push(fromSync(ls));
  } catch {
    // Accessing localStorage can throw when storage is blocked.
  }
  candidates.push(memoryStore());

  return {
    async getItem(key) {
      for (const store of candidates) {
        try {
          return await store.getItem(key);
        } catch {
          // try the next store
        }
      }
      return null;
    },
    async setItem(key, value) {
      for (const store of candidates) {
        try {
          await store.setItem(key, value);
          return;
        } catch {
          // try the next store
        }
      }
    },
  };
}

export class SettingsStore {
  private cached: Settings | undefined;

  constructor(private readonly backend: KeyValueStore = defaultStore()) {}

  async load(): Promise<Settings> {
    if (this.cached) return this.cached;
    const [baseUrl, token] = await Promise.all([
      this.backend.getItem(KEYS.baseUrl),
      this.backend.getItem(KEYS.token),
    ]);
    this.cached = {
      baseUrl: (baseUrl ?? DEFAULT_SETTINGS.baseUrl).trim(),
      token: (token ?? DEFAULT_SETTINGS.token).trim(),
    };
    return this.cached;
  }

  async save(settings: Settings): Promise<void> {
    const clean = { baseUrl: settings.baseUrl.trim(), token: settings.token.trim() };
    await this.backend.setItem(KEYS.baseUrl, clean.baseUrl);
    await this.backend.setItem(KEYS.token, clean.token);
    this.cached = clean;
  }

  /** The token currently known, for redaction; "" before the first load. */
  currentToken(): string {
    return this.cached?.token ?? "";
  }
}
