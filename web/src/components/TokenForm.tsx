import { useState, type FormEvent } from "react";

interface Props {
  user: string;
  onSave: (token: string, user: string) => void;
}

/** Token entry. The field is a password input and is cleared once saved. */
export function TokenForm({ user, onSave }: Props) {
  const [token, setToken] = useState("");
  const [label, setLabel] = useState(user);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    onSave(token.trim(), label.trim() || "local");
    setToken("");
  }

  return (
    <form className="token-form" onSubmit={submit} aria-label="Connect">
      <h2>Connect</h2>
      <p className="muted">
        Paste the gateway token (<code>AA_GATEWAY_TOKEN</code>). It is kept in this tab only.
      </p>
      <label>
        Token
        <input
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="gateway token"
        />
      </label>
      <label>
        User label
        <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={64} />
      </label>
      <button type="submit">Connect</button>
    </form>
  );
}
