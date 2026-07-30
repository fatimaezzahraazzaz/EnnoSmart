"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  FileCheck2,
  FileText,
  FolderPlus,
  Loader2,
  Lock,
  Upload,
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
}

type DepositMode = "diagnostic" | "reference"

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

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

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

export default function NewProjectPage({
  navigateTo,
  preset = null,
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

  const [draggingRaw, setDraggingRaw] = useState(false)
  const [draggingFinal, setDraggingFinal] = useState(false)

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")

  const rawFileInputRef = useRef<HTMLInputElement>(null)
  const finalFileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setOrganisme(presetOrganisme || "")
  }, [presetOrganisme])

  const baseFormIsValid = useMemo(() => {
    return (
      organisme.trim().length > 0 &&
      projectName.trim().length > 0 &&
      year.trim().length > 0 &&
      domainLabel.trim().length > 0
    )
  }, [organisme, projectName, year, domainLabel])

  const canSubmit = useMemo(() => {
    if (!baseFormIsValid) return false

    if (depositMode === "reference") {
      return finalCirFile !== null
    }

    return true
  }, [baseFormIsValid, depositMode, finalCirFile])

  const addRawFiles = (fileList: FileList) => {
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
    setFiles((prev) => prev.filter((item) => item.id !== id))
  }

  const handleRawDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setDraggingRaw(false)

    if (event.dataTransfer.files.length > 0) {
      addRawFiles(event.dataTransfer.files)
    }
  }

  const handleFinalDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setDraggingFinal(false)

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

      if (depositMode === "reference") {
        await uploadFinalCirReference({
          projectId: createdProject.id,
          file: finalCirFile as File,
          organisme: organisme.trim(),
          projectName: projectName.trim(),
          year: year.trim(),
        })

        setSuccess("Dossier créé et CIR final enregistré comme référence.")
        navigateTo("diagnosis")
        return
      }

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
          `Dossier créé. ${uploadedCount} document(s) ajouté(s), ${uploadErrors} erreur(s).`
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
                    if (!organismIsLocked) {
                      setOrganisme(event.target.value)
                    }
                  }}
                  placeholder="Exemple : Girodin"
                  required
                  readOnly={organismIsLocked}
                  className={organismIsLocked ? "bg-muted pr-9 cursor-not-allowed" : ""}
                />

                {organismIsLocked && (
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
            <CardTitle className="text-sm">Que souhaitez-vous déposer ?</CardTitle>
            <CardDescription className="text-xs">
              Choisissez le type de dépôt pour éviter de mélanger les documents de travail avec un CIR final validé.
            </CardDescription>
          </CardHeader>

          <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <button
              type="button"
              onClick={() => {
                setDepositMode("diagnostic")
                resetMessages()
              }}
              className={`text-left rounded-xl border p-4 transition-all ${
                depositMode === "diagnostic"
                  ? "border-brand bg-brand/5 ring-2 ring-brand/20"
                  : "border-border hover:border-brand/50 hover:bg-muted/30"
              }`}
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
                    Déposez les rapports, essais, notes, mails, schémas ou tableaux.
                    EnnoDiagnostic les analysera pour identifier les objectifs,
                    verrous R&D, preuves techniques et points à valider.
                  </p>
                </div>
              </div>
            </button>

            <button
              type="button"
              onClick={() => {
                setDepositMode("reference")
                resetMessages()
              }}
              className={`text-left rounded-xl border p-4 transition-all ${
                depositMode === "reference"
                  ? "border-brand bg-brand/5 ring-2 ring-brand/20"
                  : "border-border hover:border-brand/50 hover:bg-muted/30"
              }`}
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
                Après création, vous serez redirigée vers EnnoDiagnostic pour lancer l’analyse.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              <div
                onDragOver={(event) => {
                  event.preventDefault()
                  setDraggingRaw(true)
                }}
                onDragLeave={() => setDraggingRaw(false)}
                onDrop={handleRawDrop}
                onClick={() => rawFileInputRef.current?.click()}
                className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all ${
                  draggingRaw
                    ? "border-brand bg-brand/5"
                    : "border-border hover:border-brand/50 hover:bg-muted/30"
                }`}
              >
                <input
                  ref={rawFileInputRef}
                  type="file"
                  multiple
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
                          onClick={() => removeRawFile(item.id)}
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
                  setDraggingFinal(true)
                }}
                onDragLeave={() => setDraggingFinal(false)}
                onDrop={handleFinalDrop}
                onClick={() => finalFileInputRef.current?.click()}
                className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all ${
                  draggingFinal
                    ? "border-emerald-500 bg-emerald-500/5"
                    : "border-border hover:border-emerald-500/50 hover:bg-muted/30"
                }`}
              >
                <input
                  ref={finalFileInputRef}
                  type="file"
                  className="hidden"
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
                    disabled={submitting}
                  >
                    <X className="size-4" />
                  </button>
                </div>
              )}
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

            {depositMode === "reference"
              ? "Créer et enregistrer le CIR final"
              : "Créer et ouvrir EnnoDiagnostic"}
          </Button>
        </div>
      </form>
    </div>
  )
}
