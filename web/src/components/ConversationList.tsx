import type { Thread } from "../lib/types";

interface Props {
  threads: Thread[];
  activeId: string | null;
  onSelect: (threadId: string) => void;
  onNew: () => void;
  disabled?: boolean;
}

export function ConversationList({ threads, activeId, onSelect, onNew, disabled }: Props) {
  return (
    <nav className="conversations" aria-label="Conversations">
      <button type="button" className="new" onClick={onNew} disabled={disabled}>
        New conversation
      </button>
      {threads.length === 0 ? (
        <p className="muted">No conversations yet.</p>
      ) : (
        <ul>
          {threads.map((t) => (
            <li key={t.thread_id}>
              <button
                type="button"
                className={t.thread_id === activeId ? "thread active" : "thread"}
                aria-current={t.thread_id === activeId ? "true" : undefined}
                onClick={() => onSelect(t.thread_id)}
                disabled={disabled}
                title={t.updated_at}
              >
                {t.title}
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
