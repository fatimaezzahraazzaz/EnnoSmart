"use client"

import { useEffect, useRef, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  CloudUpload,
  Database,
  FileText,
  Loader2,
  RefreshCw,
  Upload,
  X,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"

import {
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

  const fileInputRef = useRef<HTMLInputElement>(null)

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
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      <div className="flex items-start justify-between gap-4 flex-wrap">
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
            className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all ${
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
                      onClick={() => removeFile(item.id)}
                      className="text-muted-foreground hover:text-destructive"
                      disabled={item.status === "uploading"}
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
    </div>
  )
}
