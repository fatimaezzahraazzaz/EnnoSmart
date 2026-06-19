"use client"

import React, { useEffect, useRef, useState } from "react"

type Props = {
  projectId: number | string
  apiBaseUrl?: string
  authToken?: string
  defaultOrganisme?: string
  defaultProject?: string
  defaultYear?: string | number
}

function apiUrl(base: string | undefined, path: string) {
  const b = (base || "http://127.0.0.1:8000").replace(/\/$/, "")
  return `${b}${path}`
}

function Badge({ children, ok }: { children: React.ReactNode; ok?: boolean }) {
  return (
    <span
      className={
        ok
          ? "rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700"
          : "rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700"
      }
    >
      {children}
    </span>
  )
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

  const [organisme, setOrganisme] = useState(String(defaultOrganisme || ""))
  const [project, setProject] = useState(String(defaultProject || ""))
  const [year, setYear] = useState(String(defaultYear || ""))
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
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
      }
    } catch {
      // silencieux
    }
  }

  useEffect(() => {
    loadLatest()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  async function uploadFinalCir() {
    if (!selectedFile) {
      setError("Sélectionne d’abord un fichier CIR final.")
      return
    }

    setLoading(true)
    setError("")

    try {
      const form = new FormData()
      form.append("file", selectedFile)
      form.append("organisme", organisme || "organisme_unknown")
      form.append("project", project || "project_unknown")
      form.append("year", year || "unknown")

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
        throw new Error(data?.detail || "Erreur upload CIR final.")
      }

      setReport(data)
      setSelectedFile(null)

      if (fileRef.current) {
        fileRef.current.value = ""
      }
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  const sections = report?.detected_sections || {}

  return (
    <div className="space-y-5 rounded-3xl border border-violet-100 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            CIR final consultant
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-600">
            Dépose ici le CIR final validé. Il n’est pas utilisé comme document brut :
            il alimente la mémoire de style CIR et la mémoire de comparaison N-1.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Badge ok={report?.style_memory?.used}>
            Mémoire de style : {report?.style_memory?.used ? "oui" : "non"}
          </Badge>
          <Badge ok={report?.cir_memory?.used}>
            Mémoire CIR N-1 : {report?.cir_memory?.used ? "oui" : "non"}
          </Badge>
        </div>
      </div>

      <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
        Important : ce fichier représente le livrable final validé par le consultant.
        Il ne doit pas être mélangé avec les documents bruts analysés par EnnoDiagnostic.
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        <input
          value={organisme}
          onChange={(e) => setOrganisme(e.target.value)}
          placeholder="Organisme ex. Girodin"
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />
        <input
          value={project}
          onChange={(e) => setProject(e.target.value)}
          placeholder="Projet ex. TGM100"
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />
        <input
          value={year}
          onChange={(e) => setYear(e.target.value)}
          placeholder="Année ex. 2023"
          className="rounded-xl border border-slate-200 px-3 py-2 text-sm"
        />
      </div>

      <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-4">
        <input
          ref={fileRef}
          type="file"
          accept=".docx,.pdf,.txt,.md"
          onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
          className="block w-full text-sm text-slate-700"
        />

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            onClick={uploadFinalCir}
            disabled={loading || !selectedFile}
            className="rounded-xl bg-violet-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
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
              CIR final enregistré
            </h3>

            <div className="mt-2 grid gap-2 text-sm text-emerald-900 md:grid-cols-2">
              <p><span className="font-medium">Fichier :</span> {report?.file?.name || "-"}</p>
              <p><span className="font-medium">Texte extrait :</span> {report?.extraction?.text_chars || 0} caractères</p>
              <p><span className="font-medium">Style memory :</span> {report?.style_memory?.path || "-"}</p>
              <p><span className="font-medium">CIR memory :</span> {report?.cir_memory?.path || "-"}</p>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            {Object.entries(sections).map(([key, value]: any) => (
              <div key={key} className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h4 className="text-sm font-semibold text-slate-900">{key}</h4>
                  <Badge ok={value?.found}>{value?.found ? "détecté" : "non détecté"}</Badge>
                </div>

                <p className="text-xs text-slate-500">{value?.chars || 0} caractères</p>

                {value?.preview && (
                  <p className="mt-2 line-clamp-6 text-sm leading-6 text-slate-600">
                    {value.preview}
                  </p>
                )}
              </div>
            ))}
          </div>

          {report?.style_memory?.examples_added?.length > 0 && (
            <div className="rounded-2xl border border-violet-100 bg-violet-50 p-4">
              <h3 className="font-semibold text-violet-900">
                Exemples ajoutés à la mémoire de style
              </h3>

              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-violet-900">
                {report.style_memory.examples_added.map((x: any, i: number) => (
                  <li key={i}>
                    rôle={x.role} — section={x.section} — {x.chars} caractères
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
