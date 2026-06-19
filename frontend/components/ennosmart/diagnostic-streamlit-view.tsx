import React, { useEffect, useMemo, useState } from "react"

type AnyObj = Record<string, any>

type StreamlitViewProps = {
  projectId: number | string
}

function getApiBase(): string {
  const viteEnv = (typeof import.meta !== "undefined" && (import.meta as any).env?.VITE_API_URL) || ""
  const nextEnv = (typeof process !== "undefined" && (process as any).env?.NEXT_PUBLIC_API_URL) || ""
  return String(viteEnv || nextEnv || "http://127.0.0.1:8000").replace(/\/$/, "")
}

function getToken(): string | null {
  return (
    localStorage.getItem("ennosmart_token") ||
    localStorage.getItem("token") ||
    localStorage.getItem("access_token")
  )
}

function pick(obj: AnyObj | null | undefined, paths: string[]): any {
  for (const path of paths) {
    const parts = path.split(".")
    let cur: any = obj
    let ok = true
    for (const part of parts) {
      if (cur && typeof cur === "object" && part in cur) {
        cur = cur[part]
      } else {
        ok = false
        break
      }
    }
    if (ok && cur !== undefined && cur !== null) return cur
  }
  return null
}

function normalizeLatestPayload(payload: AnyObj): AnyObj {
  return (
    payload?.raw_result_json ||
    payload?.diagnostic_run?.raw_result_json ||
    payload?.run?.raw_result_json ||
    payload?.latest?.raw_result_json ||
    payload ||
    {}
  )
}

function findReport(raw: AnyObj): AnyObj {
  return (
    pick(raw, [
      "script_or_pipeline_result.report",
      "bundle.report",
      "report",
      "diagnostic.report",
      "result.report",
    ]) || {}
  )
}

function findNlp(raw: AnyObj): AnyObj {
  return (
    pick(raw, [
      "script_or_pipeline_result.nlp_rag.nlp_result",
      "bundle.nlp_result",
      "nlp_result",
    ]) || {}
  )
}

function textOf(value: any, max = 700): string {
  const text = String(value || "").replace(/\s+/g, " ").trim()
  if (text.length <= max) return text
  return text.slice(0, max).trim() + "…"
}

function docOf(item: AnyObj): string {
  const meta = item?.metadata || {}
  return (
    meta.document ||
    item.document ||
    meta.source_document ||
    meta.file_name ||
    "Document non précisé"
  )
}

function roleTitle(item: AnyObj): string {
  const meta = item?.metadata || {}
  return (
    meta.theme_label ||
    item.theme_label ||
    item.title ||
    meta.final_role ||
    meta.role ||
    "Élément détecté"
  )
}

function textItem(item: AnyObj): string {
  return item?.text || item?.source_text || item?.content || item?.description || ""
}

function scoreOf(item: AnyObj): string {
  const meta = item?.metadata || {}
  const value =
    meta.frascati_score ??
    item.frascati_score ??
    meta.score ??
    item.score ??
    meta.verrou_score ??
    item.verrou_score ??
    null

  if (value === null || value === undefined || value === "") return "—"
  const num = Number(value)
  if (Number.isFinite(num)) {
    if (num <= 1) return `${Math.round(num * 100)}%`
    return `${Math.round(num)}%`
  }
  return String(value)
}

function tagOf(item: AnyObj): string {
  const meta = item?.metadata || {}
  const label = String(meta.quality_status || item.quality_status || "")
  const score = Number(meta.frascati_score ?? item.frascati_score ?? 0)

  if (label.includes("strong") || label.includes("pertinent") || score >= 0.68) {
    return "PERTINENT POUR CIR"
  }

  if (label.includes("medium") || score >= 0.45) {
    return "MOYEN POUR CIR"
  }

  return meta.frascati_decision || item.tag_cir || item.tag || "À vérifier"
}

function uniqueBy(items: AnyObj[], keyFn: (x: AnyObj) => string): AnyObj[] {
  const seen = new Set<string>()
  const out: AnyObj[] = []

  for (const item of items || []) {
    const key = keyFn(item).toLowerCase().replace(/\s+/g, " ").trim()
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(item)
  }

  return out
}

