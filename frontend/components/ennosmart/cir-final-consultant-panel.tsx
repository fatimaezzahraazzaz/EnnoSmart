"use client"

import React, { useEffect, useRef, useState } from "react"

type Props = {
  projectId: number | string
  apiBaseUrl?: string
  authToken?: string
  defaultOrganisme?: string | null
  defaultProject?: string | null
  defaultYear?: string | number | null
}

type SectionCard = {
  key: string
  label: string
  text: string
}

function toSafeString(value: unknown): string {
  if (value === null || value === undefined) return ""
  return String(value)
}

function apiUrl(base: string | undefined, path: string) {
  const b = (base || "http://127.0.0.1:8000").replace(/\/$/, "")
  return `${b}${path}`
}

function sectionLabel(key: string) {
  const labels: Record<string, string> = {
    objectifs_projet: "Objectifs du projet",
    etat_art: "État de l’art",
    insuffisances: "Limites des solutions existantes",
    verrous: "Verrous et incertitudes",
    demarche_experimentale: "Démarche R&D",
    travaux_realises: "Travaux réalisés",
    resultats_obtenus: "Résultats obtenus",
    conclusion_contribution: "Conclusion et contribution",
  }

  return labels[key] || key
}

function buildSections(report: any): SectionCard[] {
  const full = report?.sections_full || {}
  const previews = report?.detected_sections || {}

  const keys = [
    "objectifs_projet",
    "etat_art",
    "insuffisances",
    "verrous",
    "demarche_experimentale",
    "travaux_realises",
    "resultats_obtenus",
    "conclusion_contribution",
  ]

  return keys
    .map((key) => {
      const text = full?.[key] || previews?.[key]?.preview || ""
      return {
        key,
        label: sectionLabel(key),
        text: toSafeString(text).trim(),
      }
    })
    .filter((x) => x.text.length > 0)
}

export function CirFinalConsultantPanel({
  projectId,
  apiBaseUrl,
  authToken,
  defaultOrganisme = "",
  defaultProject = "",
  defaultYear = "",
}: Props) {
  const fileRef = useRef<HTMLInputElement | null>(null)

  const [organisme, setOrganisme] = useState<string>(() => toSafeString(defaultOrganisme))
  const [project, setProject] = useState<string>(() => toSafeString(defaultProject))
  const [year, setYear] = useState<string>(() => toSafeString(defaultYear))
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileInputKey, setFileInputKey] = useState<number>(0)

  const [loading, setLoading] = useState(false)
  const [report, setReport] = useState<any | null>(null)
  const [error, setError] = useState("")

  async function loadLatest() {
    try {
      const res = await fetch(
        apiUrl(apiBaseUrl, `/projects/${projectId}/cir-final-consultant/latest`),
        {
          headers: {
            ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
          },
        }
      )

      const data = await res.json()
      if (res.ok && data?.status !== "empty") {
        setReport(data)

        // Remplissage doux des champs seulement s'ils sont vides.
        // Toujours avec des chaînes pour éviter controlled/uncontrolled.
        if (!organisme && data?.organisme) setOrganisme(toSafeString(data.organisme))
        if (!project && data?.project) setProject(toSafeString(data.project))
        if (!year && data?.year) setYear(toSafeString(data.year))
      }
    } catch {
      // Absence de CIR final enregistré : pas d'erreur affichée.
    }
  }

  useEffect(() => {
    loadLatest()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  async function uploadFinalCir() {
    if (!selectedFile) {
      setError("Veuillez sélectionner un fichier CIR final.")
      return
    }

    setLoading(true)
    setError("")

    try {
      const form = new FormData()
      form.append("file", selectedFile)
      form.append("organisme", organisme.trim() || "organisme_unknown")
      form.append("project", project.trim() || "project_unknown")
      form.append("year", year.trim() || "unknown")

      const res = await fetch(
        apiUrl(apiBaseUrl, `/projects/${projectId}/cir-final-consultant/upload`),
        {
          method: "POST",
          headers: {
            ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
          },
          body: form,
        }
      )

      const data = await res.json()

      if (!res.ok) {
        throw new Error(data?.detail || "Erreur lors de l’enregistrement du CIR final.")
      }

      setReport(data)
      setSelectedFile(null)

      // Reset propre du champ fichier sans le rendre contrôlé.
      setFileInputKey((x) => x + 1)
      if (fileRef.current) {
        fileRef.current.value = ""
      }
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  const sections = buildSections(report)

  return (
    <div className="space-y-5 rounded-3xl border border-violet-100 bg-white p-5 shadow-sm">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">
          CIR précédent / CIR final consultant
        </h2>

        <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">
          Déposez ici votre version finale du CIR afin de la conserver comme
          référence pour les futurs dossiers du projet.
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <input
          value={organisme ?? ""}
          onChange={(e) => setOrganisme(e.currentTarget.value ?? "")}
          placeholder="Organisme"
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-violet-400"
        />

        <input
          value={project ?? ""}
          onChange={(e) => setProject(e.currentTarget.value ?? "")}
          placeholder="Projet"
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-violet-400"
        />

        <input
          value={year ?? ""}
          onChange={(e) => setYear(e.currentTarget.value ?? "")}
          placeholder="Année"
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-violet-400"
        />
      </div>

      <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-4">
        <input
          key={fileInputKey}
          ref={fileRef}
          type="file"
          accept=".docx,.pdf,.txt,.md"
          onChange={(e) => setSelectedFile(e.currentTarget.files?.[0] || null)}
          className="block w-full text-sm text-slate-700"
        />

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            onClick={uploadFinalCir}
            disabled={loading || !selectedFile}
            className="rounded-xl bg-violet-700 px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Enregistrement..." : "Enregistrer le CIR final"}
          </button>

          {selectedFile && (
            <span className="text-sm text-slate-600">
              Fichier sélectionné : {selectedFile.name}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {report && (
        <div className="space-y-4">
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
            <h3 className="font-semibold text-emerald-900">
              CIR final enregistré avec succès
            </h3>

            <p className="mt-1 text-sm leading-6 text-emerald-800">
              Le document est maintenant disponible comme référence pour ce projet.
            </p>

            <div className="mt-3 grid gap-2 text-sm text-emerald-900 md:grid-cols-2">
              <p>
                <span className="font-medium">Fichier :</span>{" "}
                {report?.file?.name || "-"}
              </p>

              <p>
                <span className="font-medium">Année :</span>{" "}
                {year || report?.year || "-"}
              </p>
            </div>
          </div>

          {sections.length > 0 && (
            <div>
              <h3 className="mb-3 text-sm font-semibold text-slate-900">
                Contenu reconnu dans le CIR
              </h3>

              <div className="grid gap-3 md:grid-cols-2">
                {sections.map((section) => (
                  <div
                    key={section.key}
                    className="rounded-2xl border border-slate-200 bg-white p-4"
                  >
                    <h4 className="mb-2 text-sm font-semibold text-slate-900">
                      {section.label}
                    </h4>

                    <div className="max-h-72 overflow-y-auto rounded-xl bg-slate-50 p-3">
                      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">
                        {section.text}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default CirFinalConsultantPanel
