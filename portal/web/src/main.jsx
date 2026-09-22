import React, {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import "./style.css";

const pathParts = () => window.location.pathname.match(/^\/data(?:\/([a-z0-9-]+))?\/?/);
const api = "/data/api";
const enc = value => value.split("/").map(encodeURIComponent).join("/");
const bytes = value => `${(value / 1073741824).toFixed(1)} GiB`;

function Chart({chart, refresh}) {
  const canvas = useRef(null), drag = useRef(null), [cursor, setCursor] = useState(null);
  useEffect(() => {
    const node = canvas.current; if (!node || !chart) return;
    const ctx = node.getContext("2d"), ratio = devicePixelRatio || 1, width = node.clientWidth, height = node.clientHeight;
    node.width = width * ratio; node.height = height * ratio; ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
    const pad = {l: 58, r: 18, t: 18, b: 30}, w = width - pad.l - pad.r, h = height - pad.t - pad.b;
    const all = chart.series.flatMap(series => series.points.flatMap(point => [point[1], point[2]])); if (!all.length) return;
    const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
    ctx.strokeStyle = "#2b3a55"; ctx.lineWidth = 1; for (let i = 0; i < 5; i++) { const y = pad.t + h * i / 4; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + w, y); ctx.stroke(); }
    const colors = ["#32c6ff", "#1ccf9f", "#ffb648", "#d62b8a", "#a78bfa", "#ff6767", "#60a5fa", "#f59e0b"];
    chart.series.forEach((series, index) => { ctx.strokeStyle = colors[index % colors.length]; ctx.beginPath(); series.points.forEach(([x, min, max], point) => { const px = pad.l + ((x - chart.start_s) / (chart.end_s - chart.start_s || 1)) * w, y1 = pad.t + h - ((min - lo) / span) * h, y2 = pad.t + h - ((max - lo) / span) * h; ctx.moveTo(px, y1); ctx.lineTo(px, y2); }); ctx.stroke(); });
    ctx.fillStyle = "#9eb0cd"; ctx.font = "11px ui-monospace"; ctx.fillText(`${chart.start_s.toFixed(3)} s`, pad.l, height - 8); ctx.fillText(`${chart.end_s.toFixed(3)} s`, width - 90, height - 8); ctx.fillText(hi.toPrecision(4), 4, pad.t + 8); ctx.fillText(lo.toPrecision(4), 4, pad.t + h);
    if (cursor != null) { ctx.strokeStyle = "#e9f0fb"; ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(cursor, pad.t); ctx.lineTo(cursor, pad.t + h); ctx.stroke(); ctx.setLineDash([]); }
  }, [chart, cursor]);
  if (!chart) return null;
  const zoom = event => { event.preventDefault(); const span = chart.end_s - chart.start_s, factor = event.deltaY > 0 ? 1.5 : .67, center = chart.start_s + span / 2, next = Math.max(.001, span * factor); refresh(Math.max(0, center - next / 2), center + next / 2); };
  const down = event => { drag.current = {x: event.clientX, start: chart.start_s, span: chart.end_s - chart.start_s}; event.currentTarget.setPointerCapture(event.pointerId); };
  const move = event => { const rect = event.currentTarget.getBoundingClientRect(); setCursor(event.clientX - rect.left); if (!drag.current) return; const shift = ((event.clientX - drag.current.x) / rect.width) * drag.current.span; refresh(Math.max(0, drag.current.start - shift), Math.max(.001, drag.current.start - shift + drag.current.span)); };
  return <canvas className="chart" ref={canvas} onWheel={zoom} onPointerDown={down} onPointerMove={move} onPointerUp={() => { drag.current = null; }} />;
}

function Overview({data}) { const storage = data.storage, health = storage.free_percent < 10 || storage.free_bytes < 50e9 ? "danger" : storage.free_percent < 20 || storage.free_bytes < 100e9 ? "warn" : "ok"; return <main><header className="topbar"><b>TESTBENCH DATA</b><span>READ-ONLY PORTAL</span></header><section className="storage card"><div><div className="label">SERVER STORAGE</div><strong>{bytes(storage.free_bytes)} free</strong><small>{storage.free_percent}% free · {bytes(storage.used_bytes)} used of {bytes(storage.total_bytes)}</small></div><span className={`pill ${health}`}>{health.toUpperCase()}</span></section><h2>Machines</h2><div className="machine-grid">{data.machines.map(machine => <a className="machine-card" href={`/data/${machine.machine}/`} key={machine.machine}><div className="machine-icon">▣</div><div><h3>{machine.machine}</h3><p>{machine.session_count} sessions · {machine.latest_session?.status || "no data"}</p><small>{machine.latest_session?.started_at_utc || "No sessions yet"}</small></div><span>VIEW DATA →</span></a>)}</div></main>; }

