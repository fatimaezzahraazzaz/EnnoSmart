"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertTriangle,
  ArrowUp,
  CheckCircle2,
  ChevronRight,
  Cloud,
  Database,
  Eye,
  FileCheck2,
  FileQuestion,
  FileText,
  FolderSearch2,
  History,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react"
import { getAccessToken } from "@/lib/api"


const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

type AuditSignal = {
  label: string
  source: string
  weight: number
}

type AuditItem = {
  external_id: string
  name: string
  source_path: string
  size: number
  sha256?: string
  classification: string
  confidence: number
  signals?: AuditSignal[]
  detected_identity?: { organisme?: string; project?: string; subproject?: string; year?: string }
  preview_excerpt?: string
  preview_chars?: number
  needs_ocr?: boolean
  extraction_mode?: string
  review_status?: string
  indexed?: boolean
  errors?: string[]
  recommended_version?: boolean
  index_eligible?: boolean
  selection_status?: string
  alternative_versions_count?: number
  already_in_memory_by_hash?: boolean
  already_in_memory_by_identity?: boolean
  legacy_doc?: boolean
  legacy_doc_conversion?: { required?: boolean; ok?: boolean; error?: string; converter?: string }
  indexable?: boolean
}

type AuditRun = {
  ok: boolean
  scan_id: string
  provider: string
  mode: string
  status: string
  started_at: string
  completed_at?: string
  source_scope?: string
  source_write_operations: number
  source_create_operations: number
  source_update_operations: number
  source_move_operations: number
  source_delete_operations: number
  memory_index_operations: number
  source_integrity_verified?: boolean | null
  manifest_sha256?: string
  approval_required_before_index?: boolean
  counts: Record<string, number>
  items: AuditItem[]
  errors?: string[]
}

type ImportFolder = {
  name: string
  relative_path: string
  has_children: boolean
  supported_files_direct: number
}

type FolderListing = {
  ok: boolean
  root: string
  current: string
  parent: string
  depth: number
  folders: ImportFolder[]
  supported_files_direct: number
  source_write_operations: number
}

type AuditConfiguration = {
  ok: boolean
  mode: string
  credentials_required: boolean
  client_id_required: boolean
  client_secret_required: boolean
  import_folder_configured: boolean
  import_root: string
  fake_available: boolean
  fake_root: string
  audit_root: string
  storage_separated_from_source?: boolean
  storage_configuration_error?: string
  legacy_doc_converter?: { available: boolean; name: string; path?: string; source_policy?: string }
  safety: {
    source_operations: string[]
    sharepoint_write_enabled: boolean
    source_create_enabled: boolean
    source_update_enabled: boolean
    source_move_enabled: boolean
    source_delete_enabled: boolean
    automatic_source_delete: boolean
    automatic_memory_delete: boolean
    scan_indexes_memory: boolean
    index_requires_explicit_confirmation: boolean
  }
}

type IdentityForm = {
  organisme: string
  project: string
  subproject: string
  year: string
}


function errorMessage(value: unknown): string {
  if (value instanceof Error) return value.message
  return String(value || "Erreur inattendue")
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {})
  const token = getAccessToken()
  if (token) headers.set("Authorization", `Bearer ${token}`)
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json")
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers })
  const raw = await response.text()
  let payload: any = null
  try { payload = raw ? JSON.parse(raw) : null } catch { payload = raw }
  if (!response.ok) {
    const detail = payload?.detail
    if (typeof detail === "string") throw new Error(detail)
    throw new Error(detail?.message || JSON.stringify(detail || payload || `HTTP ${response.status}`))
  }
  return payload as T
}

function classificationLabel(value: string): string {
  return ({
    cir_final_confirmed: "CIR final confirmé",
    cir_probable: "CIR probable",
    cir_draft: "Brouillon CIR",
    client_document: "Document client",
    source_missing: "Source absente",
    scan_error: "Erreur d’analyse",
    too_large: "Fichier trop volumineux",
    unsupported: "Format non pris en charge",
    legacy_doc_requires_converter: "Ancien Word · conversion requise",
  } as Record<string, string>)[value] || value.replaceAll("_", " ")
}

