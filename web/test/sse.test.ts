import { describe, expect, it } from "vitest";
import { SSEParser } from "../src/lib/sse";

describe("SSEParser", () => {
  it("parses events split at arbitrary points", () => {
    const text = "event: message\nid: 1\ndata: {\"a\":1}\n\nid: 2\ndata: {\"b\":2}\n\n";
    for (let size = 1; size <= text.length; size++) {
      const parser = new SSEParser();
      const out = [];
      for (let i = 0; i < text.length; i += size) out.push(...parser.push(text.slice(i, i + size)));
      expect(out).toEqual([
        { event: "message", data: '{"a":1}', id: "1" },
        { event: "message", data: '{"b":2}', id: "2" },
      ]);
    }
  });

  it("handles CRLF split between chunks, multi-line data and comments", () => {
    const parser = new SSEParser();
    const out = [
      ...parser.push(": keep-alive\r"),
      ...parser.push("\ndata: line one\r\ndata: line two\r"),
      ...parser.push("\n\r\n"),
    ];
    expect(out).toEqual([{ event: "message", data: "line one\nline two" }]);
  });

  it("flushes a final event without a trailing blank line", () => {
    const parser = new SSEParser();
    expect(parser.push("data: last")).toEqual([]);
    expect(parser.end()).toEqual([{ event: "message", data: "last" }]);
  });

  it("ignores events with no data", () => {
    expect(new SSEParser().push("event: ping\n\n")).toEqual([]);
  });
});
