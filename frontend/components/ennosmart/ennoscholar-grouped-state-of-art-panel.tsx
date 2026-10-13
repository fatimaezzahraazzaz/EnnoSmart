"use client"

import { useEffect, useMemo, useState } from "react"

type AnyObj = Record<string, any>

type GroupedArticle = {
  key: string
  title: string
  year?: string | number
  tag: string
  relevance_score?: number
  source?: string
  url?: string
  doi?: string
  authors?: any[]
  abstract?: string
  original: AnyObj
}

type ScholarGroup = {
  key: string
  title: string
  profile: string
  decision?: string
  support?: number
  scientificIntent: AnyObj
  sourceSignals: string[]
  articles: GroupedArticle[]
}

function cleanText(value: any, max = 220): string {
  const s = String(value ?? "").replace(/\s+/g, " ").trim()
  return s.length > max ? s.slice(0, max) + "…" : s
}

function norm(value: any): string {
  return String(value ?? "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
}

function stableKey(value: string): string {
  return norm(value).replace(/\s+/g, "_").slice(0, 140) || "unknown"
}

function paperKey(article: AnyObj): string {
  const doi = cleanText(article?.doi, 300).toLowerCase()
  if (doi) return "doi:" + doi
  const id = cleanText(article?.paper_id || article?.paperId || article?.id, 300)
  if (id) return "id:" + id
  return "title:" + norm(article?.title).slice(0, 180) + ":" + String(article?.year || "")
}

function extractReport(latest: AnyObj): AnyObj {
  return (
    latest?.report ||
    latest?.raw_result ||
    latest?.result ||
    latest?.data ||
    latest?.scholar_report ||
    latest ||
    {}
  )
}

function extractResults(report: AnyObj): AnyObj[] {
  const results =
    report?.results ||
    report?.raw_result?.results ||
    report?.report?.results ||
    []
  return Array.isArray(results) ? results : []
}

function scientificTitle(result: AnyObj): string {
  const intent = result?.scientific_intent || {}
  return cleanText(
    intent?.verrou_title ||
      result?.enriched_title ||
      result?.scientific_title ||
      result?.verrou_title ||
      result?.title ||
      "Verrou scientifique",
    260
  )
}

function scientificProfile(result: AnyObj): string {
  const intent = result?.scientific_intent || {}
  return cleanText(
    intent?.backend_enrichment_profile ||
      intent?.enrichment_profile ||
      result?.backend_enrichment_profile ||
      result?.enrichment_profile ||
      "",
    120
  )
}

function originalSignal(result: AnyObj): string {
  const intent = result?.scientific_intent || {}
  return cleanText(
    intent?.original_title ||
      result?.original_title ||
      result?.raw_item?.original_title ||
      result?.raw_item?.theme_label ||
      result?.raw_item?.title ||
      result?.verrou_title ||
      result?.title ||
      "",
    220
  )
}

function groupScholarResults(report: AnyObj): ScholarGroup[] {
  const results = extractResults(report)
  const groups = new Map<string, ScholarGroup>()

  for (const result of results) {
    const title = scientificTitle(result)
    const profile = scientificProfile(result)
    const key = stableKey((profile || "generic") + "__" + title)

    if (!groups.has(key)) {
      groups.set(key, {
        key,
        title,
        profile,
        decision: result?.decision,
        support: Number(result?.scientific_support_score ?? 0),
        scientificIntent: result?.scientific_intent || {},
        sourceSignals: [],
        articles: [],
      })
    }

    const group = groups.get(key)!
    group.support = Math.max(group.support || 0, Number(result?.scientific_support_score ?? 0))
    if (result?.decision === "verrou_scientifiquement_defendable") {
      group.decision = result.decision
    } else if (!group.decision) {
      group.decision = result?.decision
    }

    const signal = originalSignal(result)
    if (signal && !group.sourceSignals.some((x) => norm(x) === norm(signal))) {
      group.sourceSignals.push(signal)
    }

    const existingArticles = new Set(group.articles.map((a) => a.key))
    const articles = Array.isArray(result?.articles) ? result.articles : []

    for (const article of articles) {
      if (!article || !article.title) continue
      const pk = paperKey(article)
      if (existingArticles.has(pk)) continue
      existingArticles.add(pk)

      group.articles.push({
        key: pk,
        title: cleanText(article.title, 360),
        year: article.year,
        tag: article.tag || "Fondamental",
        relevance_score: Number(article.relevance_score ?? 0),
        source: article.source,
        url: article.url,
        doi: article.doi,
        authors: article.authors || [],
        abstract: article.abstract || article.tldr || "",
        original: article,
      })
    }
  }

  const tagRank: Record<string, number> = { Direct: 3, Connexe: 2, Fondamental: 1 }
  return Array.from(groups.values())
    .map((g) => ({
      ...g,
      sourceSignals: g.sourceSignals.slice(0, 8),
      articles: g.articles.sort((a, b) => {
        const tr = (tagRank[b.tag] || 0) - (tagRank[a.tag] || 0)
        if (tr !== 0) return tr
        return Number(b.relevance_score || 0) - Number(a.relevance_score || 0)
      }),
    }))
    .sort((a, b) => {
      const scoreA = (a.decision === "verrou_scientifiquement_defendable" ? 2 : 0) + (a.support || 0)
      const scoreB = (b.decision === "verrou_scientifiquement_defendable" ? 2 : 0) + (b.support || 0)
      return scoreB - scoreA
    })
}

function getApiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"
}

