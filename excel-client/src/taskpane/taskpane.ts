/**
 * The task pane: base URL and token settings, a "test connection" button and
 * the tool list. Everything shown comes from the gateway or from errors, so
 * it is written with textContent only (never innerHTML), and every error
 * string is redacted.
 */

import type { GatewayClient } from "../lib/client";
import { AddinError } from "../lib/errors";
import { parseEnvelope, toolList, unwrap } from "../lib/envelope";
import { redact } from "../lib/redact";
import type { SettingsStore } from "../lib/settings";
import { normaliseBaseUrl } from "../lib/url";

/** One line describing a health envelope, for the connection test. */
export function describeHealth(body: unknown): string {
  const result = unwrap(parseEnvelope(body)) as {
    status?: unknown;
    version?: unknown;
    plugins?: Array<{ plugin?: unknown; version?: unknown }>;
    failed_plugins?: Array<{ plugin?: unknown }>;
  };
  const models = (result.plugins ?? []).map((p) => `${p.plugin} ${p.version}`).join(", ") || "none";
  const failed = (result.failed_plugins ?? []).map((p) => String(p.plugin)).join(", ");
  return `Connected: server ${result.status} ${result.version}; models: ${models}` + (failed ? `; failed: ${failed}` : "");
}

/** "name: description" lines for the tool list. */
export function toolLines(spec: unknown): string[] {
  return toolList(spec)
    .slice(1)
    .map(([name, description]) => `${name}: ${description}`);
}

export function errorText(error: unknown, secrets: string[]): string {
  const message = error instanceof AddinError || error instanceof Error ? error.message : String(error);
  return redact(message, secrets);
}

export interface TaskpaneDeps {
  settings: SettingsStore;
  client: GatewayClient;
}

export async function initTaskpane(doc: Document, deps: TaskpaneDeps): Promise<void> {
  const el = <T extends HTMLElement>(id: string) => doc.getElementById(id) as T;
  const baseUrl = el<HTMLInputElement>("base-url");
  const token = el<HTMLInputElement>("token");
  const status = el<HTMLElement>("status");
  const tools = el<HTMLUListElement>("tools");
  const secrets = () => [token.value, deps.settings.currentToken(), ...deps.client.secrets()];

  const show = (text: string, isError = false) => {
    status.textContent = text;
    status.className = isError ? "error" : "ok";
  };

  const current = await deps.settings.load();
  baseUrl.value = current.baseUrl;
  token.value = current.token;

  el<HTMLButtonElement>("save").addEventListener("click", async () => {
    try {
      normaliseBaseUrl(baseUrl.value);
      await deps.settings.save({ baseUrl: baseUrl.value, token: token.value });
      show("Saved. Recalculate (Ctrl+Alt+F9) to refresh AA functions.");
    } catch (error) {
      show(errorText(error, secrets()), true);
    }
  });

  el<HTMLButtonElement>("test").addEventListener("click", async () => {
    show("Testing...");
    tools.replaceChildren();
    try {
      show(describeHealth(await deps.client.callTool("health", {})));
      for (const line of toolLines(await deps.client.getJson("/gateway/openapi.json"))) {
        const li = doc.createElement("li");
        li.textContent = line;
        tools.appendChild(li);
      }
    } catch (error) {
      show(errorText(error, secrets()), true);
    }
  });
}
