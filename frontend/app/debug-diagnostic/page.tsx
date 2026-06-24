"use client"

import { useEffect, useMemo, useState } from "react"

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000"

const REQUIRED_TITLES = [
  "Lecture Frascati du dossier",
  "Synthèse stratégique du projet",
  "Objectif global reformulé",
  "Verrous R&D / signaux de verrous",
  "Démarche expérimentale détectée",
  "Résultats et métriques disponibles",
  "Paramètres et contraintes techniques",
  "Points à valider par le consultant",
]

function parseJsonSafe(value: any): any {
  if (!value) return null
  if (typeof value === "object") return value
  if (typeof value === "string") {
    const s = value.trim()
    if (!s) return null
    try {
      return JSON.parse(s)
    } catch {
      return value
    }
  }
  return value
}

function findToken(): string {
  if (typeof window === "undefined") return ""

  const stores = [window.localStorage, window.sessionStorage]

  for (const store of stores) {
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i) || ""
      const value = store.getItem(key) || ""

      if (
        key.toLowerCase().includes("token") ||
        key.toLowerCase().includes("auth") ||
        value.startsWith("eyJ")
      ) {
        if (value.startsWith("{")) {
          try {
            const obj = JSON.parse(value)
            const nested =
              obj.access_token ||
              obj.accessToken ||
              obj.token ||
              obj.jwt ||
              obj.authToken
            if (nested) return String(nested)
          } catch {
            // ignore
          }
        }

        if (value.startsWith("eyJ")) return value
        if (value.length > 20 && !value.includes("{")) return value
      }
    }
  }

  return ""
}

function unwrapReport(payload: any): any {
  payload = parseJsonSafe(payload)
  if (!payload || typeof payload !== "object") return null

  const candidates = [
    payload,
    payload.report,
    payload.diagnostic,
    payload.data,
    payload.result,
    payload.latest,
    payload.diagnostic_run,
    payload.run,
    payload.item,
  ]

  for (const candidate of candidates) {
    const obj = parseJsonSafe(candidate)

    if (obj && typeof obj === "object") {
      if (
        obj.diagnostic_sections ||
        (obj.diagnostic && typeof obj.diagnostic === "object")
      ) {
        return obj
      }

      const jsonKeys = [
        "result_json",
        "report_json",
        "raw_json",
        "output_json",
        "content_json",
        "data_json",
      ]

      for (const key of jsonKeys) {
        const nested = parseJsonSafe(obj[key])
        if (
          nested &&
          typeof nested === "object" &&
          (nested.diagnostic_sections ||
            (nested.diagnostic && typeof nested.diagnostic === "object"))
        ) {
          return nested
        }
      }
    }
  }

  for (const value of Object.values(payload)) {
    const found = unwrapReport(value)
    if (found) return found
  }

  return null
}

function getDiagnosticContent(report: any): string {
  if (!report) return ""
  return (
    report?.diagnostic?.content ||
    report?.content ||
    report?.markdown ||
    ""
  ).toString()
}

function getSections(report: any): Record<string, string> {
  if (!report) return {}

  const sections =
    report?.diagnostic_sections ||
    report?.diagnostic?.sections ||
    report?.sections ||
    {}

  if (sections && typeof sections === "object") {
    return sections as Record<string, string>
  }

  return {}
}

function sectionStatus(sections: Record<string, string>) {
  return REQUIRED_TITLES.map((title) => ({
    title,
    chars: String(sections?.[title] || "").length,
    ok: Boolean(String(sections?.[title] || "").trim()),
  }))
}

function extractProjectIdFromUrl(): string {
  if (typeof window === "undefined") return "4"
  const params = new URLSearchParams(window.location.search)
  return params.get("projectId") || "4"
}

function SectionBlock({
  title,
  text,
}: {
  title: string
  text: string
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">{title}</h2>
          <p className="mt-1 text-sm text-slate-500">
            Section issue directement de la réponse backend EnnoDiagnostic.
          </p>
        </div>
        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
          {text?.length || 0} caractères
        </span>
      </div>

      <div className="rounded-xl bg-slate-50 p-4">
        <pre className="whitespace-pre-wrap break-words text-sm leading-7 text-slate-800 font-sans">
          {text?.trim() ? text : "Aucun contenu dans cette section."}
        </pre>
      </div>
    </section>
  )
}

function StatCard({
  label,
  value,
  subvalue,
}: {
  label: string
  value: string
  subvalue?: string
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="mt-2 text-2xl font-bold text-slate-900">{value}</div>
      {subvalue ? (
        <div className="mt-1 text-xs text-slate-500">{subvalue}</div>
      ) : null}
    </div>
  )
}

