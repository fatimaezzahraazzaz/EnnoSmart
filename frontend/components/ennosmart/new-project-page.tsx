"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  Building2,
  CalendarDays,
  CheckCircle2,
  Download,
  FileAudio,
  FileCheck2,
  FileText,
  FolderPlus,
  Info,
  Loader2,
  Lock,
  Sparkles,
  Upload,
  Video,
  X,
} from "lucide-react"

import {
  AppPage,
  NavigateOptions,
  NewProjectPreset,
} from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"

import { createProject, getAccessToken, uploadDocument } from "@/lib/api"
import { setCurrentProjectId } from "@/lib/project-session"

interface NewProjectPageProps {
  navigateTo: (page: AppPage, options?: NavigateOptions) => void
  preset?: NewProjectPreset | null
  returnTo?: AppPage | null
}

type DepositMode = "diagnostic" | "reference"
type FileStatus = "pending" | "uploading" | "done" | "error"
type TranscriptionStatus = "pending" | "transcribing" | "ready" | "error"

type LocalFile = {
  id: string
  file: File
  name: string
  sizeLabel: string
  typeLabel: string
  progress: number
  status: FileStatus
  error?: string
}

type TranscriptionPdfItem = {
  fileId: string
  sourceName: string
  status: TranscriptionStatus
  pdfUrl?: string
  pdfName?: string
  error?: string
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

const AUDIO_EXTENSIONS = new Set([
  "mp3",
  "wav",
  "m4a",
  "aac",
  "flac",
  "ogg",
  "opus",
  "wma",
])

const VIDEO_EXTENSIONS = new Set([
  "mp4",
  "mov",
  "avi",
  "mkv",
  "webm",
  "mpeg",
  "mpg",
  "3gp",
])

const commonDomains = [
  "Aéronautique & Spatial",
  "Automobile & Transport",
  "Biotechnologies & Santé",
  "Chimie & Matériaux",
  "Énergie & Environnement",
  "Électronique & Télécommunications",
  "Génie mécanique",
  "Informatique & Logiciels",
  "Intelligence artificielle & Data",
  "Instrumentation & Mesures",
  "Industrie & Procédés",
  "Robotique & Systèmes autonomes",
  "Sciences physiques",
  "Télécommunications & Réseaux",
]

function currentYear() {
  return String(new Date().getFullYear())
}

function formatSize(size: number) {
  if (size < 1024) return `${size} o`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} Ko`
  return `${(size / (1024 * 1024)).toFixed(1)} Mo`
}

function getExtension(name: string) {
  const parts = name.toLowerCase().split(".")
  return parts.length > 1 ? parts.pop() || "" : ""
}

function isAudioFile(file: File) {
  return (
    file.type.startsWith("audio/") ||
    AUDIO_EXTENSIONS.has(getExtension(file.name))
  )
}

function isVideoFile(file: File) {
  return (
    file.type.startsWith("video/") ||
    VIDEO_EXTENSIONS.has(getExtension(file.name))
  )
}

function isMediaFile(file: File) {
  return isAudioFile(file) || isVideoFile(file)
}

function getFileTypeLabel(name: string) {
  const lower = name.toLowerCase()
  const extension = getExtension(name)

  if (AUDIO_EXTENSIONS.has(extension)) return "Audio"
  if (VIDEO_EXTENSIONS.has(extension)) return "Vidéo"
  if (lower.endsWith(".pdf")) return "PDF"
  if (lower.endsWith(".docx") || lower.endsWith(".doc")) return "Word"
  if (lower.endsWith(".xlsx") || lower.endsWith(".xls")) return "Excel"
  if (lower.endsWith(".pptx") || lower.endsWith(".ppt")) return "PowerPoint"
  if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "Image"
  if (lower.endsWith(".msg")) return "Email"
  if (lower.endsWith(".txt")) return "Texte"

  return "Fichier"
}

function buildPdfDownloadName(originalName: string) {
  const lastDot = originalName.lastIndexOf(".")
  const stem = lastDot > 0 ? originalName.slice(0, lastDot) : originalName

  return `transcription_${stem || "media"}.pdf`
}

function safeId(file: File) {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random()}`

  return `${file.name}-${file.size}-${random}`
}

async function uploadFinalCirReference(params: {
  projectId: number
  file: File
  organisme: string
  projectName: string
  year: string
}) {
  const formData = new FormData()
  formData.append("file", params.file)
  formData.append("organisme", params.organisme || "organisme_unknown")
  formData.append("project", params.projectName || "project_unknown")
  formData.append("year", params.year || "unknown")

  const token = getAccessToken()
  const headers = new Headers()

  if (token) {
    headers.set("Authorization", `Bearer ${token}`)
  }

  const response = await fetch(
    `${API_BASE_URL}/projects/${params.projectId}/cir-final-consultant/upload`,
    {
      method: "POST",
      headers,
      body: formData,
    }
  )

  let data: any = null

  try {
    data = await response.json()
  } catch {
    data = null
  }

  if (!response.ok) {
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : "Erreur lors de l’enregistrement du CIR final."

    throw new Error(detail)
  }

  return data
}

