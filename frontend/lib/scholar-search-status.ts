type SearchResult = {
  verrou_id?: string | number
  verrou_title?: string
  subject_search_failed?: boolean
  query_planning_failed?: boolean
  search_status?: {
    execution_error?: string
    query_planning_failed?: boolean
  }
}

export function scholarSearchFailureMessage(report: { results?: SearchResult[] } | null): string {
  const results = Array.isArray(report?.results) ? report.results : []
  const failed = results.filter((result) => result && (
    result.subject_search_failed || result.search_status?.execution_error ||
    result.query_planning_failed || result.search_status?.query_planning_failed
  ))
  if (!failed.length) return ""

  const titles = failed.map((result) => result.verrou_title || `Verrou ${result.verrou_id ?? "sans identifiant"}`)
  return `Recherche incomplète : ${failed.length} verrou(s) sur ${results.length} n’ont pas pu être traités jusqu’au bout. ` +
    `Verrous concernés : ${titles.join(" ; ")}. ` +
    "Ce statut ne signifie pas qu’aucun article n’existe. Les autres résultats restent disponibles. " +
    "Relancez la recherche après résolution du problème ; si l’échec persiste, transmettez le rapport technique."
}
