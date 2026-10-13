"use client"

import React, { useEffect, useMemo, useState } from "react"

type Props = {
  projectId: number | string
  initialReport?: any
  selectionPayload?: any
  apiBaseUrl?: string
  authToken?: string
}

function apiUrl(base: string | undefined, path: string) {
  const b = (base || "http://127.0.0.1:8000").replace(/\/$/, "")
  return `${b}${path}`
}

function getStructured(result: any) {
  return (
    result?.structured_state_of_art ||
    result?.state_of_art?.structured ||
    result?.updated_state_of_art ||
    null
  )
}

function getResultTitle(result: any) {
  const structured = getStructured(result)

  return String(
    structured?.verrou_title ||
      result?.verrou_title ||
      result?.title ||
      ""
  ).trim()
}

function findPayloadVerrou(selectionPayload: any, result: any, idx: number) {
  const verrous = selectionPayload?.verrous || []

  if (!Array.isArray(verrous)) {
    return null
  }

  const title = getResultTitle(result).toLowerCase()

  const byTitle = verrous.find((v: any) => {
    const t =
      v?.verrou_title ||
      v?.title ||
      v?.scientific_intent?.verrou_title ||
      v?.selected_articles?.[0]?.verrou_scientific_validation?.verrou_title ||
      ""

    return String(t).toLowerCase().trim() === title
  })

  return byTitle || verrous[idx] || null
}

function getSelectedArticles(selectionPayload: any, result: any, idx: number) {
  if (Array.isArray(result?.selected_articles)) {
    return result.selected_articles
  }

  if (Array.isArray(result?.articles)) {
    return result.articles
  }

  const payloadVerrou = findPayloadVerrou(selectionPayload, result, idx)

  if (Array.isArray(payloadVerrou?.selected_articles)) {
    return payloadVerrou.selected_articles
  }

  return []
}

function normalizeText(value: any) {
  return String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
}

function shouldHighlightSection(result: any, sectionKey: string) {
  const instruction = normalizeText(result?.last_chat_instruction)
  const summary = normalizeText((result?.last_chat_changes || []).join(" "))
  const text = `${instruction} ${summary}`

  if (!text.trim()) {
    return false
  }

  if (sectionKey === "positionnement") {
    return text.includes("positionnement") || text.includes("intro")
  }

  if (sectionKey === "travaux_directs") {
    return (
      text.includes("travaux directement") ||
      text.includes("direct") ||
      text.includes("article a1") ||
      text.includes("article a2") ||
      text.includes("a1") ||
      text.includes("a2") ||
      text.includes("a3") ||
      text.includes("a4")
    )
  }

  if (sectionKey === "travaux_connexes") {
    return (
      text.includes("travaux connexes") ||
      text.includes("connexe") ||
      text.includes("article a5") ||
      text.includes("article a6") ||
      text.includes("article a7") ||
      text.includes("article a8") ||
      text.includes("a5") ||
      text.includes("a6") ||
      text.includes("a7") ||
      text.includes("a8")
    )
  }

  if (sectionKey === "limites") {
    return text.includes("limite") || text.includes("limites")
  }

  if (sectionKey === "gap") {
    return text.includes("gap") || text.includes("lacune") || text.includes("scientifique")
  }

  if (sectionKey === "hypotheses") {
    return (
      text.includes("hypothese") ||
      text.includes("hypotheses") ||
      text.includes("a valider") ||
      text.includes("valider consultant")
    )
  }

  if (sectionKey === "references") {
    return text.includes("reference") || text.includes("references")
  }

  return false
}

function SectionCard({
  title,
  children,
  highlight = false,
}: {
  title: string
  children: React.ReactNode
  highlight?: boolean
}) {
  return (
    <div
      className={
        highlight
          ? "rounded-2xl border border-emerald-300 bg-emerald-50 p-5 shadow-sm ring-1 ring-emerald-200"
          : "rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
      }
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-slate-900">
          {title}
        </h3>

        {highlight && (
          <span className="rounded-full bg-emerald-600 px-3 py-1 text-xs font-medium text-white">
            Corrigé par chat
          </span>
        )}
      </div>

      <div className="text-sm leading-7 text-slate-700">
        {children}
      </div>
    </div>
  )
}

