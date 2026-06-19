"use client"

import { useMemo, useRef, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  FileText,
  FolderPlus,
  Loader2,
  Upload,
  X,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"

import { createProject, uploadDocument } from "@/lib/api"
import { setCurrentProjectId } from "@/lib/project-session"

interface NewProjectPageProps {
  navigateTo: (page: AppPage) => void
}

type LocalFile = {
  id: string
  file: File
  name: string
  sizeLabel: string
  typeLabel: string
  progress: number
  status: "pending" | "uploading" | "done" | "error"
  error?: string
}

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

export default function NewProjectPage({ navigateTo }: NewProjectPageProps) {
  const [organisme, setOrganisme] = useState("")
  const [projectName, setProjectName] = useState("")
  const [year, setYear] = useState(currentYear())
  const [domainLabel, setDomainLabel] = useState("")
  const [files, setFiles] = useState<LocalFile[]>([])
  const [dragging, setDragging] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")

  const fileInputRef = useRef<HTMLInputElement>(null)

  const canSubmit = useMemo(() => {
    return organisme.trim() && projectName.trim() && year.trim() && domainLabel.trim()
  }, [organisme, projectName, year, domainLabel])

  const addFiles = (fileList: FileList) => {
    const items = Array.from(fileList).map((file) => ({
      id: `${file.name}-${file.size}-${crypto.randomUUID()}`,
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

  const removeFile = (id: string) => {
    setFiles((prev) => prev.filter((item) => item.id !== id))
  }

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setDragging(false)

    if (event.dataTransfer.files.length > 0) {
      addFiles(event.dataTransfer.files)
    }
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()

    if (!canSubmit) {
      setError("Remplis organisme, projet, année et domaine avant de créer le dossier.")
      return
    }

    setSubmitting(true)
    setError("")
    setSuccess("")

    try {
      const createdProject = await createProject({
        organisme: organisme.trim(),
        project_name: projectName.trim(),
        year: year.trim(),
        domain_label: domainLabel.trim(),
      })

      setCurrentProjectId(createdProject.id)

      let uploadedCount = 0
      let uploadErrors = 0

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
        }
      }

      if (uploadErrors > 0) {
        setSuccess(
          `Projet créé. ${uploadedCount} document(s) uploadé(s), ${uploadErrors} erreur(s).`
        )
        return
      }

      navigateTo("diagnosis")
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
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div>
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
            Nouveau dossier CIR
          </h1>
        </div>

        <p className="text-sm text-muted-foreground mt-1">
          Crée un projet, ajoute les documents, puis lance EnnoDiagnostic.
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
              Ces données créent le dossier dans PostgreSQL.
            </CardDescription>
          </CardHeader>

          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="organisme">Organisme / client</Label>
              <Input
                id="organisme"
                value={organisme}
                onChange={(event) => setOrganisme(event.target.value)}
                placeholder="Exemple : Girodin"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="projectName">Nom du projet</Label>
              <Input
                id="projectName"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="Exemple : TGM100"
                required
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
            <CardTitle className="text-sm">Documents bruts</CardTitle>
            <CardDescription className="text-xs">
              Après création, tu seras redirigée vers EnnoDiagnostic pour lancer l’analyse.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            <div
              onDragOver={(event) => {
                event.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all ${
                dragging
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
                Dépose les documents ici ou clique pour choisir
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                PDF, Word, Excel, PowerPoint, images, MSG, TXT
              </p>
            </div>

            {files.length > 0 && (
              <div className="space-y-2">
                {files.map((item) => (
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
          </CardContent>
        </Card>

        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => navigateTo("projects")}
            disabled={submitting}
          >
            Annuler
          </Button>

          <Button
            type="submit"
            className="bg-brand hover:bg-brand/90"
            disabled={submitting || !canSubmit}
          >
            {submitting ? (
              <Loader2 className="size-4 mr-2 animate-spin" />
            ) : (
              <FolderPlus className="size-4 mr-2" />
            )}
            Créer et ouvrir EnnoDiagnostic
          </Button>
        </div>
      </form>
    </div>
  )
}
