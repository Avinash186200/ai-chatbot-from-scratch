import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatView } from "./components/ChatView";
import { ConversationList } from "./components/ConversationList";
import { ProvenancePanel } from "./components/ProvenancePanel";
import { TokenForm } from "./components/TokenForm";
import { ApiClient, ApiError } from "./lib/api";
import { applyEvent, fromTranscript, provenanceRows, userItem } from "./lib/chat";
import { storage } from "./lib/storage";
import type { ChatItem, ServerConfig, Thread } from "./lib/types";

interface AppProps {
  /** Injected in tests; the browser's fetch otherwise. */
  fetchImpl?: typeof fetch;
}

export function App({ fetchImpl }: AppProps = {}) {
  const [token, setToken] = useState(storage.token());
  const [user, setUser] = useState(storage.user());
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const abort = useRef<AbortController | null>(null);

  const api = useMemo(() => (token ? new ApiClient({ token, user, fetchImpl }) : null), [token, user, fetchImpl]);

  const handleError = useCallback((err: unknown) => {
    if (err instanceof DOMException && err.name === "AbortError") return;
    if (err instanceof ApiError && err.status === 401) {
      storage.setToken("");
      setToken("");
    }
    setNotice(err instanceof Error ? err.message : "Something went wrong.");
  }, []);

  const refreshThreads = useCallback(async () => {
    if (!api) return;
    try {
      setThreads(await api.listThreads());
    } catch (err) {
      handleError(err);
    }
  }, [api, handleError]);

  useEffect(() => {
    if (!api) return;
    setNotice("");
    void refreshThreads();
    api.config().then(setConfig, () => setConfig(null));
  }, [api, refreshThreads]);

  function saveToken(newToken: string, newUser: string) {
    storage.setToken(newToken);
    storage.setUser(newUser);
    setUser(newUser);
    setToken(newToken);
    setActiveId(null);
    setItems([]);
  }

  function signOut() {
    abort.current?.abort();
    storage.setToken("");
    setToken("");
    setThreads([]);
    setItems([]);
    setActiveId(null);
  }

  async function newConversation() {
    if (!api) return;
    try {
      const thread = await api.createThread();
      setActiveId(thread.thread_id);
      setItems([]);
      setThreads((prev) => [thread, ...prev.filter((t) => t.thread_id !== thread.thread_id)]);
    } catch (err) {
      handleError(err);
    }
  }

  async function openConversation(threadId: string) {
    if (!api) return;
    try {
      const detail = await api.getThread(threadId);
      setActiveId(threadId);
      setItems(fromTranscript(detail.messages));
      setNotice("");
    } catch (err) {
      handleError(err);
    }
  }

  async function send(message: string) {
    if (!api) return;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setNotice("");
    setItems((prev) => [...prev, userItem(message)]);
    try {
      const threadId = await api.chat(
        message,
        activeId,
        (event) => {
          setItems((prev) => applyEvent(prev, event));
          if (event.thread_id) setActiveId(event.thread_id);
        },
        controller.signal,
      );
      if (threadId) setActiveId(threadId);
      await refreshThreads();
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(false);
      abort.current = null;
    }
  }

  const rows = useMemo(() => provenanceRows(items), [items]);

  return (
    <div className="app">
      <header className="topbar">
        <h1>Analytics Assistant</h1>
        {config && (
          <span className="muted" data-testid="server-config">
            agent: {config.agent} · llm: {config.llm_provider}
          </span>
        )}
        {token && (
          <button type="button" className="link" onClick={signOut}>
            Sign out
          </button>
        )}
      </header>
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {!token ? (
        <TokenForm user={user} onSave={saveToken} />
      ) : (
        <main className="layout">
          <ConversationList
            threads={threads}
            activeId={activeId}
            onSelect={openConversation}
            onNew={newConversation}
            disabled={busy}
          />
          <ChatView items={items} busy={busy} onSend={send} onStop={() => abort.current?.abort()} />
          <ProvenancePanel rows={rows} />
        </main>
      )}
    </div>
  );
}