export function EnnoScholarStructuredStateArtPanel({
  projectId,
  initialReport,
  selectionPayload,
  apiBaseUrl,
  authToken,
}: Props) {
  const [report, setReport] = useState<any>(initialReport || null)
  const [loading, setLoading] = useState(false)
  const [chatText, setChatText] = useState<Record<string, string>>({})
  const [chatLoading, setChatLoading] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (initialReport) {
      setReport(initialReport)
    }
  }, [initialReport])

  const results = useMemo(() => {
    return Array.isArray(report?.results) ? report.results : []
  }, [report])

  async function generateStructured() {
    if (!selectionPayload) {
      alert("Aucun payload de sélection disponible.")
      return
    }

    setLoading(true)

    try {
      const res = await fetch(
        apiUrl(
          apiBaseUrl,
          `/projects/${projectId}/scholar/state-of-art/write-from-selection?writer_mode=llm`
        ),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
          },
          body: JSON.stringify(selectionPayload),
        }
      )

      const data = await res.json()

      if (!res.ok) {
        throw new Error(data?.detail || "Erreur génération état de l’art.")
      }

      setReport(data)
    } catch (e: any) {
      alert(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  async function sendChat(result: any, idx: number) {
    const instruction = chatText[String(idx)] || ""

    if (!instruction.trim()) {
      alert("Écris une consigne consultant.")
      return
    }

    const current = getStructured(result)

    if (!current) {
      alert("Aucun état de l’art structuré à modifier.")
      return
    }

    const selectedArticles = getSelectedArticles(selectionPayload, result, idx)

    setChatLoading((x) => ({ ...x, [idx]: true }))

    try {
      const res = await fetch(
        apiUrl(apiBaseUrl, `/projects/${projectId}/scholar/state-of-art/chat`),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
          },
          body: JSON.stringify({
            verrou_title: result?.verrou_title || current?.verrou_title,
            current_state_of_art: current,
            selected_articles: selectedArticles,
            consultant_instruction: instruction,
          }),
        }
      )

      const data = await res.json()

      if (!res.ok) {
        throw new Error(data?.detail || "Erreur chat verrou.")
      }

      const newResults = [...results]

      newResults[idx] = {
        ...newResults[idx],
        structured_state_of_art: data.updated_state_of_art,
        draft: data.draft,
        last_chat_instruction: instruction,
        last_chat_changes: data.changes_summary || [],
        last_chat_updated_at: new Date().toISOString(),
      }

      setReport({
        ...report,
        results: newResults,
      })

      setChatText((x) => ({ ...x, [idx]: "" }))
    } catch (e: any) {
      alert(e?.message || String(e))
    } finally {
      setChatLoading((x) => ({ ...x, [idx]: false }))
    }
  }

  return (
    <div className="space-y-6 rounded-3xl border border-violet-100 bg-violet-50/40 p-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            État de l’art structuré
          </h2>

          <p className="text-sm text-slate-600">
            Rédaction par verrou, avec articles sélectionnés et mémoire de style CIR.
          </p>
        </div>

        <button
          onClick={generateStructured}
          disabled={loading || !selectionPayload}
          className="rounded-xl bg-violet-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {loading ? "Rédaction..." : "Rédiger structuré"}
        </button>
      </div>

      {results.length === 0 && (
        <div className="rounded-2xl border border-dashed border-violet-200 bg-white p-5 text-sm text-slate-600">
          Aucun état de l’art structuré pour le moment. Clique sur{" "}
          <span className="font-medium">Rédiger structuré</span>.
        </div>
      )}

      {results.map((result: any, idx: number) => {
        const s = getStructured(result)

        if (!s) {
          return null
        }

        return (
          <div
            key={idx}
            className="space-y-4 rounded-3xl border border-slate-200 bg-slate-50 p-5"
          >
            <div>
              <h2 className="text-xl font-semibold text-slate-950">
                {s.verrou_title || result.verrou_title}
              </h2>

              <div className="mt-2 flex flex-wrap gap-2 text-xs">
                <span className="rounded-full bg-white px-3 py-1 text-slate-700">
                  Articles : {result.selected_articles_count || 0}
                </span>

                <span className="rounded-full bg-white px-3 py-1 text-slate-700">
                  LLM : {result.llm_used ? "oui" : "non"}
                </span>

                <span className="rounded-full bg-white px-3 py-1 text-slate-700">
                  Style CIR : {result.style_memory?.used ? "oui" : "non"}
                </span>

                {result.last_chat_updated_at && (
                  <span className="rounded-full bg-emerald-600 px-3 py-1 font-medium text-white">
                    Dernière correction chat appliquée
                  </span>
                )}
              </div>
            </div>

            <SectionCard
              title="1. Positionnement du verrou"
              highlight={shouldHighlightSection(result, "positionnement")}
            >
              <p>{s.positionnement}</p>
            </SectionCard>

            <SectionCard
              title="2. Travaux directement liés"
              highlight={shouldHighlightSection(result, "travaux_directs")}
            >
              <div className="space-y-4">
                {(s.travaux_directs || []).map((a: any, i: number) => (
                  <div key={i} className="rounded-xl bg-white/70 p-4">
                    <p className="font-medium text-slate-900">
                      {a.article_ref} — {a.article_title}
                    </p>

                    <p className="mt-2">
                      {a.synthesis}
                    </p>

                    {a.limits_for_project && (
                      <p className="mt-2 text-slate-600">
                        Limite / transposition : {a.limits_for_project}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </SectionCard>

            <SectionCard
              title="3. Travaux connexes utiles"
              highlight={shouldHighlightSection(result, "travaux_connexes")}
            >
              <div className="space-y-4">
                {(s.travaux_connexes || []).map((a: any, i: number) => (
                  <div key={i} className="rounded-xl bg-white/70 p-4">
                    <p className="font-medium text-slate-900">
                      {a.article_ref} — {a.article_title}
                    </p>

                    <p className="mt-2">
                      {a.synthesis}
                    </p>

                    {a.limits_for_project && (
                      <p className="mt-2 text-slate-600">
                        Limite / transposition : {a.limits_for_project}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </SectionCard>

            <SectionCard
              title="4. Limites de l’état de l’art"
              highlight={shouldHighlightSection(result, "limites")}
            >
              <ul className="list-disc space-y-2 pl-5">
                {(s.limites_etat_art || []).map((x: string, i: number) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </SectionCard>

            <SectionCard
              title="5. Gap scientifique"
              highlight={shouldHighlightSection(result, "gap")}
            >
              <p>{s.gap_scientifique}</p>
            </SectionCard>

            <SectionCard
              title="6. Hypothèses à valider consultant"
              highlight={shouldHighlightSection(result, "hypotheses")}
            >
              <ul className="list-disc space-y-2 pl-5">
                {(s.hypotheses_a_valider || []).map((x: string, i: number) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </SectionCard>

            <SectionCard
              title="7. Références mobilisées"
              highlight={shouldHighlightSection(result, "references")}
            >
              <ul className="space-y-2">
                {(s.references || []).map((r: any, i: number) => (
                  <li key={i}>
                    <span className="font-medium">
                      [{r.article_ref}]
                    </span>{" "}
                    {r.reference}
                  </li>
                ))}
              </ul>
            </SectionCard>

            <div className="rounded-2xl border border-violet-100 bg-white p-4">
              <h3 className="font-semibold text-slate-900">
                Chat d’amélioration du verrou
              </h3>

              <p className="mt-1 text-sm text-slate-600">
                Exemple : “Réduis l’importance de A2”, “Reformule le gap”,
                “Rends plus prudent”.
              </p>

              <textarea
                value={chatText[String(idx)] || ""}
                onChange={(e) =>
                  setChatText((x) => ({
                    ...x,
                    [idx]: e.target.value,
                  }))
                }
                className="mt-3 min-h-24 w-full rounded-xl border border-slate-200 p-3 text-sm"
                placeholder="Consigne consultant..."
              />

              <button
                onClick={() => sendChat(result, idx)}
                disabled={chatLoading[String(idx)]}
                className="mt-3 rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {chatLoading[String(idx)]
                  ? "Correction..."
                  : "Appliquer la consigne"}
              </button>

              {result.last_chat_changes?.length > 0 && (
                <div className="mt-3 rounded-xl border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900">
                  <p className="font-medium">
                    Modifications appliquées :
                  </p>

                  <ul className="mt-1 list-disc pl-5">
                    {result.last_chat_changes.map((x: string, i: number) => (
                      <li key={i}>{x}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
