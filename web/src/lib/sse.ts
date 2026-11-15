// Incremental Server-Sent Events parser (https://html.spec.whatwg.org/multipage/server-sent-events.html).
//
// The chat endpoint is a POST with a bearer token, which the browser's
// EventSource cannot send, so the UI reads the response body with fetch and
// feeds chunks to this parser. Chunks may split anywhere, including inside a
// line or between "\r" and "\n".

export interface SSEMessage {
  event: string;
  data: string;
  id?: string;
}

export class SSEParser {
  private buffer = "";
  private data: string[] = [];
  private event = "";
  private id: string | undefined;

  push(chunk: string): SSEMessage[] {
    this.buffer += chunk;
    const out: SSEMessage[] = [];
    for (;;) {
      const match = /\r\n|\r|\n/.exec(this.buffer);
      if (!match) break;
      // A trailing "\r" may be the first half of "\r\n": wait for more input.
      if (match[0] === "\r" && match.index === this.buffer.length - 1) break;
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const message = this.line(line);
      if (message) out.push(message);
    }
    return out;
  }

  /** Flush a final event that was not followed by a blank line. */
  end(): SSEMessage[] {
    const out: SSEMessage[] = [];
    if (this.buffer) {
      const message = this.line(this.buffer);
      this.buffer = "";
      if (message) out.push(message);
    }
    const last = this.line("");
    if (last) out.push(last);
    return out;
  }

  private line(line: string): SSEMessage | null {
    if (line === "") {
      if (this.data.length === 0) {
        this.event = "";
        return null;
      }
      const message: SSEMessage = { event: this.event || "message", data: this.data.join("\n") };
      if (this.id !== undefined) message.id = this.id;
      this.data = [];
      this.event = "";
      return message;
    }
    if (line.startsWith(":")) return null; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.event = value;
    else if (field === "id" && !value.includes("\0")) this.id = value;
    return null;
  }
}
