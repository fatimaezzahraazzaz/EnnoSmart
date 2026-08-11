"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileAudio,
  FileCheck2,
  FileText,
  FolderPlus,
  Loader2,
  Lock,
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
  "Génie mécanique",
  "Informatique / IA",
  "Électronique",
  "Biotechnologie",
  "Matériaux",
  "Énergie",
  "Chimie",
  "Robotique",
  "Automobile",
  "Aéronautique",
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

  const baseFormIsValid = useMemo(() => {
    return (
      organisme.trim().length > 0 &&
      projectName.trim().length > 0 &&
      year.trim().length > 0 &&
      domainLabel.trim().length > 0
    )
  }, [organisme, projectName, year, domainLabel])

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
        domain_label: domainLabel.trim(),
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
    <div className="mx-auto max-w-5xl space-y-6 p-5 sm:p-7 lg:p-9">
      <div className="ennoma-page-header">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigateTo("dashboard")}
          className="mb-3"
        >
          <ArrowLeft className="size-4 mr-2" />
          Retour au tableau de bord
        </Button>

        <div className="flex items-center gap-2">
          <div className="size-8 rounded-lg bg-brand flex items-center justify-center">
            <FolderPlus className="size-4 text-brand-foreground" />
          </div>

          <h1 className="text-2xl font-bold text-foreground">
            {organismIsLocked
              ? `Nouveau dossier pour ${presetOrganisme}`
              : "Nouveau dossier CIR"}
          </h1>
        </div>

        <p className="text-sm text-muted-foreground mt-1">
          Créez un dossier puis choisissez le type de document à déposer.
        </p>
      </div>

      {error && (
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="p-4 flex items-start gap-3 text-destructive">
            <AlertCircle className="size-5 mt-0.5" />
            <p className="text-sm">{error}</p>
          </CardContent>
        </Card>
      )}

      {success && (
        <Card className="border-success/30 bg-success/10">
          <CardContent className="p-4 flex items-start gap-3 text-success">
            <CheckCircle2 className="size-5 mt-0.5" />
            <p className="text-sm">{success}</p>
          </CardContent>
        </Card>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Informations du projet</CardTitle>
            <CardDescription className="text-xs">
              Ces informations permettent d’identifier le dossier CIR.
            </CardDescription>
          </CardHeader>

          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="organisme">Organisme / client</Label>

              <div className="relative">
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
                  className={
                    organismIsLocked || createdProjectId !== null
                      ? "bg-muted pr-9 cursor-not-allowed"
                      : ""
                  }
                />

                {(organismIsLocked || createdProjectId !== null) && (
                  <Lock className="absolute right-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
                )}
              </div>

              {organismIsLocked && (
                <p className="text-xs text-muted-foreground">
                  Organisme sélectionné depuis la liste des projets.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="projectName">Nom du projet</Label>
              <Input
                id="projectName"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="Exemple : TGM100"
                required
                readOnly={createdProjectId !== null}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="year">Année CIR</Label>
              <Input
                id="year"
                value={year}
                onChange={(event) => setYear(event.target.value)}
                placeholder="2023"
                required
                readOnly={createdProjectId !== null}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="domainLabel">Domaine</Label>
              <Input
                id="domainLabel"
                list="domain-list"
                value={domainLabel}
                onChange={(event) => setDomainLabel(event.target.value)}
                placeholder="Exemple : Génie mécanique"
                required
                readOnly={createdProjectId !== null}
              />

              <datalist id="domain-list">
                {commonDomains.map((domain) => (
                  <option key={domain} value={domain} />
                ))}
              </datalist>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Que souhaitez-vous déposer ?</CardTitle>
            <CardDescription className="text-xs">
              Choisissez le type de dépôt pour éviter de mélanger les documents de travail avec un CIR final validé.
            </CardDescription>
          </CardHeader>

          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <button
              type="button"
              disabled={createdProjectId !== null}
              onClick={() => {
                setDepositMode("diagnostic")
                resetMessages()
              }}
              className={`text-left rounded-xl border p-4 transition-all ${
                depositMode === "diagnostic"
                  ? "border-brand bg-brand/5 ring-2 ring-brand/20"
                  : "border-border hover:border-brand/50 hover:bg-muted/30"
              } ${createdProjectId !== null ? "cursor-not-allowed opacity-70" : ""}`}
            >
              <div className="flex items-start gap-3">
                <div className="size-10 rounded-xl bg-brand/10 flex items-center justify-center flex-shrink-0">
                  <Upload className="size-5 text-brand" />
                </div>

                <div>
                  <p className="text-sm font-semibold text-foreground">
                    Documents de travail à analyser
                  </p>

                  <p className="text-xs leading-5 text-muted-foreground mt-1">
                    Déposez les rapports, essais, notes, mails, schémas, tableaux,
                    fichiers audio ou vidéos. Les médias seront transcrits et leur
                    PDF sera proposé au téléchargement.
                  </p>
                </div>
              </div>
            </button>

            <button
              type="button"
              disabled={createdProjectId !== null}
              onClick={() => {
                setDepositMode("reference")
                resetMessages()
              }}
              className={`text-left rounded-xl border p-4 transition-all ${
                depositMode === "reference"
                  ? "border-brand bg-brand/5 ring-2 ring-brand/20"
                  : "border-border hover:border-brand/50 hover:bg-muted/30"
              } ${createdProjectId !== null ? "cursor-not-allowed opacity-70" : ""}`}
            >
              <div className="flex items-start gap-3">
                <div className="size-10 rounded-xl bg-emerald-500/10 flex items-center justify-center flex-shrink-0">
                  <FileCheck2 className="size-5 text-emerald-600" />
                </div>

                <div>
                  <p className="text-sm font-semibold text-foreground">
                    CIR final validé ou CIR précédent
                  </p>

                  <p className="text-xs leading-5 text-muted-foreground mt-1">
                    Déposez un CIR déjà finalisé pour le conserver comme référence.
                    Il ne sera pas traité comme document brut : il servira pour
                    les futurs dossiers et l’amélioration du style de rédaction.
                  </p>
                </div>
              </div>
            </button>
          </CardContent>
        </Card>

        {depositMode === "diagnostic" && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">
                Documents de travail à analyser
              </CardTitle>
              <CardDescription className="text-xs">
                Les documents classiques seront importés. Les fichiers audio ou
                vidéo seront également transcrits après la création du dossier.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
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
                className={`border-2 border-dashed rounded-xl p-10 text-center transition-all ${
                  createdProjectId !== null
                    ? "cursor-not-allowed opacity-70"
                    : "cursor-pointer"
                } ${
                  draggingRaw
                    ? "border-brand bg-brand/5"
                    : "border-border hover:border-brand/50 hover:bg-muted/30"
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

                <div className="size-12 mx-auto rounded-full bg-brand/10 flex items-center justify-center mb-4">
                  <Upload className="size-6 text-brand" />
                </div>

                <p className="text-sm font-semibold text-foreground">
                  Déposez les documents ici ou cliquez pour choisir
                </p>

                <p className="text-xs text-muted-foreground mt-1">
                  PDF, Word, Excel, PowerPoint, images, MSG, TXT, MP3, WAV,
                  M4A, MP4, MOV, AVI, MKV…
                </p>
              </div>

              {files.length > 0 && (
                <div className="space-y-2">
                  {files.map((item) => {
                    const media = isMediaFile(item.file)

                    return (
                      <div
                        key={item.id}
                        className="p-3 rounded-md border border-border bg-muted/30 space-y-2"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-start gap-3 min-w-0">
                            {isVideoFile(item.file) ? (
                              <Video className="size-4 text-brand mt-0.5 flex-shrink-0" />
                            ) : isAudioFile(item.file) ? (
                              <FileAudio className="size-4 text-brand mt-0.5 flex-shrink-0" />
                            ) : (
                              <FileText className="size-4 text-brand mt-0.5 flex-shrink-0" />
                            )}

                            <div className="min-w-0">
                              <p className="text-sm font-medium text-foreground truncate">
                                {item.name}
                              </p>

                              <p className="text-xs text-muted-foreground">
                                {item.typeLabel} · {item.sizeLabel}
                              </p>

                              {media && (
                                <p className="text-xs text-brand mt-1">
                                  Un PDF de transcription sera préparé après création.
                                </p>
                              )}

                              {item.error && (
                                <p className="text-xs text-destructive mt-1">
                                  {item.error}
                                </p>
                              )}
                            </div>
                          </div>

                          <button
                            type="button"
                            onClick={() => removeRawFile(item.id)}
                            className="text-muted-foreground hover:text-destructive"
                            disabled={
                              item.status === "uploading" ||
                              createdProjectId !== null
                            }
                          >
                            <X className="size-4" />
                          </button>
                        </div>

                        <div className="flex items-center gap-2">
                          <Progress value={item.progress} className="h-2" />
                          <Badge variant="outline" className="text-xs">
                            {item.status}
                          </Badge>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {depositMode === "reference" && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">
                CIR final validé / CIR précédent
              </CardTitle>
              <CardDescription className="text-xs">
                Ce fichier sera conservé comme référence du projet. Il ne sera pas mélangé avec les documents de diagnostic.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
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
                className={`border-2 border-dashed rounded-xl p-10 text-center transition-all ${
                  createdProjectId !== null
                    ? "cursor-not-allowed opacity-70"
                    : "cursor-pointer"
                } ${
                  draggingFinal
                    ? "border-emerald-500 bg-emerald-500/5"
                    : "border-border hover:border-emerald-500/50 hover:bg-muted/30"
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

                <div className="size-12 mx-auto rounded-full bg-emerald-500/10 flex items-center justify-center mb-4">
                  <FileCheck2 className="size-6 text-emerald-600" />
                </div>

                <p className="text-sm font-semibold text-foreground">
                  Déposez le CIR final ici ou cliquez pour choisir
                </p>

                <p className="text-xs text-muted-foreground mt-1">
                  PDF ou Word du CIR final validé
                </p>
              </div>

              {finalCirFile && (
                <div className="p-3 rounded-md border border-emerald-200 bg-emerald-50 flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3 min-w-0">
                    <FileCheck2 className="size-4 text-emerald-600 mt-0.5 flex-shrink-0" />

                    <div className="min-w-0">
                      <p className="text-sm font-medium text-emerald-950 truncate">
                        {finalCirFile.name}
                      </p>

                      <p className="text-xs text-emerald-800">
                        {getFileTypeLabel(finalCirFile.name)} · {formatSize(finalCirFile.size)}
                      </p>
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={() => setFinalCirFile(null)}
                    className="text-emerald-700 hover:text-destructive"
                    disabled={submitting || createdProjectId !== null}
                  >
                    <X className="size-4" />
                  </button>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {transcriptionPdfs.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <FileAudio className="size-4 text-brand" />
                PDF de transcription
              </CardTitle>
              <CardDescription className="text-xs">
                Cette section apparaît uniquement lorsqu’un fichier audio ou
                vidéo a été déposé. Le bouton devient actif quand son PDF est prêt.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-3">
              {transcriptionPdfs.map((item) => (
                <div
                  key={item.fileId}
                  className="rounded-lg border border-border bg-muted/20 p-4"
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-start gap-3 min-w-0">
                      {item.status === "transcribing" ? (
                        <Loader2 className="size-5 mt-0.5 animate-spin text-brand flex-shrink-0" />
                      ) : item.status === "ready" ? (
                        <CheckCircle2 className="size-5 mt-0.5 text-success flex-shrink-0" />
                      ) : item.status === "error" ? (
                        <AlertCircle className="size-5 mt-0.5 text-destructive flex-shrink-0" />
                      ) : (
                        <FileAudio className="size-5 mt-0.5 text-muted-foreground flex-shrink-0" />
                      )}

                      <div className="min-w-0">
                        <p className="text-sm font-medium text-foreground truncate">
                          {item.sourceName}
                        </p>

                        <p className="text-xs text-muted-foreground mt-1">
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
                        onClick={() => downloadTranscriptionPdf(item)}
                      >
                        <Download className="size-4 mr-2" />
                        Télécharger le PDF
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => navigateTo("dashboard")}
            disabled={submitting}
          >
            Annuler
          </Button>

          {createdProjectId !== null ? (
            <Button
              type="button"
              className="bg-brand hover:bg-brand/90"
              onClick={() => navigateTo(returnTo || "diagnosis")}
              disabled={submitting}
            >
              {returnTo === "improvement" ? "Ouvrir EnnoAmelioration" : "Ouvrir EnnoDiagnostic"}
            </Button>
          ) : (
            <Button
              type="submit"
              className="bg-brand hover:bg-brand/90"
              disabled={submitting || !canSubmit}
            >
              {submitting ? (
                <Loader2 className="size-4 mr-2 animate-spin" />
              ) : depositMode === "reference" ? (
                <FileCheck2 className="size-4 mr-2" />
              ) : (
                <FolderPlus className="size-4 mr-2" />
              )}

              {submitting
                ? mediaFiles.length > 0
                  ? "Création et transcription en cours…"
                  : "Création du dossier…"
                : depositMode === "reference"
                  ? "Créer et enregistrer le CIR final"
                  : "Créer et ouvrir EnnoDiagnostic"}
            </Button>
          )}
        </div>
      </form>
    </div>
  )
}
