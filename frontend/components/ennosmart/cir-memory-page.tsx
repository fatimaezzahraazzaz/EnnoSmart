"use client"

import React, {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  BrainCircuit,
  Building2,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Cloud,
  Database,
  Download,
  ExternalLink,
  FileCheck2,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  Maximize2,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react"

import PowerAutomateImportPanel from "@/components/ennosmart/sharepoint-audit-panel"
import {
  PageHeader,
  StatusNotice,
} from "@/components/ennosmart/workspace-ui"

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
  subproject?: string
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
    collections: Array<{
      name: string
      items_count: number
    }>
  }
  ai_connections: {
    ennodiagnostic: boolean
    cir_comparison: boolean
    writing_style: boolean
    usage_rule: string
  }
  paths: Record<string, string | boolean>
}

type SearchMatch = {
  id?: string
  text?: string
  distance?: number
  metadata?: Record<string, any>
}

type Tab =
  | "library"
  | "add"
  | "search"
  | "power-automate"

type TreeProjectGroup = {
  key: string
  name: string
  items: MemoryProject[]
  directItems: MemoryProject[]
  subprojects: Array<{
    key: string
    name: string
    items: MemoryProject[]
  }>
}

type TreeOrganismGroup = {
  key: string
  name: string
  items: MemoryProject[]
  projects: TreeProjectGroup[]
}

type PreviewState = {
  open: boolean
  loading: boolean
  error: string
  objectUrl: string
  mediaType: string
  fileName: string
}

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000"

const EMPTY_PREVIEW: PreviewState = {
  open: false,
  loading: false,
  error: "",
  objectUrl: "",
  mediaType: "",
  fileName: "",
}

function cleanToken(value: unknown): string {
  if (!value) return ""

  let candidate = String(value)
    .trim()
    .replace(/^Bearer\s+/i, "")

  if (
    !candidate ||
    candidate === "undefined" ||
    candidate === "null"
  ) {
    return ""
  }

  if (
    (candidate.startsWith('"') &&
      candidate.endsWith('"')) ||
    (candidate.startsWith("'") &&
      candidate.endsWith("'"))
  ) {
    candidate = candidate.slice(1, -1).trim()
  }

  return candidate
}

function authToken(): string {
  if (typeof window === "undefined") return ""

  const stores: Storage[] = []

  try {
    stores.push(localStorage)
  } catch {}

  try {
    stores.push(sessionStorage)
  } catch {}

  const keys = [
    "access_token",
    "accessToken",
    "token",
    "auth_token",
    "authToken",
    "jwt",
    "ennosmart_token",
    "ennosmart_access_token",
  ]

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
  if (typeof value?.detail === "string") {
    return value.detail
  }
  if (value?.detail) {
    return JSON.stringify(value.detail)
  }
  return "Une erreur inattendue est survenue."
}

async function api<T = any>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const headers = new Headers(init.headers || {})
  const token = authToken()

  if (token) {
    headers.set("Authorization", `Bearer ${token}`)
  }

  const isForm =
    typeof FormData !== "undefined" &&
    init.body instanceof FormData

  if (
    init.body &&
    !isForm &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json")
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  })

  const raw = await response.text()

  let payload: any = null

  try {
    payload = raw ? JSON.parse(raw) : null
  } catch {
    payload = raw
  }

  if (!response.ok) {
    throw new Error(errorText(payload))
  }

  return payload as T
}

function formatNumber(
  value: number | undefined
): string {
  return new Intl.NumberFormat("fr-FR").format(
    Number(value || 0)
  )
}

function formatDate(value?: string): string {
  if (!value) return "—"

  const date = new Date(value)

  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("fr-FR", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date)
}

function labelForRole(role: string): string {
  return (
    {
      objectif: "Objectifs",
      etat_art: "État de l’art",
      limite: "Limites",
      verrou: "Verrous",
      methode: "Méthodes",
      resultat: "Résultats",
      contribution: "Contributions",
      style: "Style",
    } as Record<string, string>
  )[role] || role.replaceAll("_", " ")
}

function primarySource(
  project: MemoryProject
): MemorySource | null {
  if (!project.source_files?.length) return null

  if (project.indexed_file_name) {
    const exact = project.source_files.find(
      (source) =>
        source.file_name === project.indexed_file_name
    )

    if (exact) return exact
  }

  return project.source_files[0] || null
}

function fileExtension(value?: string) {
  const match = String(value || "")
    .toLowerCase()
    .match(/\.([a-z0-9]+)$/)

  return match?.[1]?.toUpperCase() || "FICHIER"
}

function projectKey(
  organisme: string,
  project: string
) {
  return `${organisme}::${project}`
}

function subprojectKey(
  organisme: string,
  project: string,
  subproject: string
) {
  return `${organisme}::${project}::${subproject}`
}

function groupByYear(items: MemoryProject[]) {
  const groups = new Map<string, MemoryProject[]>()

  items.forEach((item) => {
    const key = item.year || "Année inconnue"

    if (!groups.has(key)) {
      groups.set(key, [])
    }

    groups.get(key)!.push(item)
  })

  return Array.from(groups.entries())
    .sort(([left], [right]) =>
      String(right).localeCompare(
        String(left),
        "fr",
        { numeric: true }
      )
    )
    .map(([year, rows]) => ({
      year,
      rows,
    }))
}

function buildTree(
  items: MemoryProject[]
): TreeOrganismGroup[] {
  const organisms = new Map<
    string,
    MemoryProject[]
  >()

  items.forEach((item) => {
    const key =
      item.organisme || "Organisme non renseigné"

    if (!organisms.has(key)) {
      organisms.set(key, [])
    }

    organisms.get(key)!.push(item)
  })

  return Array.from(organisms.entries())
    .sort(([left], [right]) =>
      left.localeCompare(right, "fr")
    )
    .map(([organisme, organismItems]) => {
      const projects = new Map<
        string,
        MemoryProject[]
      >()

      organismItems.forEach((item) => {
        const key =
          item.project || "Projet non renseigné"

        if (!projects.has(key)) {
          projects.set(key, [])
        }

        projects.get(key)!.push(item)
      })

      return {
        key: organisme,
        name: organisme,
        items: organismItems,
        projects: Array.from(projects.entries())
          .sort(([left], [right]) =>
            left.localeCompare(right, "fr")
          )
          .map(([project, projectItems]) => {
            const directItems = projectItems.filter(
              (item) =>
                !String(
                  item.subproject || ""
                ).trim()
            )

            const subMap = new Map<
              string,
              MemoryProject[]
            >()

            projectItems
              .filter((item) =>
                Boolean(
                  String(
                    item.subproject || ""
                  ).trim()
                )
              )
              .forEach((item) => {
                const subproject = String(
                  item.subproject
                ).trim()

                if (!subMap.has(subproject)) {
                  subMap.set(subproject, [])
                }

                subMap.get(subproject)!.push(item)
              })

            return {
              key: projectKey(
                organisme,
                project
              ),
              name: project,
              items: projectItems,
              directItems,
              subprojects: Array.from(
                subMap.entries()
              )
                .sort(([left], [right]) =>
                  left.localeCompare(
                    right,
                    "fr"
                  )
                )
                .map(
                  ([subproject, subItems]) => ({
                    key: subprojectKey(
                      organisme,
                      project,
                      subproject
                    ),
                    name: subproject,
                    items: subItems,
                  })
                ),
            }
          }),
      }
    })
}

