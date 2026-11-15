/**
 * The token must not appear in any error string the add-in produces: the
 * client's errors, the cell error (CustomFunctions.Error) and the task pane.
 * Each test drives one error path with the token planted where a careless
 * implementation would leak it.
 */

import { createFunctions } from "../src/functions/functions";
import { redact, REDACTED } from "../src/lib/redact";
import { errorText } from "../src/taskpane/taskpane";
import { BASE, TOKEN, hangingFetch, json, makeClient, mockFetch, refusal, status } from "./helpers";

afterEach(() => jest.useRealTimers());

async function cellError(fetchImpl: typeof fetch, settings = { baseUrl: BASE, token: TOKEN }) {
  const client = makeClient(fetchImpl, { settings: () => settings });
  const fns = createFunctions(client, () => [settings.token]);
  return (await fns.forecast("RATE_LONG", 5).catch((e) => e)) as Error & { code: string };
}

function expectClean(err: Error) {
  expect(err.message).not.toContain(TOKEN);
  expect(err.message).not.toContain(encodeURIComponent(TOKEN));
  expect(String(err)).not.toContain(TOKEN);
  expect(JSON.stringify(err)).not.toContain(TOKEN);
}

describe("redact()", () => {
  test("replaces every occurrence, URL-encoded copies and any bearer value", () => {
    const text = `a ${TOKEN} b ${encodeURIComponent(TOKEN)} c Authorization: Bearer other-secret-123`;
    const out = redact(text, [TOKEN]);
    expect(out).toBe(`a ${REDACTED} b ${REDACTED} c Authorization: Bearer ${REDACTED}`);
  });

  test("empty secrets are ignored", () => {
    expect(redact("plain", ["", undefined, null])).toBe("plain");
  });
});

describe("no error path leaks the token", () => {
  test("server refusal that echoes the token in its message", async () => {
    const err = await cellError(mockFetch(() => json(refusal("SCHEMA_VIOLATION", `bad header Bearer ${TOKEN}`))).fn);
    expect(err.code).toBe("#VALUE!");
    expect(err.message).toContain("SCHEMA_VIOLATION");
    expectClean(err);
  });

  test("server refusal that echoes the bare token", async () => {
    const err = await cellError(mockFetch(() => json(refusal("SNAPSHOT_UNAVAILABLE", `token=${TOKEN}`))).fn);
    expect(err.code).toBe("#N/A");
    expectClean(err);
  });

  test.each([401, 403, 404, 500])("HTTP %d", async (code) => {
    expectClean(await cellError(mockFetch(() => status(code)).fn));
  });

  test("network failure whose exception message carries the token", async () => {
    jest.useFakeTimers();
    const { fn } = mockFetch(() => {
      throw new TypeError(`connect failed for ${BASE}?access_token=${TOKEN}`);
    });
    const p = cellError(fn);
    await jest.advanceTimersByTimeAsync(46_000);
    const err = await p;
    expect(err.code).toBe("#N/A");
    expectClean(err);
  });

  test("timeout", async () => {
    jest.useFakeTimers();
    const p = cellError(hangingFetch().fn);
    await jest.advanceTimersByTimeAsync(46_000);
    const err = await p;
    expect(err.code).toBe("#N/A");
    expectClean(err);
  });

  test("malformed JSON body that contains the token", async () => {
    const { fn } = mockFetch(() => new Response(`not json ${TOKEN}`, { status: 200 }));
    expectClean(await cellError(fn));
  });

  test("an envelope-shaped body that is not an envelope", async () => {
    const { fn } = mockFetch(() => json({ ok: "maybe", echo: TOKEN }));
    expectClean(await cellError(fn));
  });

  test("an unexpected exception from fetch itself (not a network error)", async () => {
    const fn = (() => {
      throw new RangeError(`internal state ${TOKEN}`);
    }) as unknown as typeof fetch;
    jest.useFakeTimers();
    const p = cellError(fn);
    await jest.advanceTimersByTimeAsync(46_000);
    expectClean(await p);
  });

  test("a base URL with the token pasted into it", async () => {
    const { fn, calls } = mockFetch(() => json({}));
    const err = await cellError(fn, { baseUrl: `http://evil.example.test/${TOKEN}`, token: TOKEN });
    expect(calls).toHaveLength(0);
    expectClean(err);
  });

  test("a base URL that is not a URL and contains the token", async () => {
    const err = await cellError(mockFetch(() => json({})).fn, { baseUrl: `not a url ${TOKEN}`, token: TOKEN });
    expect(err.code).toBe("#VALUE!");
    expectClean(err);
  });

  test("a settings store that throws with the token in its message", async () => {
    const client = makeClient(mockFetch(() => json({})).fn, {
      settings: async () => {
        throw new Error(`storage failure reading ${TOKEN}`);
      },
    });
    const fns = createFunctions(client, () => [TOKEN]);
    const err = (await fns.tools().catch((e) => e)) as Error & { code: string };
    expect(err.code).toBe("#VALUE!");
    expectClean(err);
  });

  test("the task pane's error text", () => {
    expect(errorText(new Error(`failed with Bearer ${TOKEN}`), [TOKEN])).not.toContain(TOKEN);
    expect(errorText(`raw ${TOKEN}`, [TOKEN])).toBe(`raw ${REDACTED}`);
  });
});
