
"use client"

import React, { useEffect, useMemo, useState } from "react"

type Project = { id: number; organisme?: string; project_name?: string; year?: number | string; domain_label?: string; status?: string }
type ApiAny = Record<string, any>

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

function cleanToken(value: any): string {
  if (!value) return ""
  let t = String(value).trim().replace(/^Bearer\s+/i, "")
  if (!t || t === "undefined" || t === "null") return ""
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) t = t.slice(1, -1).trim()
  return t
}

function token(): string {
  if (typeof window === "undefined") return ""
  const stores: Storage[] = []
  try { stores.push(localStorage) } catch {}
  try { stores.push(sessionStorage) } catch {}
  const keys = ["access_token", "accessToken", "token", "auth_token", "authToken", "jwt", "ennosmart_token", "ennosmart_access_token"]
  for (const s of stores) for (const k of keys) { const t = cleanToken(s.getItem(k)); if (t) return t }
  function scan(obj: any, depth = 0): string {
    if (!obj || depth > 4) return ""
    if (typeof obj === "string") { const t = cleanToken(obj); return t.startsWith("eyJ") ? t : "" }
    if (typeof obj !== "object") return ""
    const direct = obj.access_token || obj.accessToken || obj.token || obj.auth_token || obj.authToken || obj.jwt || obj?.state?.access_token || obj?.state?.accessToken || obj?.state?.token || obj?.data?.token || obj?.user?.token
    const d = cleanToken(direct); if (d) return d
    for (const v of Object.values(obj)) { const t = scan(v, depth + 1); if (t) return t }
    return ""
  }
  for (const s of stores) for (let i = 0; i < s.length; i++) {
    const k = s.key(i); if (!k) continue
    const raw = s.getItem(k); if (!raw) continue
    try { const t = scan(JSON.parse(raw)); if (t) return t } catch { const t = cleanToken(raw); if (t.startsWith("eyJ")) return t }
  }
  return ""
}

function projectsFrom(data: any): Project[] {
  if (Array.isArray(data)) return data
  if (Array.isArray(data?.projects)) return data.projects
  if (Array.isArray(data?.items)) return data.items
  if (Array.isArray(data?.results)) return data.results
  return []
}

function fileNameFromPath(path?: string | null): string {
  if (!path) return ""
  const p = String(path).replace(/\\/g, "/").split("/")
  return p[p.length - 1] || String(path)
}

function errText(data: any) {
  if (!data) return "Erreur API"
  if (typeof data === "string") return data
  if (typeof data?.detail === "string") return data.detail
  if (data?.detail) return JSON.stringify(data.detail, null, 2)
  return JSON.stringify(data, null, 2)
}

async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const h = new Headers(init.headers || {})
  const t = token()
  if (t) h.set("Authorization", `Bearer ${t}`)
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData
  if (init.body && !isForm && !h.has("Content-Type")) h.set("Content-Type", "application/json")
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers: h, credentials: "include" })
  const txt = await res.text()
  let data: any = null
  try { data = txt ? JSON.parse(txt) : null } catch { data = txt }
  if (!res.ok) throw new Error(`${errText(data)}\n\n${t ? "Token envoyé." : "Aucun token détecté côté frontend."}`)
  return data as T
}

function Badge({ children, tone = "gray" }: { children: React.ReactNode; tone?: "gray" | "green" | "red" | "amber" | "blue" | "purple" }) {
  const cls: Record<string, string> = {
    gray: "bg-slate-100 text-slate-700 border-slate-200",
    green: "bg-green-100 text-green-800 border-green-200",
    red: "bg-red-100 text-red-800 border-red-200",
    amber: "bg-amber-100 text-amber-800 border-amber-200",
    blue: "bg-blue-100 text-blue-800 border-blue-200",
    purple: "bg-purple-100 text-purple-800 border-purple-200",
  }
  return <span className={`inline-flex rounded-full border px-2 py-1 text-xs font-medium ${cls[tone]}`}>{children}</span>
}

