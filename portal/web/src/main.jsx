import React, {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";
import {ArrowLeft, ChevronDown, ChevronRight, Download, FileJson, FileText, Folder, FolderOpen, Moon, Pencil, Sun} from "lucide-react";
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

function fileTree(files) {
  const root = {folders:new Map(), files:[]};
  files.forEach(file => {
    const parts = file.path.split("/"); let node = root;
    parts.slice(0,-1).forEach(name => { if (!node.folders.has(name)) node.folders.set(name,{folders:new Map(),files:[]}); node=node.folders.get(name); });
    node.files.push(file);
  });
  return root;
}

function FolderBranch({node, path, depth, expanded, setExpanded, activeFolder, selectFolder, selectedFile, choose}) {
  const open = expanded.has(path.join("/"));
  const toggle = () => setExpanded(previous => { const next=new Set(previous); const key=path.join("/"); next.has(key)?next.delete(key):next.add(key); return next; });
  const folders=[...node.folders.entries()].sort(([a],[b])=>a.localeCompare(b));
  const files=[...node.files].sort((a,b)=>a.path.localeCompare(b.path));
  return <>{folders.map(([name,child]) => { const childPath=[...path,name]; return <div className="tree-branch" key={childPath.join("/")}><div className="tree-row" style={{paddingLeft:depth*14}}><button className="tree-toggle" onClick={toggle} aria-label={`${open?"Collapse":"Expand"} ${name}`}><ChevronRight className={open?"expanded":""} size={15}/></button><button className={`tree-folder ${activeFolder.join("/")===childPath.join("/")?"active":""}`} onClick={()=>selectFolder(childPath)} onDoubleClick={toggle} title="Double-click to expand or collapse">{open?<FolderOpen size={16}/>:<Folder size={16}/>}<span>{name}</span></button></div>{open&&<FolderBranch node={child} path={childPath} depth={depth+1} expanded={expanded} setExpanded={setExpanded} activeFolder={activeFolder} selectFolder={selectFolder} selectedFile={selectedFile} choose={choose}/>}</div>})}{files.map(item=><button className={`tree-file ${selectedFile?.path===item.path?"selected":""}`} style={{paddingLeft:depth*14+23}} key={item.path} onClick={()=>choose(item)}>{item.path.endsWith(".json")?<FileJson size={16}/>:<FileText size={16}/>}<span>{item.path.split("/").at(-1)}</span></button>)}</>;
}

function MainBreadcrumbs({folder, file, selectFolder}) {
  const parts=file ? file.path.split("/") : folder;
  const folders=file ? parts.slice(0,-1) : parts;
  return <nav className="main-breadcrumbs"><button onClick={()=>selectFolder([])}>Root</button>{folders.map((name,index)=><React.Fragment key={`${name}-${index}`}><ChevronRight size={14}/><button onClick={()=>selectFolder(folders.slice(0,index+1))}>{name}</button></React.Fragment>)}{file&&<><ChevronRight size={14}/><span>{parts.at(-1)}</span></>}</nav>;
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
  const [sessions,setSessions]=useState([]),[session,setSession]=useState(null),[files,setFiles]=useState([]),[folder,setFolder]=useState([]),[expanded,setExpanded]=useState(new Set()),[file,setFile]=useState(null),[preview,setPreview]=useState(null),[manifest,setManifest]=useState(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[dark,setDark]=useState(localStorage.theme!=="light");
  useEffect(()=>{document.documentElement.classList.toggle("light",!dark);localStorage.theme=dark?"dark":"light"},[dark]);
  useEffect(()=>{setLoading(true);fetch(`${api}/machines/${machine}/sessions`).then(r=>r.json()).then(setSessions).finally(()=>setLoading(false))},[machine]);
  const open=async item=>{setSession(item);setFiles([]);setFolder([]);setExpanded(new Set());setFile(null);setPreview(null);setManifest(null);setBusy(true);try{setFiles(await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(item.session_id)}/artifacts`).then(r=>r.json()))}finally{setBusy(false)}};
  const loadCsv=async(selected,offset=0)=>{setBusy(true);try{setPreview(await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/csv-preview/${enc(selected.path)}?offset=${offset}`).then(r=>r.json()))}finally{setBusy(false)}};
  const selectFolder=path=>{setFolder(path);setFile(null);setPreview(null);setManifest(null)};
  const choose=async selected=>{setFile(selected);setFolder(selected.path.split("/").slice(0,-1));setPreview(null);setManifest(null);if(selected.path==="session_manifest.json"){setBusy(true);try{setManifest(JSON.stringify(await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/manifest`).then(r=>r.json()),null,2))}finally{setBusy(false)}}else if(selected.path.endsWith(".csv"))loadCsv(selected)};
  const rename=async item=>{const display_name=window.prompt("Display name for this test:",item.display_name||item.name||item.session_id);if(!display_name)return;const result=await fetch(`${api}/machines/${machine}/sessions/${encodeURIComponent(item.session_id)}/display-name`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({display_name})}).then(r=>r.json());setSessions(v=>v.map(x=>x.session_id===item.session_id?{...x,display_name:result.display_name}:x));setSession(v=>v?.session_id===item.session_id?{...v,display_name:result.display_name}:v)};
  const zip=id=>`${api}/machines/${machine}/sessions/${encodeURIComponent(id)}/download`, label=session?.display_name||session?.name||"Test";
  return <main><header className="topbar"><a href="/data/"><ArrowLeft size={15}/> MACHINES</a><b>{machine.toUpperCase()}</b><IconButton icon={dark?Sun:Moon} onClick={()=>setDark(!dark)}>{dark?"Light":"Dark"}</IconButton></header>
    {!session&&<section className="card"><div className="file-heading"><div><div className="label">ACQUISITION SESSIONS</div><h2>Tests</h2></div></div>{loading?<Spinner/>:<table><thead><tr><th>Started</th><th>Name</th><th>Status</th><th>Runs</th><th/></tr></thead><tbody>{sessions.map(item=><tr key={item.session_id}><td>{fmtTime(item.started_at_utc)}</td><td><strong>{item.display_name||item.name||item.session_id}</strong><br/><small>{item.session_id}</small></td><td><span className={`pill ${item.status==="success"?"ok":"warn"}`}>{item.status}</span></td><td>{item.run_count}</td><td className="actions"><IconButton icon={FolderOpen} onClick={()=>open(item)}>Open</IconButton><IconButton icon={Pencil} onClick={()=>rename(item)}>Rename</IconButton><a className="button-link" href={zip(item.session_id)}><Download size={15}/>Download ZIP</a></td></tr>)}</tbody></table>}</section>}
    {session&&<section className="explorer"><aside><IconButton icon={ArrowLeft} onClick={()=>setSession(null)}>All tests</IconButton><div className="label session-label">{label}</div><nav className="sidebar-files"><FolderBranch node={fileTree(files)} path={[]} depth={0} expanded={expanded} setExpanded={setExpanded} activeFolder={folder} selectFolder={selectFolder} selectedFile={file} choose={choose}/></nav></aside><article><MainBreadcrumbs folder={folder} file={file} selectFolder={selectFolder}/>{busy?<Spinner label="Loading file"/>:!file?<div className="empty"><FolderOpen size={44}/><h2>{folder.length?folder.at(-1):"Select a file"}</h2><p>{folder.length?"Choose a file from this folder in the sidebar.":"Choose a file in the sidebar to inspect it."}</p><a className="button-link" href={zip(session.session_id)}><Download size={15}/>Download ZIP</a></div>:manifest?<><div className="file-heading"><div><div className="label">SESSION MANIFEST</div><h2>session_manifest.json</h2></div><a className="button-link" href={`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/artifacts/${enc(file.path)}`}><Download size={15}/>Download file</a></div><pre className="manifest">{manifest}</pre></>:file.path.endsWith(".csv")?<CsvViewer preview={preview} page={offset=>loadCsv(file,offset)}/>:<div className="empty"><FileText size={42}/><h2>{file.name||file.path.split("/").at(-1)}</h2><a className="button-link" href={`${api}/machines/${machine}/sessions/${encodeURIComponent(session.session_id)}/artifacts/${enc(file.path)}`}><Download size={15}/>Download file</a></div>}</article></section>}
  </main>;
}

function App(){const machine=machineFromUrl();return machine?<Machine machine={machine}/>:<Overview/>}
createRoot(document.getElementById("root")).render(<App/>);
