import { DEFAULT_SETTINGS, KEYS, SettingsStore, defaultStore, memoryStore } from "../src/lib/settings";

function syncStorage() {
  const m = new Map<string, string>();
  return {
    map: m,
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
  };
}

describe("settings storage", () => {
  test("uses OfficeRuntime.storage when it is available", async () => {
    const office = memoryStore();
    const local = syncStorage();
    const store = defaultStore({ OfficeRuntime: { storage: office }, localStorage: local });
    await store.setItem(KEYS.token, "t");
    expect(await office.getItem(KEYS.token)).toBe("t");
    expect(local.map.size).toBe(0);
  });

  test("falls back to localStorage when OfficeRuntime is missing", async () => {
    const local = syncStorage();
    const store = defaultStore({ localStorage: local });
    await store.setItem(KEYS.baseUrl, "https://h.example.test");
    expect(local.map.get(KEYS.baseUrl)).toBe("https://h.example.test");
  });

  test("falls back to localStorage when OfficeRuntime.storage fails", async () => {
    const failing = {
      getItem: async () => {
        throw new Error("storage unavailable");
      },
      setItem: async () => {
        throw new Error("storage unavailable");
      },
    };
    const local = syncStorage();
    const store = defaultStore({ OfficeRuntime: { storage: failing }, localStorage: local });
    await store.setItem(KEYS.token, "t2");
    expect(local.map.get(KEYS.token)).toBe("t2");
    expect(await store.getItem(KEYS.token)).toBe("t2");
  });

  test("works in memory when nothing else is available", async () => {
    const store = defaultStore({});
    await store.setItem("k", "v");
    expect(await store.getItem("k")).toBe("v");
  });
});

describe("SettingsStore", () => {
  test("defaults, save, trimming and the cached token", async () => {
    const s = new SettingsStore(memoryStore());
    expect(await s.load()).toEqual(DEFAULT_SETTINGS);
    expect(s.currentToken()).toBe("");
    await s.save({ baseUrl: " https://h.example.test ", token: " abc " });
    expect(await s.load()).toEqual({ baseUrl: "https://h.example.test", token: "abc" });
    expect(s.currentToken()).toBe("abc");
  });

  test("reads what was stored earlier", async () => {
    const backend = memoryStore();
    await backend.setItem(KEYS.baseUrl, "https://h.example.test");
    await backend.setItem(KEYS.token, "stored");
    expect(await new SettingsStore(backend).load()).toEqual({ baseUrl: "https://h.example.test", token: "stored" });
  });
});