function readCookie(name: string): string {
  if (typeof document === "undefined") return ""
  const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"))
  return match ? decodeURIComponent(match[2]) : ""
}

function looksLikeJwt(value: string): boolean {
  return /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value.trim())
}

function extractTokenFromValue(value: string): string {
  if (!value) return ""

  const direct = value.trim().replace(/^Bearer\s+/i, "")
  if (looksLikeJwt(direct)) return direct

  try {
    const parsed = JSON.parse(value)
    const stack: any[] = [parsed]
    const tokenKeys = [
      "token",
      "access_token",
      "accessToken",
      "auth_token",
      "authToken",
      "jwt",
      "bearer",
      "bearer_token",
      "access",
    ]

    while (stack.length) {
      const obj = stack.pop()
      if (!obj || typeof obj !== "object") continue

      for (const key of tokenKeys) {
        const candidate = obj[key]
        if (typeof candidate === "string") {
          const cleaned = candidate.trim().replace(/^Bearer\s+/i, "")
          if (cleaned && (looksLikeJwt(cleaned) || cleaned.length > 20)) return cleaned
        }
      }

      for (const v of Object.values(obj)) {
        if (typeof v === "object" && v !== null) stack.push(v)
        if (typeof v === "string") {
          const cleaned = v.trim().replace(/^Bearer\s+/i, "")
          if (looksLikeJwt(cleaned)) return cleaned
        }
      }
    }
  } catch {
    // pas un JSON, ignorer
  }

  const m = value.match(/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/)
  if (m?.[0]) return m[0]

  return ""
}

function getAuthToken(): string {
  if (typeof window === "undefined") return ""

  const priorityKeys = [
    "ennosmart_token",
    "ennosmart-auth",
    "ennosmart_auth",
    "auth",
    "auth-storage",
    "user",
    "session",
    "access_token",
    "accessToken",
    "auth_token",
    "authToken",
    "token",
    "jwt",
    "bearer_token",
  ]

  for (const key of priorityKeys) {
    const raw = localStorage.getItem(key) || sessionStorage.getItem(key) || readCookie(key)
    const token = extractTokenFromValue(raw || "")
    if (token) return token
  }

  for (const storage of [localStorage, sessionStorage]) {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (!key) continue
      const raw = storage.getItem(key) || ""
      const token = extractTokenFromValue(raw)
      if (token) return token
    }
  }

  return ""
}

