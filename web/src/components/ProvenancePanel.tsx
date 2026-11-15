import type { ProvenanceRow } from "../lib/chat";

/** Where every number in this conversation came from. */
export function ProvenancePanel({ rows }: { rows: ProvenanceRow[] }) {
  return (
    <aside className="provenance" aria-label="Provenance">
      <h2>Provenance</h2>
      {rows.length === 0 ? (
        <p className="muted">Numbers in answers come from tool results. Their sources appear here.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Tool</th>
              <th scope="col">Model</th>
              <th scope="col">Version</th>
              <th scope="col">As of</th>
              <th scope="col">Snapshot</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} title={`computed at ${r.computedAt}`}>
                <td>{r.tool}</td>
                <td>{r.model}</td>
                <td>{r.version}</td>
                <td>{r.asOf}</td>
                <td className="mono">{r.snapshotId}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </aside>
  );
}
