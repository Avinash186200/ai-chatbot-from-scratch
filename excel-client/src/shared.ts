/**
 * Entry point for the shared runtime: one page hosts both the custom
 * functions and the task pane, so they share the settings and the gateway
 * client (and its dedup and concurrency gate).
 */

import { createFunctions, registerFunctions } from "./functions/functions";
import { GatewayClient } from "./lib/client";
import { SettingsStore } from "./lib/settings";
import { initTaskpane } from "./taskpane/taskpane";

const settings = new SettingsStore();
const client = new GatewayClient({ settings: () => settings.load() });

registerFunctions(createFunctions(client, () => [settings.currentToken()]));

const office = (globalThis as { Office?: { onReady: (cb: () => void) => void } }).Office;
if (office && typeof document !== "undefined") {
  // The script loads in <head>; wait for both Office and the DOM.
  const domReady =
    document.readyState === "loading"
      ? new Promise<void>((resolve) => document.addEventListener("DOMContentLoaded", () => resolve(), { once: true }))
      : Promise.resolve();
  office.onReady(() => {
    void domReady.then(() => initTaskpane(document, { settings, client }));
  });
}
