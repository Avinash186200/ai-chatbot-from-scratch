// The token lives in sessionStorage (this tab only, gone when it closes),
// never in localStorage. Storage can be unavailable (private windows,
// blocked site data), so every access is guarded and the app still works
// with the token held only in memory.

const TOKEN_KEY = "aa.token";
const USER_KEY = "aa.user";

function safe<T>(fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

export const storage = {
  token: () => safe(() => window.sessionStorage.getItem(TOKEN_KEY) ?? "", ""),
  setToken: (value: string) =>
    safe(() => (value ? window.sessionStorage.setItem(TOKEN_KEY, value) : window.sessionStorage.removeItem(TOKEN_KEY)), undefined),
  user: () => safe(() => window.sessionStorage.getItem(USER_KEY) ?? "local", "local"),
  setUser: (value: string) => safe(() => window.sessionStorage.setItem(USER_KEY, value), undefined),
};