function getAuthHeaders(): HeadersInit {
  const token = getAuthToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function tagClass(tag: string): string {
  if (tag === "Direct") return "bg-emerald-100 text-emerald-700 border-emerald-200"
  if (tag === "Connexe") return "bg-blue-100 text-blue-700 border-blue-200"
  return "bg-slate-100 text-slate-600 border-slate-200"
}

function decisionLabel(decision?: string): string {
  if (decision === "verrou_scientifiquement_defendable") return "Défendable scientifiquement"
  if (decision === "verrou_a_confirmer_par_etat_art") return "À confirmer par état de l’art"
  if (decision === "support_scientifique_faible") return "Support faible"
  if (decision === "aucun_article_trouve") return "Aucun article trouvé"
  return decision || "Décision non disponible"
}

export function EnnoScholarGroupedStateOfArtPanel({ projectId }: { projectId: number | string }) {
  const apiBase = getApiBase()
  const [loading, setLoading] = useState(false)
  const [latest, setLatest] = useState<AnyObj | null>(null)
  const [error, setError] = useState("")
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [writerMode, setWriterMode] = useState<"template" | "auto" | "llm">("template")
  const [writing, setWriting] = useState(false)
  const [stateOfArt, setStateOfArt] = useState<AnyObj | null>(null)

  async function loadLatest() {
    setLoading(true)
    setError("")
    try {
      const res = await fetch(`${apiBase}/projects/${projectId}/scholar/latest`, {
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(),
        },
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setLatest(data)
    } catch (e: any) {
      setError(e?.message || "Impossible de charger EnnoScholar.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadLatest()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const report = useMemo(() => extractReport(latest || {}), [latest])
  const groups = useMemo(() => groupScholarResults(report), [report])

  useEffect(() => {
    const initial: Record<string, boolean> = {}
    for (const g of groups) {
      for (const a of g.articles) {
        initial[`${g.key}::${a.key}`] = a.tag === "Direct" || a.tag === "Connexe"
      }
    }
    setSelected(initial)
  }, [groups.length])

  const totalArticles = groups.reduce((acc, g) => acc + g.articles.length, 0)
  const totalSelected = Object.values(selected).filter(Boolean).length

  function selectedArticlesForGroup(group: ScholarGroup): GroupedArticle[] {
    return group.articles.filter((a) => selected[`${group.key}::${a.key}`])
  }

  function selectedDirectConnexeForGroup(group: ScholarGroup): GroupedArticle[] {
    return selectedArticlesForGroup(group).filter((a) => a.tag === "Direct" || a.tag === "Connexe")
  }

  function buildSelectionPayload() {
    const writableGroups = groups.filter((g) => selectedDirectConnexeForGroup(g).length > 0)

    return {
      agent: "EnnoScholar",
      payload_type: "selected_articles_for_state_of_art",
      organisme: report?.organisme,
      project: report?.project,
      year: report?.year,
      domain_detection: report?.domain_detection || {},
      diagnostic_context: report?.diagnostic_context || {},
      verrous: writableGroups.map((g) => ({
        verrou_id: g.key,
        verrou_title: g.title,
        scientific_intent: {
          ...(g.scientificIntent || {}),
          verrou_id: g.key,
          verrou_title: g.title,
        },
        source_signals: g.sourceSignals,
        selected_articles: selectedArticlesForGroup(g).map((a) => ({
          ...a.original,
          consultant_selected: true,
          tag: a.tag,
          relevance_score: a.relevance_score,
        })),
      })),
    }
  }

  async function writeStateOfArt() {
    setWriting(true)
    setError("")
    setStateOfArt(null)

    try {
      const payload = buildSelectionPayload()
      if (!payload.verrous.length) {
        throw new Error("Aucun verrou prêt pour rédaction : sélectionne au moins un article Direct ou Connexe.")
      }

      const res = await fetch(
        `${apiBase}/projects/${projectId}/scholar/state-of-art/write-from-selection?writer_mode=${writerMode}`,
        {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            ...getAuthHeaders(),
          },
          body: JSON.stringify(payload),
        }
      )

      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setStateOfArt(data)
    } catch (e: any) {
      setError(e?.message || "Erreur pendant la rédaction de l’état de l’art.")
    } finally {
      setWriting(false)
    }
  }

  if (loading) {
    return <div className="rounded-xl border p-4 text-sm text-slate-500">Chargement EnnoScholar…</div>
  }

  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-violet-100 bg-violet-50/60 p-4">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <h3 className="text-lg font-semibold text-slate-900">
              EnnoScholar — articles groupés par verrou scientifique
            </h3>
            <p className="text-sm text-slate-600">
              Affichage basé sur le dernier run EnnoScholar, pas sur tous les anciens articles synchronisés.
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Auth frontend : {getAuthToken() ? "token détecté" : "aucun token détecté"}
            </p>
          </div>
          <button
            onClick={loadLatest}
            className="rounded-xl border border-violet-200 bg-white px-4 py-2 text-sm font-medium text-violet-700 hover:bg-violet-50"
          >
            Recharger
          </button>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-4">
          <div className="rounded-xl bg-white p-3">
            <div className="text-xs text-slate-500">Verrous scientifiques</div>
            <div className="text-2xl font-bold">{groups.length}</div>
          </div>
          <div className="rounded-xl bg-white p-3">
            <div className="text-xs text-slate-500">Articles du dernier run</div>
            <div className="text-2xl font-bold">{totalArticles}</div>
          </div>
          <div className="rounded-xl bg-white p-3">
            <div className="text-xs text-slate-500">Articles sélectionnés</div>
            <div className="text-2xl font-bold">{totalSelected}</div>
          </div>
          <div className="rounded-xl bg-white p-3">
            <div className="text-xs text-slate-500">Mode rédaction</div>
            <select
              value={writerMode}
              onChange={(e) => setWriterMode(e.target.value as any)}
              className="mt-1 w-full rounded-lg border px-2 py-1 text-sm"
            >
              <option value="template">Template sans LLM</option>
              <option value="auto">Auto LLM si disponible</option>
              <option value="llm">LLM uniquement</option>
            </select>
          </div>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error.includes("Not authenticated") || error.includes("401") ? (
            <>
              <b>Connexion backend non transmise au nouveau panel.</b>
              <br />
              Le composant V45 est bien affiché, mais il ne reçoit pas encore le token utilisateur.
              Relance après reconnexion, ou vérifie la clé de token utilisée dans localStorage.
            </>
          ) : (
            error
          )}
        </div>
      )}

      {!groups.length && (
        <div className="rounded-xl border bg-white p-4 text-sm text-slate-600">
          Aucun résultat EnnoScholar trouvé. Lance d’abord la recherche scientifique.
        </div>
      )}

      {groups.map((group, idx) => {
        const direct = group.articles.filter((a) => a.tag === "Direct").length
        const connexe = group.articles.filter((a) => a.tag === "Connexe").length
        const fond = group.articles.filter((a) => a.tag === "Fondamental").length
        const selectedDC = selectedDirectConnexeForGroup(group).length

        return (
          <div key={group.key} className="rounded-2xl border bg-white p-5 shadow-sm">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-violet-600">
                  Verrou scientifique consolidé {idx + 1}
                </div>
                <h4 className="mt-1 text-lg font-semibold text-slate-900">{group.title}</h4>
                <p className="mt-1 text-sm text-slate-600">
                  {decisionLabel(group.decision)} — support {Math.round((group.support || 0) * 100)}%
                </p>
              </div>

              <div className="flex flex-wrap gap-2 text-xs">
                <span className="rounded-full border bg-emerald-50 px-2 py-1 text-emerald-700">Direct {direct}</span>
                <span className="rounded-full border bg-blue-50 px-2 py-1 text-blue-700">Connexe {connexe}</span>
                <span className="rounded-full border bg-slate-50 px-2 py-1 text-slate-600">Fondamental {fond}</span>
              </div>
            </div>

            {group.sourceSignals.length > 0 && (
              <div className="mt-4 rounded-xl bg-slate-50 p-3">
                <div className="text-sm font-medium text-slate-800">Signaux EnnoDiagnostic liés</div>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">
                  {group.sourceSignals.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </div>
            )}

            {selectedDC === 0 && (
              <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                Ce verrou ne sera pas rédigé tant qu’aucun article Direct ou Connexe n’est sélectionné.
              </div>
            )}

            <div className="mt-4 space-y-2">
              {group.articles.map((article) => {
                const key = `${group.key}::${article.key}`
                return (
                  <label
                    key={key}
                    className="flex cursor-pointer gap-3 rounded-xl border p-3 hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={!!selected[key]}
                      onChange={(e) =>
                        setSelected((prev) => ({
                          ...prev,
                          [key]: e.target.checked,
                        }))
                      }
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`rounded-full border px-2 py-0.5 text-xs ${tagClass(article.tag)}`}>
                          {article.tag}
                        </span>
                        <span className="text-xs text-slate-500">
                          Score {Math.round(Number(article.relevance_score || 0) * 100)}%
                        </span>
                        {article.year && <span className="text-xs text-slate-500">{article.year}</span>}
                      </div>
                      <div className="mt-1 font-medium text-slate-900">{article.title}</div>
                      <div className="mt-1 text-xs text-slate-500">
                        Source : {article.source || "source inconnue"}
                        {article.url && (
                          <>
                            {" "}
                            —{" "}
                            <a
                              href={article.url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-violet-700 underline"
                            >
                              ouvrir
                            </a>
                          </>
                        )}
                      </div>
                    </div>
                  </label>
                )
              })}
            </div>
          </div>
        )
      })}

      {!!groups.length && (
        <div className="rounded-2xl border bg-white p-5">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <h4 className="font-semibold">Rédaction de l’état de l’art</h4>
              <p className="text-sm text-slate-600">
                Seuls les verrous avec au moins un article Direct ou Connexe sélectionné seront rédigés.
              </p>
            </div>
            <button
              onClick={writeStateOfArt}
              disabled={writing}
              className="rounded-xl bg-violet-600 px-5 py-2 text-sm font-semibold text-white hover:bg-violet-700 disabled:opacity-50"
            >
              {writing ? "Rédaction…" : "Rédiger l’état de l’art"}
            </button>
          </div>
        </div>
      )}

      {stateOfArt && (
        <div className="rounded-2xl border bg-white p-5">
          <h3 className="text-lg font-semibold">Résultat rédaction</h3>
          <p className="mt-1 text-sm text-slate-600">
            Verrous rédigés : {stateOfArt.verrous_written || 0} — erreurs citations :{" "}
            {stateOfArt.citation_errors || 0}
          </p>

          {Array.isArray(stateOfArt.skipped) && stateOfArt.skipped.length > 0 && (
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              {stateOfArt.skipped.length} verrou(s) non rédigé(s), faute d’article Direct/Connexe.
            </div>
          )}

          <div className="mt-5 space-y-6">
            {(stateOfArt.results || []).map((r: AnyObj, i: number) => {
              const draft = r?.state_of_art?.draft || ""
              return (
                <div key={i} className="rounded-xl border p-4">
                  <h4 className="font-semibold">
                    État de l’art V{i + 1} — {r?.verrou_title}
                  </h4>
                  <p className="mb-3 text-sm text-slate-500">
                    Articles sélectionnés : {r?.selected_articles_count || 0}
                  </p>
                  <div className="prose prose-sm max-w-none whitespace-pre-wrap">
                    {draft || "Aucun texte généré."}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default EnnoScholarGroupedStateOfArtPanel
