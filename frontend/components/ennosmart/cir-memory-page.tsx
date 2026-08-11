"use client"

import React, { useEffect, useMemo, useState } from "react"
import {
  BrainCircuit,
  Building2,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  FileCheck2,
  FolderPlus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
  Cloud,
} from "lucide-react"
import PowerAutomateImportPanel from "@/components/ennosmart/sharepoint-audit-panel"

type MemorySource = {
  file_name: string
  file_path: string
  size_mb: number
  modified_at?: string
}

type MemoryProject = {
  id: string
  organisme: string
  project: string
  year: string
  indexed: boolean
  status: "indexed" | "pending"
  source_files: MemorySource[]
  source_count: number
  indexed_file_name?: string
  chunks_count: number
  cards_count: number
  role_counts: Record<string, number>
  memory_counts: Record<string, number>
  domain_counts: Record<string, number>
  indexed_at?: string
}

type Catalog = {
  ok: boolean
  version: string
  updated_at?: string
  organisms: string[]
  projects: MemoryProject[]
  stats: {
    organisms_count: number
    projects_count: number
    indexed_projects_count: number
    pending_projects_count: number
    chunks_count: number
    cards_count: number
    relations_count: number
    vector_items_count: number
  }
  vector_db: {
    exists: boolean
    collection: string
    runtime_ready: boolean
    runtime_dependencies: Record<string, boolean>
    collections: Array<{ name: string; items_count: number }>
  }
  ai_connections: {
    ennodiagnostic: boolean
    cir_comparison: boolean
    writing_style: boolean
    usage_rule: string
  }
  paths: Record<string, string>
}

type SearchMatch = {
  id?: string
  text?: string
  distance?: number
  metadata?: Record<string, any>
}

type Tab = "library" | "add" | "search" | "power-automate"

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

function cleanToken(value: unknown): string {
  if (!value) return ""
  let candidate = String(value).trim().replace(/^Bearer\s+/i, "")
  if (!candidate || candidate === "undefined" || candidate === "null") return ""
  if ((candidate.startsWith('"') && candidate.endsWith('"')) || (candidate.startsWith("'") && candidate.endsWith("'"))) {
    candidate = candidate.slice(1, -1).trim()
  }
  return candidate
}

function authToken(): string {
  if (typeof window === "undefined") return ""
  const stores: Storage[] = []
  try { stores.push(localStorage) } catch {}
  try { stores.push(sessionStorage) } catch {}
  const keys = ["access_token", "accessToken", "token", "auth_token", "authToken", "jwt", "ennosmart_token", "ennosmart_access_token"]
  for (const store of stores) {
    for (const key of keys) {
      const value = cleanToken(store.getItem(key))
      if (value) return value
    }
  }
  return ""
}

function errorText(value: any): string {
  if (typeof value === "string") return value
  if (typeof value?.detail === "string") return value.detail
  if (value?.detail) return JSON.stringify(value.detail)
  return "Une erreur inattendue est survenue."
}

async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {})
  const token = authToken()
  if (token) headers.set("Authorization", `Bearer ${token}`)
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData
  if (init.body && !isForm && !headers.has("Content-Type")) headers.set("Content-Type", "application/json")
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" })
  const raw = await response.text()
  let payload: any = null
  try { payload = raw ? JSON.parse(raw) : null } catch { payload = raw }
  if (!response.ok) throw new Error(errorText(payload))
  return payload as T
}

function formatNumber(value: number | undefined): string {
  return new Intl.NumberFormat("fr-FR").format(Number(value || 0))
}

function formatDate(value?: string): string {
  if (!value) return "—"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" }).format(date)
}

function labelForRole(role: string): string {
  return ({
    objectif: "Objectifs",
    etat_art: "État de l’art",
    limite: "Limites",
    verrou: "Verrous",
    methode: "Méthodes",
    resultat: "Résultats",
    contribution: "Contributions",
    style: "Style",
  } as Record<string, string>)[role] || role.replaceAll("_", " ")
}

