export type ImprovementResearchSource = Record<string, any>

export function normalizeSourceDecision(value: unknown) {
  const decision = String(value || "").trim().toLowerCase()
  if (["accept", "accepted", "garde", "kept"].includes(decision)) return "accepted"
  if (["reject", "rejected", "rejete", "rejeté", "ecarte", "écarté"].includes(decision)) return "rejected"
  return "pending"
}

function sourceKeys(source: ImprovementResearchSource) {
  // candidate_id remains stable when extraction creates an article_id.
  return [
    ["candidate", source.candidate_id],
    ["article", source.article_id || source.fulltext_preparation?.article_id],
    ["evidence", source.evidence_id],
    ["doi", source.doi],
  ].filter(([, value]) => String(value || "").trim())
    .map(([kind, value]) => `${kind}:${String(value).trim()}`)
}

export function hydrateImprovementResearchSources(
  snapshots: ImprovementResearchSource[],
  currentSources: ImprovementResearchSource[],
) {
  const current = new Map<string, ImprovementResearchSource>()
  for (const source of currentSources) {
    for (const key of sourceKeys(source)) current.set(`${source.guided_session_id || ""}:${key}`, source)
  }
  return snapshots.map((source) => {
    const searchId = String(source.guided_session_id || "")
    let latest = sourceKeys(source).map((key) => current.get(`${searchId}:${key}`)).find(Boolean)
    if (!latest && !searchId) {
      // A legacy snapshot is hydrated only when its origin is unambiguous.
      for (const key of sourceKeys(source)) {
        const matches = currentSources.filter((row) => sourceKeys(row).includes(key))
        if (matches.length === 1) { latest = matches[0]; break }
        if (matches.length > 1) break
      }
    }
    return latest ? { ...source, ...latest } : source
  })
}

export function researchSourceSearchId(source: ImprovementResearchSource, context: Record<string, any>) {
  if (source.guided_session_id) return String(source.guided_session_id)
  const history = context.research_history || (context.scholar_handoff ? [context.scholar_handoff] : [])
  const matches = history.filter((search: Record<string, any>) => (search.sources || []).some(
    (row: ImprovementResearchSource) => row.candidate_id === source.candidate_id,
  ))
  return matches.length === 1 ? String(matches[0].guided_session_id || "") : ""
}

export function improvementResearchByMessage(messages: Array<Record<string, any>>, context: Record<string, any>) {
  const currentSources = context.research_sources || context.scholar_handoff?.sources || []
  const result = new Map<string, ImprovementResearchSource[]>()
  messages.forEach((message, index) => {
    const metadata = message.metadata || {}
    const handoff = metadata.scholar_handoff || {}
    const rows = [message.research_sources, message.sources, metadata.research_sources, metadata.sources,
      metadata.research?.sources, metadata.research?.candidates, handoff.sources, metadata.scholar?.sources]
      .find(Array.isArray)
    if (rows?.length) {
      const snapshots = rows.map((row: ImprovementResearchSource) => ({ ...row,
        guided_session_id: row.guided_session_id || handoff.guided_session_id || metadata.guided_session_id,
      }))
      result.set(String(message.message_id || index), hydrateImprovementResearchSources(snapshots, currentSources))
    }
  })
  for (const search of context.research_history || []) {
    if (search.message_id && messages.some((message) => message.message_id === search.message_id)) {
      result.set(String(search.message_id), hydrateImprovementResearchSources(search.sources || [], currentSources))
    }
  }
  // Compatibility for the old single-search payload. Never attach a combined
  // corpus to the last answer (which may be a rewrite or a selection receipt).
  const latest = context.scholar_handoff
  if (result.size === 0 && latest?.sources?.length) {
    const message = [...messages].reverse().find((row) => row.role === "assistant"
      && row.intent === "awaiting_evidence"
      && (row.metadata?.routing?.needs_scholar || row.metadata?.agents_used?.includes("EnnoScholar")))
    if (message) result.set(String(message.message_id), latest.sources.map((row: ImprovementResearchSource) => ({
      ...row, guided_session_id: latest.guided_session_id,
    })))
  }
  return result
}

export function researchSourceArticleId(source: ImprovementResearchSource) {
  const id = Number(source.article_id || source.fulltext_preparation?.article_id || 0)
  return Number.isSafeInteger(id) && id > 0 ? id : 0
}

export function researchSourceReady(source: ImprovementResearchSource) {
  return source.article_card_ready === true
    || source.fulltext_preparation?.ready_for_writing === true
    || source.fulltext_preparation?.article_card_ready === true
}

export function researchSourcePdfLink(source: ImprovementResearchSource) {
  const httpUrl = (value: unknown) => {
    const url = String(value || "").trim()
    return /^https?:\/\//i.test(url) ? url : ""
  }
  const explicitPdf = httpUrl(source.pdf_url || source.fulltext_preparation?.pdf_url)
  if (explicitPdf) return { url: explicitPdf, direct: true }
  const sourceUrl = httpUrl(source.source_url || source.url)
  if (/\.pdf(?:[?#]|$)|\/pdf\/|\/document(?:[?#]|$)/i.test(sourceUrl)) {
    return { url: sourceUrl, direct: true }
  }
  const doi = String(source.doi || "").trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
  return {
    url: httpUrl(source.site_url) || sourceUrl || (doi ? `https://doi.org/${doi}` : ""),
    direct: false,
  }
}
