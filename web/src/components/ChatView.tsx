import { useEffect, useRef, useState, type FormEvent } from "react";
import type { ChatItem } from "../lib/types";
import { ToolCallCard } from "./ToolCallCard";

interface Props {
  items: ChatItem[];
  busy: boolean;
  onSend: (message: string) => void;
  onStop: () => void;
}

const EXAMPLES = [
  "Forecast RATE_LONG 5 days ahead as of 2023-06-30",
  "What regime is POLICY_RATE in as of 2023-06-30?",
  "What is a snapshot id?",
];

/**
 * The conversation. Model output is rendered as React text nodes only:
 * no dangerouslySetInnerHTML, no markdown-to-HTML, so a reply that
 * contains markup shows the markup as text.
 */
export function ChatView({ items, busy, onSend, onStop }: Props) {
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [items]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const message = draft.trim();
    if (!message || busy) return;
    setDraft("");
    onSend(message);
  }

  return (
    <section className="chat" aria-label="Chat">
      <div className="log" role="log" aria-live="polite">
        {items.length === 0 && (
          <div className="empty">
            <p className="muted">Ask about the synthetic series. Every number comes from a tool call with an as-of date.</p>
            <ul>
              {EXAMPLES.map((ex) => (
                <li key={ex}>
                  <button type="button" className="example" onClick={() => setDraft(ex)}>
                    {ex}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {items.map((it) => {
          switch (it.kind) {
            case "user":
              return (
                <p key={it.key} className="msg user">
                  {it.text}
                </p>
              );
            case "assistant":
              return (
                <p key={it.key} className="msg assistant" data-testid="assistant-text">
                  {it.text}
                </p>
              );
            case "route":
              return (
                <p key={it.key} className="route">
                  route: {it.route} ({it.source})
                </p>
              );
            case "tool":
              return <ToolCallCard key={it.key} item={it} />;
            case "error":
              return (
                <p key={it.key} className="msg error" role="alert">
                  {it.text}
                </p>
              );
          }
        })}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={submit}>
        <input
          type="text"
          aria-label="Message"
          value={draft}
          maxLength={4000}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask a question"
        />
        {busy ? (
          <button type="button" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!draft.trim()}>
            Send
          </button>
        )}
      </form>
    </section>
  );
}