function asList(value: any): AnyObj[] {
  return Array.isArray(value) ? value.filter((x) => x && typeof x === "object") : []
}

function extractSections(raw: AnyObj) {
  const report = findReport(raw)
  const nlp = findNlp(raw)

  const chroma = report?.chroma_sections || {}
  const objectives = uniqueBy(asList(chroma.objectifs), (x) => textItem(x))
  const verrous = uniqueBy(asList(chroma.verrous), (x) => roleTitle(x))

  const methods = uniqueBy(asList(chroma.methodes), (x) => textItem(x)).slice(0, 8)
  const results = uniqueBy(asList(chroma.resultats), (x) => textItem(x)).slice(0, 8)
  const params = uniqueBy(asList(chroma.parametres), (x) => textItem(x)).slice(0, 8)

  const content = report?.diagnostic?.content || report?.content || ""

  const inputs = report?.inputs_status || {}
  const frascati = report?.frascati_summary || {}
  const pipeline = report?.pipeline_before_agent || pick(raw, ["script_or_pipeline_result.nlp_rag"]) || {}

  const nlpStats = pipeline?.nlp_stats || nlp?.stats || {}

  return {
    report,
    content,
    objectives,
    verrous,
    methods,
    results,
    params,
    metrics: {
      docsUsed: pipeline?.documents_used_count,
      docsLoaded: pipeline?.documents_loaded_count,
      candidates: nlpStats?.raw_candidates ?? nlpStats?.candidates,
      kept: nlpStats?.raw_kept ?? nlpStats?.kept,
      nlpVerrous: nlpStats?.merged_verrous ?? nlpStats?.verrous_final,
      chunks: pipeline?.index_report?.chunks_indexed,
      global: inputs?.global_sources_count,
      objectifs: inputs?.objectifs_count,
      verrous: inputs?.verrous_count,
      frascati: frascati?.average_frascati_score,
    },
  }
}

function MetricCard({ label, value }: { label: string; value: any }) {
  return (
    <div className="rounded-2xl border border-violet-100 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900">{value ?? "—"}</div>
    </div>
  )
}

function SourceCard({
  item,
  index,
  compact = false,
}: {
  item: AnyObj
  index: number
  compact?: boolean
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-slate-900">
            {index}. {roleTitle(item)}
          </div>
          <div className="mt-1 text-xs text-slate-500">{docOf(item)}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="rounded-full bg-violet-50 px-3 py-1 text-xs font-medium text-violet-700">
            {tagOf(item)}
          </span>
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700">
            Score {scoreOf(item)}
          </span>
        </div>
      </div>

      <p className="mt-3 text-sm leading-6 text-slate-700">
        {textOf(textItem(item), compact ? 350 : 900)}
      </p>
    </div>
  )
}

