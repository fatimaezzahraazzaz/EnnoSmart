"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  FileUp,
  History,
  Library,
  Loader2,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  createImprovementSession,
  decideImprovementSources,
  decideImprovementVersion,
  deleteImprovementSession,
  getDocuments,
  getImprovementProjectContext,
  getImprovementSession,
  getImprovementSourceDocument,
  getProjects,
  listImprovementSessions,
  restoreImprovementVersion,
  sendImprovementMessage,
  uploadDocument,
  type DocumentRead,
  type ImprovementSession,
  type ImprovementProjectContext,
  type ImprovementSection,
  type ImprovementVersion,
  type ProjectRead,
} from "@/lib/api"
import { getCurrentProjectId, setCurrentProjectId } from "@/lib/project-session"
import { cn } from "@/lib/utils"

function normalizeSourceDecision(value: unknown) {
  const decision = String(value || "").trim().toLowerCase()
  if (["accept", "accepted", "garde", "kept"].includes(decision)) return "accepted"
  if (["reject", "rejected", "rejete", "rejeté", "ecarte", "écarté"].includes(decision)) return "rejected"
  return "pending"
}

function isDirectPdfUrl(value: unknown) {
  const url = String(value || "").trim().toLowerCase()
  return url.endsWith(".pdf") || url.includes("/pdf/") || url.endsWith("/document")
}

function publicationSiteUrl(source: Record<string, any>) {
  const explicit = String(source.site_url || "").trim()
  if (explicit) return explicit
  const doi = String(source.doi || "").trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
  if (doi) return `https://doi.org/${doi}`
  const rawUrl = String(source.url || "").trim()
  if (/arxiv\.org\/pdf\//i.test(rawUrl)) return rawUrl.replace(/\/pdf\//i, "/abs/").replace(/\.pdf$/i, "")
  if (/(?:hal\.science|hal\.archives-ouvertes\.fr)\/.+\/document$/i.test(rawUrl)) return rawUrl.replace(/\/document$/i, "")
  return rawUrl && !isDirectPdfUrl(rawUrl) ? rawUrl : ""
}


type Props = {
  onImmersiveModeChange?: (immersive: boolean) => void
  onCreateProject?: () => void
}

type ImprovementMode = "section" | "full_document"

const quickPrompts = [
  "Améliore la clarté et la fluidité sans changer les faits.",
  "Renforce l'argumentation avec les preuves déjà validées.",
  "Renforce les verrous sans inventer de difficulté.",
  "Enrichis l'état de l'art avec des références traçables.",
  "Applique un style CIR professionnel.",
  "Identifie ce qui reste insuffisamment démontré.",
  "Vérifie la chaîne incertitude–méthode–résultat–connaissance pour le CIR.",
]

const MAX_INTERACTIVE_SELECTION_CHARS = 80_000


function sectionHasChildren(sections: ImprovementSection[], index: number) {
  return Boolean(sections[index + 1] && sections[index + 1].level > sections[index].level)
}


function visibleSectionRows(
  sections: ImprovementSection[],
  expandedIds: Set<string>,
  query: string,
) {
  const wanted = query.trim().toLocaleLowerCase("fr")
  if (wanted) {
    return sections
      .map((section, index) => ({ section, index, hasChildren: sectionHasChildren(sections, index) }))
      .filter(({ section }) => section.title.toLocaleLowerCase("fr").includes(wanted))
  }

  const rows: Array<{ section: ImprovementSection; index: number; hasChildren: boolean }> = []
  const collapsedParentLevels: number[] = []
  sections.forEach((section, index) => {
    while (
      collapsedParentLevels.length > 0
      && section.level <= collapsedParentLevels[collapsedParentLevels.length - 1]
    ) {
      collapsedParentLevels.pop()
    }
    if (collapsedParentLevels.length > 0) return
    const hasChildren = sectionHasChildren(sections, index)
    rows.push({ section, index, hasChildren })
    if (hasChildren && !expandedIds.has(section.section_id)) {
      collapsedParentLevels.push(section.level)
    }
  })
  return rows
}


function expandedPathForSection(sections: ImprovementSection[], sectionId?: string | null) {
  if (!sectionId) return new Set<string>()
  const stack: ImprovementSection[] = []
  for (const section of sections) {
    while (stack.length > 0 && stack[stack.length - 1].level >= section.level) stack.pop()
    if (section.section_id === sectionId) {
      return new Set([...stack.map((row) => row.section_id), section.section_id])
    }
    stack.push(section)
  }
  return new Set<string>()
}


function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Une erreur inattendue est survenue."
}


function evidenceReferenceLabel(reference: string) {
  const parts = String(reference || "").split(":")
  const detail = parts.slice(2).join(" ").replace(/[_-]+/g, " ").trim()
  if (parts[0] === "D") return detail ? `Preuve projet · ${detail}` : "Preuve projet"
  if (parts[0] === "S") return detail ? `Référence validée · ${detail}` : "Référence validée"
  if (parts[0] === "P") return "Identité officielle du projet"
  return detail || reference
}


function versionLabel(version: ImprovementVersion) {
  if (version.status === "original") return `V${version.version_number} · Original`
  if (version.status === "candidate") return `V${version.version_number} · Proposition`
  if (version.status === "accepted") return `V${version.version_number} · Active`
  if (version.status === "rejected") return `V${version.version_number} · Rejetée`
  return `V${version.version_number} · ${version.status}`
}


