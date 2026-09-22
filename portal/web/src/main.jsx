import React, {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import {ArrowLeft, ChevronRight, Download, FileJson, FileText, Folder, FolderOpen, Moon, Pencil, Sun} from "lucide-react";
import "./style.css";

const api = "/data/api";
const machineFromUrl = () => window.location.pathname.match(/^\/data(?:\/([a-z0-9-]+))?\/?/)?.[1] || null;
const enc = path => path.split("/").map(encodeURIComponent).join("/");
const fmtTime = value => value ? new Intl.DateTimeFormat("nl-NL", {dateStyle:"medium", timeStyle:"medium"}).format(new Date(value)) : "—";
const Spinner = ({label="Loading"}) => <div className="loading"><i/>{label}…</div>;
const IconButton = ({icon: Icon, children, ...props}) => <button {...props}><Icon size={15}/>{children}</button>;

function entriesFor(files, folder) {
  const prefix = folder.length ? `${folder.join("/")}/` : "";
  const found = new Map();
  files.filter(file => file.path.startsWith(prefix)).forEach(file => {
    const [name, ...rest] = file.path.slice(prefix.length).split("/");
    found.set(`${rest.length ? "d" : "f"}:${name}`, rest.length ? {kind:"folder", name} : {kind:"file", name, file});
  });
  return [...found.values()].sort((a,b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
}

function CsvViewer({preview, page}) {
  if (!preview) return <Spinner label="Loading CSV"/>;
  return <><div className="file-heading"><div><div className="label">CSV PREVIEW</div><h2>{preview.path.split("/").at(-1)}</h2><small>Rows {preview.offset+1}–{preview.offset+preview.rows.length} · 250 rows per page</small></div><div><IconButton icon={ArrowLeft} disabled={!preview.offset} onClick={()=>page(Math.max(0,preview.offset-preview.limit))}>Previous</IconButton><IconButton icon={ChevronRight} disabled={!preview.has_more} onClick={()=>page(preview.offset+preview.limit)}>Next</IconButton></div></div><div className="csv-scroll"><table className="csv"><thead><tr><th>#</th>{preview.columns.map(c=><th key={c}>{c}</th>)}</tr></thead><tbody>{preview.rows.map((row,i)=><tr key={preview.offset+i}><td>{preview.offset+i+1}</td>{preview.columns.map((_,j)=><td key={j}>{row[j]||""}</td>)}</tr>)}</tbody></table></div></>;
}

function Overview() {
  const [data,setData]=useState(null); useEffect(()=>{fetch(`${api}/overview`).then(r=>r.json()).then(setData)},[]);
  if(!data)return <main><Spinner/></main>;
  const s=data.storage, state=s.free_percent<10||s.free_bytes<50e9?"danger":s.free_percent<20||s.free_bytes<100e9?"warn":"ok", gib=v=>`${(v/1073741824).toFixed(1)} GiB`;
  return <main><header className="topbar"><b>TESTBENCH DATA</b><span>READ-ONLY PORTAL</span></header><section className="storage card"><div><div className="label">SERVER STORAGE</div><strong>{gib(s.free_bytes)} free</strong><small>{s.free_percent}% free · {gib(s.used_bytes)} used of {gib(s.total_bytes)}</small></div><span className={`pill ${state}`}>{state.toUpperCase()}</span></section><h2>Machines</h2><div className="machine-grid">{data.machines.map(m=><a className="machine-card" href={`/data/${m.machine}/`} key={m.machine}><div className="machine-icon">▣</div><div><h3>{m.machine}</h3><p>{m.session_count} sessions · {m.latest_session?.status||"no data"}</p><small>{m.latest_session?.started_at_utc||"No sessions yet"}</small></div><span>VIEW DATA →</span></a>)}</div></main>;
}

function Machine({machine}) {
  const [sessions,setSessions]=useState([]),[session,setSession]=useState(null),[files,setFiles]=useState([]),[folder,setFolder]=useState([]),[file,setFile]=useState(null),[preview,setPreview]=useState(null),[manifest,setManifest]=useState(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[dark,setDark]=useState(localStorage.theme!=="light");
  useEffect(()=>{document.documentElement.classList.toggle("light",!dark);localStorage.theme=dark?"dark":"light"},[dark]);
  useEffect(()=>{setLoading(true);fetch(`${api}/machines/${machine}/sessions`).then(r=>r.json()).then(setSessions).finally(()=>setLoading(false))},[machine]);
  const open=async item=>{setSession(item);setFiles([]);setFolder([]);setFile(null);setPreview(null);setManifest(null);setBusy(true);try{setFiles(await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(item.session_id)}/artifacts`).then(r=>r.json()))}finally{setBusy(false)}};
  const loadCsv=async(selected,offset=0)=>{setBusy(true);try{setPreview(await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/csv-preview/${enc(selected.path)}?offset=${offset}`).then(r=>r.json()))}finally{setBusy(false)}};
  const choose=async selected=>{setFile(selected);setPreview(null);setManifest(null);if(selected.path==="session_manifest.json"){setBusy(true);try{setManifest(JSON.stringify(await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/manifest`).then(r=>r.json()),null,2))}finally{setBusy(false)}}else if(selected.path.endsWith(".csv"))loadCsv(selected)};
  const rename=async item=>{const display_name=window.prompt("Display name for this test:",item.display_name||item.name||item.session_id);if(!display_name)return;const result=await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(item.session_id)}/display-name`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({display_name})}).then(r=>r.json());setSessions(v=>v.map(x=>x.session_id===item.session_id?{...x,display_name:result.display_name}:x));setSession(v=>v?.session_id===item.session_id?{...v,display_name:result.display_name}:v)};
  const zip=id=>`${api}/machines/${machine}/sessions/${encodeURIComponent(id)}/download`, label=session?.display_name||session?.name||"Test", tiles=entriesFor(files,folder);
  return <main><header className="topbar"><a href="/data/"><ArrowLeft size={15}/> MACHINES</a><b>{machine.toUpperCase()}</b><IconButton icon={dark?Sun:Moon} onClick={()=>setDark(!dark)}>{dark?"Light":"Dark"}</IconButton></header>
    {!session&&<section className="card"><div className="file-heading"><div><div className="label">ACQUISITION SESSIONS</div><h2>Tests</h2></div></div>{loading?<Spinner/>:<table><thead><tr><th>Started</th><th>Name</th><th>Status</th><th>Runs</th><th/></tr></thead><tbody>{sessions.map(item=><tr key={item.session_id}><td>{fmtTime(item.started_at_utc)}</td><td><strong>{item.display_name||item.name||item.session_id}</strong><br/><small>{item.session_id}</small></td><td><span className={`pill ${item.status==="success"?"ok":"warn"}`}>{item.status}</span></td><td>{item.run_count}</td><td className="actions"><IconButton icon={FolderOpen} onClick={()=>open(item)}>Open</IconButton><IconButton icon={Pencil} onClick={()=>rename(item)}>Rename</IconButton><a className="button-link" href={zip(item.session_id)}><Download size={15}/>Download ZIP</a></td></tr>)}</tbody></table>}</section>}
    {session&&<section className="explorer"><aside><IconButton icon={ArrowLeft} onClick={()=>setSession(null)}>All tests</IconButton><div className="label session-label">{label}</div><div className="sidebar-crumbs"><button onClick={()=>setFolder([])}>root</button>{folder.map((name,i)=><React.Fragment key={i}><ChevronRight size={13}/><button onClick={()=>setFolder(folder.slice(0,i+1))}>{name}</button></React.Fragment>)}</div><nav className="sidebar-files">{folder.length>0&&<button className="side-item up" onClick={()=>setFolder(folder.slice(0,-1))}><ArrowLeft size={16}/>Up one folder</button>}{tiles.map(entry=>entry.kind==="folder"?<button className="side-item folder" key={entry.name} onClick={()=>setFolder([...folder,entry.name])}><Folder size={17}/>{entry.name}</button>:<button className={`side-item ${file?.path===entry.file.path?"selected":""}`} key={entry.file.path} onClick={()=>choose(entry.file)}>{entry.name==="session_manifest.json"?<FileJson size={17}/>:<FileText size={17}/>}<span>{entry.name}</span></button>)}</nav></aside><article>{busy?<Spinner label="Loading file"/>:!file?<div className="empty"><FolderOpen size={44}/><h2>Select a file</h2><p>Choose a file in the sidebar to inspect it.</p><a className="button-link" href={zip(session.session_id)}><Download size={15}/>Download ZIP</a></div>:manifest?<><div className="file-heading"><div><div className="label">SESSION MANIFEST</div><h2>session_manifest.json</h2></div><a className="button-link" href={`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/artifacts/${enc(file.path)}`}><Download size={15}/>Download file</a></div><pre className="manifest">{manifest}</pre></>:file.path.endsWith(".csv")?<CsvViewer preview={preview} page={offset=>loadCsv(file,offset)}/>:<div className="empty"><FileText size={42}/><h2>{file.name||file.path.split("/").at(-1)}</h2><a className="button-link" href={`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/artifacts/${enc(file.path)}`}><Download size={15}/>Download file</a></div>}</article></section>}
  </main>;
}

function App(){const machine=machineFromUrl();return machine?<Machine machine={machine}/>:<Overview/>}
createRoot(document.getElementById("root")).render(<App/>);