function classificationStyle(value: string): string {
  if (value === "cir_final_confirmed") return "border-emerald-200 bg-emerald-50 text-emerald-800"
  if (value === "cir_probable") return "border-amber-200 bg-amber-50 text-amber-800"
  if (value === "cir_draft") return "border-orange-200 bg-orange-50 text-orange-800"
  if (value === "legacy_doc_requires_converter") return "border-blue-200 bg-blue-50 text-blue-800"
  if (value === "scan_error" || value === "source_missing") return "border-rose-200 bg-rose-50 text-rose-800"
  return "border-slate-200 bg-slate-50 text-slate-700"
}

function formatSize(bytes: number): string {
  if (!bytes) return "0 Ko"
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} Ko`
  return `${(bytes / 1024 / 1024).toFixed(1)} Mo`
}

function Stat({ label, value, tone = "violet" }: { label: string; value: number | string; tone?: "violet" | "green" | "amber" | "slate" }) {
  const styles = {
    violet: "bg-violet-50 text-violet-800",
    green: "bg-emerald-50 text-emerald-800",
    amber: "bg-amber-50 text-amber-800",
    slate: "bg-slate-50 text-slate-800",
  }
  return <div className={`rounded-2xl p-4 ${styles[tone]}`}><p className="text-2xl font-bold">{value}</p><p className="mt-1 text-xs font-semibold uppercase tracking-wide opacity-70">{label}</p></div>
}


export default function PowerAutomateImportPanel({ onMemoryChanged }: { onMemoryChanged?: () => Promise<void> | void }) {
  const [configuration, setConfiguration] = useState<AuditConfiguration | null>(null)
  const [run, setRun] = useState<AuditRun | null>(null)
  const [provider, setProvider] = useState<"fake" | "inbox">("fake")
  const [deepScan, setDeepScan] = useState(false)
  const [professionalFolderReadAcknowledged, setProfessionalFolderReadAcknowledged] = useState(false)
  const [selectedId, setSelectedId] = useState("")
  const [filter, setFilter] = useState("")
  const [classFilter, setClassFilter] = useState("all")
  const [identity, setIdentity] = useState<IdentityForm>({ organisme: "", project: "", subproject: "", year: "" })
  const [confirmIndex, setConfirmIndex] = useState(false)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState("")
  const [error, setError] = useState("")
  const [folderListing, setFolderListing] = useState<FolderListing | null>(null)
  const [folderFilter, setFolderFilter] = useState("")
  const [folderLoading, setFolderLoading] = useState(false)

  const selected = useMemo(
    () => run?.items?.find((item) => item.external_id === selectedId) || null,
    [run, selectedId],
  )

  const visibleItems = useMemo(() => {
    const needle = filter.trim().toLocaleLowerCase("fr")
    return (run?.items || []).filter((item) => {
      if (classFilter === "recommended" && !item.recommended_version) return false
      if (classFilter === "legacy_doc" && !item.legacy_doc) return false
      if (!['all', 'recommended', 'legacy_doc'].includes(classFilter) && item.classification !== classFilter) return false
      if (!needle) return true
      return `${item.name} ${item.source_path} ${item.detected_identity?.organisme || ""} ${item.detected_identity?.project || ""} ${item.detected_identity?.subproject || ""}`
        .toLocaleLowerCase("fr")
        .includes(needle)
    })
  }, [run, filter, classFilter])

  const visibleFolders = useMemo(() => {
    const needle = folderFilter.trim().toLocaleLowerCase("fr")
    return (folderListing?.folders || []).filter((folder) => !needle || folder.name.toLocaleLowerCase("fr").includes(needle))
  }, [folderListing, folderFilter])

  function selectItem(item: AuditItem, sourceScope = run?.source_scope || "") {
    const scopeParts = sourceScope.split("/").filter(Boolean)
    const scopeYear = [...scopeParts].reverse().find((part) => /^(?:19|20)\d{2}$/.test(part)) || ""
    const scopeProject = [...scopeParts.slice(1)].reverse().find((part) => !/^(?:19|20)\d{2}$/.test(part)) || ""
    setSelectedId(item.external_id)
    setIdentity({
      organisme: item.detected_identity?.organisme || scopeParts[0] || "",
      project: item.detected_identity?.project || scopeProject,
      subproject: item.detected_identity?.subproject || "",
      year: item.detected_identity?.year || scopeYear,
    })
    setConfirmIndex(false)
  }

  async function loadFolders(parent = "") {
    setFolderLoading(true)
    setError("")
    try {
      const listing = await request<FolderListing>(`/cir-memory/import-inbox/folders?provider=inbox&parent=${encodeURIComponent(parent)}`)
      setFolderListing(listing)
      setFolderFilter("")
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setFolderLoading(false)
    }
  }

  async function loadState() {
    setLoading(true)
    setError("")
    try {
      const [config, scans] = await Promise.all([
        request<AuditConfiguration>("/cir-memory/import-inbox/configuration"),
        request<{ ok: boolean; scans: Array<{ scan_id: string }> }>("/cir-memory/import-inbox/scans?limit=1"),
      ])
      setConfiguration(config)
      if (config.import_folder_configured) await loadFolders("")
      if (scans.scans[0]?.scan_id) {
        const latest = await request<AuditRun>(`/cir-memory/import-inbox/scans/${encodeURIComponent(scans.scans[0].scan_id)}`)
        setRun(latest)
        if (latest.items?.[0]) selectItem(latest.items[0], latest.source_scope)
      }
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setLoading(false)
    }
  }

  async function startAudit() {
    if (provider === "inbox" && !professionalFolderReadAcknowledged) {
      setError("Confirmez d’abord la lecture du dossier de copies professionnel.")
      return
    }
    const message = provider === "fake"
      ? "Lancer le test sur la bibliothèque factice ? Aucun fichier source et aucune mémoire ne seront modifiés."
      : `Analyser « ${folderListing?.current || "racine"} » et tous ses sous-dossiers en lecture seule ? Aucun fichier ne sera créé, déplacé, modifié ou supprimé.`
    if (!window.confirm(message)) return
    setLoading(true)
    setError("")
    setNotice("Audit en lecture seule en cours…")
    try {
      const result = await request<AuditRun>("/cir-memory/import-inbox/scans", {
        method: "POST",
        body: JSON.stringify({
          provider,
          deep_scan: deepScan,
          confirm_read_only_audit: true,
          acknowledge_professional_copy_folder_read: provider === "inbox" && professionalFolderReadAcknowledged,
          relative_folder: provider === "inbox" ? folderListing?.current || "" : "",
        }),
      })
      setRun(result)
      if (result.items?.[0]) selectItem(result.items[0], result.source_scope)
      const integrity = result.source_integrity_verified === true ? " Intégrité des sources vérifiée." : ""
      setNotice(`Audit terminé : ${result.counts?.audited || 0} document(s) analysé(s).${integrity}`)
    } catch (reason) {
      setError(errorMessage(reason))
      setNotice("")
    } finally {
      setLoading(false)
    }
  }

  async function indexSelected() {
    if (!selected || !confirmIndex) return
    if (run?.provider === "fake") {
      setError("Une source factice ne peut jamais être ajoutée à la mémoire de production.")
      return
    }
    if (!identity.organisme.trim() || !identity.project.trim() || !identity.year.trim()) {
      setError("Confirmez l’entreprise, le projet et l’année.")
      return
    }
    if (!selected.recommended_version || !selected.index_eligible) {
      setError("Seule la version finale recommandée et sans conflit Memory V2 peut être indexée.")
      return
    }
    if (!run?.manifest_sha256) {
      setError("Le manifeste signé du scan est absent. Relancez le scan.")
      return
    }
    if (!window.confirm("Cette action extraira le CIR depuis la copie de travail puis reconstruira la mémoire externe. Aucun original OneDrive et aucune copie permanente dans le projet ne seront créés. Continuer ?")) return
    setLoading(true)
    setError("")
    setNotice("Indexation locale explicitement autorisée en cours…")
    try {
      await request(`/cir-memory/import-inbox/scans/${encodeURIComponent(run!.scan_id)}/items/${encodeURIComponent(selected.external_id)}/index`, {
        method: "POST",
        body: JSON.stringify({
          ...identity,
          confirm_local_memory_changes: "INDEXER_DANS_MEMORY_V2",
          confirm_manifest_sha256: run?.manifest_sha256 || "",
        }),
      })
      const refreshed = await request<AuditRun>(`/cir-memory/import-inbox/scans/${encodeURIComponent(run!.scan_id)}`)
      setRun(refreshed)
      await onMemoryChanged?.()
      setNotice("Le CIR a été indexé dans la mémoire externe. Le dossier de copies n’a été ni modifié, ni déplacé, ni supprimé et aucun duplicata permanent n’a été ajouté au projet.")
      setConfirmIndex(false)
    } catch (reason) {
      setError(errorMessage(reason))
      setNotice("")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadState() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const counts = run?.counts || {}
  const fakeIsolation = run?.provider === "fake"

  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-3xl border border-violet-100 bg-white shadow-[0_14px_45px_rgba(50,20,90,.07)]">
        <div className="grid gap-6 bg-[linear-gradient(120deg,#f7f2ff,#fff_55%,#eefcf7)] p-6 sm:p-8 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div>
            <div className="flex items-center gap-3"><div className="rounded-2xl bg-violet-700 p-3 text-white"><Cloud className="size-6" /></div><div><p className="text-xs font-bold uppercase tracking-[.16em] text-violet-700">Bibliothèque professionnelle synchronisée</p><h2 className="text-2xl font-semibold tracking-tight text-slate-950">Recherche guidée des CIR</h2></div></div>
            <p className="mt-4 max-w-3xl text-sm leading-6 text-slate-600">EnnoSmart parcourt la copie locale synchronisée client par client, puis projet et année. Il lit les PDF/DOCX, calcule leur hash, élimine les doublons et place les CIR détectés dans une file de validation.</p>
            <div className="mt-5 flex flex-wrap gap-2 text-xs font-semibold">
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-white px-3 py-2 text-emerald-800"><Eye className="size-4" />Lecture seule</span>
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-white px-3 py-2 text-emerald-800"><LockKeyhole className="size-4" />Aucune suppression</span>
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-white px-3 py-2 text-emerald-800"><ShieldCheck className="size-4" />Validation avant Chroma</span>
            </div>
          </div>
          <div className="rounded-2xl border border-emerald-200 bg-white/85 p-5">
            <p className="flex items-center gap-2 text-sm font-bold text-emerald-900"><ShieldCheck className="size-4" />Contrat de sécurité actif</p>
            <ul className="mt-3 space-y-2 text-xs leading-5 text-slate-600">
              <li>• Aucun Client ID, secret ou appel Microsoft Graph</li>
              <li>• Source : lister, lire et calculer le hash uniquement</li>
              <li>• Création, modification, déplacement et suppression interdits</li>
              <li>• Un scan ne déclenche aucune indexation</li>
            </ul>
          </div>
        </div>
      </section>

      {notice && <div className="flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"><CheckCircle2 className="mt-0.5 size-4 shrink-0" />{notice}</div>}
      {error && <div className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{error}</div>}
      {configuration?.storage_separated_from_source === false && <div className="flex items-start gap-3 rounded-2xl border border-rose-300 bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-900"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{configuration.storage_configuration_error || "La zone d’audit locale chevauche le dossier OneDrive : scan bloqué."}</div>}

      <section className="rounded-3xl border border-blue-100 bg-blue-50/70 p-5 sm:p-6">
        <div className="flex items-start gap-3"><div className="rounded-xl bg-blue-700 p-2 text-white"><Cloud className="size-5" /></div><div><h3 className="font-semibold text-blue-950">Source locale configurée</h3><p className="mt-1 break-all text-xs leading-5 text-blue-800"><code>{configuration?.import_root || "Chargement…"}</code></p></div></div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-2xl border border-emerald-200 bg-white p-4 text-xs leading-5 text-emerald-900"><strong>SharePoint autorisé :</strong><br />Get files, Get properties, Get metadata, Get file content.</div>
          <div className="rounded-2xl border border-rose-200 bg-white p-4 text-xs leading-5 text-rose-900"><strong>SharePoint interdit :</strong><br />Create, Update, Rename, Move, Delete, Grant access, Stop sharing.</div>
        </div>
        <p className="mt-3 text-[11px] leading-5 text-blue-700">EnnoSmart ne travaille que sur une copie locale de traitement configurée sur le serveur. Il n’écrit jamais dans la bibliothèque synchronisée.</p>
        <div className={`mt-3 rounded-xl border px-3.5 py-3 text-xs leading-5 ${configuration?.legacy_doc_converter?.available ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
          <strong>Anciens fichiers Word `.doc` :</strong> {configuration?.legacy_doc_converter?.available ? "convertisseur local disponible ; seules les copies de travail seront converties." : "LibreOffice headless absent ; ces fichiers seront signalés et resteront non indexables jusqu’à son installation."}
        </div>
      </section>

      <section className="rounded-3xl border border-violet-100 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="grid flex-1 gap-3 sm:grid-cols-2">
            <label className={`cursor-pointer rounded-2xl border p-4 transition ${provider === "fake" ? "border-violet-400 bg-violet-50 ring-4 ring-violet-50" : "border-slate-200"}`}>
              <input type="radio" name="provider" value="fake" checked={provider === "fake"} onChange={() => setProvider("fake")} className="sr-only" />
              <span className="flex items-center gap-2 font-semibold text-slate-900"><FolderSearch2 className="size-4 text-violet-700" />Fausse boîte Power Automate</span>
              <span className="mt-1 block text-xs leading-5 text-slate-500">Jeu de test isolé, recommandé pour la première validation.</span>
            </label>
            <label className={`rounded-2xl border p-4 transition ${configuration?.import_folder_configured ? "cursor-pointer" : "cursor-not-allowed opacity-60"} ${provider === "inbox" ? "border-violet-400 bg-violet-50 ring-4 ring-violet-50" : "border-slate-200"}`}>
              <input type="radio" name="provider" value="inbox" checked={provider === "inbox"} disabled={!configuration?.import_folder_configured} onChange={() => { setProvider("inbox"); if (!folderListing) void loadFolders("") }} className="sr-only" />
              <span className="flex items-center gap-2 font-semibold text-slate-900"><Cloud className="size-4 text-violet-700" />Dossier OneDrive professionnel</span>
              <span className="mt-1 block break-all text-xs leading-5 text-slate-500">{configuration?.import_folder_configured ? configuration.import_root : `Dossier introuvable : ${configuration?.import_root || "à configurer"}`}</span>
            </label>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <label className="flex items-center gap-2 text-xs font-medium text-slate-600"><input type="checkbox" checked={deepScan} onChange={(event) => setDeepScan(event.target.checked)} className="size-4 rounded border-slate-300 accent-violet-700" />OCR approfondi si nécessaire</label>
            <button type="button" onClick={startAudit} disabled={loading || folderLoading || configuration?.storage_separated_from_source === false || (provider === "fake" && !configuration?.fake_available) || (provider === "inbox" && !folderListing?.current)} className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-800 disabled:cursor-not-allowed disabled:opacity-50">
              {loading ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}{provider === "fake" ? "Lancer le test factice" : "Scanner ce dossier"}
            </button>
          </div>
        </div>
        {provider === "inbox" && folderListing && <div className="mt-5 rounded-2xl border border-violet-100 bg-slate-50/70 p-4 sm:p-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div><p className="text-sm font-bold text-slate-900">Navigation client → projet → année</p><p className="mt-1 text-xs text-slate-500">Ouvre les dossiers jusqu’au périmètre souhaité, puis clique sur « Scanner ce dossier ». Tous ses sous-dossiers seront inclus.</p></div>
            {folderListing.current && <button type="button" onClick={() => loadFolders(folderListing.parent)} disabled={folderLoading} className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"><ArrowUp className="size-4" />Dossier précédent</button>}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-1.5 text-xs font-semibold text-slate-600">
            <button type="button" onClick={() => loadFolders("")} className="rounded-lg bg-white px-2.5 py-1.5 text-violet-700 shadow-sm">Clients</button>
            {(folderListing.current ? folderListing.current.split("/") : []).map((part, index, parts) => {
              const path = parts.slice(0, index + 1).join("/")
              return <span key={path} className="inline-flex items-center gap-1.5"><ChevronRight className="size-3 text-slate-300" /><button type="button" onClick={() => loadFolders(path)} className="rounded-lg bg-white px-2.5 py-1.5 hover:text-violet-700">{part}</button></span>
            })}
          </div>
          <label className="relative mt-4 block"><Search className="absolute left-3 top-3 size-4 text-slate-400" /><input value={folderFilter} onChange={(event) => setFolderFilter(event.target.value)} placeholder={folderListing.depth === 0 ? "Rechercher un client…" : "Rechercher un projet, une année ou un sous-dossier…"} className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm outline-none focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /></label>
          <div className="mt-3 max-h-72 space-y-1.5 overflow-auto pr-1">
            {folderLoading ? <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-500"><LoaderCircle className="size-4 animate-spin" />Chargement des dossiers…</div> : visibleFolders.map((folder) => <button key={folder.relative_path} type="button" onClick={() => loadFolders(folder.relative_path)} className="flex w-full items-center gap-3 rounded-xl border border-slate-100 bg-white px-3.5 py-3 text-left transition hover:border-violet-200 hover:bg-violet-50"><FolderSearch2 className="size-4 shrink-0 text-violet-600" /><span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-800">{folder.name}</span><span className="text-[11px] text-slate-400">{folder.supported_files_direct} fichier(s) direct(s)</span><ChevronRight className="size-4 text-slate-300" /></button>)}
            {!folderLoading && !visibleFolders.length && <div className="rounded-xl border border-dashed border-slate-200 bg-white py-7 text-center text-xs text-slate-500">Aucun sous-dossier. Tu peux scanner le dossier courant directement.</div>}
          </div>
          {folderListing.current ? <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3 text-xs leading-5 text-emerald-800"><strong>Périmètre prêt :</strong> {folderListing.current}<br />Le scan restera strictement limité à ce dossier et à ses descendants.</div> : <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 text-xs text-amber-800">Choisis d’abord un client. Le scan de toute la racine est volontairement désactivé.</div>}
        </div>}
        {provider === "inbox" && <label className="mt-4 flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><input type="checkbox" checked={professionalFolderReadAcknowledged} onChange={(event) => setProfessionalFolderReadAcknowledged(event.target.checked)} className="mt-0.5 size-4 accent-amber-700" /><span>Je confirme la lecture du dossier professionnel. EnnoSmart ne créera, ne modifiera, ne déplacera et ne supprimera aucun fichier source.</span></label>}
      </section>

      {run && (
        <>
          {run.source_scope && <div className="rounded-2xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm text-violet-900"><strong>Périmètre analysé :</strong> {run.source_scope}</div>}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <Stat label="Analysés" value={counts.audited || 0} />
            <Stat label="CIR confirmés" value={counts.cir_final_confirmed || 0} tone="green" />
            <Stat label="Versions retenues" value={counts.recommended_for_index || 0} tone="green" />
            <Stat label="À vérifier" value={counts.cir_probable || 0} tone="amber" />
            <Stat label="Brouillons" value={counts.cir_draft || 0} tone="slate" />
            <Stat label="Anciennes versions" value={(counts.older_alternatives || 0) + (counts.exact_duplicates || 0)} tone="slate" />
          </div>

          {run.manifest_sha256 && <div className="rounded-2xl border border-violet-200 bg-violet-50 px-4 py-3 text-xs leading-5 text-violet-900"><strong>Manifeste de sélection :</strong> <code className="break-all">{run.manifest_sha256}</code><br />L’API vérifiera cette signature avant chaque indexation.</div>}

          <div className={`flex flex-col gap-3 rounded-2xl border p-4 text-sm sm:flex-row sm:items-center sm:justify-between ${run.source_integrity_verified === true ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-slate-200 bg-slate-50 text-slate-700"}`}>
            <span className="flex items-center gap-2 font-semibold">{run.source_integrity_verified === true ? <CheckCircle2 className="size-5" /> : <History className="size-5" />}{run.source_integrity_verified === true ? "Copies locales vérifiées par hash ; aucune opération d’écriture source exécutée." : "Audit terminé sans opération d’écriture déclarée."}</span>
            <span className="text-xs">Créations : {run.source_create_operations} · Modifications : {run.source_update_operations} · Déplacements : {run.source_move_operations} · Suppressions : {run.source_delete_operations} · Indexations : {run.memory_index_operations}</span>
          </div>

          <div className="grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(330px,.65fr)]">
            <section className="rounded-3xl border border-violet-100 bg-white p-5 shadow-sm sm:p-6">
              <div className="flex flex-col gap-3 sm:flex-row">
                <label className="relative flex-1"><Search className="absolute left-3 top-3 size-4 text-slate-400" /><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Nom, chemin, entreprise ou projet…" className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2.5 pl-9 pr-3 text-sm outline-none focus:border-violet-400 focus:bg-white focus:ring-4 focus:ring-violet-100" /></label>
                <select value={classFilter} onChange={(event) => setClassFilter(event.target.value)} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-violet-400">
                  <option value="all">Toutes les catégories</option>
                  <option value="recommended">Versions finales recommandées</option>
                  <option value="cir_final_confirmed">CIR confirmés</option>
                  <option value="cir_probable">CIR probables</option>
                  <option value="cir_draft">Brouillons</option>
                  <option value="client_document">Documents client</option>
                  <option value="legacy_doc">Anciens Word .doc</option>
                </select>
                <button type="button" onClick={loadState} disabled={loading} className="grid size-10 place-items-center rounded-xl border border-slate-200 text-slate-600 hover:bg-slate-50"><RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} /></button>
              </div>
              <div className="mt-5 max-h-[700px] space-y-2 overflow-auto pr-1">
                {visibleItems.map((item) => (
                  <button key={item.external_id} type="button" onClick={() => selectItem(item)} className={`flex w-full items-start gap-3 rounded-2xl border p-4 text-left transition ${selectedId === item.external_id ? "border-violet-300 bg-violet-50" : "border-slate-100 hover:border-violet-200 hover:bg-slate-50"}`}>
                    <div className="mt-0.5 rounded-xl bg-white p-2 text-violet-700 shadow-sm">{item.classification === "cir_final_confirmed" ? <FileCheck2 className="size-5" /> : item.classification.includes("cir") ? <FileQuestion className="size-5" /> : <FileText className="size-5" />}</div>
                    <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="truncate font-semibold text-slate-900">{item.name}</p><span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${classificationStyle(item.classification)}`}>{classificationLabel(item.classification)}</span>{item.recommended_version && <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-800">Version recommandée</span>}{item.selection_status === "older_alternative" && <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold text-slate-600">Ancienne version écartée</span>}{item.selection_status === "already_in_memory" && <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-800">Déjà en mémoire</span>}{item.selection_status === "memory_version_conflict" && <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">Version existante à archiver</span>}</div><p className="mt-1 truncate text-xs text-slate-500">{item.source_path}</p><p className="mt-2 text-xs text-slate-400">Confiance {Math.round((item.confidence || 0) * 100)} % · {formatSize(item.size)}{item.alternative_versions_count ? ` · ${item.alternative_versions_count} alternative(s)` : ""}</p></div>
                  </button>
                ))}
              </div>
            </section>

            <aside className="h-fit rounded-3xl border border-violet-100 bg-white p-5 shadow-sm sm:p-6 lg:sticky lg:top-6">
              {selected ? <div>
                <div className="flex items-start justify-between gap-3"><div><span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${classificationStyle(selected.classification)}`}>{classificationLabel(selected.classification)}</span><h3 className="mt-3 break-words text-xl font-semibold text-slate-950">{selected.name}</h3></div><span className="text-2xl font-bold text-violet-700">{Math.round((selected.confidence || 0) * 100)}%</span></div>
                <p className="mt-2 break-all text-xs leading-5 text-slate-400">{selected.source_path}</p>
                {selected.preview_excerpt && <div className="mt-4 rounded-2xl bg-slate-50 p-4"><p className="text-xs font-bold uppercase tracking-wide text-slate-400">Aperçu extrait</p><p className="mt-2 line-clamp-8 text-xs leading-5 text-slate-600">{selected.preview_excerpt}</p></div>}
                <div className="mt-4"><p className="text-xs font-bold uppercase tracking-wide text-slate-400">Pourquoi cette catégorie ?</p><div className="mt-2 flex flex-wrap gap-2">{(selected.signals || []).map((signal, index) => <span key={`${signal.label}-${index}`} className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-600">{signal.label}</span>)}</div></div>
                {selected.needs_ocr && <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">Le document semble scanné. Relancez avec « OCR approfondi » avant toute validation.</div>}
                {selected.selection_status === "older_alternative" && <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-700">Cette version a été écartée au profit d’une version finale mieux classée. Elle ne peut pas être indexée.</div>}
                {selected.selection_status === "already_in_memory" && <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-800">Ce fichier exact est déjà présent dans Memory V2 : aucune nouvelle indexation n’est nécessaire.</div>}
                {selected.selection_status === "memory_version_conflict" && <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">Une version existe déjà pour ce projet et cette année. Vérifiez-la dans « Bibliothèque » et archivez-la seulement si la version recommandée doit réellement la remplacer.</div>}
                {selected.legacy_doc && selected.legacy_doc_conversion?.ok && <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs leading-5 text-emerald-800">Ancien Word converti avec succès sur une copie locale. Le `.doc` OneDrive original reste inchangé.</div>}
                {selected.classification === "legacy_doc_requires_converter" && <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-800">Installez LibreOffice headless sur le serveur, puis relancez ce scan. Aucun traitement n’est effectué sur l’original.</div>}
                {(selected.classification === "cir_final_confirmed" || selected.classification === "cir_probable") && <div className="mt-5 border-t border-slate-100 pt-5">
                  <p className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Database className="size-4 text-violet-700" />Validation avant Memory V2</p>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <label className="space-y-1.5"><span className="text-xs font-semibold text-slate-700">Organisme <span aria-hidden="true">*</span></span><input value={identity.organisme} onChange={(event) => setIdentity({ ...identity, organisme: event.target.value })} placeholder="Ex. 6NAPSE GROUP" required className="min-h-11 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /></label>
                    <label className="space-y-1.5"><span className="text-xs font-semibold text-slate-700">Projet <span aria-hidden="true">*</span></span><input value={identity.project} onChange={(event) => setIdentity({ ...identity, project: event.target.value })} placeholder="Ex. CEVAA" required className="min-h-11 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /></label>
                    <label className="space-y-1.5"><span className="text-xs font-semibold text-slate-700">Sous-projet <span className="font-normal text-slate-400">(facultatif)</span></span><input value={identity.subproject} onChange={(event) => setIdentity({ ...identity, subproject: event.target.value })} placeholder="Ex. APACHE" className="min-h-11 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /></label>
                    <label className="space-y-1.5"><span className="text-xs font-semibold text-slate-700">Année <span aria-hidden="true">*</span></span><input value={identity.year} onChange={(event) => setIdentity({ ...identity, year: event.target.value })} inputMode="numeric" placeholder="2024" required className="min-h-11 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-violet-400 focus:ring-4 focus:ring-violet-100" /></label>
                  </div>
                  <div aria-live="polite" className="mt-3 rounded-xl border border-violet-100 bg-violet-50 px-3 py-2.5 text-xs leading-5 text-violet-900"><span className="font-semibold">Classement retenu :</span> {[identity.organisme, identity.project, identity.subproject, identity.year].filter(Boolean).join(" › ") || "À compléter"}</div>
                  {fakeIsolation ? <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-800"><strong>Test isolé :</strong> les documents factices ne peuvent pas être envoyés dans la mémoire de production.</div> : <>
                    <label className="mt-4 flex items-start gap-2 text-xs leading-5 text-slate-600"><input type="checkbox" checked={confirmIndex} disabled={!selected.recommended_version || !selected.index_eligible} onChange={(event) => setConfirmIndex(event.target.checked)} className="mt-0.5 size-4 accent-violet-700 disabled:opacity-40" /><span>Je confirme l’identité, la version finale recommandée et le manifeste signé. Le fichier OneDrive restera inchangé.</span></label>
                    <button type="button" onClick={indexSelected} disabled={loading || !confirmIndex || selected.indexed || !selected.recommended_version || !selected.index_eligible} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-3 text-sm font-semibold text-white hover:bg-violet-800 disabled:cursor-not-allowed disabled:opacity-50"><Database className="size-4" />{selected.indexed ? "Déjà indexé" : selected.recommended_version && selected.index_eligible ? "Valider, extraire et indexer" : "Version non indexable"}</button>
                  </>}
                </div>}
                {(selected.errors || []).length > 0 && <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">{selected.errors?.join(" · ")}</div>}
              </div> : <div className="py-10 text-center text-sm text-slate-500"><FileQuestion className="mx-auto size-8 text-violet-300" /><p className="mt-3">Sélectionnez un document pour voir les preuves de classification.</p></div>}
            </aside>
          </div>
        </>
      )}
    </div>
  )
}