export default function DebugDiagnosticPage() {
  const [projectId, setProjectId] = useState("4")
  const [token, setToken] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [payload, setPayload] = useState<any>(null)

  useEffect(() => {
    setProjectId(extractProjectIdFromUrl())
    setToken(findToken())
  }, [])

  const report = useMemo(() => unwrapReport(payload), [payload])
  const content = useMemo(() => getDiagnosticContent(report), [report])
  const sections = useMemo(() => getSections(report), [report])
  const statuses = useMemo(() => sectionStatus(sections), [sections])

  const diagnosticStatus =
    report?.diagnostic?.status || report?.status || "-"

  const sectionsCount = statuses.filter((s) => s.ok).length

  const hasPreviousComparison = content.includes(
    "Comparaison avec le CIR précédent"
  )

  async function loadLatest() {
    setLoading(true)
    setError("")
    setPayload(null)

    try {
      const headers: Record<string, string> = {
        Accept: "application/json",
      }

      if (token) {
        headers.Authorization = `Bearer ${token}`
      }

      const res = await fetch(
        `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/diagnostic/latest`,
        { headers }
      )

      const text = await res.text()
      let data: any = null

      try {
        data = JSON.parse(text)
      } catch {
        data = { raw: text }
      }

      if (!res.ok) {
        setError(`HTTP ${res.status} — ${text.slice(0, 800)}`)
      }

      setPayload(data)
    } catch (e: any) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (projectId) {
      loadLatest()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  return (
    <main className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-7xl px-6 py-8">
        {/* Header */}
        <div className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="mb-2 inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                EnnoDiagnostic · Vue de contrôle backend
              </div>
              <h1 className="text-3xl font-bold tracking-tight text-slate-900">
                Diagnostic CIR — rendu consultant
              </h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
                Cette page affiche exactement le contenu renvoyé par le backend
                pour le diagnostic le plus récent. Elle sert à vérifier que les
                sections envoyées au frontend sont complètes, lisibles et
                correctement structurées pour un consultant.
              </p>
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">
                  Project ID
                </label>
                <input
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                  className="w-28 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
              </div>

              <button
                onClick={loadLatest}
                disabled={loading}
                className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loading ? "Chargement..." : "Recharger le diagnostic"}
              </button>
            </div>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
              <div className="text-slate-500">API</div>
              <div className="mt-1 font-medium text-slate-800">
                {API_BASE_URL}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
              <div className="text-slate-500">Authentification</div>
              <div
                className={`mt-1 font-medium ${
                  token ? "text-emerald-700" : "text-amber-700"
                }`}
              >
                {token ? "Token trouvé" : "Token non trouvé"}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
              <div className="text-slate-500">Projet</div>
              <div className="mt-1 font-medium text-slate-800">
                ID {projectId}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
              <div className="text-slate-500">Source</div>
              <div className="mt-1 font-medium text-slate-800">
                /diagnostic/latest
              </div>
            </div>
          </div>

          {error ? (
            <div className="mt-6 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
              <div className="font-semibold">Erreur backend</div>
              <div className="mt-1 whitespace-pre-wrap">{error}</div>
            </div>
          ) : null}
        </div>

        {/* KPI */}
        <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Statut du diagnostic"
            value={String(diagnosticStatus)}
            subvalue="Valeur renvoyée par le backend"
          />
          <StatCard
            label="Sections détectées"
            value={`${sectionsCount}/${REQUIRED_TITLES.length}`}
            subvalue="Sections attendues présentes"
          />
          <StatCard
            label="Contenu principal"
            value={`${content.length}`}
            subvalue="Nombre de caractères dans diagnostic.content"
          />
          <StatCard
            label="Comparaison CIR précédent"
            value={hasPreviousComparison ? "Présente" : "Absente"}
            subvalue="Doit rester absente dans la version rapide"
          />
        </div>

        {/* Tableau contrôle */}
        <div className="mt-6 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-4">
            <h2 className="text-xl font-semibold text-slate-900">
              Tableau de contrôle des sections
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Vérification rapide de la présence des blocs que le consultant
              doit voir dans l’interface.
            </p>
          </div>

          <div className="overflow-hidden rounded-2xl border border-slate-200">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-100">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
                    Section
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
                    Statut
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
                    Longueur
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {statuses.map((s) => (
                  <tr key={s.title} className="hover:bg-slate-50">
                    <td className="px-4 py-3 text-sm font-medium text-slate-800">
                      {s.title}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      {s.ok ? (
                        <span className="inline-flex rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700">
                          Présente
                        </span>
                      ) : (
                        <span className="inline-flex rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-700">
                          Vide
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {s.chars} caractères
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Sortie complète */}
        <div className="mt-6 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-4">
            <h2 className="text-xl font-semibold text-slate-900">
              Sortie complète du backend
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Bloc principal <code>diagnostic.content</code> tel qu’il est
              renvoyé par l’API.
            </p>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5">
            <pre className="whitespace-pre-wrap break-words text-sm leading-7 text-slate-800 font-sans">
              {content?.trim()
                ? content
                : "Aucun contenu trouvé dans diagnostic.content."}
            </pre>
          </div>
        </div>

        {/* Sections séparées */}
        <div className="mt-6 space-y-6">
          {REQUIRED_TITLES.map((title) => (
            <SectionBlock
              key={title}
              title={title}
              text={String(sections?.[title] || "")}
            />
          ))}
        </div>

        {/* Réponse brute */}
        <details className="mt-6 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <summary className="cursor-pointer text-lg font-semibold text-slate-900">
            Réponse brute backend (technique)
          </summary>
          <p className="mt-2 text-sm text-slate-500">
            Bloc utile uniquement pour le debug technique. À laisser fermé pour
            un consultant.
          </p>

          <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <pre className="whitespace-pre-wrap break-words text-xs leading-6 text-slate-700">
              {JSON.stringify(payload, null, 2)}
            </pre>
          </div>
        </details>
      </div>
    </main>
  )
}