function Button({ children, onClick, disabled, type = "button", kind = "dark" }: { children: React.ReactNode; onClick?: () => void; disabled?: boolean; type?: "button" | "submit"; kind?: "dark" | "light" | "blue" | "green" }) {
  const cls = kind === "green" ? "bg-green-700 text-white hover:bg-green-800" : kind === "blue" ? "bg-blue-700 text-white hover:bg-blue-800" : kind === "light" ? "bg-slate-100 text-slate-900 hover:bg-slate-200" : "bg-slate-900 text-white hover:bg-slate-800"
  return <button type={type} onClick={onClick} disabled={disabled} className={`${cls} rounded-xl px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50`}>{children}</button>
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="rounded-2xl border bg-white p-5 shadow-sm"><h2 className="mb-4 text-lg font-semibold">{title}</h2>{children}</section>
}

function LogsView({ logs }: { logs: any[] }) {
  if (!logs?.length) return <p className="text-sm text-slate-500">Aucun log pour le moment.</p>
  const tone = (s: string) => s === "ok" ? "green" : s === "error" ? "red" : s === "warning" ? "amber" : "gray"
  return <div className="space-y-2">{logs.map((l, i) => <div key={i} className="rounded-xl border bg-slate-50 p-3 text-sm"><div className="mb-1 flex flex-wrap gap-2"><Badge tone={tone(String(l.status || "")) as any}>{l.status || "info"}</Badge><Badge>{l.step || "step"}</Badge><span className="text-xs text-slate-500">{l.time}</span></div><div className="font-medium text-slate-800">{l.message}</div><details className="mt-2"><summary className="cursor-pointer text-xs text-slate-500">Détails</summary><pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-white">{JSON.stringify(l, null, 2)}</pre></details></div>)}</div>
}

export default function CirMemoryPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [status, setStatus] = useState<ApiAny | null>(null)
  const [index, setIndex] = useState<ApiAny | null>(null)
  const [search, setSearch] = useState<ApiAny | null>(null)
  const [last, setLast] = useState<any>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState("")
  const [error, setError] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [createFile, setCreateFile] = useState<File | null>(null)
  const [query, setQuery] = useState("")
  const [searchEngine, setSearchEngine] = useState<"chroma" | "lexical">("chroma")
  const [roles, setRoles] = useState(["etat_art", "verrou", "demarche", "resultat", "bibliography"])
  const [statuses, setStatuses] = useState(["validated", "working"])
  const [form, setForm] = useState({ organisme: "", project_name: "", year: String(new Date().getFullYear()), domain_label: "" })

  const selected = useMemo(() => projects.find((p) => Number(p.id) === Number(selectedId)) || null, [projects, selectedId])
  const grouped = useMemo(() => {
    const m = new Map<string, Project[]>()
    for (const p of projects) { const k = p.organisme || "Sans organisme"; m.set(k, [...(m.get(k) || []), p]) }
    return Array.from(m.entries()).map(([organisme, items]) => ({ organisme, items: items.sort((a,b) => String(a.project_name||"").localeCompare(String(b.project_name||"")) || Number(b.year||0)-Number(a.year||0)) }))
  }, [projects])

  const hasCirFinal = Boolean(status?.latest_cir_final)
  const validatedReady = Boolean(status?.files_found?.validated_knowledge && status?.files_found?.validated_style && status?.files_found?.validated_chunks)
  const metadata = status?.validated_metadata || {}
  const chromaReady = Boolean(status?.chroma?.available && Number(status?.chroma?.items_count || 0) > 0)

  function ok(m: string, d?: any) {
    setMsg(m); setError("")
    if (d !== undefined) {
      setLast(d)
      const arr = [...(Array.isArray(d?.logs) ? d.logs : []), ...(Array.isArray(d?.chroma_result?.logs) ? d.chroma_result.logs : []), ...(Array.isArray(d?.index_result?.logs) ? d.index_result.logs : [])]
      setLogs(arr)
    }
  }
  function fail(e: any) { setError(e instanceof Error ? e.message : String(e)); setMsg("") }

  async function loadProjects() {
    setLoading(true)
    try { const d = await api("/projects"); const list = projectsFrom(d); setProjects(list); if (!selectedId && list[0]) setSelectedId(Number(list[0].id)); ok(`Projets chargés : ${list.length}`, d) } catch (e) { fail(e) } finally { setLoading(false) }
  }
  async function loadStatus(id = selectedId) { if (!id) return; setLoading(true); try { const d = await api(`/projects/${id}/cir-memory/status`); setStatus(d); ok("Statut chargé", d) } catch(e){ fail(e) } finally{ setLoading(false) } }
  async function loadIndex(id = selectedId, rebuild = false) { if (!id) return; setLoading(true); try { const d = await api(`/projects/${id}/cir-memory/index?rebuild=${rebuild ? "true" : "false"}`); setIndex(d); ok(rebuild ? "Index reconstruit" : "Index chargé", d) } catch(e){ fail(e) } finally{ setLoading(false) } }

  async function quickCreateProject(): Promise<Project | null> {
    const payload = { organisme: form.organisme.trim(), project_name: form.project_name.trim(), year: Number(form.year), domain_label: form.domain_label.trim() || null }
    if (!payload.organisme || !payload.project_name || !payload.year) throw new Error("Remplis organisme, nom projet et année.")
    const d: any = await api("/cir-memory/projects/quick-create", { method: "POST", body: JSON.stringify(payload) })
    const p = d?.project || d
    await loadProjects()
    if (p?.id) { setSelectedId(Number(p.id)); return p as Project }
    return null
  }
  async function createOnly(e: React.FormEvent) { e.preventDefault(); setLoading(true); try { const p = await quickCreateProject(); ok("Projet créé/sélectionné.", p) } catch(e){ fail(e) } finally{ setLoading(false) } }
  async function createAndProcess() {
    setLoading(true)
    try {
      if (!createFile) throw new Error("Choisis un CIR final dans le formulaire de création.")
      const p = await quickCreateProject(); const id = Number(p?.id); if (!id) throw new Error("Projet créé mais ID introuvable.")
      const fd = new FormData(); fd.append("file", createFile)
      const d = await api(`/projects/${id}/cir-memory/upload-final?rebuild_index=true`, { method: "POST", body: fd })
      ok("Projet créé + CIR traité + Chroma alimenté.", d); setCreateFile(null); setSelectedId(id); await loadStatus(id); await loadIndex(id, false)
    } catch(e){ fail(e) } finally{ setLoading(false) }
  }
  async function action(path: string, label: string) { if(!selectedId) return; setLoading(true); try { const d=await api(path,{method:"POST"}); ok(label,d); await loadStatus(); await loadIndex() } catch(e){ fail(e) } finally{ setLoading(false) } }
  async function uploadAndProcess() { if(!selectedId || !file){ setError("Choisis un projet et un fichier CIR final."); return } setLoading(true); try{ const fd=new FormData(); fd.append("file", file); const d=await api(`/projects/${selectedId}/cir-memory/upload-final?rebuild_index=true`,{method:"POST", body:fd}); ok("CIR final traité : extraction + NLP + JSON + Chroma.", d); setFile(null); await loadStatus(); await loadIndex(selectedId,false) }catch(e){ fail(e) }finally{ setLoading(false) } }
  async function runSearch() { if(!selectedId || !query.trim()){ setError("Sélectionne un projet et écris une requête."); return } setLoading(true); try{ const endpoint=searchEngine==="chroma" ? `/projects/${selectedId}/cir-memory/rag-search` : `/projects/${selectedId}/cir-memory/search`; const d=await api(endpoint,{method:"POST", body:JSON.stringify({query, roles, memory_statuses: statuses, top_k: 10})}); setSearch(d); ok(`Recherche ${searchEngine} terminée : ${d?.matches_count || 0} résultat(s)`, d) }catch(e){ fail(e) }finally{ setLoading(false) } }
  function toggle(v:string, list:string[], setter:(x:string[])=>void){ setter(list.includes(v) ? list.filter(x=>x!==v) : [...list,v]) }

  useEffect(() => { loadProjects() /* eslint-disable-next-line */ }, [])
  useEffect(() => { if(selectedId){ loadStatus(selectedId); loadIndex(selectedId,false) } /* eslint-disable-next-line */ }, [selectedId])

  return <main className="min-h-screen bg-slate-50 p-6"><div className="mx-auto max-w-7xl space-y-6">
    <header className="rounded-2xl border bg-white p-6 shadow-sm"><div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between"><div><h1 className="text-2xl font-bold">Mémoire CIR RAG</h1><p className="text-sm text-slate-600">Créer projet, traiter CIR final, logs extraction/NLP, stockage Chroma et recherche RAG mémoire.</p><p className="mt-1 text-xs text-slate-500">API : {API_BASE}</p></div><div className="flex flex-wrap gap-2"><Badge tone={token() ? "green" : "red"}>{token() ? "Token OK" : "Token absent"}</Badge><Badge tone={chromaReady ? "purple" : "amber"}>Chroma : {status?.chroma?.available ? `${status?.chroma?.items_count || 0} items` : "indisponible"}</Badge><Button kind="light" onClick={loadProjects} disabled={loading}>Recharger</Button></div></div></header>
    {msg && <div className="rounded-xl border border-green-200 bg-green-50 p-4 text-sm text-green-800">{msg}</div>}
    {error && <div className="whitespace-pre-wrap rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}

    <div className="grid gap-6 lg:grid-cols-[1.35fr_0.85fr]"><Card title="Liste des organismes / projets / années"><div className="max-h-[620px] overflow-auto rounded-xl border"><table className="w-full text-sm"><thead className="bg-slate-100"><tr><th className="p-3 text-left">ID</th><th className="p-3 text-left">Organisme</th><th className="p-3 text-left">Projet</th><th className="p-3 text-left">Année</th><th className="p-3 text-left">Action</th></tr></thead><tbody>{grouped.length===0 ? <tr><td colSpan={5} className="p-6 text-center text-slate-500">Aucun projet chargé.</td></tr> : grouped.map(g=><React.Fragment key={g.organisme}><tr className="bg-slate-50"><td colSpan={5} className="p-3 font-semibold">{g.organisme}</td></tr>{g.items.map(p=><tr key={p.id} className={`border-t ${Number(p.id)===Number(selectedId)?"bg-blue-50":"bg-white"}`}><td className="p-3 font-mono text-xs">{p.id}</td><td className="p-3">{p.organisme}</td><td className="p-3 font-medium">{p.project_name}</td><td className="p-3">{p.year}</td><td className="p-3"><Button kind="light" onClick={()=>setSelectedId(Number(p.id))}>{Number(p.id)===Number(selectedId)?"Sélectionné":"Sélectionner"}</Button></td></tr>)}</React.Fragment>)}</tbody></table></div></Card>
    <div className="space-y-6"><Card title="Ajouter projet + traiter sur place"><form onSubmit={createOnly} className="space-y-3"><input className="w-full rounded-xl border px-3 py-2 text-sm" placeholder="Organisme" value={form.organisme} onChange={e=>setForm({...form, organisme:e.target.value})}/><input className="w-full rounded-xl border px-3 py-2 text-sm" placeholder="Nom projet" value={form.project_name} onChange={e=>setForm({...form, project_name:e.target.value})}/><input className="w-full rounded-xl border px-3 py-2 text-sm" placeholder="Année" value={form.year} onChange={e=>setForm({...form, year:e.target.value})}/><input className="w-full rounded-xl border px-3 py-2 text-sm" placeholder="Domaine optionnel" value={form.domain_label} onChange={e=>setForm({...form, domain_label:e.target.value})}/><input type="file" accept=".docx,.pdf,.txt,.md" className="w-full rounded-xl border bg-white px-3 py-2 text-sm" onChange={e=>setCreateFile(e.target.files?.[0] || null)}/>{createFile && <div className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">CIR à traiter : <b>📄 {createFile.name}</b></div>}<div className="flex flex-wrap gap-2"><Button type="submit" disabled={loading}>Créer seulement</Button><Button type="button" kind="green" onClick={createAndProcess} disabled={loading || !createFile}>Créer + traiter CIR + Chroma</Button></div></form></Card>
    <Card title="Projet sélectionné">{selected ? <div className="space-y-3 text-sm"><div className="rounded-xl bg-slate-50 p-3"><b>{selected.organisme}</b><br/>{selected.project_name}<br/>Année : {selected.year} · ID : {selected.id}<div className="mt-3 border-t pt-3"><div className="text-xs font-semibold text-slate-500">CIR final associé</div>{hasCirFinal ? <div className="mt-1 rounded-lg bg-white px-3 py-2 text-sm text-slate-800">📄 {status?.latest_cir_final_name || fileNameFromPath(status?.latest_cir_final)}</div> : <div className="mt-1 text-sm text-slate-500">Aucun CIR final importé pour ce projet.</div>}</div><div className="mt-3 flex flex-wrap gap-2"><Badge tone={hasCirFinal?"green":"red"}>CIR final {hasCirFinal?"détecté":"absent"}</Badge><Badge tone={validatedReady?"green":"amber"}>JSON {validatedReady?"OK":"à construire"}</Badge><Badge tone={metadata?.nlp_pipeline_used?"green":"amber"}>NLP {metadata?.nlp_pipeline_used?"CIR utilisé":"non traité/fallback"}</Badge><Badge tone={chromaReady?"purple":"amber"}>RAG Chroma {chromaReady?"OK":"vide"}</Badge></div></div><div className="grid grid-cols-2 gap-2"><Button kind="light" onClick={()=>loadStatus()} disabled={loading}>Status</Button><Button kind="light" onClick={()=>loadIndex(selected.id,true)} disabled={loading}>Rebuild index</Button><Button kind="green" onClick={()=>action(`/projects/${selected.id}/cir-memory/build-validated?rebuild_index=true`,"Build validated + Chroma terminé")} disabled={loading || !hasCirFinal}>Build validated + Chroma</Button><Button kind="light" onClick={()=>action(`/projects/${selected.id}/cir-memory/store-chroma?reset_project=true`,"Stockage Chroma terminé")} disabled={loading}>Store Chroma</Button><Button kind="light" onClick={()=>action(`/projects/${selected.id}/cir-memory/build-working/scholar?rebuild_index=true`,"Working Scholar + Chroma construit")} disabled={loading}>Build Scholar</Button><Button kind="light" onClick={()=>action(`/projects/${selected.id}/cir-memory/build-working/diagnostic?rebuild_index=true`,"Working Diagnostic + Chroma construit")} disabled={loading}>Build Diagnostic</Button><Button kind="blue" onClick={()=>action(`/projects/${selected.id}/cir-memory/build-all`,"Build all + Chroma terminé")} disabled={loading}>Build all</Button></div></div> : <p className="text-sm text-slate-500">Aucun projet sélectionné.</p>}</Card></div></div>

    <div className="grid gap-6 lg:grid-cols-2"><Card title="Importer CIR final validé"><p className="mb-3 text-sm text-slate-600">Traitement complet : extraction texte → NLP CIR → JSON mémoire → Chroma RAG.</p><input type="file" accept=".docx,.pdf,.txt,.md" className="mb-3 w-full rounded-xl border bg-white px-3 py-2 text-sm" onChange={e=>setFile(e.target.files?.[0] || null)}/>{file && <div className="mb-3 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">Fichier sélectionné :<br/><b>📄 {file.name}</b></div>}<Button kind="green" onClick={uploadAndProcess} disabled={loading || !file || !selected}>Upload + extraction + NLP + Chroma</Button></Card>
    <Card title="Statut mémoire / RAG"><div className="space-y-4 text-sm"><div className="rounded-xl border bg-slate-50 p-3"><h3 className="mb-2 font-semibold">État du CIR final</h3>{hasCirFinal ? <div className="space-y-2"><Badge tone="green">CIR final détecté</Badge><div>📄 {status?.latest_cir_final_name || fileNameFromPath(status?.latest_cir_final)}</div>{validatedReady ? <Badge tone="green">Mémoire validée construite</Badge> : <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900">Le CIR final existe mais il n’a pas encore été transformé en JSON + Chroma. Clique sur <b>Build validated + Chroma</b>.</div>}</div> : <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-900">Aucun CIR final détecté pour ce projet. Upload un fichier CIR final.</div>}</div><div><h3 className="mb-2 font-semibold">JSON mémoire générés</h3><div className="flex flex-wrap gap-2">{status?.files_found ? Object.entries(status.files_found).map(([k,v])=><Badge key={k} tone={v?"green":"amber"}>{k}: {v?"oui":"non"}</Badge>) : <span className="text-slate-500">Non chargé</span>}</div></div><div><h3 className="mb-2 font-semibold">Chroma RAG</h3><div className="rounded-xl bg-slate-50 p-3"><div>Disponible : <b>{status?.chroma?.available ? "oui" : "non"}</b></div><div>Items : <b>{String(status?.chroma?.items_count ?? "-")}</b></div><div>Collection : <b>{status?.chroma?.collection || "-"}</b></div><div className="break-all text-xs text-slate-500">{status?.chroma?.chroma_dir}</div>{status?.chroma?.error && <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-2 text-red-800">{status.chroma.error}</div>}</div></div><div><h3 className="mb-2 font-semibold">Compteurs organisme</h3><div className="grid grid-cols-2 gap-2">{index?.counts ? Object.entries(index.counts).map(([k,v])=><div key={k} className="rounded-xl bg-slate-50 p-3"><div className="text-xs text-slate-500">{k}</div><div className="text-xl font-bold">{String(v)}</div></div>) : <span className="text-slate-500">Non chargé</span>}</div></div></div></Card></div>

    <Card title="Logs traitement extraction / NLP / JSON / Chroma"><LogsView logs={logs}/></Card>

    <Card title="Recherche RAG dans la mémoire de l’organisme"><div className="mb-3 flex flex-wrap gap-2"><button onClick={()=>setSearchEngine("chroma")} className={`rounded-full border px-3 py-1 text-sm ${searchEngine==="chroma"?"bg-purple-900 text-white":"bg-white"}`}>Chroma RAG</button><button onClick={()=>setSearchEngine("lexical")} className={`rounded-full border px-3 py-1 text-sm ${searchEngine==="lexical"?"bg-slate-900 text-white":"bg-white"}`}>Lexical fallback</button></div><div className="flex flex-col gap-3 md:flex-row"><input className="flex-1 rounded-xl border px-3 py-2 text-sm" placeholder="tenue au feu REI 60 paroi biosourcée" value={query} onChange={e=>setQuery(e.target.value)}/><Button kind={searchEngine==="chroma"?"blue":"dark"} onClick={runSearch} disabled={loading || !selected}>Rechercher</Button></div><div className="mt-4 flex flex-wrap gap-2">{["etat_art","verrou","demarche","resultat","conclusion","bibliography"].map(r=><button key={r} onClick={()=>toggle(r,roles,setRoles)} className={`rounded-full border px-3 py-1 text-sm ${roles.includes(r)?"bg-slate-900 text-white":"bg-white"}`}>{r}</button>)}</div><div className="mt-3 flex flex-wrap gap-2">{["validated","working"].map(s=><button key={s} onClick={()=>toggle(s,statuses,setStatuses)} className={`rounded-full border px-3 py-1 text-sm ${statuses.includes(s)?"bg-blue-900 text-white":"bg-white"}`}>{s}</button>)}</div><div className="mt-5 space-y-3">{(search?.matches || []).map((m:any,i:number)=><article key={m.chunk_id || i} className="rounded-xl border bg-slate-50 p-4"><div className="mb-2 flex flex-wrap gap-2"><Badge tone={m.memory_status==="validated"?"green":"amber"}>{m.memory_status}</Badge><Badge>{m.role}</Badge><Badge tone="blue">score {m.score}</Badge><Badge tone="purple">{search?.engine || searchEngine}</Badge><span className="text-xs text-slate-500">{m.project} · {m.year}</span></div><h3 className="mb-2 font-semibold">{m.section_title}</h3><p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{m.text}</p></article>)}</div></Card>
    <details className="rounded-2xl border bg-white p-5 shadow-sm"><summary className="cursor-pointer font-semibold">Dernière réponse brute API</summary><pre className="mt-4 max-h-[420px] overflow-auto rounded-xl bg-slate-950 p-4 text-xs text-white">{JSON.stringify(last,null,2)}</pre></details>
  </div></main>
}