export function DiagnosticStreamlitView({ projectId }: StreamlitViewProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [payload, setPayload] = useState<AnyObj | null>(null)

  async function loadLatest() {
    setLoading(true)
    setError(null)

    try {
      const token = getToken()
      const res = await fetch(`${getApiBase()}/projects/${projectId}/diagnostic/latest`, {
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      })

      if (!res.ok) {
        throw new Error(`Impossible de charger le diagnostic (${res.status})`)
      }

      const data = await res.json()
      setPayload(normalizeLatestPayload(data))
    } catch (e: any) {
      setError(e?.message || "Erreur de chargement")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadLatest()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const view = useMemo(() => {
    if (!payload) return null
    return extractSections(payload)
  }, [payload])

  if (loading && !view) {
    return (
      <div className="rounded-2xl border border-violet-100 bg-white p-6 text-slate-600 shadow-sm">
        Chargement de la vue diagnostic…
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-red-100 bg-red-50 p-6 text-red-700">
        {error}
      </div>
    )
  }

  if (!view) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-6 text-slate-600 shadow-sm">
        Aucun diagnostic disponible.
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="rounded-3xl border border-violet-100 bg-gradient-to-br from-violet-50 to-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-slate-950">
              Vue EnnoDiagnostic complète
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Affichage type Streamlit : sortie agent, sources Chroma, verrous dédupliqués et validation consultant.
            </p>
          </div>

          <button
            onClick={loadLatest}
            className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-medium text-white shadow hover:bg-violet-700"
          >
            Actualiser
          </button>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
          <MetricCard label="Docs utilisés" value={view.metrics.docsUsed} />
          <MetricCard label="Docs extraits" value={view.metrics.docsLoaded} />
          <MetricCard label="Candidats" value={view.metrics.candidates} />
          <MetricCard label="Kept" value={view.metrics.kept} />
          <MetricCard label="Verrous NLP" value={view.metrics.nlpVerrous} />
          <MetricCard label="Chunks" value={view.metrics.chunks} />
          <MetricCard label="Sources verrou" value={view.metrics.verrous} />
          <MetricCard
            label="Frascati"
            value={
              view.metrics.frascati !== undefined && view.metrics.frascati !== null
                ? `${Math.round(Number(view.metrics.frascati) * 100)}%`
                : "—"
            }
          />
        </div>
      </div>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <h3 className="text-lg font-semibold text-slate-950">
          Objectif global reformulé
        </h3>

        {view.content ? (
          <div className="prose prose-slate mt-4 max-w-none">
            <pre className="whitespace-pre-wrap rounded-2xl bg-slate-50 p-4 text-sm leading-6 text-slate-800">
              {view.content}
            </pre>
          </div>
        ) : (
          <p className="mt-3 text-sm text-slate-500">
            Aucun contenu reformulé disponible.
          </p>
        )}
      </section>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <h3 className="text-lg font-semibold text-slate-950">
          Objectifs détectés
        </h3>

        <div className="mt-4 space-y-3">
          {view.objectives.length ? (
            view.objectives.map((item, i) => (
              <SourceCard key={`obj-${i}`} item={item} index={i + 1} compact />
            ))
          ) : (
            <p className="text-sm text-slate-500">Aucun objectif détecté.</p>
          )}
        </div>
      </section>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <h3 className="text-lg font-semibold text-slate-950">
          Verrous R&D / signaux de verrous
        </h3>

        <div className="mt-4 space-y-3">
          {view.verrous.length ? (
            view.verrous.map((item, i) => (
              <SourceCard key={`verrou-${i}`} item={item} index={i + 1} />
            ))
          ) : (
            <p className="text-sm text-slate-500">Aucun verrou détecté.</p>
          )}
        </div>
      </section>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <h3 className="text-lg font-semibold text-slate-950">
          Preuves techniques utiles
        </h3>

        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {[...view.methods, ...view.results, ...view.params].slice(0, 16).map((item, i) => (
            <SourceCard key={`proof-${i}`} item={item} index={i + 1} compact />
          ))}
        </div>
      </section>

      <section className="rounded-3xl border border-violet-100 bg-violet-50/40 p-6 shadow-sm">
        <h3 className="text-lg font-semibold text-slate-950">
          Verrous synchronisés pour validation
        </h3>
        <p className="mt-1 text-sm text-slate-600">
          Version propre dédupliquée : le consultant garde, rejette ou demande une reformulation.
        </p>

        <div className="mt-4 space-y-3">
          {view.verrous.length ? (
            view.verrous.map((item, i) => (
              <div
                key={`valid-${i}`}
                className="rounded-2xl border border-violet-100 bg-white p-4 shadow-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-slate-900">
                      {roleTitle(item)}
                    </div>
                    <div className="mt-1 text-xs text-slate-500">{docOf(item)}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded-full bg-violet-100 px-3 py-1 text-xs font-medium text-violet-700">
                      {tagOf(item)}
                    </span>
                    <span className="rounded-full bg-white px-3 py-1 text-xs font-medium text-slate-700 ring-1 ring-slate-200">
                      Score {scoreOf(item)}
                    </span>
                    <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700">
                      En attente
                    </span>
                  </div>
                </div>
                <p className="mt-3 text-sm leading-6 text-slate-700">
                  {textOf(textItem(item), 650)}
                </p>
              </div>
            ))
          ) : (
            <p className="text-sm text-slate-500">Aucun verrou synchronisé.</p>
          )}
        </div>
      </section>
    </div>
  )
}

export default DiagnosticStreamlitView