function Pill({ children, tone = "slate" }: { children: React.ReactNode; tone?: "slate" | "green" | "amber" | "violet" }) {
  const tones = {
    slate: "border-slate-200 bg-slate-50 text-slate-600",
    green: "border-emerald-200 bg-emerald-50 text-emerald-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    violet: "border-violet-200 bg-violet-50 text-violet-700",
  }
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${tones[tone]}`}>{children}</span>
}

function PrimaryButton({ children, onClick, disabled, type = "button" }: { children: React.ReactNode; onClick?: () => void; disabled?: boolean; type?: "button" | "submit" }) {
  return (
    <button type={type} onClick={onClick} disabled={disabled} className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-800 disabled:cursor-not-allowed disabled:opacity-50">
      {children}
    </button>
  )
}

function SecondaryButton({ children, onClick, disabled }: { children: React.ReactNode; onClick?: () => void; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-violet-200 hover:bg-violet-50 hover:text-violet-800 disabled:cursor-not-allowed disabled:opacity-50">
      {children}
    </button>
  )
}

function StatCard({ icon, label, value, hint }: { icon: React.ReactNode; label: string; value: string; hint: string }) {
  return (
    <div className="rounded-2xl border border-violet-100 bg-white p-4 shadow-[0_12px_32px_rgba(50,20,90,.05)]">
      <div className="flex items-start justify-between gap-3">
        <div><p className="text-xs font-semibold uppercase tracking-[.12em] text-slate-400">{label}</p><p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">{value}</p></div>
        <div className="rounded-xl bg-violet-50 p-2.5 text-violet-700">{icon}</div>
      </div>
      <p className="mt-2 text-xs text-slate-500">{hint}</p>
    </div>
  )
}

export default function CirMemoryPage() {
  const [tab, setTab] = useState<Tab>("library")
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [selectedId, setSelectedId] = useState("")
  const [filter, setFilter] = useState("")
  const [organisationFilter, setOrganisationFilter] = useState("all")
  const [yearFilter, setYearFilter] = useState("all")
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState("")
  const [error, setError] = useState("")
  const [form, setForm] = useState({ organisme: "Scalian", project: "", year: String(new Date().getFullYear()) })
  const [file, setFile] = useState<File | null>(null)
  const [query, setQuery] = useState("")
  const [searchRole, setSearchRole] = useState("")
  const [searchOrganisation, setSearchOrganisation] = useState("")
  const [matches, setMatches] = useState<SearchMatch[]>([])
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteConfirmation, setDeleteConfirmation] = useState("")

  const selected = useMemo(() => catalog?.projects.find((item) => item.id === selectedId) || null, [catalog, selectedId])
  const years = useMemo(() => Array.from(new Set((catalog?.projects || []).map((item) => item.year))).sort().reverse(), [catalog])
  const visibleProjects = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase("fr")
    return (catalog?.projects || []).filter((item) => {
      if (organisationFilter !== "all" && item.organisme !== organisationFilter) return false
      if (yearFilter !== "all" && item.year !== yearFilter) return false
      return !needle || `${item.organisme} ${item.project} ${item.year} ${item.indexed_file_name || ""}`.toLocaleLowerCase("fr").includes(needle)
    })
  }, [catalog, filter, organisationFilter, yearFilter])

  function success(message: string) { setNotice(message); setError("") }
  function failure(reason: unknown) { setError(reason instanceof Error ? reason.message : String(reason)); setNotice("") }

  async function loadCatalog(quiet = false) {
    if (!quiet) setLoading(true)
    try {
      const result = await api<Catalog>("/cir-memory/v2/catalog")
      setCatalog(result)
      if (!selectedId && result.projects[0]) setSelectedId(result.projects[0].id)
      if (!quiet) success(`${result.stats.indexed_projects_count} CIR finaux connectés à Memory V2.`)
    } catch (reason) {
      failure(reason)
    } finally {
      if (!quiet) setLoading(false)
    }
  }

  async function createSlot() {
    if (!form.organisme.trim() || !form.project.trim() || !form.year.trim()) return failure("Renseigne l’entreprise, le projet et l’année.")
    setLoading(true)
    try {
      await api("/cir-memory/v2/library", { method: "POST", body: JSON.stringify(form) })
      success("L’emplacement du projet a été créé. Tu peux maintenant ajouter son CIR final.")
      await loadCatalog(true)
    } catch (reason) { failure(reason) } finally { setLoading(false) }
  }

  async function uploadAndIndex(event: React.FormEvent) {
    event.preventDefault()
    if (!file) return failure("Choisis le CIR final validé à importer.")
    if (!form.organisme.trim() || !form.project.trim() || !form.year.trim()) return failure("Renseigne l’entreprise, le projet et l’année.")
    setLoading(true)
    setNotice("Extraction, analyse NLP et reconstruction de la base vectorielle en cours…")
    setError("")
    try {
      const data = new FormData()
      data.append("file", file)
      data.append("organisme", form.organisme.trim())
      data.append("project", form.project.trim())
      data.append("year", form.year.trim())
      data.append("vision_mode", "text_only")
      data.append("formula_mode", "off")
      const result = await api<any>("/cir-memory/v2/upload", { method: "POST", body: data })
      success(`CIR indexé : ${formatNumber(result?.chunks_count)} passages et ${formatNumber(result?.cards_count)} cartes ajoutés.`)
      setFile(null)
      await loadCatalog(true)
      setTab("library")
    } catch (reason) { failure(reason) } finally { setLoading(false) }
  }

  async function processExisting(project: MemoryProject) {
    setLoading(true)
    setNotice("Traitement du CIR existant et reconstruction de Memory V2 en cours…")
    setError("")
    try {
      await api("/cir-memory/v2/process-existing", {
        method: "POST",
        body: JSON.stringify({ organisme: project.organisme, project: project.project, year: project.year, file_name: project.source_files[0]?.file_name || "" }),
      })
      success("Le CIR existant est maintenant indexé dans la vraie base vectorielle.")
      await loadCatalog(true)
    } catch (reason) { failure(reason) } finally { setLoading(false) }
  }

  async function rebuild() {
    setLoading(true)
    setNotice("Reconstruction du catalogue, du graphe et des collections Chroma…")
    setError("")
    try {
      await api("/cir-memory/v2/rebuild", { method: "POST" })
      success("Memory V2 a été reconstruite depuis les fichiers déjà validés.")
      await loadCatalog(true)
    } catch (reason) { failure(reason) } finally { setLoading(false) }
  }

  async function removeProject(project: MemoryProject) {
    if (deleteConfirmation.trim() !== project.project) {
      return failure(`Écris exactement « ${project.project} » pour confirmer.`)
    }
    setLoading(true)
    setNotice("Suppression de la mémoire active et reconstruction de Chroma en cours…")
    setError("")
    try {
      const result = await api<any>("/cir-memory/v2/projects/remove", {
        method: "POST",
        body: JSON.stringify({
          organisme: project.organisme,
          project: project.project,
          year: project.year,
          confirmation: "SUPPRIMER_DE_MEMORY_V2",
        }),
      })
      const nextCatalog = result?.catalog as Catalog | undefined
      if (nextCatalog) {
        setCatalog(nextCatalog)
        setSelectedId(nextCatalog.projects[0]?.id || "")
      } else {
        setSelectedId("")
        await loadCatalog(true)
      }
      setDeleteOpen(false)
      setDeleteConfirmation("")
      success(`Le projet ${project.project} (${project.year}) a été retiré de toute la mémoire locale. L’archive de récupération a été conservée.`)
    } catch (reason) { failure(reason) } finally { setLoading(false) }
  }

  async function runSearch(event: React.FormEvent) {
    event.preventDefault()
    if (!query.trim()) return failure("Écris une question pour interroger la mémoire.")
    setLoading(true)
    try {
      const result = await api<any>("/cir-memory/v2/search", {
        method: "POST",
        body: JSON.stringify({ query: query.trim(), organisme: searchOrganisation, role: searchRole, top_k: 10 }),
      })
      setMatches(Array.isArray(result?.matches) ? result.matches : [])
      success(`${result?.matches_count || 0} résultat(s) trouvé(s) dans Memory V2.`)
    } catch (reason) { failure(reason) } finally { setLoading(false) }
  }

  useEffect(() => { loadCatalog() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const stats = catalog?.stats
  const agentsConnected = catalog ? Object.values(catalog.ai_connections).filter((value) => value === true).length : 0

  return (
    <div className="min-h-full bg-[radial-gradient(circle_at_top_left,rgba(139,92,246,.08),transparent_28%)] p-4 sm:p-7 lg:p-9">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="overflow-hidden rounded-[28px] bg-[radial-gradient(circle_at_80%_5%,rgba(216,180,254,.28),transparent_25%),linear-gradient(125deg,#240747,#51209a_56%,#7c3aed)] px-6 py-7 text-white shadow-xl shadow-violet-950/15 sm:px-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="mb-4 flex items-center gap-3">
                <img src="/ennoma-logo.png" alt="Ennoma" className="size-11 rounded-[13px] shadow-lg" />
                <span className="rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs font-semibold text-violet-100">Memory V2 · CIR validés</span>
              </div>
              <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Mémoire CIR</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-violet-100 sm:text-base">La base d’expérience commune du cabinet : style de rédaction, comparaison historique et projets similaires pour les agents Ennoma.</p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-2"><ShieldCheck className="size-4" /> CIR finaux validés uniquement</span>
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/10 px-3 py-2 text-emerald-100"><span className="size-2 rounded-full bg-emerald-300" /> {catalog?.vector_db.exists ? "Chroma connecté" : "Chroma absent"}</span>
            </div>
          </div>
        </header>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard icon={<Building2 className="size-5" />} label="Entreprises" value={formatNumber(stats?.organisms_count)} hint="Bases d’expérience partagées" />
          <StatCard icon={<FileCheck2 className="size-5" />} label="CIR indexés" value={formatNumber(stats?.indexed_projects_count)} hint="Projets et années validés" />
          <StatCard icon={<Database className="size-5" />} label="Vecteurs" value={formatNumber(stats?.vector_items_count)} hint="Collection globale Memory V2" />
          <StatCard icon={<BrainCircuit className="size-5" />} label="Agents reliés" value={`${agentsConnected}/3`} hint="Diagnostic, comparaison, style" />
        </div>

        {notice && <div className="flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"><CheckCircle2 className="mt-0.5 size-4 shrink-0" /><span>{notice}</span></div>}
        {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">{error}</div>}

        <nav className="flex w-full gap-1 rounded-2xl border border-violet-100 bg-white p-1.5 shadow-sm sm:w-fit">
          {([
            ["library", "Bibliothèque", Database],
            ["add", "Ajouter un CIR", UploadCloud],
            ["power-automate", "Collecte automatique", Cloud],
            ["search", "Recherche", Search],
          ] as const).map(([value, label, Icon]) => (
            <button key={value} onClick={() => setTab(value)} className={`flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition sm:flex-none ${tab === value ? "bg-violet-700 text-white shadow-sm" : "text-slate-600 hover:bg-violet-50 hover:text-violet-800"}`}>
              <Icon className="size-4" />{label}
            </button>
          ))}
        </nav>

        {tab === "library" && (
          <div className="grid gap-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,.55fr)]">
            <section className="rounded-3xl border border-violet-100 bg-white p-5 shadow-[0_12px_36px_rgba(50,20,90,.06)] sm:p-6">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                <div><h2 className="text-xl font-semibold tracking-tight text-slate-900">CIR de la mémoire</h2><p className="mt-1 text-sm text-slate-500">{visibleProjects.length} projet(s) affiché(s) sur {catalog?.projects.length || 0}</p></div>
                <SecondaryButton onClick={() => loadCatalog()} disabled={loading}><RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />Actualiser</SecondaryButton>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px_130px]">
                <label className="relative"><Search className="absolute left-3 top-3 size-4 text-slate-400" /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Rechercher un projet ou un CIR…" className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2.5 pl-9 pr-3 text-sm outline-none transition focus:border-violet-400 focus:bg-white focus:ring-4 focus:ring-violet-100" /></label>
                <select value={organisationFilter} onChange={(event) => setOrganisationFilter(event.target.value)} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-violet-400"><option value="all">Toutes les entreprises</option>{catalog?.organisms.map((item) => <option key={item}>{item}</option>)}</select>
                <select value={yearFilter} onChange={(event) => setYearFilter(event.target.value)} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-violet-400"><option value="all">Toutes années</option>{years.map((item) => <option key={item}>{item}</option>)}</select>
              </div>
              <div className="mt-5 max-h-[650px] space-y-2 overflow-auto pr-1">
                {visibleProjects.map((item) => (
                  <button key={item.id} onClick={() => { setSelectedId(item.id); setDeleteOpen(false); setDeleteConfirmation("") }} className={`group flex w-full items-center gap-4 rounded-2xl border p-4 text-left transition ${selectedId === item.id ? "border-violet-300 bg-violet-50 shadow-sm" : "border-slate-100 bg-white hover:border-violet-200 hover:bg-slate-50"}`}>
                    <div className={`grid size-11 shrink-0 place-items-center rounded-xl ${item.indexed ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{item.indexed ? <FileCheck2 className="size-5" /> : <Clock3 className="size-5" />}</div>
                    <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="truncate font-semibold text-slate-900">{item.project}</h3><Pill tone="violet">{item.year}</Pill>{item.indexed ? <Pill tone="green">Indexé</Pill> : <Pill tone="amber">À traiter</Pill>}</div><p className="mt-1 truncate text-xs text-slate-500">{item.organisme} · {item.indexed_file_name || item.source_files[0]?.file_name || "Emplacement créé, CIR à ajouter"}</p></div>
                    <div className="hidden text-right sm:block"><p className="text-sm font-bold text-slate-800">{formatNumber(item.cards_count)}</p><p className="text-[11px] text-slate-400">cartes</p></div>
                    <ChevronRight className="size-4 shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-violet-600" />
                  </button>
                ))}
                {!visibleProjects.length && <div className="rounded-2xl border border-dashed border-slate-200 py-12 text-center text-sm text-slate-500">Aucun CIR ne correspond aux filtres.</div>}
              </div>
            </section>

            <aside className="h-fit rounded-3xl border border-violet-100 bg-white p-5 shadow-[0_12px_36px_rgba(50,20,90,.06)] sm:p-6 lg:sticky lg:top-6">
              {selected ? (
                <div>
                  <div className="flex items-start justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-[.14em] text-violet-600">{selected.organisme}</p><h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">{selected.project}</h2><p className="mt-1 text-sm text-slate-500">Exercice {selected.year}</p></div>{selected.indexed ? <CheckCircle2 className="size-6 text-emerald-600" /> : <Clock3 className="size-6 text-amber-600" />}</div>
                  <div className="mt-5 rounded-2xl bg-slate-50 p-4"><p className="text-xs font-semibold uppercase tracking-wide text-slate-400">CIR final</p><p className="mt-2 break-words text-sm font-medium text-slate-800">{selected.indexed_file_name || selected.source_files[0]?.file_name || "Aucun fichier ajouté"}</p>{selected.source_files[0] && <p className="mt-1 text-xs text-slate-500">{selected.source_files[0].size_mb} Mo</p>}</div>
                  {selected.indexed && <div className="mt-5 grid grid-cols-2 gap-3"><div className="rounded-2xl border border-slate-100 p-3"><p className="text-xl font-bold text-slate-900">{formatNumber(selected.chunks_count)}</p><p className="text-xs text-slate-500">passages</p></div><div className="rounded-2xl border border-slate-100 p-3"><p className="text-xl font-bold text-slate-900">{formatNumber(selected.cards_count)}</p><p className="text-xs text-slate-500">cartes</p></div></div>}
                  <div className="mt-5"><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Contenu mémorisé</p><div className="flex flex-wrap gap-2">{Object.entries(selected.role_counts || {}).sort((a, b) => b[1] - a[1]).map(([role, count]) => <Pill key={role}>{labelForRole(role)} · {count}</Pill>)}</div></div>
                  {!selected.indexed && selected.source_files.length > 0 && <PrimaryButton onClick={() => processExisting(selected)} disabled={loading}><Sparkles className="size-4" />Indexer ce CIR existant</PrimaryButton>}
                  {!selected.indexed && selected.source_files.length === 0 && <div className="mt-5"><PrimaryButton onClick={() => { setForm({ organisme: selected.organisme, project: selected.project, year: selected.year }); setTab("add") }}><UploadCloud className="size-4" />Ajouter le CIR final</PrimaryButton></div>}
                  <p className="mt-5 text-xs leading-5 text-slate-400">Dernière indexation : {formatDate(selected.indexed_at)}</p>
                  <div className="mt-6 border-t border-slate-100 pt-5">
                    {!deleteOpen ? (
                      <button type="button" onClick={() => { setDeleteOpen(true); setDeleteConfirmation("") }} className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-rose-200 bg-white px-4 py-2.5 text-sm font-semibold text-rose-700 transition hover:bg-rose-50">
                        <Trash2 className="size-4" />Supprimer ce projet de Memory V2
                      </button>
                    ) : (
                      <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
                        <p className="text-sm font-semibold text-rose-900">Suppression locale complète</p>
                        <p className="mt-1 text-xs leading-5 text-rose-700">Le CIR, ses passages, cartes, relations et vecteurs Chroma seront retirés. SharePoint et le dossier Power Automate ne seront jamais modifiés. Une archive locale récupérable sera conservée.</p>
                        <label className="mt-3 block"><span className="text-xs font-medium text-rose-800">Écris « {selected.project} » pour confirmer</span><input value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} className="mt-1.5 w-full rounded-xl border border-rose-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-rose-400 focus:ring-4 focus:ring-rose-100" /></label>
                        <div className="mt-3 grid grid-cols-2 gap-2"><button type="button" onClick={() => { setDeleteOpen(false); setDeleteConfirmation("") }} disabled={loading} className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50">Annuler</button><button type="button" onClick={() => removeProject(selected)} disabled={loading || deleteConfirmation.trim() !== selected.project} className="inline-flex items-center justify-center gap-2 rounded-xl bg-rose-700 px-3 py-2 text-sm font-semibold text-white hover:bg-rose-800 disabled:cursor-not-allowed disabled:opacity-50">{loading ? <RefreshCw className="size-4 animate-spin" /> : <Trash2 className="size-4" />}Supprimer</button></div>
                      </div>
                    )}
                  </div>
                </div>
              ) : <p className="text-sm text-slate-500">Sélectionne un projet.</p>}
            </aside>
          </div>
        )}

        {tab === "add" && (
          <section className="mx-auto max-w-3xl rounded-3xl border border-violet-100 bg-white p-6 shadow-[0_16px_45px_rgba(50,20,90,.07)] sm:p-8">
            <div className="flex items-start gap-4"><div className="rounded-2xl bg-violet-100 p-3 text-violet-700"><UploadCloud className="size-6" /></div><div><h2 className="text-2xl font-semibold tracking-tight text-slate-900">Ajouter un CIR final</h2><p className="mt-1 text-sm leading-6 text-slate-500">Si l’entreprise ou le projet n’existe pas, ils seront créés automatiquement. Un seul fichier validé suffit.</p></div></div>
            <form onSubmit={uploadAndIndex} className="mt-7 space-y-5">
              <div className="grid gap-4 sm:grid-cols-2"><label className="space-y-2"><span className="text-sm font-semibold text-slate-700">Entreprise</span><input list="memory-organisms" value={form.organisme} onChange={(event) => setForm({ ...form, organisme: event.target.value })} placeholder="Ex. Scalian" className="w-full rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /><datalist id="memory-organisms">{catalog?.organisms.map((item) => <option key={item} value={item} />)}</datalist></label><label className="space-y-2"><span className="text-sm font-semibold text-slate-700">Année</span><input value={form.year} onChange={(event) => setForm({ ...form, year: event.target.value })} inputMode="numeric" placeholder="2026" className="w-full rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /></label></div>
              <label className="block space-y-2"><span className="text-sm font-semibold text-slate-700">Nom du projet</span><input value={form.project} onChange={(event) => setForm({ ...form, project: event.target.value })} placeholder="Ex. AI-RADAR" className="w-full rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /></label>
              <label className={`flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed px-5 py-9 text-center transition ${file ? "border-emerald-300 bg-emerald-50" : "border-violet-200 bg-violet-50/50 hover:border-violet-400 hover:bg-violet-50"}`}><input type="file" accept=".pdf,.docx,.txt,.md" className="sr-only" onChange={(event) => setFile(event.target.files?.[0] || null)} />{file ? <><FileCheck2 className="size-8 text-emerald-600" /><p className="mt-3 font-semibold text-emerald-900">{file.name}</p><p className="mt-1 text-xs text-emerald-700">{(file.size / 1024 / 1024).toFixed(2)} Mo · cliquer pour remplacer</p></> : <><UploadCloud className="size-8 text-violet-600" /><p className="mt-3 font-semibold text-slate-800">Choisir le CIR final validé</p><p className="mt-1 text-xs text-slate-500">PDF, DOCX, TXT ou MD</p></>}</label>
              <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4 text-sm leading-6 text-blue-800"><strong>Traitement automatique :</strong> extraction du texte, analyse CIR, création des cartes de connaissance et mise à jour de la collection vectorielle utilisée par les agents.</div>
              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end"><SecondaryButton onClick={createSlot} disabled={loading}><FolderPlus className="size-4" />Créer sans fichier</SecondaryButton><PrimaryButton type="submit" disabled={loading || !file}>{loading ? <RefreshCw className="size-4 animate-spin" /> : <Sparkles className="size-4" />}Ajouter et indexer</PrimaryButton></div>
            </form>
          </section>
        )}

        {tab === "power-automate" && <PowerAutomateImportPanel onMemoryChanged={() => loadCatalog(true)} />}

        {tab === "search" && (
          <section className="rounded-3xl border border-violet-100 bg-white p-6 shadow-[0_12px_36px_rgba(50,20,90,.06)] sm:p-8">
            <div className="max-w-3xl"><h2 className="text-2xl font-semibold tracking-tight text-slate-900">Interroger la mémoire vectorielle</h2><p className="mt-1 text-sm leading-6 text-slate-500">Retrouve des projets, méthodes, verrous ou exemples de rédaction proches dans les CIR validés.</p></div>
            <form onSubmit={runSearch} className="mt-6 grid gap-3 lg:grid-cols-[minmax(0,1fr)_170px_190px_auto]"><label className="relative"><Search className="absolute left-3.5 top-3.5 size-4 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Quels projets ont rencontré des verrous similaires ?" className="w-full rounded-xl border border-slate-200 py-3 pl-10 pr-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /></label><select value={searchRole} onChange={(event) => setSearchRole(event.target.value)} className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm outline-none focus:border-violet-400"><option value="">Tous les rôles</option>{["objectif", "etat_art", "limite", "verrou", "methode", "resultat", "contribution", "style"].map((role) => <option key={role} value={role}>{labelForRole(role)}</option>)}</select><select value={searchOrganisation} onChange={(event) => setSearchOrganisation(event.target.value)} className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm outline-none focus:border-violet-400"><option value="">Toutes les entreprises</option>{catalog?.organisms.map((item) => <option key={item}>{item}</option>)}</select><PrimaryButton type="submit" disabled={loading}>{loading ? <RefreshCw className="size-4 animate-spin" /> : <Search className="size-4" />}Rechercher</PrimaryButton></form>
            <div className="mt-7 grid gap-4 lg:grid-cols-2">{matches.map((match, index) => { const metadata = match.metadata || {}; return <article key={match.id || index} className="rounded-2xl border border-slate-100 bg-slate-50 p-5"><div className="flex flex-wrap items-center gap-2"><Pill tone="violet">{labelForRole(String(metadata.role || "mémoire"))}</Pill><Pill>{metadata.project || "Projet"} · {metadata.year || "—"}</Pill></div><h3 className="mt-3 font-semibold text-slate-900">{metadata.section_title || metadata.document || "Passage CIR"}</h3><p className="mt-2 line-clamp-6 whitespace-pre-wrap text-sm leading-6 text-slate-600">{match.text}</p><p className="mt-3 text-xs text-slate-400">{metadata.organisme} · {metadata.memory_class || "expérience"}</p></article> })}</div>
            {!matches.length && <div className="mt-7 rounded-2xl border border-dashed border-slate-200 py-12 text-center"><BrainCircuit className="mx-auto size-8 text-violet-300" /><p className="mt-3 text-sm text-slate-500">Les résultats vectoriels apparaîtront ici.</p></div>}
          </section>
        )}

        <details className="rounded-2xl border border-slate-200 bg-white px-5 py-4 text-sm text-slate-600">
          <summary className="cursor-pointer font-semibold text-slate-700">État technique de Memory V2</summary>
          <div className="mt-4 grid gap-4 lg:grid-cols-2"><div className="rounded-xl bg-slate-50 p-4"><p className="font-semibold text-slate-800">Connexions IA</p><div className="mt-3 space-y-2 text-xs"><p>EnnoDiagnostic : {catalog?.ai_connections.ennodiagnostic ? "connecté" : "absent"}</p><p>Comparaison CIR : {catalog?.ai_connections.cir_comparison ? "connectée" : "absente"}</p><p>Style rédactionnel : {catalog?.ai_connections.writing_style ? "connecté" : "absent"}</p></div></div><div className="rounded-xl bg-slate-50 p-4"><p className="font-semibold text-slate-800">Source unique</p><p className="mt-2 break-all text-xs text-slate-500">{catalog?.paths.v2_root}</p><p className="mt-1 break-all text-xs text-slate-500">Collection : {catalog?.vector_db.collection}</p><div className="mt-3"><SecondaryButton onClick={rebuild} disabled={loading}><RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />Reconstruire l’index</SecondaryButton></div></div></div>
        </details>
      </div>
    </div>
  )
}