export default function EnnoAmeliorationPage({ onImmersiveModeChange, onCreateProject }: Props) {
  const [projectId, setProjectId] = useState<number | null>(null)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [projectContext, setProjectContext] = useState<ImprovementProjectContext | null>(null)
  const [projectChooserOpen, setProjectChooserOpen] = useState(false)
  const [projectQuery, setProjectQuery] = useState("")
  const [sessions, setSessions] = useState<ImprovementSession[]>([])
  const [current, setCurrent] = useState<ImprovementSession | null>(null)
  const [documents, setDocuments] = useState<DocumentRead[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [draft, setDraft] = useState("")
  const [pendingMessage, setPendingMessage] = useState("")
  const [selectedText, setSelectedText] = useState("")
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(null)
  const [sectionsPanelOpen, setSectionsPanelOpen] = useState(true)
  const [expandedSectionIds, setExpandedSectionIds] = useState<Set<string>>(() => new Set())
  const [sectionQuery, setSectionQuery] = useState("")
  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(true)
  const [sourcePreviewUrl, setSourcePreviewUrl] = useState("")
  const [sourcePreviewLoading, setSourcePreviewLoading] = useState(false)
  const [sourcePreviewError, setSourcePreviewError] = useState("")
  const [creating, setCreating] = useState(false)
  const [newTitle, setNewTitle] = useState("")
  const [newText, setNewText] = useState("")
  const [newDocumentId, setNewDocumentId] = useState("")
  const [newInstruction, setNewInstruction] = useState("")
  const [targetMode, setTargetMode] = useState<ImprovementMode>("section")
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const artifactRef = useRef<HTMLTextAreaElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  const versions = current?.versions || []
  const candidate = useMemo(
    () => [...versions].reverse().find((version) => version.status === "candidate") || null,
    [versions],
  )
  const activeVersion = useMemo(
    () => versions.find((version) => version.version_id === current?.active_version_id) || versions[0] || null,
    [versions, current?.active_version_id],
  )
  const comparisonOriginal = useMemo(
    () => (
      candidate
        ? versions.find((version) => version.version_id === candidate.parent_version_id) || activeVersion
        : activeVersion
    ),
    [candidate, versions, activeVersion],
  )
  const structuredResult = (
    candidate?.generation?.structured_result
    || candidate?.generation?.trace
    || null
  ) as Record<string, any> | null
  const documentStructure = (current?.context?.document_structure || {}) as Record<string, any>
  const sourceDocument = documents.find((document) => document.id === current?.source_document_id) || null
  const sourceDocumentIsPdf = Boolean(
    sourceDocument
    && (
      String(sourceDocument.content_type || "").toLowerCase().includes("pdf")
      || String(sourceDocument.filename || "").toLowerCase().endsWith(".pdf")
    )
  )
  const activeProject = projectContext?.project || projects.find((project) => project.id === projectId) || null
  const filteredProjects = useMemo(() => {
    const query = projectQuery.trim().toLocaleLowerCase("fr")
    if (!query) return projects
    return projects.filter((project) => (
      `${project.project_name} ${project.organisme} ${project.year} ${project.domain_label || ""}`
        .toLocaleLowerCase("fr")
        .includes(query)
    ))
  }, [projects, projectQuery])
  const sections = current?.context?.sections || []
  const visibleSections = useMemo(
    () => visibleSectionRows(sections, expandedSectionIds, sectionQuery),
    [sections, expandedSectionIds, sectionQuery],
  )
  const researchSources = (
    current?.context?.research_sources
    || current?.context?.scholar_handoff?.sources
    || []
  ) as Array<Record<string, any>>

  useEffect(() => {
    let cancelled = false
    let objectUrl = ""
    const documentId = Number(current?.source_document_id || 0)
    if (!projectId || !documentId) {
      setSourcePreviewUrl("")
      setSourcePreviewError("")
      setSourcePreviewLoading(false)
      return () => {
        cancelled = true
      }
    }
    setSourcePreviewLoading(true)
    setSourcePreviewUrl("")
    setSourcePreviewError("")
    getImprovementSourceDocument(projectId, documentId)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setSourcePreviewUrl(objectUrl)
      })
      .catch((previewError) => {
        if (!cancelled) setSourcePreviewError(getErrorMessage(previewError))
      })
      .finally(() => {
        if (!cancelled) setSourcePreviewLoading(false)
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [projectId, current?.session_id, current?.source_document_id])

  const refreshList = async (id: number) => {
    const response = await listImprovementSessions(id)
    setSessions(response.sessions)
  }

  const openSession = async (sessionId: string, id = projectId) => {
    if (!id) return
    setBusy(true)
    setError("")
    try {
      const response = await getImprovementSession(id, sessionId)
      setCurrent(response.session)
      setCreating(false)
      const nextSectionId = response.session.target_section_id || null
      setSelectedSectionId(nextSectionId)
      setExpandedSectionIds(expandedPathForSection(response.session.context?.sections || [], nextSectionId))
      setSectionQuery("")
      setSelectedText("")
      const scope = response.session.target_scope
      setTargetMode(scope === "full_document" ? "full_document" : "section")
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const loadProject = async (id: number) => {
    setLoading(true)
    setError("")
    setProjectId(id)
    setCurrentProjectId(id)
    setCurrent(null)
    setSessions([])
    setDocuments([])
    setSelectedText("")
    setSelectedSectionId(null)
    setProjectChooserOpen(false)
    try {
      const [sessionResponse, documentResponse, contextResponse] = await Promise.all([
        listImprovementSessions(id),
        getDocuments(id),
        getImprovementProjectContext(id),
      ])
      setSessions(sessionResponse.sessions)
      setDocuments(documentResponse)
      setProjectContext(contextResponse.context)
      if (sessionResponse.sessions[0]) {
        setCreating(false)
        // Le shell, les documents et la liste sont déjà utilisables. Le détail
        // potentiellement volumineux de la dernière conversation arrive ensuite.
        setLoading(false)
        void openSession(sessionResponse.sessions[0].session_id, id)
      } else {
        setCreating(true)
      }
    } catch (requestError) {
      setProjectContext(null)
      setError(getErrorMessage(requestError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    onImmersiveModeChange?.(true)
    getProjects()
      .then((availableProjects) => {
        setProjects(availableProjects)
        const savedId = getCurrentProjectId()
        const selected = availableProjects.find((project) => project.id === savedId) || availableProjects[0]
        if (selected) return loadProject(selected.id)
        setLoading(false)
        return undefined
      })
      .catch((requestError) => {
        setError(getErrorMessage(requestError))
        setLoading(false)
      })

    return () => onImmersiveModeChange?.(false)
  }, [onImmersiveModeChange])

  useEffect(() => {
    const viewport = messagesEndRef.current?.closest<HTMLElement>("[data-slot='scroll-area-viewport']")
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" })
  }, [current?.messages?.length, pendingMessage, busy])

  useEffect(() => {
    const compactViewport = window.matchMedia("(max-width: 1439px)")
    const adaptPanels = (event: MediaQueryListEvent | MediaQueryList) => {
      if (event.matches) setLeftOpen(false)
    }
    adaptPanels(compactViewport)
    compactViewport.addEventListener("change", adaptPanels)
    return () => compactViewport.removeEventListener("change", adaptPanels)
  }, [])

  const createSession = async () => {
    if (!projectId) return
    const selectedDocumentId = Number(newDocumentId) || undefined
    if (targetMode === "full_document" && !selectedDocumentId) {
      setError("Choisissez un document CIR du projet ou importez-le depuis votre PC.")
      return
    }
    if (!selectedDocumentId && !newText.trim()) {
      setError("Collez une section, choisissez un document ou importez un fichier.")
      return
    }
    if (!newInstruction.trim()) {
      setError("Décrivez l'amélioration attendue avant de lancer l'analyse.")
      return
    }
    const instruction = newInstruction.trim()
    let sessionCreated = false
    setBusy(true)
    setError("")
    try {
      const response = await createImprovementSession(projectId, {
        title: newTitle.trim() || undefined,
        source_text: targetMode === "section" ? newText.trim() || undefined : undefined,
        source_document_id: selectedDocumentId,
        target_scope: targetMode,
      })
      sessionCreated = true
      setCurrent(response.session)
      setCreating(false)
      setPendingMessage(instruction)
      const improved = await sendImprovementMessage(projectId, response.session.session_id, {
        message: instruction,
        target_scope: targetMode,
      })
      setCurrent(improved.session)
      setNewTitle("")
      setNewText("")
      setNewDocumentId("")
      setNewInstruction("")
      setSelectedSectionId(null)
      setExpandedSectionIds(new Set())
      setSectionQuery("")
      const contextResponse = await getImprovementProjectContext(projectId)
      setProjectContext(contextResponse.context)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
      if (sessionCreated) setDraft(instruction)
    } finally {
      setPendingMessage("")
      setBusy(false)
    }
  }

  const handleLocalFile = async (file?: File) => {
    if (!projectId || !file) return
    setBusy(true)
    setError("")
    try {
      const document = await uploadDocument(projectId, file, "Texte à améliorer")
      setDocuments((rows) => [document, ...rows.filter((row) => row.id !== document.id)])
      setNewDocumentId(String(document.id))
      setNewTitle((title) => title.trim() || file.name)
      setNewText("")
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const sendMessage = async () => {
    if (!projectId || !current || !draft.trim() || busy) return
    if (selectedText.length > MAX_INTERACTIVE_SELECTION_CHARS) {
      setError(
        `Le passage sélectionné contient ${selectedText.length.toLocaleString("fr-FR")} caractères. `
        + "Choisissez la section par son titre dans l'arborescence afin de cibler aussi ses sous-sections.",
      )
      return
    }
    const outgoingMessage = draft.trim()
    setBusy(true)
    setError("")
    setPendingMessage(outgoingMessage)
    setDraft("")
    try {
      const section = sections.find((row) => row.section_id === selectedSectionId)
      const response = await sendImprovementMessage(projectId, current.session_id, {
        message: outgoingMessage,
        selected_text: selectedText || undefined,
        target_scope: selectedText ? "selection" : selectedSectionId ? "section" : targetMode,
        target_section_id: selectedText ? undefined : selectedSectionId || undefined,
        target_section_title: selectedText ? undefined : section?.title,
      })
      setCurrent(response.session)
      setSelectedText("")
      setRightOpen(true)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
      setDraft((currentDraft) => currentDraft || outgoingMessage)
    } finally {
      setPendingMessage("")
      setBusy(false)
    }
  }

  const decide = async (decision: "accepted" | "rejected") => {
    if (!projectId || !current || !candidate || busy) return
    setBusy(true)
    setError("")
    try {
      const response = await decideImprovementVersion(
        projectId,
        current.session_id,
        candidate.version_id,
        decision,
      )
      setCurrent(response.session)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const restore = async (version: ImprovementVersion) => {
    if (!projectId || !current || busy) return
    setBusy(true)
    setError("")
    try {
      const response = await restoreImprovementVersion(
        projectId,
        current.session_id,
        version.version_id,
      )
      setCurrent(response.session)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const decideSource = async (candidateId: string, decision: "accepted" | "rejected") => {
    if (!projectId || !current || busy) return
    setBusy(true)
    setError("")
    try {
      const response = await decideImprovementSources(
        projectId,
        current.session_id,
        [candidateId],
        decision,
      )
      setCurrent(response.session)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const removeSession = async (session: ImprovementSession) => {
    if (!projectId || !window.confirm(`Supprimer la conversation « ${session.title} » ?`)) return
    setBusy(true)
    try {
      await deleteImprovementSession(projectId, session.session_id)
      const remaining = sessions.filter((row) => row.session_id !== session.session_id)
      setSessions(remaining)
      if (current?.session_id === session.session_id) {
        setCurrent(null)
        if (remaining[0]) await openSession(remaining[0].session_id, projectId)
        else setCreating(true)
      }
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const captureSelection = () => {
    const element = artifactRef.current
    if (!element) return
    const selected = element.value.slice(element.selectionStart, element.selectionEnd).trim()
    if (selected.length > MAX_INTERACTIVE_SELECTION_CHARS) {
      setSelectedText("")
      setError(
        `Cette sélection contient ${selected.length.toLocaleString("fr-FR")} caractères. `
        + "Utilisez l'arborescence des sections pour éviter d'envoyer accidentellement le CIR complet.",
      )
      return
    }
    setError("")
    setSelectedText(selected)
    if (selected) setTargetMode("section")
  }

  const toggleSection = (sectionId: string) => {
    setExpandedSectionIds((currentIds) => {
      const nextIds = new Set(currentIds)
      if (nextIds.has(sectionId)) nextIds.delete(sectionId)
      else nextIds.add(sectionId)
      return nextIds
    })
  }

  const selectSection = (section: ImprovementSection) => {
    setSelectedSectionId(section.section_id)
    setExpandedSectionIds((currentIds) => new Set([
      ...currentIds,
      ...expandedPathForSection(sections, section.section_id),
    ]))
    setSelectedText("")
    setTargetMode("section")
    setError("")
    setRightOpen(true)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const element = artifactRef.current
        if (!element) return
        const position = Math.min(section.start, element.value.length)
        element.focus({ preventScroll: true })
        element.setSelectionRange(position, position)
      })
    })
  }

  if (loading) {
    return <div className="grid h-full place-items-center"><Loader2 className="size-7 animate-spin text-primary" /></div>
  }

  if (!projectId) {
    return (
      <div className="grid h-full place-items-center p-8">
        <div className="max-w-md rounded-2xl border bg-card p-8 text-center shadow-sm">
          <Sparkles className="mx-auto size-9 text-primary" />
          <h1 className="mt-4 text-xl font-semibold">Sélectionnez d'abord un projet</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            EnnoAmelioration conserve ses conversations et ses versions séparément pour chaque projet.
          </p>
          <Button className="mt-5 gap-2" onClick={onCreateProject}>
            <Plus className="size-4" /> Nouveau projet
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="relative flex h-full max-h-full min-h-0 overflow-hidden bg-[radial-gradient(circle_at_80%_0%,rgba(139,92,246,.08),transparent_28rem),var(--background)]">
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        accept=".pdf,.doc,.docx,.txt,.md"
        onChange={(event) => handleLocalFile(event.target.files?.[0])}
      />

      {leftOpen ? (
        <aside className="flex h-full min-h-0 w-[clamp(232px,18vw,276px)] shrink-0 flex-col overflow-hidden border-r border-border/70 bg-card/90 shadow-[8px_0_28px_rgb(45_20_80_/_0.035)] backdrop-blur-xl max-lg:absolute max-lg:inset-y-0 max-lg:left-0 max-lg:z-30 max-lg:w-[min(86vw,276px)]">
          <div className="flex h-14 items-center gap-2 border-b px-3">
            <div className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
              <Sparkles className="size-4" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">EnnoAmelioration</p>
              <p className="text-[11px] text-muted-foreground">Révision CIR contrôlée</p>
            </div>
            <Button variant="ghost" size="sm" className="size-8 p-0" onClick={() => setLeftOpen(false)}>
              <PanelLeftClose className="size-4" />
            </Button>
          </div>
          <div className="border-b p-3">
            <button
              type="button"
              className="w-full rounded-xl border bg-background p-2.5 text-left hover:bg-muted/50"
              onClick={() => setProjectChooserOpen((open) => !open)}
            >
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1">
                  <span className="block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Projet actif</span>
                  <span className="mt-0.5 block truncate text-xs font-semibold">{activeProject?.project_name}</span>
                  <span className="block truncate text-[10px] text-muted-foreground">
                    {[activeProject?.organisme, activeProject?.year].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <ChevronDown className={cn("size-4 shrink-0 transition-transform", projectChooserOpen && "rotate-180")} />
              </div>
            </button>
            {projectChooserOpen && (
              <div className="mt-2 space-y-2 rounded-xl border bg-background p-2 shadow-sm">
                <Input
                  className="h-8 text-xs"
                  placeholder="Rechercher un projet…"
                  value={projectQuery}
                  onChange={(event) => setProjectQuery(event.target.value)}
                />
                <div className="max-h-48 space-y-1 overflow-y-auto">
                  {filteredProjects.map((project) => (
                    <button
                      key={project.id}
                      type="button"
                      className={cn(
                        "w-full rounded-lg px-2 py-2 text-left hover:bg-muted",
                        project.id === projectId && "bg-primary/10 text-primary",
                      )}
                      onClick={() => loadProject(project.id)}
                    >
                      <span className="block truncate text-xs font-medium">{project.project_name}</span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {project.organisme} · {project.year}
                      </span>
                    </button>
                  ))}
                  {filteredProjects.length === 0 && (
                    <p className="px-2 py-3 text-center text-xs text-muted-foreground">Aucun projet trouvé.</p>
                  )}
                </div>
                <Button variant="outline" size="sm" className="w-full justify-start gap-2" onClick={onCreateProject}>
                  <Plus className="size-3.5" /> Nouveau projet
                </Button>
              </div>
            )}
            {projectContext && !projectChooserOpen && (
              <div className="mt-2 flex flex-wrap gap-1">
                <Badge variant="outline" className="h-5 px-1.5 text-[9px]">{projectContext.documents.count} doc.</Badge>
                <Badge variant={projectContext.diagnostic.available ? "default" : "outline"} className="h-5 px-1.5 text-[9px]">Preuves projet</Badge>
                <Badge variant={projectContext.scholar.available ? "default" : "outline"} className="h-5 px-1.5 text-[9px]">Références validées</Badge>
                <Badge variant={projectContext.cir_memory.available ? "default" : "outline"} className="h-5 px-1.5 text-[9px]">Style CIR</Badge>
              </div>
            )}
          </div>
          <div className="p-3">
            <Button
              className="w-full justify-start gap-2"
              onClick={() => { setCreating(true); setCurrent(null); setSelectedText("") }}
            >
              <Plus className="size-4" /> Nouvelle amélioration
            </Button>
          </div>
          <ScrollArea className="min-h-0 flex-1 px-2">
            <div className="space-y-1 pb-4">
              {sessions.map((session) => (
                <div
                  key={session.session_id}
                  className={cn(
                    "group flex items-start rounded-xl border border-transparent p-2.5 hover:bg-muted/60",
                    current?.session_id === session.session_id && "border-border bg-muted",
                  )}
                >
                  <button className="min-w-0 flex-1 text-left" onClick={() => openSession(session.session_id)}>
                    <p className="truncate text-sm font-medium">{session.title}</p>
                    <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                      {session.preview || "Conversation vide"}
                    </p>
                    <div className="mt-2 flex items-center gap-1.5">
                      <Badge variant="outline" className="h-5 px-1.5 text-[10px]">{session.message_count} messages</Badge>
                      {session.candidate_count > 0 && <Badge className="h-5 px-1.5 text-[10px]">À valider</Badge>}
                    </div>
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="size-7 shrink-0 p-0 opacity-0 group-hover:opacity-100"
                    onClick={() => removeSession(session)}
                    title="Supprimer la conversation"
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          </ScrollArea>
          {current && sections.length > 0 && (
            <div className={cn(
              "flex shrink-0 flex-col border-t bg-card",
              sectionsPanelOpen && "h-[46%] min-h-[190px]",
            )}>
              <button
                type="button"
                className="flex h-10 shrink-0 items-center gap-2 px-3 text-left hover:bg-muted/60"
                onClick={() => setSectionsPanelOpen((open) => !open)}
              >
                {sectionsPanelOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                <span className="flex-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Sections détectées
                </span>
                <Badge variant="outline" className="h-5 px-1.5 text-[10px]">{sections.length}</Badge>
              </button>
              {sectionsPanelOpen && (
                <>
                  <div className="shrink-0 px-3 pb-2">
                    <Input
                      className="h-8 text-xs"
                      placeholder="Rechercher une section…"
                      value={sectionQuery}
                      onChange={(event) => setSectionQuery(event.target.value)}
                    />
                  </div>
                  <ScrollArea className="min-h-0 flex-1 px-2 pb-3">
                    <div className="space-y-0.5 pr-2">
                      {visibleSections.map(({ section, hasChildren }) => (
                        <div
                          key={section.section_id}
                          className={cn(
                            "flex min-w-0 items-center rounded-lg hover:bg-muted",
                            selectedSectionId === section.section_id && "bg-primary/10 text-primary",
                          )}
                          style={{ paddingLeft: `${4 + Math.max(0, section.level - 1) * 10}px` }}
                        >
                          {hasChildren ? (
                            <button
                              type="button"
                              className="grid size-7 shrink-0 place-items-center rounded-md hover:bg-background/80"
                              onClick={() => toggleSection(section.section_id)}
                              title={expandedSectionIds.has(section.section_id) ? "Replier" : "Déplier"}
                            >
                              {expandedSectionIds.has(section.section_id)
                                ? <ChevronDown className="size-3" />
                                : <ChevronRight className="size-3" />}
                            </button>
                          ) : <span className="block size-7 shrink-0" />}
                          <button
                            type="button"
                            className="min-w-0 flex-1 truncate py-1.5 pr-2 text-left text-xs"
                            onClick={() => selectSection(section)}
                            title={section.title}
                          >
                            {section.title}
                          </button>
                        </div>
                      ))}
                      {visibleSections.length === 0 && (
                        <p className="px-2 py-4 text-center text-xs text-muted-foreground">Aucune section trouvée.</p>
                      )}
                    </div>
                  </ScrollArea>
                </>
              )}
            </div>
          )}
        </aside>
      ) : (
        <Button
          variant="outline"
          size="sm"
          className="absolute left-3 top-3 z-20 size-9 bg-background p-0 shadow-sm"
          onClick={() => setLeftOpen(true)}
          title="Ouvrir les conversations"
        >
          <PanelLeftOpen className="size-4" />
        </Button>
      )}

      <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
          <div className={cn("min-w-0 flex-1", !leftOpen && "pl-11")}>
            <p className="truncate text-sm font-semibold">{creating ? "Nouvelle amélioration" : current?.title || "EnnoAmelioration"}</p>
            <p className="truncate text-[11px] text-muted-foreground">
              {selectedText
                ? `${selectedText.length} caractères sélectionnés`
                : sections.find((row) => row.section_id === selectedSectionId)?.title
                  || (targetMode === "full_document" ? "CIR complet" : "Section")}
            </p>
          </div>
          {current && (
            <div className="hidden items-center gap-1 rounded-lg border bg-muted/30 p-1 md:flex">
              {([
                ["section", "Section"],
                ["full_document", "CIR complet"],
              ] as Array<[ImprovementMode, string]>).map(([mode, label]) => (
                <button
                  key={mode}
                  type="button"
                  className={cn(
                    "rounded-md px-2.5 py-1 text-[11px] font-medium",
                    targetMode === mode ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                  )}
                  onClick={() => {
                    setTargetMode(mode)
                    if (mode === "full_document") {
                      setSelectedText("")
                      setSelectedSectionId(null)
                    } else if (mode === "section") {
                      setSelectedText("")
                    }
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          {current && (
            <Button variant="outline" size="sm" className="gap-2" onClick={() => setRightOpen((value) => !value)}>
              {rightOpen ? <PanelRightClose className="size-4" /> : <PanelRightOpen className="size-4" />}
              <span className="hidden xl:inline">Artifact</span>
            </Button>
          )}
        </header>

        {error && (
          <div className="mx-4 mt-3 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <X className="mt-0.5 size-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError("")}><X className="size-3.5" /></button>
          </div>
        )}

        {creating ? (
          <ScrollArea className="flex-1">
            <div className="mx-auto flex min-h-full max-w-3xl flex-col justify-center p-6 lg:p-10">
              <div className="mb-7 text-center">
                <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-primary/10 text-primary"><Sparkles className="size-6" /></div>
                <h1 className="mt-4 text-2xl font-semibold">Nouvelle conversation</h1>
                <p className="mt-2 text-sm text-muted-foreground">Importez un CIR complet ou collez une section, puis décrivez librement l'amélioration attendue. L'original reste conservé.</p>
              </div>
              <div className="space-y-4 rounded-2xl border bg-card p-5 shadow-sm">
                <div>
                  <p className="mb-2 text-xs font-medium text-muted-foreground">Portée du texte</p>
                  <div className="grid grid-cols-2 gap-2">
                    {([
                      ["section", "Section"],
                      ["full_document", "CIR complet"],
                    ] as Array<[ImprovementMode, string]>).map(([mode, label]) => (
                      <Button
                        key={mode}
                        type="button"
                        variant={targetMode === mode ? "default" : "outline"}
                        size="sm"
                        onClick={() => {
                          setTargetMode(mode)
                          if (mode === "full_document") setNewText("")
                        }}
                      >
                        {label}
                      </Button>
                    ))}
                  </div>
                </div>
                {targetMode === "section" && (
                  <>
                    <div>
                      <label className="mb-2 block text-xs font-medium text-muted-foreground" htmlFor="improvement-source-text">
                        Texte de la section
                      </label>
                      <Textarea
                        id="improvement-source-text"
                        className="min-h-48 resize-y"
                        placeholder="Collez ici la section à améliorer…"
                        value={newText}
                        onChange={(event) => {
                          setNewText(event.target.value)
                          if (event.target.value.trim()) setNewDocumentId("")
                        }}
                      />
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <div className="h-px flex-1 bg-border" /><span>ou partir d'un document</span><div className="h-px flex-1 bg-border" />
                    </div>
                  </>
                )}
                {targetMode === "full_document" && (
                  <p className="rounded-xl border bg-muted/30 px-3 py-2.5 text-xs text-muted-foreground">
                    Le CIR complet est chargé depuis un document afin de conserver sa structure, ses sections et ses sous-sections.
                  </p>
                )}
                <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
                  <select
                    className="h-10 rounded-md border bg-background px-3 text-sm"
                    value={newDocumentId}
                    onChange={(event) => {
                      setNewDocumentId(event.target.value)
                      if (event.target.value) setNewText("")
                    }}
                  >
                    <option value="">{targetMode === "full_document" ? "Choisir le document CIR" : "Choisir un document du projet"}</option>
                    {documents.map((document) => <option key={document.id} value={document.id}>{document.filename}</option>)}
                  </select>
                  <Button variant="outline" className="gap-2" onClick={() => fileInputRef.current?.click()}>
                    <FileUp className="size-4" /> Importer depuis le PC
                  </Button>
                </div>
                <div>
                  <label className="mb-2 block text-xs font-medium text-muted-foreground" htmlFor="improvement-instruction">
                    Que souhaitez-vous améliorer ?
                  </label>
                  <Textarea
                    id="improvement-instruction"
                    className="min-h-28 resize-y"
                    placeholder="Ex. : renforce l'argumentation R&D/CIR uniquement à partir des preuves disponibles dans le projet, sans inventer de faits."
                    value={newInstruction}
                    onChange={(event) => setNewInstruction(event.target.value)}
                  />
                  <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
                    {quickPrompts.slice(0, 4).map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        className="shrink-0 rounded-full border px-3 py-1.5 text-[11px] text-muted-foreground hover:bg-muted"
                        onClick={() => setNewInstruction(prompt)}
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
                <Button
                  className="w-full gap-2"
                  disabled={busy || (!newText.trim() && !newDocumentId) || !newInstruction.trim()}
                  onClick={() => createSession()}
                >
                  {busy ? <Loader2 className="size-4 animate-spin" /> : <MessageSquareText className="size-4" />}
                  Analyser et améliorer
                </Button>
              </div>
            </div>
          </ScrollArea>
        ) : current ? (
          <>
            <ScrollArea className="min-h-0 flex-1">
              <div className="mx-auto max-w-3xl space-y-6 px-5 py-7">
                {(current.messages || []).map((message) => (
                  <div key={message.message_id} className={cn("flex", message.role === "consultant" ? "justify-end" : "justify-start")}>
                    <div className={cn(
                      "max-w-[86%] rounded-2xl px-4 py-3 text-sm leading-6",
                      message.role === "consultant" ? "bg-primary text-primary-foreground" : "border bg-card",
                    )}>
                      <p className="whitespace-pre-wrap">{message.content}</p>
                    </div>
                  </div>
                ))}
                {pendingMessage && (
                  <div className="flex justify-end">
                    <div className="max-w-[86%] rounded-2xl bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground">
                      <p className="whitespace-pre-wrap">{pendingMessage}</p>
                      <p className="mt-2 text-[10px] text-primary-foreground/70">Message envoyé</p>
                    </div>
                  </div>
                )}
                {busy && (
                  <div className="flex justify-start">
                    <div className="flex items-center gap-2 rounded-2xl border bg-card px-4 py-3 text-sm text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" /> Demande reçue — analyse en cours…
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>
            </ScrollArea>
            <div className="shrink-0 border-t bg-background/95 p-4 backdrop-blur">
              <div className="mx-auto max-w-3xl">
                {selectedText && (
                  <div className="mb-2 flex items-center gap-2 rounded-lg bg-primary/5 px-3 py-2 text-xs text-primary">
                    <FileText className="size-3.5" /> Passage sélectionné ({selectedText.length} caractères)
                    <button className="ml-auto" onClick={() => setSelectedText("")}><X className="size-3.5" /></button>
                  </div>
                )}
                <div className="mb-2 flex gap-2 overflow-x-auto pb-1">
                  {quickPrompts.map((prompt) => (
                    <button key={prompt} className="shrink-0 rounded-full border px-3 py-1.5 text-[11px] text-muted-foreground hover:bg-muted" onClick={() => setDraft(prompt)}>
                      {prompt}
                    </button>
                  ))}
                </div>
                <div className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/30">
                  <Textarea
                    className="max-h-32 min-h-11 flex-1 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
                    placeholder="Demandez une amélioration précise…"
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault()
                        sendMessage()
                      }
                    }}
                  />
                  <Button size="sm" className="size-10 shrink-0 rounded-xl p-0" disabled={!draft.trim() || busy} onClick={sendMessage}>
                    {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                  </Button>
                </div>
              </div>
            </div>
          </>
        ) : null}
      </section>

      {rightOpen && current && (
        <>
          <button
            type="button"
            className="absolute inset-0 z-20 hidden bg-foreground/10 backdrop-blur-[1px] max-xl:block"
            aria-label="Fermer le panneau d'amélioration"
            onClick={() => setRightOpen(false)}
          />
          <aside className="flex h-full min-h-0 w-[clamp(340px,36vw,620px)] min-w-[340px] shrink-0 flex-col overflow-hidden border-l bg-card max-xl:absolute max-xl:inset-y-0 max-xl:right-0 max-xl:z-30 max-xl:w-[min(92vw,620px)] max-xl:min-w-0 max-xl:shadow-[-18px_0_45px_rgb(45_20_80_/_0.12)]">
          <div className="flex h-14 items-center gap-2 border-b px-4">
            <Sparkles className="size-4 text-primary" />
            <p className="flex-1 text-sm font-semibold">Artifact d'amélioration</p>
            {candidate ? <Badge>Proposition V{candidate.version_number}</Badge> : <Badge variant="outline">Version active</Badge>}
          </div>
          <Tabs defaultValue="improved" className="min-h-0 flex-1 gap-0">
            <div className="border-b px-3 py-2">
              <TabsList className={cn("grid w-full", current.source_document_id ? "grid-cols-5" : "grid-cols-4")}>
                <TabsTrigger value="improved">Améliorée</TabsTrigger>
                <TabsTrigger value="diff">Comparatif</TabsTrigger>
                <TabsTrigger value="audit">Audit</TabsTrigger>
                <TabsTrigger value="sources">Sources</TabsTrigger>
                {current.source_document_id && <TabsTrigger value="document">Document</TabsTrigger>}
              </TabsList>
            </div>
            <TabsContent value="improved" className="min-h-0 overflow-hidden p-0">
              <div className="flex h-full flex-col">
                <div className="border-b bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
                  {current.source_document_id && documentStructure?.preservation?.source_binary_immutable ? (
                    <span>
                      Gabarit original conservé : seuls les blocs textuels sont proposés à la révision. {Number(documentStructure.figure_count || 0)} figure(s) et {Number(documentStructure.table_count || 0)} tableau(x) restent attachés à leur emplacement dans le document source.
                    </span>
                  ) : (
                    <span>Sélectionnez un passage dans le texte pour cibler la prochaine demande.</span>
                  )}
                </div>
                <Textarea
                  ref={artifactRef}
                  readOnly
                  value={(candidate || activeVersion)?.content || ""}
                  onSelect={captureSelection}
                  className="min-h-0 flex-1 resize-none rounded-none border-0 p-5 font-sans text-sm leading-6 shadow-none focus-visible:ring-0"
                />
              </div>
            </TabsContent>
            <TabsContent value="diff" className="min-h-0 overflow-hidden p-0">
              <ScrollArea className="h-full">
                <div className="space-y-5 p-4">
                  <div className="grid gap-3 xl:grid-cols-2">
                    <div className="overflow-hidden rounded-xl border">
                      <div className="border-b bg-muted/40 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Texte original</div>
                      <Textarea
                        readOnly
                        value={comparisonOriginal?.content || ""}
                        className="min-h-72 resize-none rounded-none border-0 text-xs leading-5 shadow-none focus-visible:ring-0"
                      />
                    </div>
                    <div className="overflow-hidden rounded-xl border border-primary/30">
                      <div className="border-b bg-primary/5 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-primary">Version améliorée</div>
                      <Textarea
                        readOnly
                        value={(candidate || activeVersion)?.content || ""}
                        className="min-h-72 resize-none rounded-none border-0 text-xs leading-5 shadow-none focus-visible:ring-0"
                      />
                    </div>
                  </div>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="mr-auto text-sm font-semibold">Pourquoi ces modifications ?</h3>
                    </div>
                    <div className="mt-3 space-y-3">
                      {((structuredResult?.changes || []) as Array<Record<string, any>>).map((change, index) => (
                        <div key={change.change_id || index} className="rounded-xl border p-3">
                          <div className="flex items-center gap-2">
                            <Badge variant="outline" className="text-[10px]">{change.operation || "modification"}</Badge>
                            {(change.evidence_refs || []).map((reference: string) => (
                              <Badge key={reference} className="text-[10px]">{evidenceReferenceLabel(reference)}</Badge>
                            ))}
                            {(change.style_refs || []).length > 0 && <Badge variant="outline" className="text-[10px]">Pattern CIR</Badge>}
                          </div>
                          {change.before && <p className="mt-2 line-clamp-4 rounded-lg bg-red-50/70 p-2 text-xs text-red-900">{change.before}</p>}
                          {change.after && <p className="mt-2 line-clamp-5 rounded-lg bg-emerald-50/70 p-2 text-xs text-emerald-900">{change.after}</p>}
                          <p className="mt-2 text-xs leading-5 text-muted-foreground">{change.reason}</p>
                        </div>
                      ))}
                      {!candidate && <p className="text-sm text-muted-foreground">Aucune proposition en attente.</p>}
                    </div>
                  </div>
                </div>
              </ScrollArea>
            </TabsContent>
            <TabsContent value="audit" className="min-h-0 overflow-hidden p-0">
              <ScrollArea className="h-full">
                <div className="space-y-3 p-4">
                  {(candidate?.audit?.findings || []).length ? candidate?.audit?.findings?.map((finding, index) => (
                    <div key={`${finding.code}-${index}`} className="rounded-xl border p-3">
                      <div className="flex items-center gap-2"><ShieldCheck className="size-4 text-primary" /><p className="text-sm font-semibold">{finding.label}</p><Badge variant="outline" className="ml-auto text-[10px]">{finding.severity}</Badge></div>
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">{finding.explanation}</p>
                      <p className="mt-2 text-xs leading-5">{finding.recommendation}</p>
                    </div>
                  )) : <p className="text-sm text-muted-foreground">L'audit détaillé apparaîtra avec la prochaine proposition.</p>}
                  {((structuredResult?.unsupported_claims || []) as Array<Record<string, any>>).map((claim, index) => (
                    <div key={`unsupported-${index}`} className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-amber-950">
                      <p className="text-sm font-semibold">Preuve à confirmer</p>
                      <p className="mt-2 text-xs leading-5">{claim.claim || claim.reason}</p>
                      <p className="mt-1 text-xs text-amber-800">{claim.reason}</p>
                    </div>
                  ))}
                  {((structuredResult?.questions_for_consultant || []) as string[]).map((question, index) => (
                    <div key={`question-${index}`} className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-950">
                      <span className="font-semibold">À valider : </span>{question}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </TabsContent>
            <TabsContent value="sources" className="min-h-0 overflow-hidden p-0">
              <ScrollArea className="h-full">
                <div className="space-y-4 p-4">
                  <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-3 text-emerald-950">
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="size-4" />
                      <p className="text-sm font-semibold">Corpus privé de cette conversation</p>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-emerald-800">
                      Les sources et références validées de cette conversation ne sont utilisées par aucune autre conversation.
                      Pour travailler sur un autre dossier, ouvrez simplement une nouvelle conversation ; aucun changement
                      de projet dans le tableau de bord n'est nécessaire.
                    </p>
                  </div>
                  {current.context?.scholar_handoff && (
                    <div className="rounded-xl border border-primary/30 bg-primary/5 p-3">
                      <div className="flex items-center gap-2"><Library className="size-4 text-primary" /><p className="text-sm font-semibold">Recherche scientifique liée</p></div>
                      <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
                        {current.context.scholar_handoff.assistant_message
                          || current.context.scholar_handoff.error
                          || "La recherche ciblée est rattachée à cette amélioration."}
                      </p>
                    </div>
                  )}
                  {((structuredResult?.sources_used || []) as Array<Record<string, any>>).length > 0 && (
                    <div className="rounded-xl border border-primary/30 bg-primary/5 p-3">
                      <div className="flex items-center gap-2"><Library className="size-4 text-primary" /><p className="text-sm font-semibold">Sources effectivement reliées aux modifications</p></div>
                      <div className="mt-3 space-y-2">
                        {((structuredResult?.sources_used || []) as Array<Record<string, any>>).map((source, index) => (
                          <div key={source.evidence_id || index} className="rounded-lg bg-background p-2.5 text-xs">
                            <p className="font-medium">{source.evidence_id} {source.title ? `· ${source.title}` : ""}</p>
                            <p className="mt-1 text-muted-foreground">
                              {[
                                Array.isArray(source.authors) ? source.authors.slice(0, 4).join(", ") : "",
                                source.year,
                              ].filter(Boolean).join(" · ")}
                            </p>
                            {(source.doi || source.url) && (
                              <a
                                href={source.doi ? `https://doi.org/${String(source.doi).replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")}` : source.url}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-1 inline-block font-medium text-primary hover:underline"
                              >
                                Ouvrir la source
                              </a>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {researchSources.length > 0 && (
                    <div className="space-y-3">
                      {researchSources.map((source) => {
                        const decision = normalizeSourceDecision(source.consultant_decision)
                        const siteUrl = publicationSiteUrl(source)
                        const pdfUrl = String(source.pdf_url || (isDirectPdfUrl(source.url) ? source.url : "") || "").trim()
                        return (
                          <div key={source.candidate_id} className="rounded-xl border p-3">
                            <div className="flex items-start gap-2">
                              <div className="min-w-0 flex-1">
                                <p className="text-sm font-semibold leading-5">{source.title}</p>
                                <p className="mt-1 text-[11px] text-muted-foreground">
                                  {[Array.isArray(source.authors) ? source.authors.slice(0, 3).join(", ") : "", source.year, source.provider].filter(Boolean).join(" · ")}
                                </p>
                              </div>
                              <Badge variant={decision === "accepted" ? "default" : "outline"} className="shrink-0 text-[10px]">
                                {decision === "accepted" ? "Gardée" : decision === "rejected" ? "Écartée" : "À valider"}
                              </Badge>
                            </div>
                            {source.reason && <p className="mt-2 text-xs leading-5">{source.reason}</p>}
                            {source.abstract && <p className="mt-2 line-clamp-5 text-xs leading-5 text-muted-foreground">{source.abstract}</p>}
                            <div className="mt-3 flex flex-wrap items-center gap-2">
                              {siteUrl && (
                                <a
                                  href={siteUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="inline-flex h-7 items-center rounded-lg border bg-background px-2.5 text-[11px] font-medium hover:bg-muted"
                                >
                                  Consulter la publication
                                </a>
                              )}
                              {!siteUrl && pdfUrl && (
                                <a
                                  href={pdfUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="inline-flex h-7 items-center rounded-lg border bg-background px-2.5 text-[11px] font-medium hover:bg-muted"
                                >
                                  Ouvrir le PDF
                                </a>
                              )}
                              {decision !== "rejected" && (
                                  <Button variant="outline" size="sm" className="h-7 text-[11px]" disabled={busy} onClick={() => decideSource(source.candidate_id, "rejected")}>
                                    <X className="size-3" /> Écarter
                                  </Button>
                              )}
                              {decision !== "accepted" && (
                                  <Button size="sm" className="h-7 text-[11px]" disabled={busy} onClick={() => decideSource(source.candidate_id, "accepted")}>
                                    <Check className="size-3" /> Garder
                                  </Button>
                              )}
                              {source.article_card_ready && <span className="text-[10px] text-emerald-700">Preuve préparée pour la rédaction</span>}
                              {decision === "accepted" && !source.article_card_ready && (
                                <span className="text-[10px] text-amber-700">Gardée, mais preuve exploitable pour la rédaction encore indisponible</span>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                  <div className="rounded-xl border p-3">
                    <div className="flex items-center gap-2"><Library className="size-4 text-primary" /><p className="text-sm font-semibold">Corpus scientifique validé</p></div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {candidate?.evidence?.scholar?.selected_article_count != null
                        ? `${candidate.evidence.scholar.selected_article_count} article(s) sélectionné(s), ${candidate.evidence.scholar.writing_ready_card_count || 0} preuve(s) exploitable(s) pour la rédaction.`
                        : "Aucun renfort scientifique n'a été demandé pour cette version."}
                    </p>
                    <div className="mt-3 space-y-2">
                      {(candidate?.evidence?.scholar?.evidence || []).map((source: any, index: number) => (
                        <div key={`${source.article_id}-${index}`} className="rounded-lg bg-muted/50 p-2.5 text-xs">
                          <p className="font-medium">{source.citation_id ? `[${source.citation_id}] ` : ""}{source.title}</p>
                          <p className="mt-1 text-muted-foreground">
                            {[
                              Array.isArray(source.authors) ? source.authors.slice(0, 4).join(", ") : "",
                              source.year,
                            ].filter(Boolean).join(" · ")}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-2">
                            {source.doi && (
                              <a
                                href={`https://doi.org/${String(source.doi).replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")}`}
                                target="_blank"
                                rel="noreferrer"
                                className="font-medium text-primary hover:underline"
                              >
                                DOI
                              </a>
                            )}
                            {source.source_url && (
                              <a href={source.source_url} target="_blank" rel="noreferrer" className="font-medium text-primary hover:underline">
                                Source
                              </a>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-xl border p-3">
                    <div className="flex items-center gap-2"><ShieldCheck className="size-4 text-primary" /><p className="text-sm font-semibold">Analyse CIR du dossier</p></div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {candidate?.evidence?.diagnostic?.available
                        ? `${candidate.evidence.diagnostic.verrous?.length || 0} verrou(s) transmis comme contexte consultatif.`
                        : "Aucun contrôle d'éligibilité n'a été demandé pour cette version."}
                    </p>
                  </div>
                </div>
              </ScrollArea>
            </TabsContent>
            {current.source_document_id && (
              <TabsContent value="document" className="min-h-0 overflow-hidden p-0">
                <div className="flex h-full min-h-0 flex-col">
                  <div className="flex items-center gap-2 border-b bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
                    <FileText className="size-3.5" />
                    <span className="min-w-0 flex-1 truncate">{sourceDocument?.filename || "Document source original"}</span>
                    {sourcePreviewUrl && (
                      <a href={sourcePreviewUrl} target="_blank" rel="noreferrer" className="font-medium text-primary hover:underline">
                        Ouvrir
                      </a>
                    )}
                  </div>
                  {sourcePreviewLoading ? (
                    <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" /> Chargement du document original…
                    </div>
                  ) : sourcePreviewError ? (
                    <div className="m-4 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                      {sourcePreviewError}
                    </div>
                  ) : sourcePreviewUrl && sourceDocumentIsPdf ? (
                    <iframe
                      title={sourceDocument?.filename || "Document source"}
                      src={`${sourcePreviewUrl}#zoom=page-fit&view=Fit`}
                      className="min-h-0 w-full flex-1 border-0 bg-muted/20"
                    />
                  ) : sourcePreviewUrl ? (
                    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
                      <FileText className="size-10 text-muted-foreground" />
                      <p className="max-w-md text-sm text-muted-foreground">
                        Le document original est conservé. Ce format s'ouvre dans son application native afin de préserver sa mise en page.
                      </p>
                      <a href={sourcePreviewUrl} download={sourceDocument?.filename || true} className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground">
                        Télécharger le document original
                      </a>
                    </div>
                  ) : null}
                </div>
              </TabsContent>
            )}
          </Tabs>

          {candidate && (
            <div className="grid grid-cols-2 gap-2 border-t p-3">
              <Button variant="outline" className="gap-2" disabled={busy} onClick={() => decide("rejected")}><X className="size-4" /> Rejeter</Button>
              <Button className="gap-2" disabled={busy} onClick={() => decide("accepted")}><Check className="size-4" /> Accepter</Button>
            </div>
          )}
          <div className="border-t p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold"><History className="size-3.5" /> Historique des versions</div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {versions.map((version) => (
                <Button
                  key={version.version_id}
                  variant={version.is_active ? "default" : "outline"}
                  size="sm"
                  className="h-7 shrink-0 gap-1.5 text-[11px]"
                  disabled={version.is_active || version.status === "candidate" || version.status === "rejected" || busy}
                  onClick={() => restore(version)}
                  title={version.is_active ? "Version active" : "Restaurer cette version"}
                >
                  {!version.is_active && version.status !== "candidate" && <RotateCcw className="size-3" />}
                  {versionLabel(version)}
                </Button>
              ))}
            </div>
          </div>
          </aside>
        </>
      )}
    </div>
  )
}
