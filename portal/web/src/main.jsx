import React, {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import "./style.css";

const machineFromPath = () => window.location.pathname.match(/^\/([a-z0-9-]+)\/machine-data\/?/)?.[1] || null;
const portalApi = () => window.location.pathname.match(/^(\/[a-z0-9-]+\/machine-data)\/?/)?.[1] + "/api";
const encodedPath = path => path.split("/").map(encodeURIComponent).join("/");

function Plot({plot}) {
  if (!plot) return null;
  const points = plot.points, xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = (value, min, max, size) => max === min ? size / 2 : ((value - min) / (max - min)) * size;
  const line = points.map(([x, y]) => `${scale(x, minX, maxX, 900).toFixed(1)},${(360 - scale(y, minY, maxY, 360)).toFixed(1)}`).join(" ");
  return <section><h2>Signal preview</h2><p><code>{plot.path}</code> · {points.length.toLocaleString()} sampled points</p>
    <svg className="plot" viewBox="0 0 900 360" role="img" aria-label={`${plot.y_label} against ${plot.x_label}`}><polyline points={line}/></svg>
    <div className="plot-labels"><span>{minX.toFixed(3)} {plot.x_label}</span><span>{maxX.toFixed(3)} {plot.x_label}</span><span>{minY.toFixed(3)} to {maxY.toFixed(3)} {plot.y_label}</span></div>
  </section>;
}

function App() {
  const [machine] = useState(machineFromPath()), [summary, setSummary] = useState(null), [sessions, setSessions] = useState([]);
  const [artifacts, setArtifacts] = useState([]), [plot, setPlot] = useState(null), [error, setError] = useState("");
  const api = portalApi();
  useEffect(() => {
    if (!machine || !api) { setError("Choose a machine data URL."); return; }
    const load = async () => { try { const [info, runs] = await Promise.all([fetch(`${api}/machines/${machine}`).then(r => r.ok ? r.json() : Promise.reject(r.status)), fetch(`${api}/machines/${machine}/sessions`).then(r => r.ok ? r.json() : Promise.reject(r.status))]); setSummary(info); setSessions(runs); setError(""); } catch { setError("The data portal could not load this machine."); } };
    load(); const timer = setInterval(load, 5000); return () => clearInterval(timer);
  }, [machine, api]);
  if (error) return <main><h1>TestbenchDAQ data</h1><p>{error}</p></main>;
  const artifactsUrl = id => `${api}/machines/${machine}/sessions/${encodeURIComponent(id)}/artifacts`;
  const showArtifacts = async id => { const files = await fetch(artifactsUrl(id)).then(r => r.json()); setArtifacts(files.map(file => ({...file, sessionId: id}))); setPlot(null); };
  const showPlot = async file => { const response = await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(file.sessionId)}/plot/${encodedPath(file.path)}`); if (!response.ok) { setError("This CSV could not be plotted."); return; } setPlot(await response.json()); };
  const artifactUrl = file => `${artifactsUrl(file.sessionId)}/${encodedPath(file.path)}`;
  return <main><header><h1>{machine || "Machine"} data</h1><p>Read-only view · refreshes every 5 seconds</p></header>
    <section className="status"><h2>Current state</h2><p><strong>{summary?.active_session?.live_status?.phase || "idle"}</strong></p>{summary?.active_session && <p>Session {summary.active_session.session_id}; run {summary.active_session.live_status?.current_run ?? "—"}</p>}</section>
    <section><h2>Sessions</h2><table><thead><tr><th>Started</th><th>Name</th><th>Status</th><th>Runs</th><th>Actions</th></tr></thead><tbody>{sessions.map(session => <tr key={session.session_id}><td>{session.started_at_utc || "—"}</td><td>{session.name || "—"}</td><td>{session.status}</td><td>{session.run_count}</td><td className="actions"><a href={`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/manifest`} target="_blank" rel="noreferrer">manifest</a><button onClick={() => showArtifacts(session.session_id)}>files</button><a href={`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/download`}>download ZIP</a></td></tr>)}</tbody></table></section>
    {artifacts.length > 0 && <section><h2>Session files</h2><p>CSV signal files can be previewed with up to 2,000 evenly sampled points. ZIP downloads contain every file in the session.</p><ul className="files">{artifacts.map(file => <li key={file.path}><a href={artifactUrl(file)}>{file.path}</a> ({file.size_bytes.toLocaleString()} bytes) {file.path.endsWith(".csv") && <button onClick={() => showPlot(file)}>graph</button>}</li>)}</ul></section>}
    <Plot plot={plot}/></main>;
}

createRoot(document.getElementById("root")).render(<App />);
