/**
 * Token redaction. Every error string the add-in shows (a cell's error
 * tooltip, the task pane) goes through `redact` with the token in use, so a
 * server that echoes headers, a URL with the token pasted into it, or an
 * exception message can never put the token on screen.
 */

export const REDACTED = "[redacted]";

export function redact(text: unknown, secrets: ReadonlyArray<string | undefined | null>): string {
  let out = typeof text === "string" ? text : String(text);
  for (const secret of secrets) {
    if (!secret) continue;
    const variants = new Set([secret, encodeURIComponent(secret), secret.trim()]);
    for (const v of variants) {
      if (v) out = out.split(v).join(REDACTED);
    }
  }
  // Any bearer credential, whatever its value.
  return out.replace(/(bearer\s+)(?!\[redacted\])[^\s"',;]+/gi, `$1${REDACTED}`);
}