async function transcribeMediaToPdf(projectId: number, file: File) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const formData = new FormData()
  formData.append("file", file)

  const response = await fetch(
    `${API_BASE_URL}/projects/${projectId}/documents/transcribe-video`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: formData,
    }
  )

  if (!response.ok) {
    let detail = "Erreur lors de la transcription."

    try {
      const payload = await response.json()

      if (typeof payload?.detail === "string") {
        detail = payload.detail
      } else if (Array.isArray(payload?.detail)) {
        detail = payload.detail
          .map((item: { msg?: string; type?: string }) => item.msg || item.type)
          .filter(Boolean)
          .join(" | ")
      }
    } catch {
      // La réponse peut être vide ou non JSON.
    }

    throw new Error(detail)
  }

  const blob = await response.blob()

  if (!blob.size) {
    throw new Error("Le backend a retourné un PDF vide.")
  }

  return blob
}

export default function NewProjectPage({
  navigateTo,
  preset = null,
  returnTo = null,
}: NewProjectPageProps) {
  const presetOrganisme = preset?.organisme || ""
  const organismIsLocked = Boolean(preset?.lockOrganisme && presetOrganisme)

  const [organisme, setOrganisme] = useState(presetOrganisme)
  const [projectName, setProjectName] = useState("")
  const [year, setYear] = useState(currentYear())
  const [domainLabel, setDomainLabel] = useState("")
  const [customDomain, setCustomDomain] = useState("")

  const [depositMode, setDepositMode] = useState<DepositMode>("diagnostic")

  const [files, setFiles] = useState<LocalFile[]>([])
  const [finalCirFile, setFinalCirFile] = useState<File | null>(null)
  const [transcriptionPdfs, setTranscriptionPdfs] = useState<TranscriptionPdfItem[]>([])
  const [createdProjectId, setCreatedProjectId] = useState<number | null>(null)

  const [draggingRaw, setDraggingRaw] = useState(false)
  const [draggingFinal, setDraggingFinal] = useState(false)

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")

  const rawFileInputRef = useRef<HTMLInputElement>(null)
  const finalFileInputRef = useRef<HTMLInputElement>(null)
  const pdfUrlsRef = useRef<string[]>([])

  useEffect(() => {
    setOrganisme(presetOrganisme || "")
  }, [presetOrganisme])

  useEffect(() => {
    return () => {
      for (const url of pdfUrlsRef.current) {
        window.URL.revokeObjectURL(url)
      }
    }
  }, [])

  const effectiveDomain =
    domainLabel === "__other__"
      ? customDomain.trim()
      : domainLabel.trim()

  const baseFormIsValid = useMemo(() => {
    return (
      organisme.trim().length > 0 &&
      projectName.trim().length > 0 &&
      year.trim().length > 0 &&
      effectiveDomain.length > 0
    )
  }, [organisme, projectName, year, effectiveDomain])

  const mediaFiles = useMemo(
    () => files.filter((item) => isMediaFile(item.file)),
    [files]
  )

  const canSubmit = useMemo(() => {
    if (createdProjectId !== null) return false
    if (!baseFormIsValid) return false

    if (depositMode === "reference") {
      return finalCirFile !== null
    }

    return true
  }, [
    baseFormIsValid,
    createdProjectId,
    depositMode,
    finalCirFile,
  ])

  const yearOptions = useMemo(() => {
    const current = new Date().getFullYear()
    return Array.from({ length: 9 }, (_, index) =>
      String(current + 1 - index)
    )
  }, [])

  const addRawFiles = (fileList: FileList) => {
    if (createdProjectId !== null) return

    const items = Array.from(fileList).map((file) => ({
      id: safeId(file),
      file,
      name: file.name,
      sizeLabel: formatSize(file.size),
      typeLabel: getFileTypeLabel(file.name),
      progress: 0,
      status: "pending" as const,
    }))

    setFiles((prev) => [...prev, ...items])
    setError("")
    setSuccess("")
  }

  const removeRawFile = (id: string) => {
    if (createdProjectId !== null) return
    setFiles((prev) => prev.filter((item) => item.id !== id))
  }

  const handleRawDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setDraggingRaw(false)

    if (createdProjectId !== null) return

    if (event.dataTransfer.files.length > 0) {
      addRawFiles(event.dataTransfer.files)
    }
  }

  const handleFinalDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setDraggingFinal(false)

    if (createdProjectId !== null) return

    const file = event.dataTransfer.files?.[0]
    if (file) {
      setFinalCirFile(file)
      setError("")
      setSuccess("")
    }
  }

  const resetMessages = () => {
    setError("")
    setSuccess("")
  }

  const updateTranscriptionItem = (
    fileId: string,
    patch: Partial<TranscriptionPdfItem>
  ) => {
    setTranscriptionPdfs((prev) =>
      prev.map((item) =>
        item.fileId === fileId ? { ...item, ...patch } : item
      )
    )
  }

  const downloadTranscriptionPdf = (item: TranscriptionPdfItem) => {
    if (!item.pdfUrl || !item.pdfName) return

    const link = document.createElement("a")
    link.href = item.pdfUrl
    link.download = item.pdfName
    document.body.appendChild(link)
    link.click()
    link.remove()
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()

    if (!baseFormIsValid) {
      setError("Renseignez l’organisme, le projet, l’année et le domaine avant de créer le dossier.")
      return
    }

    if (depositMode === "reference" && !finalCirFile) {
      setError("Ajoutez le CIR final validé ou le CIR précédent avant de continuer.")
      return
    }

    if (createdProjectId !== null) {
      return
    }

    setSubmitting(true)
    setError("")
    setSuccess("")
    setTranscriptionPdfs([])

    try {
      const createdProject = await createProject({
        organisme: organisme.trim(),
        project_name: projectName.trim(),
        year: year.trim(),
        domain_label: effectiveDomain,
      })

      setCurrentProjectId(createdProject.id)
      setCreatedProjectId(createdProject.id)

      if (depositMode === "reference") {
        await uploadFinalCirReference({
          projectId: createdProject.id,
          file: finalCirFile as File,
          organisme: organisme.trim(),
          projectName: projectName.trim(),
          year: year.trim(),
        })

        setSuccess("Dossier créé et CIR final enregistré comme référence.")
        navigateTo(returnTo || "diagnosis")
        return
      }

      const initialTranscriptions: TranscriptionPdfItem[] = mediaFiles.map(
        (item) => ({
          fileId: item.id,
          sourceName: item.name,
          status: "pending",
        })
      )

      // S'il n'y a aucun média, ce tableau reste vide :
      // aucune zone ni aucun bouton PDF ne seront affichés.
      setTranscriptionPdfs(initialTranscriptions)

      let uploadedCount = 0
      let uploadErrors = 0
      const uploadedMediaFiles: LocalFile[] = []

      for (const item of files) {
        setFiles((prev) =>
          prev.map((file) =>
            file.id === item.id
              ? { ...file, status: "uploading", progress: 35, error: undefined }
              : file
          )
        )

        try {
          await uploadDocument(createdProject.id, item.file, "Document brut")
          uploadedCount += 1

          if (isMediaFile(item.file)) {
            uploadedMediaFiles.push(item)
          }

          setFiles((prev) =>
            prev.map((file) =>
              file.id === item.id
                ? { ...file, status: "done", progress: 100 }
                : file
            )
          )
        } catch (err) {
          uploadErrors += 1

          setFiles((prev) =>
            prev.map((file) =>
              file.id === item.id
                ? {
                    ...file,
                    status: "error",
                    progress: 100,
                    error:
                      err instanceof Error
                        ? err.message
                        : "Erreur upload.",
                  }
                : file
            )
          )

          if (isMediaFile(item.file)) {
            updateTranscriptionItem(item.id, {
              status: "error",
              error: "Le média n’a pas pu être importé dans le dossier.",
            })
          }
        }
      }

      let readyPdfCount = 0
      let transcriptionErrorCount = 0

      // Traitement séquentiel : un média à la fois pour éviter plusieurs
      // gros modèles concurrents en mémoire GPU.
      for (const item of uploadedMediaFiles) {
        updateTranscriptionItem(item.id, {
          status: "transcribing",
          error: undefined,
        })

        try {
          const pdfBlob = await transcribeMediaToPdf(
            createdProject.id,
            item.file
          )

          const pdfUrl = window.URL.createObjectURL(pdfBlob)
          pdfUrlsRef.current.push(pdfUrl)

          updateTranscriptionItem(item.id, {
            status: "ready",
            pdfUrl,
            pdfName: buildPdfDownloadName(item.name),
            error: undefined,
          })

          readyPdfCount += 1
        } catch (err) {
          transcriptionErrorCount += 1

          updateTranscriptionItem(item.id, {
            status: "error",
            error:
              err instanceof Error
                ? err.message
                : "Erreur inconnue pendant la transcription.",
          })
        }
      }

      if (mediaFiles.length === 0) {
        if (uploadErrors > 0) {
          setSuccess(
            `Dossier créé. ${uploadedCount} document(s) ajouté(s), ${uploadErrors} erreur(s).`
          )
          return
        }

        // Aucun média : comportement historique, redirection directe.
        navigateTo(returnTo || "diagnosis")
        return
      }

      const details = [
        `${uploadedCount} document(s) ajouté(s)`,
        `${readyPdfCount} PDF de transcription prêt(s)`,
      ]

      if (uploadErrors > 0) {
        details.push(`${uploadErrors} erreur(s) d’upload`)
      }

      if (transcriptionErrorCount > 0) {
        details.push(`${transcriptionErrorCount} erreur(s) de transcription`)
      }

      setSuccess(
        `Dossier créé. ${details.join(", ")}. Téléchargez les PDF ci-dessous avant d’ouvrir EnnoDiagnostic.`
      )
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de créer le dossier."
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="workspace-page-wide pb-10">
      <div className="mx-auto w-full max-w-[1480px] space-y-5">

        {/* ---------------------------------------------------------------- */}
        {/* En-tête de la page                                               */}
        {/* ---------------------------------------------------------------- */}

        <section className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => navigateTo("projects")}
              className="-ml-2 mb-2 h-9 rounded-xl px-2.5 text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-4" />
              Retour aux projets
            </Button>

            <div className="flex items-start gap-3">
              <div className="mt-0.5 grid size-10 shrink-0 place-items-center rounded-xl bg-brand text-brand-foreground shadow-sm">
                <FolderPlus className="size-5" />
              </div>

              <div className="min-w-0">
                <h1 className="text-2xl font-semibold tracking-[-0.03em] text-foreground sm:text-[28px]">
                  {organismIsLocked
                    ? `Nouveau dossier pour ${presetOrganisme}`
                    : "Nouveau dossier CIR"}
                </h1>

                <p className="mt-1 text-sm leading-6 text-muted-foreground">
                  Renseignez le projet, choisissez le type de dépôt puis ajoutez les documents utiles.
                </p>
              </div>
            </div>
          </div>

          {organismIsLocked && (
            <div className="flex shrink-0 items-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 shadow-sm">
              <Building2 className="size-4 text-brand" />
              <div>
                <p className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                  Organisme
                </p>
                <p className="text-sm font-semibold text-foreground">
                  {presetOrganisme}
                </p>
              </div>
            </div>
          )}
        </section>

        {/* ---------------------------------------------------------------- */}
        {/* Messages                                                         */}
        {/* ---------------------------------------------------------------- */}

        {error && (
          <Card className="rounded-2xl border-destructive/25 bg-destructive/[0.045] shadow-none">
            <CardContent className="flex items-start gap-3 p-4 text-destructive">
              <AlertCircle className="mt-0.5 size-5 shrink-0" />
              <p className="text-sm leading-6">{error}</p>
            </CardContent>
          </Card>
        )}

        {success && (
          <Card className="rounded-2xl border-success/25 bg-success/[0.055] shadow-none">
            <CardContent className="flex items-start gap-3 p-4 text-success">
              <CheckCircle2 className="mt-0.5 size-5 shrink-0" />
              <p className="text-sm leading-6">{success}</p>
            </CardContent>
          </Card>
        )}

        <form onSubmit={handleSubmit}>
          <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">

            {/* ============================================================ */}
            {/* Colonne principale                                          */}
            {/* ============================================================ */}

            <div className="space-y-5">

              {/* ---------------------------------------------------------- */}
              {/* Informations du projet                                     */}
              {/* ---------------------------------------------------------- */}

              <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">
                <CardHeader className="border-b border-border/70 bg-muted/[0.10] px-5 py-4 sm:px-6">
                  <div className="flex items-start gap-3">
                    <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-brand/[0.07] text-brand">
                      <FolderPlus className="size-4" />
                    </span>

                    <div>
                      <CardTitle className="text-sm font-semibold">
                        Informations du projet
                      </CardTitle>
                      <CardDescription className="mt-1 text-xs">
                        Ces informations permettent d’identifier le dossier CIR.
                      </CardDescription>
                    </div>
                  </div>
                </CardHeader>

                <CardContent className="grid gap-x-5 gap-y-5 p-5 sm:p-6 md:grid-cols-2">

                  {/* Organisme */}

                  <div className="space-y-2">
                    <Label htmlFor="organisme">
                      Organisme / client <span className="text-destructive">*</span>
                    </Label>

                    <div className="relative">
                      <Building2 className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />

                      <Input
                        id="organisme"
                        value={organisme}
                        onChange={(event) => {
                          if (!organismIsLocked && createdProjectId === null) {
                            setOrganisme(event.target.value)
                          }
                        }}
                        placeholder="Exemple : Girodin"
                        required
                        readOnly={organismIsLocked || createdProjectId !== null}
                        className={`h-11 rounded-xl pl-10 ${
                          organismIsLocked || createdProjectId !== null
                            ? "cursor-not-allowed bg-muted/60 pr-9"
                            : ""
                        }`}
                      />

                      {(organismIsLocked || createdProjectId !== null) && (
                        <Lock className="absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      )}
                    </div>

                    {organismIsLocked && (
                      <p className="text-[11px] leading-5 text-muted-foreground">
                        Organisme sélectionné depuis la liste des projets.
                      </p>
                    )}
                  </div>

                  {/* Nom projet */}

                  <div className="space-y-2">
                    <Label htmlFor="projectName">
                      Nom du projet <span className="text-destructive">*</span>
                    </Label>

                    <Input
                      id="projectName"
                      value={projectName}
                      onChange={(event) => setProjectName(event.target.value)}
                      placeholder="Exemple : TGM100"
                      required
                      readOnly={createdProjectId !== null}
                      className="h-11 rounded-xl"
                    />
                  </div>

                  {/* Année */}

                  <div className="space-y-2">
                    <Label htmlFor="year">
                      Année CIR <span className="text-destructive">*</span>
                    </Label>

                    <div className="relative">
                      <CalendarDays className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />

                      <select
                        id="year"
                        value={year}
                        onChange={(event) => setYear(event.target.value)}
                        required
                        disabled={createdProjectId !== null}
                        className="h-11 w-full appearance-none rounded-xl border border-input bg-background pl-10 pr-9 text-sm text-foreground outline-none transition focus:border-brand/40 focus:ring-2 focus:ring-brand/10 disabled:cursor-not-allowed disabled:bg-muted/60"
                      >
                        {yearOptions.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>

                      <ChevronDownIcon />
                    </div>
                  </div>

                  {/* Domaine */}

                  <div className="space-y-2">
                    <Label htmlFor="domainLabel">
                      Domaine <span className="text-destructive">*</span>
                    </Label>

                    <div className="relative">
                      <Sparkles className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />

                      <select
                        id="domainLabel"
                        value={domainLabel}
                        onChange={(event) => {
                          const value = event.target.value
                          setDomainLabel(value)

                          if (value !== "__other__") {
                            setCustomDomain("")
                          }
                        }}
                        required
                        disabled={createdProjectId !== null}
                        className="h-11 w-full appearance-none rounded-xl border border-input bg-background pl-10 pr-9 text-sm text-foreground outline-none transition focus:border-brand/40 focus:ring-2 focus:ring-brand/10 disabled:cursor-not-allowed disabled:bg-muted/60"
                      >
                        <option value="" disabled>
                          Sélectionnez un domaine
                        </option>

                        {commonDomains.map((domain) => (
                          <option key={domain} value={domain}>
                            {domain}
                          </option>
                        ))}

                        <option value="__other__">
                          Autre domaine
                        </option>
                      </select>

                      <ChevronDownIcon />
                    </div>

                    {domainLabel === "__other__" && (
                      <div className="mt-3 space-y-2 animate-fadeIn">
                        <Label htmlFor="customDomain">
                          Précisez le domaine{" "}
                          <span className="text-destructive">*</span>
                        </Label>

                        <Input
                          id="customDomain"
                          value={customDomain}
                          onChange={(event) =>
                            setCustomDomain(event.target.value)
                          }
                          placeholder="Exemple : Géosciences, Agriculture, Optique…"
                          required
                          readOnly={createdProjectId !== null}
                          className="h-11 rounded-xl"
                        />
                      </div>
                    )}

                    <p className="text-[11px] text-muted-foreground">
                      Le domaine aide les agents à contextualiser le dossier.
                    </p>
                  </div>
                </CardContent>
              </Card>

              {/* ---------------------------------------------------------- */}
              {/* Type de dépôt                                              */}
              {/* ---------------------------------------------------------- */}

              <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">
                <CardHeader className="border-b border-border/70 bg-muted/[0.10] px-5 py-4 sm:px-6">
                  <CardTitle className="text-sm font-semibold">
                    Que souhaitez-vous déposer ?
                  </CardTitle>
                  <CardDescription className="mt-1 text-xs">
                    Séparez les documents de travail des CIR déjà finalisés.
                  </CardDescription>
                </CardHeader>

                <CardContent className="grid gap-3 p-5 sm:p-6 md:grid-cols-2">

                  {/* Diagnostic */}

                  <button
                    type="button"
                    disabled={createdProjectId !== null}
                    onClick={() => {
                      setDepositMode("diagnostic")
                      resetMessages()
                    }}
                    className={`group min-h-[150px] rounded-2xl border p-4 text-left transition-all ${
                      depositMode === "diagnostic"
                        ? "border-brand bg-brand/[0.045] ring-2 ring-brand/15"
                        : "border-border bg-background hover:border-brand/35 hover:bg-brand/[0.018]"
                    } ${
                      createdProjectId !== null
                        ? "cursor-not-allowed opacity-70"
                        : ""
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand">
                        <Upload className="size-5" />
                      </span>

                      <div className="min-w-0">
                        <div className="flex items-start justify-between gap-3">
                          <p className="text-sm font-semibold text-foreground">
                            Documents de travail à analyser
                          </p>

                          {depositMode === "diagnostic" && (
                            <CheckCircle2 className="size-4 shrink-0 text-brand" />
                          )}
                        </div>

                        <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                          Rapports, essais, notes, mails, schémas, tableaux, audio et vidéo destinés à EnnoDiagnostic.
                        </p>
                      </div>
                    </div>
                  </button>

                  {/* Référence */}

                  <button
                    type="button"
                    disabled={createdProjectId !== null}
                    onClick={() => {
                      setDepositMode("reference")
                      resetMessages()
                    }}
                    className={`group min-h-[150px] rounded-2xl border p-4 text-left transition-all ${
                      depositMode === "reference"
                        ? "border-emerald-500/45 bg-emerald-500/[0.045] ring-2 ring-emerald-500/10"
                        : "border-border bg-background hover:border-emerald-500/30 hover:bg-emerald-500/[0.018]"
                    } ${
                      createdProjectId !== null
                        ? "cursor-not-allowed opacity-70"
                        : ""
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-emerald-500/10 text-emerald-600">
                        <FileCheck2 className="size-5" />
                      </span>

                      <div className="min-w-0">
                        <div className="flex items-start justify-between gap-3">
                          <p className="text-sm font-semibold text-foreground">
                            CIR final validé ou CIR précédent
                          </p>

                          {depositMode === "reference" && (
                            <CheckCircle2 className="size-4 shrink-0 text-emerald-600" />
                          )}
                        </div>

                        <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                          CIR déjà finalisé conservé comme référence, séparé des documents bruts du diagnostic.
                        </p>
                      </div>
                    </div>
                  </button>
                </CardContent>
              </Card>

              {/* ---------------------------------------------------------- */}
              {/* Dépôt diagnostic                                           */}
              {/* ---------------------------------------------------------- */}

              {depositMode === "diagnostic" && (
                <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">
                  <CardHeader className="border-b border-border/70 bg-muted/[0.10] px-5 py-4 sm:px-6">
                    <CardTitle className="text-sm font-semibold">
                      Documents de travail à analyser
                    </CardTitle>
                    <CardDescription className="mt-1 text-xs">
                      Les fichiers audio et vidéo seront transcrits après la création du dossier.
                    </CardDescription>
                  </CardHeader>

                  <CardContent className="space-y-4 p-5 sm:p-6">
                    <div
                      onDragOver={(event) => {
                        event.preventDefault()
                        if (createdProjectId === null) {
                          setDraggingRaw(true)
                        }
                      }}
                      onDragLeave={() => setDraggingRaw(false)}
                      onDrop={handleRawDrop}
                      onClick={() => {
                        if (createdProjectId === null) {
                          rawFileInputRef.current?.click()
                        }
                      }}
                      className={`group rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-all ${
                        createdProjectId !== null
                          ? "cursor-not-allowed opacity-70"
                          : "cursor-pointer"
                      } ${
                        draggingRaw
                          ? "border-brand bg-brand/[0.045]"
                          : "border-border bg-muted/[0.12] hover:border-brand/40 hover:bg-brand/[0.02]"
                      }`}
                    >
                      <input
                        ref={rawFileInputRef}
                        type="file"
                        multiple
                        disabled={createdProjectId !== null}
                        accept=".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.msg,.txt,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.wma,.mp4,.mov,.avi,.mkv,.webm,.mpeg,.mpg,.3gp,audio/*,video/*"
                        className="hidden"
                        onChange={(event) => {
                          if (event.target.files?.length) {
                            addRawFiles(event.target.files)
                          }
                        }}
                      />

                      <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-brand/10 text-brand transition group-hover:scale-105">
                        <Upload className="size-6" />
                      </div>

                      <p className="mt-4 text-sm font-semibold text-foreground">
                        Glissez-déposez vos fichiers ici
                      </p>

                      <p className="mt-1 text-xs text-muted-foreground">
                        ou cliquez pour parcourir
                      </p>

                      <p className="mx-auto mt-3 max-w-xl text-[11px] leading-5 text-muted-foreground/80">
                        PDF, DOCX, XLSX, PPTX, images, MSG, TXT, MP3, WAV, M4A, MP4, MOV, AVI, MKV…
                      </p>
                    </div>

                    {files.length > 0 && (
                      <div className="space-y-2">
                        {files.map((item) => {
                          const media = isMediaFile(item.file)

                          return (
                            <div
                              key={item.id}
                              className="rounded-xl border border-border bg-muted/[0.14] p-3"
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="flex min-w-0 items-start gap-3">
                                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-background text-brand shadow-sm">
                                    {isVideoFile(item.file) ? (
                                      <Video className="size-4" />
                                    ) : isAudioFile(item.file) ? (
                                      <FileAudio className="size-4" />
                                    ) : (
                                      <FileText className="size-4" />
                                    )}
                                  </span>

                                  <div className="min-w-0">
                                    <p className="truncate text-sm font-medium text-foreground">
                                      {item.name}
                                    </p>

                                    <p className="mt-0.5 text-xs text-muted-foreground">
                                      {item.typeLabel} · {item.sizeLabel}
                                    </p>

                                    {media && (
                                      <p className="mt-1 text-[11px] text-brand">
                                        Un PDF de transcription sera préparé automatiquement.
                                      </p>
                                    )}

                                    {item.error && (
                                      <p className="mt-1 text-[11px] text-destructive">
                                        {item.error}
                                      </p>
                                    )}
                                  </div>
                                </div>

                                <button
                                  type="button"
                                  onClick={() => removeRawFile(item.id)}
                                  className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition hover:bg-destructive/5 hover:text-destructive"
                                  disabled={
                                    item.status === "uploading" ||
                                    createdProjectId !== null
                                  }
                                >
                                  <X className="size-4" />
                                </button>
                              </div>

                              {(item.status !== "pending" || item.progress > 0) && (
                                <div className="mt-3 flex items-center gap-3">
                                  <Progress value={item.progress} className="h-1.5 flex-1" />
                                  <Badge variant="outline" className="rounded-full text-[10px]">
                                    {item.status}
                                  </Badge>
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}

              {/* ---------------------------------------------------------- */}
              {/* Dépôt référence                                            */}
              {/* ---------------------------------------------------------- */}

              {depositMode === "reference" && (
                <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">
                  <CardHeader className="border-b border-border/70 bg-muted/[0.10] px-5 py-4 sm:px-6">
                    <CardTitle className="text-sm font-semibold">
                      CIR final validé / CIR précédent
                    </CardTitle>
                    <CardDescription className="mt-1 text-xs">
                      Ce fichier est conservé comme référence et reste séparé des documents de diagnostic.
                    </CardDescription>
                  </CardHeader>

                  <CardContent className="space-y-4 p-5 sm:p-6">
                    <div
                      onDragOver={(event) => {
                        event.preventDefault()
                        if (createdProjectId === null) {
                          setDraggingFinal(true)
                        }
                      }}
                      onDragLeave={() => setDraggingFinal(false)}
                      onDrop={handleFinalDrop}
                      onClick={() => {
                        if (createdProjectId === null) {
                          finalFileInputRef.current?.click()
                        }
                      }}
                      className={`group rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-all ${
                        createdProjectId !== null
                          ? "cursor-not-allowed opacity-70"
                          : "cursor-pointer"
                      } ${
                        draggingFinal
                          ? "border-emerald-500 bg-emerald-500/[0.045]"
                          : "border-border bg-muted/[0.12] hover:border-emerald-500/40 hover:bg-emerald-500/[0.02]"
                      }`}
                    >
                      <input
                        ref={finalFileInputRef}
                        type="file"
                        className="hidden"
                        disabled={createdProjectId !== null}
                        accept=".docx,.pdf,.txt,.md"
                        onChange={(event) => {
                          const file = event.target.files?.[0]

                          if (file) {
                            setFinalCirFile(file)
                            resetMessages()
                          }
                        }}
                      />

                      <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-emerald-500/10 text-emerald-600 transition group-hover:scale-105">
                        <FileCheck2 className="size-6" />
                      </div>

                      <p className="mt-4 text-sm font-semibold text-foreground">
                        Glissez le CIR final ici
                      </p>

                      <p className="mt-1 text-xs text-muted-foreground">
                        ou cliquez pour parcourir
                      </p>

                      <p className="mt-3 text-[11px] text-muted-foreground/80">
                        PDF, DOCX, TXT ou Markdown
                      </p>
                    </div>

                    {finalCirFile && (
                      <div className="flex items-start justify-between gap-3 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.045] p-3">
                        <div className="flex min-w-0 items-start gap-3">
                          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-background text-emerald-600 shadow-sm">
                            <FileCheck2 className="size-4" />
                          </span>

                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-foreground">
                              {finalCirFile.name}
                            </p>

                            <p className="mt-0.5 text-xs text-muted-foreground">
                              {getFileTypeLabel(finalCirFile.name)} ·{" "}
                              {formatSize(finalCirFile.size)}
                            </p>
                          </div>
                        </div>

                        <button
                          type="button"
                          onClick={() => setFinalCirFile(null)}
                          className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground transition hover:bg-destructive/5 hover:text-destructive"
                          disabled={submitting || createdProjectId !== null}
                        >
                          <X className="size-4" />
                        </button>
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}

              {/* ---------------------------------------------------------- */}
              {/* Transcriptions                                             */}
              {/* ---------------------------------------------------------- */}

              {transcriptionPdfs.length > 0 && (
                <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">
                  <CardHeader className="border-b border-border/70 bg-muted/[0.10] px-5 py-4 sm:px-6">
                    <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                      <FileAudio className="size-4 text-brand" />
                      PDF de transcription
                    </CardTitle>

                    <CardDescription className="mt-1 text-xs">
                      Les PDF sont proposés uniquement pour les médias déposés.
                    </CardDescription>
                  </CardHeader>

                  <CardContent className="space-y-3 p-5 sm:p-6">
                    {transcriptionPdfs.map((item) => (
                      <div
                        key={item.fileId}
                        className="rounded-xl border border-border bg-muted/[0.12] p-4"
                      >
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="flex min-w-0 items-start gap-3">
                            {item.status === "transcribing" ? (
                              <Loader2 className="mt-0.5 size-5 shrink-0 animate-spin text-brand" />
                            ) : item.status === "ready" ? (
                              <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" />
                            ) : item.status === "error" ? (
                              <AlertCircle className="mt-0.5 size-5 shrink-0 text-destructive" />
                            ) : (
                              <FileAudio className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
                            )}

                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium text-foreground">
                                {item.sourceName}
                              </p>

                              <p className="mt-1 text-xs text-muted-foreground">
                                {item.status === "pending" &&
                                  "En attente de transcription…"}
                                {item.status === "transcribing" &&
                                  "Transcription et génération du PDF en cours…"}
                                {item.status === "ready" &&
                                  "PDF prêt à être téléchargé."}
                                {item.status === "error" &&
                                  (item.error || "Erreur pendant la transcription.")}
                              </p>
                            </div>
                          </div>

                          {item.status === "ready" && (
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="rounded-xl"
                              onClick={() => downloadTranscriptionPdf(item)}
                            >
                              <Download className="size-4" />
                              Télécharger
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}

              {/* ---------------------------------------------------------- */}
              {/* Actions mobile/tablette                                    */}
              {/* ---------------------------------------------------------- */}

              <div className="flex flex-wrap justify-end gap-2 xl:hidden">
                <Button
                  type="button"
                  variant="outline"
                  className="rounded-xl"
                  onClick={() => navigateTo("projects")}
                  disabled={submitting}
                >
                  Annuler
                </Button>

                {createdProjectId !== null ? (
                  <Button
                    type="button"
                    className="rounded-xl bg-brand hover:bg-brand/90"
                    onClick={() => navigateTo(returnTo || "diagnosis")}
                    disabled={submitting}
                  >
                    {returnTo === "improvement"
                      ? "Ouvrir EnnoAmelioration"
                      : "Ouvrir EnnoDiagnostic"}
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    className="rounded-xl bg-brand hover:bg-brand/90"
                    disabled={submitting || !canSubmit}
                  >
                    {submitting ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : depositMode === "reference" ? (
                      <FileCheck2 className="size-4" />
                    ) : (
                      <FolderPlus className="size-4" />
                    )}

                    {submitting
                      ? mediaFiles.length > 0
                        ? "Création et transcription…"
                        : "Création du dossier…"
                      : depositMode === "reference"
                        ? "Créer et enregistrer le CIR final"
                        : "Créer et ouvrir EnnoDiagnostic"}
                  </Button>
                )}
              </div>
            </div>

            {/* ============================================================ */}
            {/* Colonne récapitulative                                      */}
            {/* ============================================================ */}

            <aside className="hidden xl:block">
              <div className="sticky top-5 space-y-4">

                <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">
                  <CardHeader className="border-b border-border/70 bg-muted/[0.10] px-5 py-4">
                    <CardTitle className="text-sm font-semibold">
                      Récapitulatif
                    </CardTitle>

                    <CardDescription className="mt-1 text-xs">
                      Vérifiez les informations avant de créer le dossier.
                    </CardDescription>
                  </CardHeader>

                  <CardContent className="space-y-4 p-5">
                    <SummaryRow label="Organisme" value={organisme || "—"} />
                    <SummaryRow label="Nom du projet" value={projectName || "—"} />
                    <SummaryRow label="Année CIR" value={year || "—"} />
                    <SummaryRow label="Domaine" value={effectiveDomain || "—"} />
                    <SummaryRow
                      label="Type de dépôt"
                      value={
                        depositMode === "diagnostic"
                          ? "Documents de travail"
                          : "CIR final / précédent"
                      }
                    />

                    {depositMode === "diagnostic" && (
                      <SummaryRow
                        label="Fichiers"
                        value={`${files.length} document${files.length > 1 ? "s" : ""}`}
                      />
                    )}

                    {depositMode === "reference" && finalCirFile && (
                      <SummaryRow label="Fichier" value={finalCirFile.name} />
                    )}

                    <div className="rounded-xl border border-brand/10 bg-brand/[0.035] p-3.5">
                      <div className="flex items-start gap-2.5">
                        <Info className="mt-0.5 size-4 shrink-0 text-brand" />

                        <div>
                          <p className="text-xs font-semibold text-foreground">
                            Conseil
                          </p>

                          <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
                            Les informations du projet resteront disponibles dans le dossier après sa création.
                          </p>
                        </div>
                      </div>
                    </div>

                    {createdProjectId !== null ? (
                      <Button
                        type="button"
                        className="h-11 w-full rounded-xl bg-brand hover:bg-brand/90"
                        onClick={() => navigateTo(returnTo || "diagnosis")}
                        disabled={submitting}
                      >
                        {returnTo === "improvement"
                          ? "Ouvrir EnnoAmelioration"
                          : "Ouvrir EnnoDiagnostic"}
                      </Button>
                    ) : (
                      <Button
                        type="submit"
                        className="h-11 w-full rounded-xl bg-brand shadow-[0_8px_22px_rgba(90,50,150,.16)] hover:bg-brand/90"
                        disabled={submitting || !canSubmit}
                      >
                        {submitting ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : depositMode === "reference" ? (
                          <FileCheck2 className="size-4" />
                        ) : (
                          <FolderPlus className="size-4" />
                        )}

                        {submitting
                          ? mediaFiles.length > 0
                            ? "Création et transcription…"
                            : "Création du dossier…"
                          : depositMode === "reference"
                            ? "Créer et enregistrer"
                            : "Créer le dossier"}
                      </Button>
                    )}

                    <Button
                      type="button"
                      variant="ghost"
                      className="w-full rounded-xl text-muted-foreground"
                      onClick={() => navigateTo("projects")}
                      disabled={submitting}
                    >
                      Annuler
                    </Button>
                  </CardContent>
                </Card>
              </div>
            </aside>
          </div>
        </form>
      </div>
    </div>
  )
}


/* -------------------------------------------------------------------------- */
/* Sous-composants visuels                                                    */
/* -------------------------------------------------------------------------- */

function SummaryRow({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <div className="border-b border-border/65 pb-3 last:border-b-0 last:pb-0">
      <p className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
        {label}
      </p>

      <p className="mt-1 break-words text-sm font-medium text-foreground">
        {value}
      </p>
    </div>
  )
}


function ChevronDownIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}
