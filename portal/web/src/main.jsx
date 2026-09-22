import React, {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import "./style.css";

const machineFromPath = () => {
  const match = window.location.pathname.match(/^\/([a-z0-9-]+)\/machine-data\/?/);
  return match?.[1] || null;
};

function App() {
  const [machine] = useState(machineFromPath());
  const [summary, setSummary] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!machine) { setError("Choose a machine data URL."); return; }
    const load = async () => {
      try {
        const [info, runs] = await Promise.all([
          fetch(`/api/machines/${machine}`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
          fetch(`/api/machines/${machine}/sessions`).then(r => r.ok ? r.json() : Promise.reject(r.status)),
        ]);
        setSummary(info); setSessions(runs); setError("");
      } catch { setError("The data portal could not load this machine."); }
    };
    load(); const timer = setInterval(load, 5000); return () => clearInterval(timer);
  }, [machine]);

  if (error) return <main><h1>TestbenchDAQ data</h1><p>{error}</p></main>;
  const showArtifacts = async (sessionId) => {
    const files = await fetch(`/api/machines/${machine}/sessions/${encodeURIComponent(sessionId)}/artifacts`).then(r => r.json());
    setArtifacts(files.map(file => ({...file, sessionId})));
  };
  return <main>
    <header><h1>{machine || "Machine"} data</h1><p>Read-only view · refreshes every 5 seconds</p></header>
    <section className="status"><h2>Current state</h2>
      <p><strong>{summary?.active_session?.live_status?.phase || "idle"}</strong></p>
      {summary?.active_session && <p>Session {summary.active_session.session_id}; run {summary.active_session.live_status?.current_run ?? "—"}</p>}
    </section>
    <section><h2>Sessions</h2><table><thead><tr><th>Started</th><th>Name</th><th>Status</th><th>Runs</th><th>Manifest</th></tr></thead>
      <tbody>{sessions.map(session => <tr key={session.session_id}><td>{session.started_at_utc || "—"}</td><td>{session.name || "—"}</td><td>{session.status}</td><td>{session.run_count}</td><td><a href={`/api/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/manifest`} target="_blank">JSON</a> · <button onClick={() => showArtifacts(session.session_id)}>files</button></td></tr>)}</tbody>
    </table></section>
    {artifacts.length > 0 && <section><h2>Session files</h2><ul>{artifacts.map(file => <li key={file.path}><a href={`/api/machines/${machine}/sessions/${encodeURIComponent(file.sessionId)}/artifacts/${file.path}`}>{file.path}</a> ({file.size_bytes} bytes)</li>)}</ul></section>}
  </main>;
}
createRoot(document.getElementById("root")).render(<App />);
