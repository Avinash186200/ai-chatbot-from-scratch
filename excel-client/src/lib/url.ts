/**
 * Base URL validation and the token guard: the bearer token is only ever sent
 * over https://, or over plain http:// to this machine (loopback), where it
 * never crosses a network.
 */

import { ConfigError } from "./errors";

export function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[/, "").replace(/\]$/, "");
  if (host === "localhost" || host === "::1") return true;
  const m = /^127\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  return m !== null && m.slice(1).every((part) => Number(part) <= 255);
}

export function canSendToken(url: URL): boolean {
  return url.protocol === "https:" || (url.protocol === "http:" && isLoopbackHost(url.hostname));
}

/** A clean base URL without a trailing slash, or a ConfigError. */
export function normaliseBaseUrl(raw: string): string {
  const text = (raw ?? "").trim();
  if (!text) throw new ConfigError("Set the gateway base URL in the task pane");
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    throw new ConfigError("The base URL is not a valid URL");
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new ConfigError("The base URL must start with https://");
  }
  if (url.username || url.password) {
    throw new ConfigError("The base URL must not contain credentials");
  }
  if (url.search || url.hash) {
    throw new ConfigError("The base URL must not contain a query or fragment");
  }
  if (!canSendToken(url)) {
    throw new ConfigError("The base URL must use https:// (plain http:// is allowed only for localhost)");
  }
  return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
}