function Pill({
  children,
  tone = "slate",
}: {
  children: React.ReactNode
  tone?:
    | "slate"
    | "green"
    | "amber"
    | "violet"
}) {
  const tones = {
    slate:
      "border-border bg-muted/50 text-muted-foreground",
    green:
      "border-success/25 bg-success/8 text-success",
    amber:
      "border-warning/25 bg-warning/8 text-warning-foreground",
    violet:
      "border-brand/25 bg-brand/8 text-brand",
  }

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

function PrimaryButton({
  children,
  onClick,
  disabled,
  type = "button",
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: "button" | "submit"
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-xs transition-colors hover:bg-primary/90 focus-visible:ring-3 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  )
}

function SecondaryButton({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-card px-4 py-2.5 text-sm font-semibold text-foreground shadow-xs transition-colors hover:border-brand/25 hover:bg-brand/5 focus-visible:ring-3 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {children}
    </button>
  )
}

function StatCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode
  label: string
  value: string
  hint: string
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-xs">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[.12em] text-muted-foreground">
            {label}
          </p>
          <p className="mt-2 text-2xl font-bold tracking-tight text-foreground">
            {value}
          </p>
        </div>

        <div className="rounded-lg bg-brand/8 p-2.5 text-brand">
          {icon}
        </div>
      </div>

      <p className="mt-2 text-xs text-muted-foreground">
        {hint}
      </p>
    </div>
  )
}

