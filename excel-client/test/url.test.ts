import { ConfigError } from "../src/lib/errors";
import { canSendToken, isLoopbackHost, normaliseBaseUrl } from "../src/lib/url";
import { TOKEN, json, makeClient, mockFetch } from "./helpers";

describe("the token is only sent to https:// or loopback", () => {
  test.each([
    "https://gateway.example.test",
    "https://gateway.example.test:8443/base",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://127.10.20.30",
    "http://[::1]:8000",
    "http://LOCALHOST:8000",
  ])("%s is allowed", async (baseUrl) => {
    const { fn, calls } = mockFetch(() => json({ paths: {} }));
    await makeClient(fn, { settings: () => ({ baseUrl, token: TOKEN }) }).getJson("/gateway/openapi.json");
    expect(calls).toHaveLength(1);
    expect((calls[0].init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
  });

  test.each([
    "http://gateway.example.test",
    "http://localhost.example.test",
    "http://127.0.0.1.example.test",
    "http://192.0.2.10:8000", // documentation range (RFC 5737)
    "http://[::2]",
    "ftp://gateway.example.test",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "https://user:pass@gateway.example.test",
    "https://gateway.example.test/?token=x",
    "https://gateway.example.test/#frag",
    "gateway.example.test",
    "",
  ])("%p is refused before any fetch", async (baseUrl) => {
    const { fn, calls } = mockFetch(() => json({}));
    await expect(
      makeClient(fn, { settings: () => ({ baseUrl, token: TOKEN }) }).getJson("/gateway/openapi.json"),
    ).rejects.toBeInstanceOf(ConfigError);
    expect(calls).toHaveLength(0);
  });
});

describe("helpers", () => {
  test("isLoopbackHost", () => {
    expect(isLoopbackHost("localhost")).toBe(true);
    expect(isLoopbackHost("127.0.0.1")).toBe(true);
    expect(isLoopbackHost("[::1]")).toBe(true);
    expect(isLoopbackHost("127.0.0.256")).toBe(false);
    expect(isLoopbackHost("128.0.0.1")).toBe(false);
    expect(isLoopbackHost("example.test")).toBe(false);
  });

  test("canSendToken uses the parsed URL, so tricks normalise first", () => {
    expect(canSendToken(new URL("http://127.1:8000"))).toBe(true); // WHATWG URL -> 127.0.0.1
    expect(canSendToken(new URL("http://localhost@evil.example.test"))).toBe(false);
  });

  test("normaliseBaseUrl strips trailing slashes", () => {
    expect(normaliseBaseUrl(" https://h.example.test/a/b/ ")).toBe("https://h.example.test/a/b");
    expect(normaliseBaseUrl("http://localhost:8000/")).toBe("http://localhost:8000");
  });
});
