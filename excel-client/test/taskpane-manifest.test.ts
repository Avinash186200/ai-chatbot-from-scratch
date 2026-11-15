import { readFileSync } from "fs";
import { join } from "path";
import { describeHealth, toolLines } from "../src/taskpane/taskpane";
import { okEnvelope, refusal } from "./helpers";

describe("task pane helpers", () => {
  test("describeHealth lists the loaded model packages and versions", () => {
    const env = okEnvelope({
      status: "ok",
      version: "0.2.0",
      plugins: [
        { plugin: "aa-model-ar1", version: "1.1.0" },
        { plugin: "aa-model-regime", version: "0.4.0" },
      ],
      failed_plugins: [{ plugin: "broken" }],
    });
    expect(describeHealth(env)).toBe(
      "Connected: server ok 0.2.0; models: aa-model-ar1 1.1.0, aa-model-regime 0.4.0; failed: broken",
    );
  });

  test("describeHealth throws on a refusal", () => {
    expect(() => describeHealth(refusal("INTERNAL", "x"))).toThrow();
  });

  test("toolLines", () => {
    expect(toolLines({ paths: { "/a": { post: { operationId: "a", description: "A." } } } })).toEqual(["a: A."]);
  });
});

describe("manifest.xml", () => {
  const manifest = readFileSync(join(__dirname, "..", "manifest.xml"), "utf8");
  const page = readFileSync(join(__dirname, "..", "src", "shared.html"), "utf8");

  test("custom functions use the AA namespace", () => {
    expect(manifest).toMatch(/<bt:String id="Functions.Namespace" DefaultValue="AA"\/>/);
    expect(manifest).not.toMatch(/MCP/);
  });

  test("shared runtime: functions and task pane load the same page", () => {
    expect(manifest).toMatch(/<Runtime resid="Shared.Url" lifetime="long"\/>/);
    expect(manifest).toMatch(/<Set Name="SharedRuntime" MinVersion="1.1"\/>/);
    const pageRefs = manifest.match(/<SourceLocation resid="Shared.Url"\/>/g) ?? [];
    expect(pageRefs.length).toBe(2); // custom functions Page + task pane button
  });

  test("every URL is a localhost placeholder", () => {
    const urls = manifest.match(/https?:\/\/[^"<\s]+/g) ?? [];
    const namespaces = ["http://schemas.microsoft.com/", "http://www.w3.org/"];
    const external = urls.filter((u) => !namespaces.some((ns) => u.startsWith(ns)));
    expect(external.length).toBeGreaterThan(0);
    for (const u of external) expect(u.startsWith("https://localhost:3000/") || u === "https://localhost:3000").toBe(true);
  });

  test("generic name and a placeholder id", () => {
    expect(manifest).toContain('<DisplayName DefaultValue="Analytics Assistant"/>');
    expect(manifest).toMatch(/<Id>[0-9a-f-]{36}<\/Id>/);
  });

  test("the page loads office.js and the bundle, and has the settings controls", () => {
    expect(page).toContain("https://appsforoffice.microsoft.com/lib/1/hosted/office.js");
    expect(page).toContain('src="shared.js"');
    for (const id of ["base-url", "token", "save", "test", "status", "tools"]) expect(page).toContain(`id="${id}"`);
    expect(page).toMatch(/id="token" type="password"/);
  });
});