function YearCirRows({
  items,
  selectedId,
  onSelect,
  onOpen,
}: {
  items: MemoryProject[]
  selectedId: string
  onSelect: (item: MemoryProject) => void
  onOpen: (item: MemoryProject) => void
}) {
  const years = groupByYear(items)

  return (
    <div className="space-y-1.5">
      {years.map(({ year, rows }) => (
        <div
          key={year}
          className="rounded-xl border border-slate-100 bg-slate-50/65 p-2"
        >
          <div className="flex items-center gap-2 px-1 pb-1.5">
            <span className="rounded-lg border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-bold text-violet-700">
              {year}
            </span>
            <span className="text-[10px] text-slate-400">
              {rows.length} CIR
            </span>
          </div>

          <div className="space-y-1">
            {rows.map((item) => {
              const source = primarySource(item)
              const filename =
                item.indexed_file_name ||
                source?.file_name ||
                "CIR final à ajouter"

              const selected =
                selectedId === item.id

              return (
                <div
                  key={item.id}
                  className={`group flex items-center gap-2 rounded-lg border px-2.5 py-2 transition ${
                    selected
                      ? "border-violet-300 bg-white shadow-sm ring-1 ring-violet-100"
                      : "border-transparent bg-white/70 hover:border-violet-200 hover:bg-white"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(item)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <div
                      className={`grid size-8 shrink-0 place-items-center rounded-lg ${
                        item.indexed
                          ? "bg-emerald-50 text-emerald-700"
                          : "bg-amber-50 text-amber-700"
                      }`}
                    >
                      {item.indexed ? (
                        <FileCheck2 className="size-4" />
                      ) : (
                        <Clock3 className="size-4" />
                      )}
                    </div>

                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[11px] font-semibold text-slate-800">
                        {filename}
                      </p>

                      <div className="mt-0.5 flex items-center gap-1.5">
                        <span className="text-[9px] font-medium text-slate-400">
                          {fileExtension(filename)}
                        </span>

                        {source ? (
                          <span className="text-[9px] text-slate-400">
                            · {source.size_mb} Mo
                          </span>
                        ) : null}

                        <span
                          className={`text-[9px] font-medium ${
                            item.indexed
                              ? "text-emerald-600"
                              : "text-amber-600"
                          }`}
                        >
                          ·{" "}
                          {item.indexed
                            ? "Indexé"
                            : "À traiter"}
                        </span>
                      </div>
                    </div>
                  </button>

                  {source ? (
                    <button
                      type="button"
                      onClick={() => onOpen(item)}
                      className="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg border border-violet-200 bg-violet-50 px-2 text-[9px] font-semibold text-violet-700 opacity-100 transition hover:bg-violet-100 sm:opacity-0 sm:group-hover:opacity-100"
                      title="Ouvrir le CIR"
                    >
                      <ExternalLink className="size-3" />
                      Ouvrir
                    </button>
                  ) : null}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function CirMemoryPage() {
  const [tab, setTab] =
    useState<Tab>("library")
  const [catalog, setCatalog] =
    useState<Catalog | null>(null)
  const [selectedId, setSelectedId] =
    useState("")
  const [filter, setFilter] =
    useState("")
  const [
    organisationFilter,
    setOrganisationFilter,
  ] = useState("all")
  const [yearFilter, setYearFilter] =
    useState("all")
  const [loading, setLoading] =
    useState(false)
  const [notice, setNotice] =
    useState("")
  const [error, setError] =
    useState("")
  const [form, setForm] = useState({
    organisme: "",
    project: "",
    subproject: "",
    year: String(
      new Date().getFullYear()
    ),
  })
  const [file, setFile] =
    useState<File | null>(null)
  const [query, setQuery] =
    useState("")
  const [searchRole, setSearchRole] =
    useState("")
  const [
    searchOrganisation,
    setSearchOrganisation,
  ] = useState("")
  const [matches, setMatches] =
    useState<SearchMatch[]>([])
  const [deleteOpen, setDeleteOpen] =
    useState(false)
  const [
    deleteConfirmation,
    setDeleteConfirmation,
  ] = useState("")

  const [
    expandedOrganisms,
    setExpandedOrganisms,
  ] = useState<Set<string>>(
    () => new Set()
  )

  const [
    expandedProjects,
    setExpandedProjects,
  ] = useState<Set<string>>(
    () => new Set()
  )

  const [
    expandedSubprojects,
    setExpandedSubprojects,
  ] = useState<Set<string>>(
    () => new Set()
  )

  const [preview, setPreview] =
    useState<PreviewState>(
      EMPTY_PREVIEW
    )

  const previewUrlRef =
    useRef("")

  const selected = useMemo(
    () =>
      catalog?.projects.find(
        (item) =>
          item.id === selectedId
      ) || null,
    [catalog, selectedId]
  )

  const years = useMemo(
    () =>
      Array.from(
        new Set(
          (catalog?.projects || []).map(
            (item) => item.year
          )
        )
      )
        .sort()
        .reverse(),
    [catalog]
  )

  const visibleProjects = useMemo(() => {
    const needle = filter
      .trim()
      .toLocaleLowerCase("fr")

    return (catalog?.projects || []).filter(
      (item) => {
        if (
          organisationFilter !== "all" &&
          item.organisme !==
            organisationFilter
        ) {
          return false
        }

        if (
          yearFilter !== "all" &&
          item.year !== yearFilter
        ) {
          return false
        }

        return (
          !needle ||
          `${item.organisme} ${item.project} ${
            item.subproject || ""
          } ${item.year} ${
            item.indexed_file_name || ""
          } ${item.source_files
            .map(
              (source) =>
                source.file_name
            )
            .join(" ")}`
            .toLocaleLowerCase("fr")
            .includes(needle)
        )
      }
    )
  }, [
    catalog,
    filter,
    organisationFilter,
    yearFilter,
  ])

  const tree = useMemo(
    () => buildTree(visibleProjects),
    [visibleProjects]
  )

  const visibleUniqueProjects =
    useMemo(() => {
      return new Set(
        visibleProjects.map((item) =>
          projectKey(
            item.organisme,
            item.project
          )
        )
      ).size
    }, [visibleProjects])

  const selectedSource =
    selected
      ? primarySource(selected)
      : null

  function success(message: string) {
    setNotice(message)
    setError("")
  }

  function failure(reason: unknown) {
    setError(
      reason instanceof Error
        ? reason.message
        : String(reason)
    )
    setNotice("")
  }

  function toggleSet(
    setter: React.Dispatch<
      React.SetStateAction<Set<string>>
    >,
    key: string
  ) {
    setter((current) => {
      const next = new Set(current)

      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }

      return next
    })
  }

  function expandPath(
    item: MemoryProject
  ) {
    setExpandedOrganisms(
      (current) =>
        new Set([
          ...current,
          item.organisme,
        ])
    )

    setExpandedProjects(
      (current) =>
        new Set([
          ...current,
          projectKey(
            item.organisme,
            item.project
          ),
        ])
    )

    if (item.subproject) {
      setExpandedSubprojects(
        (current) =>
          new Set([
            ...current,
            subprojectKey(
              item.organisme,
              item.project,
              item.subproject || ""
            ),
          ])
      )
    }
  }

  function selectProject(
    item: MemoryProject
  ) {
    setSelectedId(item.id)
    setDeleteOpen(false)
    setDeleteConfirmation("")
    expandPath(item)
  }

  async function loadCatalog(
    quiet = false
  ) {
    if (!quiet) setLoading(true)

    try {
      const result =
        await api<Catalog>(
          "/cir-memory/v2/catalog"
        )

      setCatalog(result)

      const first =
        result.projects.find(
          (item) => item.indexed
        ) || result.projects[0]

      if (
        !selectedId &&
        first
      ) {
        setSelectedId(first.id)
        expandPath(first)
      }

      if (!quiet) {
        success(
          `${result.stats.indexed_projects_count} CIR finaux connectés à Memory V2.`
        )
      }
    } catch (reason) {
      failure(reason)
    } finally {
      if (!quiet) {
        setLoading(false)
      }
    }
  }

  async function createSlot() {
    if (
      !form.organisme.trim() ||
      !form.project.trim() ||
      !form.year.trim()
    ) {
      return failure(
        "Renseigne l’entreprise, le projet et l’année."
      )
    }

    setLoading(true)

    try {
      await api(
        "/cir-memory/v2/library",
        {
          method: "POST",
          body: JSON.stringify(form),
        }
      )

      success(
        "L’identité est valide. Aucun dossier ni duplicata du CIR n’a été créé."
      )

      await loadCatalog(true)
    } catch (reason) {
      failure(reason)
    } finally {
      setLoading(false)
    }
  }

  async function uploadAndIndex(
    event: React.FormEvent
  ) {
    event.preventDefault()

    if (!file) {
      return failure(
        "Choisis le CIR final validé à importer."
      )
    }

    if (
      !form.organisme.trim() ||
      !form.project.trim() ||
      !form.year.trim()
    ) {
      return failure(
        "Renseigne l’entreprise, le projet et l’année."
      )
    }

    setLoading(true)
    setNotice(
      "Extraction, analyse NLP et reconstruction de la base vectorielle en cours…"
    )
    setError("")

    try {
      const data = new FormData()

      data.append("file", file)
      data.append(
        "organisme",
        form.organisme.trim()
      )
      data.append(
        "project",
        form.project.trim()
      )
      data.append(
        "subproject",
        form.subproject.trim()
      )
      data.append(
        "year",
        form.year.trim()
      )
      data.append(
        "vision_mode",
        "text_only"
      )
      data.append(
        "formula_mode",
        "off"
      )

      const result =
        await api<any>(
          "/cir-memory/v2/upload",
          {
            method: "POST",
            body: data,
          }
        )

      success(
        `CIR indexé : ${formatNumber(
          result?.chunks_count
        )} passages et ${formatNumber(
          result?.cards_count
        )} cartes ajoutés.`
      )

      setFile(null)
      await loadCatalog(true)
      setTab("library")
    } catch (reason) {
      failure(reason)
    } finally {
      setLoading(false)
    }
  }

  async function processExisting(
    project: MemoryProject
  ) {
    setLoading(true)
    setNotice(
      "Traitement du CIR existant et reconstruction de Memory V2 en cours…"
    )
    setError("")

    try {
      await api(
        "/cir-memory/v2/process-existing",
        {
          method: "POST",
          body: JSON.stringify({
            organisme:
              project.organisme,
            project:
              project.project,
            subproject:
              project.subproject ||
              "",
            year: project.year,
            file_name:
              project.source_files[0]
                ?.file_name || "",
          }),
        }
      )

      success(
        "Le CIR existant est maintenant indexé dans la vraie base vectorielle."
      )

      await loadCatalog(true)
    } catch (reason) {
      failure(reason)
    } finally {
      setLoading(false)
    }
  }

  async function rebuild() {
    setLoading(true)
    setNotice(
      "Reconstruction du catalogue, du graphe et de la collection Chroma globale…"
    )
    setError("")

    try {
      await api(
        "/cir-memory/v2/rebuild",
        {
          method: "POST",
        }
      )

      success(
        "Memory V2 a été reconstruite depuis les fichiers déjà validés."
      )

      await loadCatalog(true)
    } catch (reason) {
      failure(reason)
    } finally {
      setLoading(false)
    }
  }

  async function removeProject(
    project: MemoryProject
  ) {
    if (
      deleteConfirmation.trim() !==
      project.project
    ) {
      return failure(
        `Écris exactement « ${project.project} » pour confirmer.`
      )
    }

    setLoading(true)
    setNotice(
      "Suppression de la mémoire active et reconstruction de Chroma en cours…"
    )
    setError("")

    try {
      const result =
        await api<any>(
          "/cir-memory/v2/projects/remove",
          {
            method: "POST",
            body: JSON.stringify({
              organisme:
                project.organisme,
              project:
                project.project,
              subproject:
                project.subproject ||
                "",
              year: project.year,
              confirmation:
                "SUPPRIMER_DE_MEMORY_V2",
            }),
          }
        )

      const nextCatalog =
        result?.catalog as
          | Catalog
          | undefined

      if (nextCatalog) {
        setCatalog(nextCatalog)
        setSelectedId(
          nextCatalog.projects[0]
            ?.id || ""
        )
      } else {
        setSelectedId("")
        await loadCatalog(true)
      }

      setDeleteOpen(false)
      setDeleteConfirmation("")

      success(
        `Le projet ${project.project} (${project.year}) a été retiré de toute la mémoire locale. L’archive de récupération a été conservée.`
      )
    } catch (reason) {
      failure(reason)
    } finally {
      setLoading(false)
    }
  }

  async function runSearch(
    event: React.FormEvent
  ) {
    event.preventDefault()

    if (!query.trim()) {
      return failure(
        "Écris une question pour interroger la mémoire."
      )
    }

    setLoading(true)

    try {
      const result =
        await api<any>(
          "/cir-memory/v2/search",
          {
            method: "POST",
            body: JSON.stringify({
              query: query.trim(),
              organisme:
                searchOrganisation,
              role: searchRole,
              top_k: 10,
            }),
          }
        )

      setMatches(
        Array.isArray(
          result?.matches
        )
          ? result.matches
          : []
      )

      success(
        `${result?.matches_count || 0} résultat(s) trouvé(s) dans Memory V2.`
      )
    } catch (reason) {
      failure(reason)
    } finally {
      setLoading(false)
    }
  }

  function revokePreviewUrl() {
    if (!previewUrlRef.current) {
      return
    }

    URL.revokeObjectURL(
      previewUrlRef.current
    )

    previewUrlRef.current = ""
  }

  async function openCir(
    item: MemoryProject
  ) {
    const source =
      primarySource(item)

    if (!source) {
      return failure(
        "Aucun CIR final n’est disponible pour cette entrée."
      )
    }

    selectProject(item)

    setPreview({
      open: true,
      loading: true,
      error: "",
      objectUrl: "",
      mediaType: "",
      fileName:
        item.indexed_file_name ||
        source.file_name,
    })

    try {
      const token = authToken()
      const headers = new Headers()

      if (token) {
        headers.set(
          "Authorization",
          `Bearer ${token}`
        )
      }

      const response = await fetch(
        `${API_BASE}/cir-memory/v2/projects/${encodeURIComponent(
          item.id
        )}/source-preview`,
        {
          headers,
          credentials: "include",
          cache: "no-store",
        }
      )

      if (!response.ok) {
        let detail = ""

        try {
          const payload =
            await response.json()

          detail = String(
            payload?.detail || ""
          )
        } catch {
          detail =
            await response
              .text()
              .catch(() => "")
        }

        throw new Error(
          detail ||
            `Ouverture impossible (HTTP ${response.status}).`
        )
      }

      const blob =
        await response.blob()
      const objectUrl =
        URL.createObjectURL(blob)

      revokePreviewUrl()
      previewUrlRef.current =
        objectUrl

      setPreview({
        open: true,
        loading: false,
        error: "",
        objectUrl,
        mediaType:
          response.headers.get(
            "Content-Type"
          ) ||
          blob.type ||
          "application/pdf",
        fileName:
          item.indexed_file_name ||
          source.file_name,
      })
    } catch (reason) {
      setPreview({
        open: true,
        loading: false,
        error:
          reason instanceof Error
            ? reason.message
            : String(reason),
        objectUrl: "",
        mediaType: "",
        fileName:
          item.indexed_file_name ||
          source.file_name,
      })
    }
  }

  async function downloadCir(
    item: MemoryProject
  ) {
    const source =
      primarySource(item)

    if (!source) {
      return failure(
        "Aucun fichier source disponible."
      )
    }

    try {
      const token = authToken()
      const headers = new Headers()

      if (token) {
        headers.set(
          "Authorization",
          `Bearer ${token}`
        )
      }

      const response = await fetch(
        `${API_BASE}/cir-memory/v2/projects/${encodeURIComponent(
          item.id
        )}/source-download`,
        {
          headers,
          credentials: "include",
        }
      )

      if (!response.ok) {
        throw new Error(
          `Téléchargement impossible (HTTP ${response.status}).`
        )
      }

      const blob =
        await response.blob()
      const url =
        URL.createObjectURL(blob)

      const anchor =
        document.createElement("a")

      anchor.href = url
      anchor.download =
        item.indexed_file_name ||
        source.file_name

      anchor.click()

      window.setTimeout(() => {
        URL.revokeObjectURL(url)
      }, 1000)
    } catch (reason) {
      failure(reason)
    }
  }

  function closePreview() {
    revokePreviewUrl()
    setPreview(EMPTY_PREVIEW)
  }

  useEffect(() => {
    void loadCatalog()

    return () => {
      revokePreviewUrl()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const stats = catalog?.stats

  const agentsConnected =
    catalog
      ? Object.values(
          catalog.ai_connections
        ).filter(
          (value) => value === true
        ).length
      : 0

  return (
    <div className="workspace-page-wide min-h-full space-y-6">
      <PageHeader
        eyebrow="Memory V2 · CIR validés"
        title="Mémoire CIR"
        description="La base d'expérience commune du cabinet : style de rédaction, comparaison historique et projets similaires pour les agents Ennoma."
        icon={Database}
        context={
          <>
            <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
              <ShieldCheck className="size-4 text-brand" />
              CIR finaux validés uniquement
            </span>

            <span
              className={`inline-flex items-center gap-2 text-xs font-medium ${
                catalog?.vector_db.exists
                  ? "text-success"
                  : "text-warning-foreground"
              }`}
            >
              <span className="size-2 rounded-full bg-current" />
              {catalog?.vector_db.exists
                ? "Chroma connecté"
                : "Chroma absent"}
            </span>
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          icon={
            <Building2 className="size-5" />
          }
          label="Entreprises"
          value={formatNumber(
            stats?.organisms_count
          )}
          hint="Bases d’expérience partagées"
        />

        <StatCard
          icon={
            <FileCheck2 className="size-5" />
          }
          label="CIR indexés"
          value={formatNumber(
            stats?.indexed_projects_count
          )}
          hint="Projets et années validés"
        />

        <StatCard
          icon={
            <Database className="size-5" />
          }
          label="Vecteurs"
          value={formatNumber(
            stats?.vector_items_count
          )}
          hint="Collection globale Memory V2"
        />

        <StatCard
          icon={
            <BrainCircuit className="size-5" />
          }
          label="Agents reliés"
          value={`${agentsConnected}/3`}
          hint="Diagnostic, comparaison, style"
        />
      </div>

      {notice ? (
        <StatusNotice
          state="validated"
          title={notice}
        />
      ) : null}

      {error ? (
        <StatusNotice
          state="failed"
          title="Opération impossible"
          description={error}
        />
      ) : null}

      <nav className="flex w-full gap-1 overflow-x-auto rounded-xl border border-border bg-card p-1.5 shadow-xs sm:w-fit">
        {(
          [
            [
              "library",
              "Bibliothèque",
              Database,
            ],
            [
              "add",
              "Ajouter un CIR",
              UploadCloud,
            ],
            [
              "power-automate",
              "Collecte automatique",
              Cloud,
            ],
            [
              "search",
              "Recherche",
              Search,
            ],
          ] as const
        ).map(
          ([
            value,
            label,
            Icon,
          ]) => (
            <button
              key={value}
              onClick={() =>
                setTab(value)
              }
              className={`flex flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-4 py-2.5 text-sm font-semibold transition-colors sm:flex-none ${
                tab === value
                  ? "bg-primary text-primary-foreground shadow-xs"
                  : "text-muted-foreground hover:bg-brand/5 hover:text-foreground"
              }`}
            >
              <Icon className="size-4" />
              {label}
            </button>
          )
        )}
      </nav>

      {tab === "library" ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(330px,.55fr)]">
          <section className="rounded-3xl border border-violet-100 bg-white p-5 shadow-[0_12px_36px_rgba(50,20,90,.06)] sm:p-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h2 className="text-xl font-semibold tracking-tight text-slate-900">
                  CIR de la mémoire
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  {visibleProjects.length} CIR
                  {" · "}
                  {visibleUniqueProjects} projet(s)
                  {" · "}
                  {tree.length} entreprise(s)
                </p>
              </div>

              <SecondaryButton
                onClick={() =>
                  loadCatalog()
                }
                disabled={loading}
              >
                <RefreshCw
                  className={`size-4 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />
                Actualiser
              </SecondaryButton>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px_130px]">
              <label className="relative">
                <Search className="absolute left-3 top-3 size-4 text-slate-400" />

                <input
                  value={filter}
                  onChange={(event) =>
                    setFilter(
                      event.target.value
                    )
                  }
                  placeholder="Rechercher projet, sous-projet ou CIR…"
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2.5 pl-9 pr-3 text-sm outline-none transition focus:border-violet-400 focus:bg-white focus:ring-4 focus:ring-violet-100"
                />
              </label>

              <select
                value={
                  organisationFilter
                }
                onChange={(event) =>
                  setOrganisationFilter(
                    event.target.value
                  )
                }
                className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-violet-400"
              >
                <option value="all">
                  Toutes les entreprises
                </option>

                {catalog?.organisms.map(
                  (item) => (
                    <option key={item}>
                      {item}
                    </option>
                  )
                )}
              </select>

              <select
                value={yearFilter}
                onChange={(event) =>
                  setYearFilter(
                    event.target.value
                  )
                }
                className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-violet-400"
              >
                <option value="all">
                  Toutes années
                </option>

                {years.map((item) => (
                  <option key={item}>
                    {item}
                  </option>
                ))}
              </select>
            </div>

            <div className="mt-5 max-h-[690px] space-y-2 overflow-auto pr-1">
              {tree.map(
                (organism) => {
                  const organismOpen =
                    expandedOrganisms.has(
                      organism.key
                    ) ||
                    Boolean(filter.trim())

                  return (
                    <div
                      key={organism.key}
                      className="overflow-hidden rounded-2xl border border-slate-200 bg-white"
                    >
                      <button
                        type="button"
                        onClick={() =>
                          toggleSet(
                            setExpandedOrganisms,
                            organism.key
                          )
                        }
                        className="flex w-full items-center gap-3 bg-slate-50/70 px-4 py-3 text-left transition hover:bg-violet-50/60"
                      >
                        <div className="grid size-9 place-items-center rounded-xl bg-violet-100 text-violet-700">
                          {organismOpen ? (
                            <FolderOpen className="size-4.5" />
                          ) : (
                            <Folder className="size-4.5" />
                          )}
                        </div>

                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-bold uppercase tracking-[.08em] text-violet-700">
                            {organism.name}
                          </p>

                          <p className="mt-0.5 text-[10px] text-slate-400">
                            {
                              organism.projects
                                .length
                            }{" "}
                            projet(s) ·{" "}
                            {
                              organism.items
                                .length
                            }{" "}
                            CIR
                          </p>
                        </div>

                        {organismOpen ? (
                          <ChevronDown className="size-4 text-slate-400" />
                        ) : (
                          <ChevronRight className="size-4 text-slate-400" />
                        )}
                      </button>

                      {organismOpen ? (
                        <div className="space-y-2 border-t border-slate-100 p-2.5">
                          {organism.projects.map(
                            (projectGroup) => {
                              const projectOpen =
                                expandedProjects.has(
                                  projectGroup.key
                                ) ||
                                Boolean(
                                  filter.trim()
                                )

                              return (
                                <div
                                  key={
                                    projectGroup.key
                                  }
                                  className="rounded-xl border border-slate-100 bg-white"
                                >
                                  <button
                                    type="button"
                                    onClick={() =>
                                      toggleSet(
                                        setExpandedProjects,
                                        projectGroup.key
                                      )
                                    }
                                    className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left hover:bg-slate-50"
                                  >
                                    {projectOpen ? (
                                      <ChevronDown className="size-3.5 text-violet-500" />
                                    ) : (
                                      <ChevronRight className="size-3.5 text-slate-400" />
                                    )}

                                    <div className="grid size-7 place-items-center rounded-lg bg-indigo-50 text-indigo-600">
                                      <Folder className="size-3.5" />
                                    </div>

                                    <div className="min-w-0 flex-1">
                                      <p className="truncate text-[12px] font-semibold text-slate-850">
                                        {
                                          projectGroup.name
                                        }
                                      </p>
                                      <p className="text-[9px] text-slate-400">
                                        {
                                          projectGroup.items
                                            .length
                                        }{" "}
                                        CIR
                                      </p>
                                    </div>
                                  </button>

                                  {projectOpen ? (
                                    <div className="space-y-2 border-t border-slate-100 px-3 py-2.5 pl-8">
                                      {projectGroup.directItems.length >
                                      0 ? (
                                        <YearCirRows
                                          items={
                                            projectGroup.directItems
                                          }
                                          selectedId={
                                            selectedId
                                          }
                                          onSelect={
                                            selectProject
                                          }
                                          onOpen={
                                            openCir
                                          }
                                        />
                                      ) : null}

                                      {projectGroup.subprojects.map(
                                        (
                                          subproject
                                        ) => {
                                          const subOpen =
                                            expandedSubprojects.has(
                                              subproject.key
                                            ) ||
                                            Boolean(
                                              filter.trim()
                                            )

                                          return (
                                            <div
                                              key={
                                                subproject.key
                                              }
                                              className="rounded-xl border border-violet-100 bg-violet-50/20"
                                            >
                                              <button
                                                type="button"
                                                onClick={() =>
                                                  toggleSet(
                                                    setExpandedSubprojects,
                                                    subproject.key
                                                  )
                                                }
                                                className="flex w-full items-center gap-2 px-3 py-2 text-left"
                                              >
                                                {subOpen ? (
                                                  <ChevronDown className="size-3 text-violet-500" />
                                                ) : (
                                                  <ChevronRight className="size-3 text-slate-400" />
                                                )}

                                                <span className="text-[10px] font-semibold text-violet-700">
                                                  Sous-projet
                                                </span>

                                                <span className="min-w-0 flex-1 truncate text-[11px] font-semibold text-slate-700">
                                                  {
                                                    subproject.name
                                                  }
                                                </span>

                                                <span className="text-[9px] text-slate-400">
                                                  {
                                                    subproject.items
                                                      .length
                                                  }{" "}
                                                  CIR
                                                </span>
                                              </button>

                                              {subOpen ? (
                                                <div className="border-t border-violet-100 p-2">
                                                  <YearCirRows
                                                    items={
                                                      subproject.items
                                                    }
                                                    selectedId={
                                                      selectedId
                                                    }
                                                    onSelect={
                                                      selectProject
                                                    }
                                                    onOpen={
                                                      openCir
                                                    }
                                                  />
                                                </div>
                                              ) : null}
                                            </div>
                                          )
                                        }
                                      )}
                                    </div>
                                  ) : null}
                                </div>
                              )
                            }
                          )}
                        </div>
                      ) : null}
                    </div>
                  )
                }
              )}

              {!visibleProjects.length ? (
                <div className="rounded-2xl border border-dashed border-slate-200 py-12 text-center text-sm text-slate-500">
                  Aucun CIR ne correspond aux filtres.
                </div>
              ) : null}
            </div>
          </section>

          <aside className="h-fit rounded-3xl border border-violet-100 bg-white p-5 shadow-[0_12px_36px_rgba(50,20,90,.06)] sm:p-6 lg:sticky lg:top-6">
            {selected ? (
              <div>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-bold uppercase tracking-[.14em] text-violet-600">
                      {selected.organisme}
                    </p>

                    <h2 className="mt-2 truncate text-2xl font-semibold tracking-tight text-slate-900">
                      {selected.project}
                    </h2>

                    {selected.subproject ? (
                      <p className="mt-1 text-sm font-medium text-violet-700">
                        Sous-projet ·{" "}
                        {selected.subproject}
                      </p>
                    ) : null}

                    <p className="mt-1 text-sm text-slate-500">
                      Exercice{" "}
                      {selected.year}
                    </p>
                  </div>

                  {selected.indexed ? (
                    <CheckCircle2 className="size-6 shrink-0 text-emerald-600" />
                  ) : (
                    <Clock3 className="size-6 shrink-0 text-amber-600" />
                  )}
                </div>

                <div className="mt-5 flex flex-wrap items-center gap-1 text-[10px] text-slate-400">
                  <span>
                    {selected.organisme}
                  </span>
                  <ChevronRight className="size-3" />
                  <span>
                    {selected.project}
                  </span>

                  {selected.subproject ? (
                    <>
                      <ChevronRight className="size-3" />
                      <span>
                        {selected.subproject}
                      </span>
                    </>
                  ) : null}

                  <ChevronRight className="size-3" />
                  <span>
                    {selected.year}
                  </span>
                </div>

                <div className="mt-5 rounded-2xl border border-violet-100 bg-[linear-gradient(135deg,rgba(250,248,255,.95),rgba(246,243,255,.90))] p-4">
                  <div className="flex items-start gap-3">
                    <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-white text-violet-700 shadow-sm">
                      <FileText className="size-5" />
                    </div>

                    <div className="min-w-0 flex-1">
                      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                        CIR final
                      </p>

                      <p className="mt-1 break-words text-sm font-semibold leading-5 text-slate-800">
                        {selected.indexed_file_name ||
                          selectedSource?.file_name ||
                          "Aucun fichier ajouté"}
                      </p>

                      {selectedSource ? (
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
                          <span className="rounded-md bg-white px-1.5 py-0.5 font-semibold text-violet-700">
                            {fileExtension(
                              selected.indexed_file_name ||
                                selectedSource.file_name
                            )}
                          </span>

                          <span>
                            {
                              selectedSource.size_mb
                            }{" "}
                            Mo
                          </span>
                        </div>
                      ) : null}
                    </div>
                  </div>

                  {selectedSource ? (
                    <div className="mt-4 grid grid-cols-2 gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          openCir(selected)
                        }
                        className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-3 py-2.5 text-xs font-semibold text-white shadow-sm transition hover:bg-violet-800"
                      >
                        <ExternalLink className="size-3.5" />
                        Ouvrir le CIR
                      </button>

                      <button
                        type="button"
                        onClick={() =>
                          downloadCir(
                            selected
                          )
                        }
                        className="inline-flex items-center justify-center gap-2 rounded-xl border border-violet-200 bg-white px-3 py-2.5 text-xs font-semibold text-violet-700 transition hover:bg-violet-50"
                      >
                        <Download className="size-3.5" />
                        Original
                      </button>
                    </div>
                  ) : null}
                </div>

                {selected.indexed ? (
                  <div className="mt-5 grid grid-cols-2 gap-3">
                    <div className="rounded-2xl border border-slate-100 p-3">
                      <p className="text-xl font-bold text-slate-900">
                        {formatNumber(
                          selected.chunks_count
                        )}
                      </p>

                      <p className="text-xs text-slate-500">
                        passages
                      </p>
                    </div>

                    <div className="rounded-2xl border border-slate-100 p-3">
                      <p className="text-xl font-bold text-slate-900">
                        {formatNumber(
                          selected.cards_count
                        )}
                      </p>

                      <p className="text-xs text-slate-500">
                        cartes
                      </p>
                    </div>
                  </div>
                ) : null}

                <div className="mt-5">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                    Contenu mémorisé
                  </p>

                  <div className="flex flex-wrap gap-2">
                    {Object.entries(
                      selected.role_counts ||
                        {}
                    )
                      .sort(
                        (a, b) =>
                          b[1] - a[1]
                      )
                      .map(
                        ([
                          role,
                          count,
                        ]) => (
                          <Pill
                            key={role}
                          >
                            {labelForRole(
                              role
                            )}{" "}
                            · {count}
                          </Pill>
                        )
                      )}
                  </div>
                </div>

                {!selected.indexed &&
                selected.source_files
                  .length > 0 ? (
                  <div className="mt-5">
                    <PrimaryButton
                      onClick={() =>
                        processExisting(
                          selected
                        )
                      }
                      disabled={loading}
                    >
                      <Sparkles className="size-4" />
                      Indexer ce CIR existant
                    </PrimaryButton>
                  </div>
                ) : null}

                {!selected.indexed &&
                selected.source_files
                  .length === 0 ? (
                  <div className="mt-5">
                    <PrimaryButton
                      onClick={() => {
                        setForm({
                          organisme:
                            selected.organisme,
                          project:
                            selected.project,
                          subproject:
                            selected.subproject ||
                            "",
                          year: selected.year,
                        })
                        setTab("add")
                      }}
                    >
                      <UploadCloud className="size-4" />
                      Ajouter le CIR final
                    </PrimaryButton>
                  </div>
                ) : null}

                <p className="mt-5 text-xs leading-5 text-slate-400">
                  Dernière indexation :{" "}
                  {formatDate(
                    selected.indexed_at
                  )}
                </p>

                <div className="mt-6 border-t border-slate-100 pt-5">
                  {!deleteOpen ? (
                    <button
                      type="button"
                      onClick={() => {
                        setDeleteOpen(true)
                        setDeleteConfirmation(
                          ""
                        )
                      }}
                      className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-rose-200 bg-white px-4 py-2.5 text-sm font-semibold text-rose-700 transition hover:bg-rose-50"
                    >
                      <Trash2 className="size-4" />
                      Supprimer ce projet de Memory V2
                    </button>
                  ) : (
                    <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
                      <p className="text-sm font-semibold text-rose-900">
                        Suppression locale complète
                      </p>

                      <p className="mt-1 text-xs leading-5 text-rose-700">
                        Ses passages, cartes, relations et vecteurs Chroma seront retirés. SharePoint et le dossier Power Automate ne seront jamais modifiés. Une archive récupérable restera dans la mémoire externe, hors du dépôt.
                      </p>

                      <label className="mt-3 block">
                        <span className="text-xs font-medium text-rose-800">
                          Écris «{" "}
                          {selected.project}{" "}
                          » pour confirmer
                        </span>

                        <input
                          value={
                            deleteConfirmation
                          }
                          onChange={(
                            event
                          ) =>
                            setDeleteConfirmation(
                              event
                                .target
                                .value
                            )
                          }
                          className="mt-1.5 w-full rounded-xl border border-rose-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-rose-400 focus:ring-4 focus:ring-rose-100"
                        />
                      </label>

                      <div className="mt-3 grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            setDeleteOpen(
                              false
                            )
                            setDeleteConfirmation(
                              ""
                            )
                          }}
                          disabled={loading}
                          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50"
                        >
                          Annuler
                        </button>

                        <button
                          type="button"
                          onClick={() =>
                            removeProject(
                              selected
                            )
                          }
                          disabled={
                            loading ||
                            deleteConfirmation.trim() !==
                              selected.project
                          }
                          className="inline-flex items-center justify-center gap-2 rounded-xl bg-rose-700 px-3 py-2 text-sm font-semibold text-white hover:bg-rose-800 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {loading ? (
                            <RefreshCw className="size-4 animate-spin" />
                          ) : (
                            <Trash2 className="size-4" />
                          )}
                          Supprimer
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="grid min-h-[300px] place-items-center text-center">
                <div>
                  <Folder className="mx-auto size-9 text-violet-300" />
                  <p className="mt-3 text-sm font-semibold text-slate-700">
                    Sélectionne un CIR
                  </p>
                  <p className="mt-1 text-xs text-slate-400">
                    Projet → sous-projet → année → CIR
                  </p>
                </div>
              </div>
            )}
          </aside>
        </div>
      ) : null}

      {tab === "add" ? (
        <section className="mx-auto max-w-3xl rounded-3xl border border-violet-100 bg-white p-6 shadow-[0_16px_45px_rgba(50,20,90,.07)] sm:p-8">
          <div className="flex items-start gap-4">
            <div className="rounded-2xl bg-violet-100 p-3 text-violet-700">
              <UploadCloud className="size-6" />
            </div>

            <div>
              <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
                Ajouter un CIR final
              </h2>

              <p className="mt-1 text-sm leading-6 text-slate-500">
                L’organisme et le projet sont obligatoires. Le sous-projet reste facultatif.
              </p>
            </div>
          </div>

          <form
            onSubmit={uploadAndIndex}
            className="mt-7 space-y-5"
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="space-y-2">
                <span className="text-sm font-semibold text-slate-700">
                  Organisme *
                </span>

                <input
                  list="memory-organisms"
                  value={form.organisme}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      organisme:
                        event.target.value,
                    })
                  }
                  placeholder="Ex. 6NAPSE GROUP"
                  required
                  className="min-h-11 w-full rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100"
                />

                <datalist id="memory-organisms">
                  {catalog?.organisms.map(
                    (item) => (
                      <option
                        key={item}
                        value={item}
                      />
                    )
                  )}
                </datalist>
              </label>

              <label className="space-y-2">
                <span className="text-sm font-semibold text-slate-700">
                  Projet *
                </span>

                <input
                  value={form.project}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      project:
                        event.target.value,
                    })
                  }
                  placeholder="Ex. CEVAA"
                  required
                  className="min-h-11 w-full rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100"
                />
              </label>

              <label className="space-y-2">
                <span className="text-sm font-semibold text-slate-700">
                  Sous-projet{" "}
                  <span className="font-normal text-slate-400">
                    (facultatif)
                  </span>
                </span>

                <input
                  value={form.subproject}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      subproject:
                        event.target.value,
                    })
                  }
                  placeholder="Ex. Clip Fam"
                  className="min-h-11 w-full rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100"
                />
              </label>

              <label className="space-y-2">
                <span className="text-sm font-semibold text-slate-700">
                  Année *
                </span>

                <input
                  value={form.year}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      year:
                        event.target.value,
                    })
                  }
                  inputMode="numeric"
                  placeholder="2024"
                  required
                  className="min-h-11 w-full rounded-xl border border-slate-200 px-3.5 py-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100"
                />
              </label>
            </div>

            <div className="rounded-xl border border-violet-100 bg-violet-50 px-4 py-3 text-sm text-violet-900">
              <span className="font-semibold">
                Classement :
              </span>{" "}
              {[
                form.organisme,
                form.project,
                form.subproject,
                form.year,
              ]
                .filter(Boolean)
                .join(" › ") ||
                "À compléter"}
            </div>

            <label
              className={`flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed px-5 py-9 text-center transition ${
                file
                  ? "border-emerald-300 bg-emerald-50"
                  : "border-violet-200 bg-violet-50/50 hover:border-violet-400 hover:bg-violet-50"
              }`}
            >
              <input
                type="file"
                accept=".pdf,.doc,.docx,.docm,.txt,.md"
                className="sr-only"
                onChange={(event) =>
                  setFile(
                    event.target.files?.[0] ||
                      null
                  )
                }
              />

              {file ? (
                <>
                  <FileCheck2 className="size-8 text-emerald-600" />
                  <p className="mt-3 font-semibold text-emerald-900">
                    {file.name}
                  </p>
                  <p className="mt-1 text-xs text-emerald-700">
                    {(
                      file.size /
                      1024 /
                      1024
                    ).toFixed(2)}{" "}
                    Mo · cliquer pour remplacer
                  </p>
                </>
              ) : (
                <>
                  <UploadCloud className="size-8 text-violet-600" />
                  <p className="mt-3 font-semibold text-slate-800">
                    Choisir le CIR final validé
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    PDF, DOCX, TXT ou MD
                  </p>
                </>
              )}
            </label>

            <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4 text-sm leading-6 text-blue-800">
              <strong>
                Traitement automatique :
              </strong>{" "}
              extraction du texte, analyse CIR, création des cartes de connaissance et mise à jour de l’unique collection vectorielle globale.
            </div>

            <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
              <SecondaryButton
                onClick={createSlot}
                disabled={loading}
              >
                <ShieldCheck className="size-4" />
                Vérifier l’identité
              </SecondaryButton>

              <PrimaryButton
                type="submit"
                disabled={
                  loading || !file
                }
              >
                {loading ? (
                  <RefreshCw className="size-4 animate-spin" />
                ) : (
                  <Sparkles className="size-4" />
                )}
                Ajouter et indexer
              </PrimaryButton>
            </div>
          </form>
        </section>
      ) : null}

      {tab === "power-automate" ? (
        <PowerAutomateImportPanel
          onMemoryChanged={() =>
            loadCatalog(true)
          }
        />
      ) : null}

      {tab === "search" ? (
        <section className="rounded-3xl border border-violet-100 bg-white p-6 shadow-[0_12px_36px_rgba(50,20,90,.06)] sm:p-8">
          <div className="max-w-3xl">
            <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
              Interroger la mémoire vectorielle
            </h2>

            <p className="mt-1 text-sm leading-6 text-slate-500">
              Retrouve des projets, méthodes, verrous ou exemples de rédaction proches dans les CIR validés.
            </p>
          </div>

          <form
            onSubmit={runSearch}
            className="mt-6 grid gap-3 lg:grid-cols-[minmax(0,1fr)_170px_190px_auto]"
          >
            <label className="relative">
              <Search className="absolute left-3.5 top-3.5 size-4 text-slate-400" />

              <input
                value={query}
                onChange={(event) =>
                  setQuery(
                    event.target.value
                  )
                }
                placeholder="Quels projets ont rencontré des verrous similaires ?"
                className="w-full rounded-xl border border-slate-200 py-3 pl-10 pr-3 text-sm outline-none transition focus:border-violet-400 focus:ring-4 focus:ring-violet-100"
              />
            </label>

            <select
              value={searchRole}
              onChange={(event) =>
                setSearchRole(
                  event.target.value
                )
              }
              className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm outline-none focus:border-violet-400"
            >
              <option value="">
                Tous les rôles
              </option>

              {[
                "objectif",
                "etat_art",
                "limite",
                "verrou",
                "methode",
                "resultat",
                "contribution",
                "style",
              ].map((role) => (
                <option
                  key={role}
                  value={role}
                >
                  {labelForRole(role)}
                </option>
              ))}
            </select>

            <select
              value={
                searchOrganisation
              }
              onChange={(event) =>
                setSearchOrganisation(
                  event.target.value
                )
              }
              className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm outline-none focus:border-violet-400"
            >
              <option value="">
                Toutes les entreprises
              </option>

              {catalog?.organisms.map(
                (item) => (
                  <option key={item}>
                    {item}
                  </option>
                )
              )}
            </select>

            <PrimaryButton
              type="submit"
              disabled={loading}
            >
              {loading ? (
                <RefreshCw className="size-4 animate-spin" />
              ) : (
                <Search className="size-4" />
              )}
              Rechercher
            </PrimaryButton>
          </form>

          <div className="mt-7 grid gap-4 lg:grid-cols-2">
            {matches.map(
              (match, index) => {
                const metadata =
                  match.metadata || {}

                return (
                  <article
                    key={
                      match.id ||
                      index
                    }
                    className="rounded-2xl border border-slate-100 bg-slate-50 p-5"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <Pill tone="violet">
                        {labelForRole(
                          String(
                            metadata.role ||
                              "mémoire"
                          )
                        )}
                      </Pill>

                      <Pill>
                        {[
                          metadata.project ||
                            "Projet",
                          metadata.subproject,
                          metadata.year ||
                            "—",
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </Pill>
                    </div>

                    <h3 className="mt-3 font-semibold text-slate-900">
                      {metadata.section_title ||
                        metadata.document ||
                        "Passage CIR"}
                    </h3>

                    <p className="mt-2 line-clamp-6 whitespace-pre-wrap text-sm leading-6 text-slate-600">
                      {match.text}
                    </p>

                    <p className="mt-3 text-xs text-slate-400">
                      {metadata.organisme} ·{" "}
                      {metadata.memory_class ||
                        "expérience"}
                    </p>
                  </article>
                )
              }
            )}
          </div>

          {!matches.length ? (
            <div className="mt-7 rounded-2xl border border-dashed border-slate-200 py-12 text-center">
              <BrainCircuit className="mx-auto size-8 text-violet-300" />

              <p className="mt-3 text-sm text-slate-500">
                Les résultats vectoriels apparaîtront ici.
              </p>
            </div>
          ) : null}
        </section>
      ) : null}

      <details className="rounded-2xl border border-slate-200 bg-white px-5 py-4 text-sm text-slate-600">
        <summary className="cursor-pointer font-semibold text-slate-700">
          État technique de Memory V2
        </summary>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl bg-slate-50 p-4">
            <p className="font-semibold text-slate-800">
              Connexions IA
            </p>

            <div className="mt-3 space-y-2 text-xs">
              <p>
                EnnoDiagnostic :{" "}
                {catalog?.ai_connections
                  .ennodiagnostic
                  ? "connecté"
                  : "absent"}
              </p>

              <p>
                Comparaison CIR :{" "}
                {catalog?.ai_connections
                  .cir_comparison
                  ? "connectée"
                  : "absente"}
              </p>

              <p>
                Style rédactionnel :{" "}
                {catalog?.ai_connections
                  .writing_style
                  ? "connecté"
                  : "absent"}
              </p>
            </div>
          </div>

          <div className="rounded-xl bg-slate-50 p-4">
            <p className="font-semibold text-slate-800">
              Source unique
            </p>

            <p className="mt-2 break-all text-xs text-slate-500">
              {String(
                catalog?.paths.v2_root ||
                  ""
              )}
            </p>

            <p className="mt-1 break-all text-xs text-slate-500">
              Collection :{" "}
              {catalog?.vector_db
                .collection}
            </p>

            <div className="mt-3">
              <SecondaryButton
                onClick={rebuild}
                disabled={loading}
              >
                <RefreshCw
                  className={`size-4 ${
                    loading
                      ? "animate-spin"
                      : ""
                  }`}
                />
                Reconstruire l’index
              </SecondaryButton>
            </div>
          </div>
        </div>
      </details>

      {preview.open ? (
        <div className="fixed inset-0 z-[120] bg-slate-950/25 p-2 backdrop-blur-[2px] sm:p-4">
          <div className="mx-auto flex h-full max-w-[1500px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
            <div className="flex h-14 shrink-0 items-center gap-3 border-b border-slate-200 px-4">
              <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-violet-100 text-violet-700">
                <FileText className="size-4" />
              </div>

              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-slate-900">
                  {preview.fileName ||
                    "CIR final"}
                </p>

                <p className="text-[10px] text-slate-400">
                  Aperçu du document original
                </p>
              </div>

              {selected ? (
                <button
                  type="button"
                  onClick={() =>
                    downloadCir(selected)
                  }
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 text-[10px] font-semibold text-slate-700 hover:bg-slate-50"
                >
                  <Download className="size-3.5" />
                  Télécharger l’original
                </button>
              ) : null}

              <button
                type="button"
                onClick={closePreview}
                className="grid size-8 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50"
                aria-label="Fermer"
              >
                <X className="size-4" />
              </button>
            </div>

            <div className="relative min-h-0 flex-1 bg-slate-100">
              {preview.loading ? (
                <div className="absolute inset-0 z-10 grid place-items-center bg-white/80">
                  <div className="flex items-center gap-2 rounded-xl border bg-white px-4 py-3 text-xs text-slate-500 shadow-sm">
                    <Loader2 className="size-4 animate-spin text-violet-600" />
                    Préparation du CIR…
                  </div>
                </div>
              ) : null}

              {preview.error ? (
                <div className="grid h-full place-items-center p-6">
                  <div className="max-w-md rounded-2xl border border-rose-200 bg-rose-50 p-5 text-center">
                    <p className="text-sm font-semibold text-rose-800">
                      Aperçu indisponible
                    </p>
                    <p className="mt-2 text-xs leading-5 text-rose-700">
                      {preview.error}
                    </p>
                  </div>
                </div>
              ) : preview.objectUrl ? (
                <iframe
                  src={
                    preview.mediaType
                      .toLowerCase()
                      .includes("pdf")
                      ? `${preview.objectUrl}#zoom=page-width`
                      : preview.objectUrl
                  }
                  title={
                    preview.fileName ||
                    "CIR final"
                  }
                  className="h-full w-full border-0 bg-white"
                />
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