function Machine({machine}) {
  const [sessions, setSessions] = useState([]), [files, setFiles] = useState([]), [selected, setSelected] = useState(null), [chart, setChart] = useState(null), [columns, setColumns] = useState([]), [range, setRange] = useState([null, null]), [dark, setDark] = useState(localStorage.theme !== "light");
  useEffect(() => { document.documentElement.classList.toggle("light", !dark); localStorage.theme = dark ? "dark" : "light"; }, [dark]);
  useEffect(() => { fetch(`${api}/machines/${machine}/sessions`).then(r => r.json()).then(setSessions); }, [machine]);
  const loadFiles = async id => { setSelected(id); setChart(null); setFiles(await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(id)}/artifacts`).then(r => r.json())); };
  const chartUrl = (file, chosen = columns, window = range) => `${api}/machines/${machine}/sessions/${encodeURIComponent(selected)}/chart/${enc(file)}?columns=${encodeURIComponent(chosen.join(","))}&buckets=1400${window[0] == null ? "" : `&start_s=${window[0]}&end_s=${window[1]}`}`;
  const openChart = async file => { setChart(null); const meta = await fetch(chartUrl(file, [], [null, null])).then(r => r.json()); const defaults = file.includes("/signals/") ? meta.available_columns.slice(0, 1) : []; setColumns(defaults); setRange([meta.start_s, meta.end_s]); setChart({...meta, file}); if (defaults.length) { const loaded = await fetch(chartUrl(file, defaults, [meta.start_s, meta.end_s])).then(r => r.json()); setChart({...loaded, file}); } };
  const refresh = async (start, end, chosen = columns) => { if (!chart) return; setRange([start, end]); const loaded = await fetch(chartUrl(chart.file, chosen, [start, end])).then(r => r.json()); setChart({...loaded, file: chart.file}); };
  const toggle = column => { const next = columns.includes(column) ? columns.filter(v => v !== column) : [...columns, column]; setColumns(next); refresh(chart.start_s, chart.end_s, next); };
  return <main><header className="topbar"><a href="/data/">← MACHINES</a><b>{machine.toUpperCase()}</b><button onClick={() => setDark(!dark)}>{dark ? "LIGHT" : "DARK"}</button></header><section className="card"><h2>Sessions</h2><table><thead><tr><th>Started</th><th>Status</th><th>Runs</th><th>Actions</th></tr></thead><tbody>{sessions.map(session => <tr key={session.session_id}><td>{session.started_at_utc}</td><td><span className={`pill ${session.status === "success" ? "ok" : "warn"}`}>{session.status}</span></td><td>{session.run_count}</td><td><button onClick={() => loadFiles(session.session_id)}>FILES</button><a href={`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/download`}>DOWNLOAD ZIP</a></td></tr>)}</tbody></table></section>{selected && <section className="card"><h2>Files · {selected}</h2><div className="files">{files.map(file => <div key={file.path}><a href={`${api}/machines/${machine}/sessions/${encodeURIComponent(selected)}/artifacts/${enc(file.path)}`}>{file.path}</a>{file.path.endsWith(".csv") && <button onClick={() => openChart(file.path)}>GRAPH</button>}</div>)}</div></section>}{chart && <section className="card"><div className="chart-title"><div><h2>Interactive graph</h2><small>{chart.file} · wheel: zoom · drag: pan</small></div><button onClick={() => refresh(0, chart.end_s)}>RESET</button></div><div className="channels">{chart.available_columns.map(column => <label key={column}><input type="checkbox" checked={columns.includes(column)} onChange={() => toggle(column)}/>{column}</label>)}</div><Chart chart={chart} refresh={refresh}/></section>}</main>;
}

function App() { const machine = pathParts()?.[1] || null, [overview, setOverview] = useState(null); useEffect(() => { if (!machine) fetch(`${api}/overview`).then(r => r.json()).then(setOverview); }, [machine]); return machine ? <Machine machine={machine}/> : overview ? <Overview data={overview}/> : <main>Loading…</main>; }
createRoot(document.getElementById("root")).render(<App />);
