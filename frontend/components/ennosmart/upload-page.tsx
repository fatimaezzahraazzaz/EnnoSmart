"use client"

import { useEffect, useRef, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  CloudUpload,
  Database,
  Download,
  FileAudio,
  FileText,
  Loader2,
  RefreshCw,
  Upload,
  Video,
  X,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"

import {
  getAccessToken,
  getDocuments,
  getProjects,
  importExistingDocuments,
  uploadDocument,
  type DocumentRead,
  type ProjectRead,
} from "@/lib/api"
import { getCurrentProjectId, setCurrentProjectId } from "@/lib/project-session"

interface UploadPageProps {
  navigateTo: (page: AppPage) => void
}

type UploadStatus = "pending" | "uploading" | "done" | "error"
type UploadTab = "documents" | "media"

interface LocalFileItem {
  id: string
  file: File
  name: string
  sizeLabel: string
  typeLabel: string
  status: UploadStatus
  progress: number
  error?: string
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

const AUDIO_VIDEO_EXTENSIONS = new Set([
  "mp3",
  "wav",
  "m4a",
  "aac",
  "flac",
  "ogg",
  "opus",
  "wma",
  "mp4",
  "mov",
  "avi",
  "mkv",
  "webm",
  "mpeg",
  "mpg",
  "3gp",
])

function getFileTypeLabel(name: string) {
  const lower = name.toLowerCase()

  if (lower.endsWith(".pdf")) return "PDF"
  if (lower.endsWith(".docx") || lower.endsWith(".doc")) return "Word"
  if (lower.endsWith(".xlsx") || lower.endsWith(".xls")) return "Excel"
  if (lower.endsWith(".pptx") || lower.endsWith(".ppt")) return "PowerPoint"
  if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "Image"
  if (lower.endsWith(".msg")) return "Email"
  if (lower.endsWith(".txt")) return "Texte"

  return "Fichier"
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

function isSupportedMediaFile(file: File) {
  const extension = getExtension(file.name)

  return (
    file.type.startsWith("audio/") ||
    file.type.startsWith("video/") ||
    AUDIO_VIDEO_EXTENSIONS.has(extension)
  )
}

function buildPdfDownloadName(originalName: string) {
  const lastDot = originalName.lastIndexOf(".")
  const stem = lastDot > 0 ? originalName.slice(0, lastDot) : originalName

  return `transcription_${stem || "media"}.pdf`
}

export default function UploadPage({ navigateTo }: UploadPageProps) {
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [project, setProject] = useState<ProjectRead | null>(null)
  const [documents, setDocuments] = useState<DocumentRead[]>([])
  const [localFiles, setLocalFiles] = useState<LocalFileItem[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")

  const [activeTab, setActiveTab] = useState<UploadTab>("documents")
  const [mediaFile, setMediaFile] = useState<File | null>(null)
  const [mediaDragging, setMediaDragging] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [transcriptionError, setTranscriptionError] = useState("")
  const [transcriptionSuccess, setTranscriptionSuccess] = useState("")
  const [transcriptionPdfUrl, setTranscriptionPdfUrl] = useState<string | null>(null)
  const [transcriptionPdfName, setTranscriptionPdfName] = useState("")

  const fileInputRef = useRef<HTMLInputElement>(null)
  const mediaFileInputRef = useRef<HTMLInputElement>(null)

  const loadData = async () => {
    setLoading(true)
    setError("")

    try {
      const projectList = await getProjects()
      setProjects(projectList)

      if (projectList.length === 0) {
        setProject(null)
        setDocuments([])
        return
      }

      const storedProjectId = getCurrentProjectId()
      const selectedProject =
        projectList.find((item) => item.id === storedProjectId) || projectList[0]

      setCurrentProjectId(selectedProject.id)
      setProject(selectedProject)

      const docs = await getDocuments(selectedProject.id)
      setDocuments(docs)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger les documents."
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  useEffect(() => {
    return () => {
      if (transcriptionPdfUrl) {
        window.URL.revokeObjectURL(transcriptionPdfUrl)
      }
    }
  }, [transcriptionPdfUrl])

  const addFiles = (fileList: FileList) => {
    const items = Array.from(fileList).map((file) => ({
      id: `${file.name}-${file.size}-${crypto.randomUUID()}`,
      file,
      name: file.name,
      sizeLabel: formatSize(file.size),
      typeLabel: getFileTypeLabel(file.name),
      status: "pending" as UploadStatus,
      progress: 0,
    }))

    setLocalFiles((prev) => [...prev, ...items])
    setSuccess("")
    setError("")
  }

  const removeFile = (id: string) => {
    setLocalFiles((prev) => prev.filter((item) => item.id !== id))
  }

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setIsDragging(false)

    if (event.dataTransfer.files.length > 0) {
      addFiles(event.dataTransfer.files)
    }
  }

  const uploadSelectedFiles = async () => {
    if (!project) return

    if (localFiles.length === 0) {
      setError("Ajoute au moins un fichier avant de lancer l’upload.")
      return
    }

    setUploading(true)
    setError("")
    setSuccess("")

    let uploadedCount = 0

    for (const item of localFiles) {
      setLocalFiles((prev) =>
        prev.map((file) =>
          file.id === item.id
            ? { ...file, status: "uploading", progress: 30, error: undefined }
            : file
        )
      )

      try {
        const uploaded = await uploadDocument(project.id, item.file, "Document brut")
        uploadedCount += 1

        setDocuments((prev) => [uploaded, ...prev])
        setLocalFiles((prev) =>
          prev.map((file) =>
            file.id === item.id
              ? { ...file, status: "done", progress: 100 }
              : file
          )
        )
      } catch (err) {
        setLocalFiles((prev) =>
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
      }
    }

    setUploading(false)

    if (uploadedCount > 0) {
      setSuccess(`${uploadedCount} document(s) importé(s). Redirection vers EnnoDiagnostic...`)
      setTimeout(() => navigateTo("diagnosis"), 700)
    }
  }

  const importExisting = async () => {
    if (!project) return

    setImporting(true)
    setError("")
    setSuccess("")

    try {
      const imported = await importExistingDocuments(project.id)
      const docs = await getDocuments(project.id)
      setDocuments(docs)
      setSuccess(`${imported.length} document(s) lié(s). Redirection vers EnnoDiagnostic...`)
      setTimeout(() => navigateTo("diagnosis"), 700)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible d’importer les documents existants."
      )
    } finally {
      setImporting(false)
    }
  }

  const clearTranscriptionPdf = () => {
    if (transcriptionPdfUrl) {
      window.URL.revokeObjectURL(transcriptionPdfUrl)
    }

    setTranscriptionPdfUrl(null)
    setTranscriptionPdfName("")
  }

  const selectMediaFile = (file: File | null) => {
    setTranscriptionError("")
    setTranscriptionSuccess("")
    clearTranscriptionPdf()

    if (!file) {
      setMediaFile(null)
      return
    }

    if (!isSupportedMediaFile(file)) {
      setMediaFile(null)
      setTranscriptionError(
        "Format non supporté. Utilise un fichier audio ou vidéo : MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, WMA, MP4, MOV, AVI, MKV, WEBM, MPEG, MPG ou 3GP."
      )
      return
    }

    setMediaFile(file)
  }

  const handleMediaDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setMediaDragging(false)

    const file = event.dataTransfer.files?.[0] || null
    selectMediaFile(file)
  }

  const handleTranscribe = async () => {
    if (!project || !mediaFile) return

    const token = getAccessToken()

    if (!token) {
      setTranscriptionError("Utilisateur non authentifié.")
      return
    }

    setTranscribing(true)
    setTranscriptionError("")
    setTranscriptionSuccess("")
    clearTranscriptionPdf()

    const formData = new FormData()
    formData.append("file", mediaFile)

    try {
      const response = await fetch(
        `${API_BASE_URL}/projects/${project.id}/documents/transcribe-video`,
        {
          method: "POST",
          body: formData,
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      )

      if (!response.ok) {
        let detail = "Erreur lors de la transcription."

        try {
          const errorData = await response.json()

          if (typeof errorData?.detail === "string") {
            detail = errorData.detail
          } else if (Array.isArray(errorData?.detail)) {
            detail = errorData.detail
              .map((item: { msg?: string; type?: string }) => item.msg || item.type)
              .filter(Boolean)
              .join(" | ")
          }
        } catch {
          // La réponse d'erreur peut être vide ou non JSON.
        }

        throw new Error(detail)
      }

      const blob = await response.blob()

      if (!blob.size) {
        throw new Error("Le backend a retourné un PDF vide.")
      }

      const objectUrl = window.URL.createObjectURL(blob)
      const downloadName = buildPdfDownloadName(mediaFile.name)

      // IMPORTANT :
      // On ne télécharge plus automatiquement.
      // On conserve le Blob en mémoire et on active le bouton de téléchargement.
      setTranscriptionPdfUrl(objectUrl)
      setTranscriptionPdfName(downloadName)
      setTranscriptionSuccess(
        `Transcription terminée. Le PDF de "${mediaFile.name}" est prêt à être téléchargé.`
      )
    } catch (err) {
      setTranscriptionError(
        err instanceof Error
          ? err.message
          : "Erreur inconnue pendant la transcription."
      )
    } finally {
      setTranscribing(false)
    }
  }

  const downloadTranscriptionPdf = () => {
    if (!transcriptionPdfUrl || !transcriptionPdfName) return

    const link = document.createElement("a")
    link.href = transcriptionPdfUrl
    link.download = transcriptionPdfName
    document.body.appendChild(link)
    link.click()
    link.remove()
  }

  if (loading) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <Card>
          <CardContent className="p-8 flex items-center justify-center gap-3 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Chargement des documents depuis FastAPI...
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!project) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center">
            <p className="text-sm font-medium text-foreground">
              Aucun projet disponible.
            </p>
            <Button
              className="mt-4"
              variant="outline"
              onClick={() => navigateTo("projects")}
            >
              Retour aux projets
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-5 sm:p-7 lg:p-9">
      <div className="ennoma-page-header flex flex-wrap items-start justify-between gap-4">
        <div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigateTo("project-detail")}
            className="mb-3"
          >
            <ArrowLeft className="size-4 mr-2" />
            Retour au détail projet
          </Button>

          <div className="flex items-center gap-2">
            <div className="size-8 rounded-lg bg-brand flex items-center justify-center">
              <Upload className="size-4 text-brand-foreground" />
            </div>
            <h1 className="text-2xl font-bold text-foreground tracking-tight">
              Dépôt de documents
            </h1>
          </div>

          <p className="text-sm text-muted-foreground mt-1">
            {project.organisme} — {project.project_name} — {project.year}
          </p>
        </div>

        <Button variant="outline" size="sm" onClick={loadData}>
          <RefreshCw className="size-4 mr-2" />
          Actualiser
        </Button>
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

      <div
        className="inline-flex rounded-lg border border-border bg-muted/30 p-1"
        role="tablist"
        aria-label="Type d'import"
      >
        <Button
          type="button"
          size="sm"
          variant={activeTab === "documents" ? "default" : "ghost"}
          onClick={() => setActiveTab("documents")}
          role="tab"
          aria-selected={activeTab === "documents"}
        >
          <FileText className="size-4 mr-2" />
          Documents de travail
        </Button>

        <Button
          type="button"
          size="sm"
          variant={activeTab === "media" ? "default" : "ghost"}
          onClick={() => setActiveTab("media")}
          role="tab"
          aria-selected={activeTab === "media"}
        >
          <Video className="size-4 mr-2" />
          Vidéo / Audio
        </Button>
      </div>

      {activeTab === "documents" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <CloudUpload className="size-4 text-brand" />
              Import de fichiers
            </CardTitle>
            <CardDescription className="text-xs">
              Après upload, le consultant est redirigé vers EnnoDiagnostic pour lancer l’analyse.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            <div
              onDragOver={(event) => {
                event.preventDefault()
                setIsDragging(true)
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`cursor-pointer rounded-3xl border-2 border-dashed p-12 text-center transition-all hover:-translate-y-0.5 hover:border-brand/50 hover:bg-brand/[0.035] ${
                isDragging
                  ? "border-brand bg-brand/5"
                  : "border-border hover:border-brand/50 hover:bg-muted/30"
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => {
                  if (event.target.files?.length) {
                    addFiles(event.target.files)
                  }
                }}
              />

              <div className="size-12 mx-auto rounded-full bg-brand/10 flex items-center justify-center mb-4">
                <Upload className="size-6 text-brand" />
              </div>

              <p className="text-sm font-semibold text-foreground">
                Dépose les fichiers ici ou clique pour choisir
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                PDF, Word, Excel, PowerPoint, images, MSG, TXT
              </p>
            </div>

            {localFiles.length > 0 && (
              <div className="space-y-2">
                {localFiles.map((item) => (
                  <div
                    key={item.id}
                    className="p-3 rounded-md border border-border bg-muted/30 space-y-2"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-start gap-3 min-w-0">
                        <FileText className="size-4 text-brand mt-0.5 flex-shrink-0" />
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-foreground truncate">
                            {item.name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {item.typeLabel} · {item.sizeLabel}
                          </p>
                          {item.error && (
                            <p className="text-xs text-destructive mt-1">
                              {item.error}
                            </p>
                          )}
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => removeFile(item.id)}
                        className="text-muted-foreground hover:text-destructive"
                        disabled={item.status === "uploading"}
                        aria-label={`Supprimer ${item.name}`}
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
                ))}
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <Button
                className="bg-brand hover:bg-brand/90"
                onClick={uploadSelectedFiles}
                disabled={uploading || localFiles.length === 0}
              >
                {uploading ? (
                  <Loader2 className="size-4 mr-2 animate-spin" />
                ) : (
                  <Upload className="size-4 mr-2" />
                )}
                Envoyer et ouvrir EnnoDiagnostic
              </Button>

              <Button
                variant="outline"
                onClick={importExisting}
                disabled={importing}
              >
                {importing ? (
                  <Loader2 className="size-4 mr-2 animate-spin" />
                ) : (
                  <Database className="size-4 mr-2" />
                )}
                Importer existants et ouvrir EnnoDiagnostic
              </Button>
            </div>

            <p className="text-xs text-muted-foreground">
              Documents actuellement en base : {documents.length}
            </p>
          </CardContent>
        </Card>
      )}

      {activeTab === "media" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Video className="size-4 text-brand" />
              Transcription vidéo / audio
            </CardTitle>
            <CardDescription className="text-xs">
              Dépose un fichier audio ou vidéo. EnnoSmart le transcrit, puis prépare
              un PDF que tu peux télécharger une fois le traitement terminé.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            {transcriptionError && (
              <div className="p-3 rounded-md border border-destructive/30 bg-destructive/10 text-destructive text-sm">
                <AlertCircle className="inline size-4 mr-2" />
                {transcriptionError}
              </div>
            )}

            {transcriptionSuccess && (
              <div className="p-3 rounded-md border border-success/30 bg-success/10 text-success text-sm">
                <CheckCircle2 className="inline size-4 mr-2" />
                {transcriptionSuccess}
              </div>
            )}

            <div
              onDragOver={(event) => {
                event.preventDefault()
                setMediaDragging(true)
              }}
              onDragLeave={() => setMediaDragging(false)}
              onDrop={handleMediaDrop}
              onClick={() => {
                if (!transcribing) {
                  mediaFileInputRef.current?.click()
                }
              }}
              className={`border-2 border-dashed rounded-xl p-10 text-center transition-all ${
                transcribing
                  ? "cursor-not-allowed opacity-70"
                  : "cursor-pointer"
              } ${
                mediaDragging
                  ? "border-brand bg-brand/5"
                  : "border-border hover:border-brand/50 hover:bg-muted/30"
              }`}
            >
              <input
                ref={mediaFileInputRef}
                type="file"
                accept="audio/*,video/*,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.wma,.mp4,.mov,.avi,.mkv,.webm,.mpeg,.mpg,.3gp"
                className="hidden"
                disabled={transcribing}
                onChange={(event) => {
                  selectMediaFile(event.target.files?.[0] || null)
                }}
              />

              <div className="size-12 mx-auto rounded-full bg-brand/10 flex items-center justify-center mb-4">
                {mediaFile ? (
                  <FileAudio className="size-6 text-brand" />
                ) : (
                  <Upload className="size-6 text-brand" />
                )}
              </div>

              {mediaFile ? (
                <>
                  <p className="text-sm font-semibold text-foreground">
                    {mediaFile.name}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {formatSize(mediaFile.size)}
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm font-semibold text-foreground">
                    Dépose un fichier vidéo ou audio ici, ou clique pour choisir
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    MP4, MOV, AVI, MKV, WEBM, MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, WMA
                  </p>
                </>
              )}
            </div>

            {mediaFile && (
              <div className="flex flex-wrap gap-2">
                <Button
                  className="bg-brand hover:bg-brand/90"
                  onClick={handleTranscribe}
                  disabled={transcribing || Boolean(transcriptionPdfUrl)}
                >
                  {transcribing ? (
                    <Loader2 className="size-4 mr-2 animate-spin" />
                  ) : (
                    <Video className="size-4 mr-2" />
                  )}

                  {transcribing
                    ? "Transcription en cours..."
                    : transcriptionPdfUrl
                      ? "Transcription terminée"
                      : "Lancer la transcription"}
                </Button>

                <Button
                  variant={transcriptionPdfUrl ? "default" : "outline"}
                  onClick={downloadTranscriptionPdf}
                  disabled={!transcriptionPdfUrl || transcribing}
                >
                  <Download className="size-4 mr-2" />
                  Télécharger le PDF
                </Button>

                <Button
                  variant="outline"
                  onClick={() => {
                    clearTranscriptionPdf()
                    setMediaFile(null)
                    setTranscriptionError("")
                    setTranscriptionSuccess("")

                    if (mediaFileInputRef.current) {
                      mediaFileInputRef.current.value = ""
                    }
                  }}
                  disabled={transcribing}
                >
                  <X className="size-4 mr-2" />
                  Annuler
                </Button>
              </div>
            )}

            {transcribing && (
              <div className="rounded-md border border-border bg-muted/30 p-4">
                <div className="flex items-center gap-3">
                  <Loader2 className="size-5 animate-spin text-brand" />
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      Transcription en cours
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      Le temps dépend de la durée du média. Garde cette page ouverte
                      jusqu’à ce que le bouton « Télécharger le PDF » devienne disponible.
                    </p>
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
