export type ScholarV2Citation = {
  url: string
  title: string
  start_index: number | null
  end_index: number | null
  cited: boolean
}

export type ScholarV2Article = {
  id: string
  title: string
  authors: string[]
  year: number | null
  abstract: string
  doi: string
  arxiv_id: string
  url: string
  pdf_url: string
  source: string
  sources: string[]
  citation_count: number
  access_level: "metadata_only" | "abstract_only" | "fulltext"
  analysis_basis: "metadata" | "abstract" | "fulltext" | null
  relevance_score: number | null
  relevance_tag: "DIRECT" | "CONNEXE" | "FONDAMENTAL" | null
  ranking_reason: string
  matched_queries: string[]
}

export type ScholarV2Request = {
  session_id: string | null
  message: string
  history?: Array<{ role: "user" | "assistant"; content: string }>
}

export type ScholarV2Response = {
  session_id: string
  answer: string
  citations: ScholarV2Citation[]
  articles: ScholarV2Article[]
  articles_total: number
  active_article_ids: string[]
  partial: boolean
  reason: string | null
  tool_calls: number
  tool_counts: Record<string, number>
  rounds: number
  tool_activity: Array<{ tool: string; status: "completed" | "error" | "skipped" }>
}

export type ScholarV2ArticlePage = {
  session_id: string
  total: number
  offset: number
  limit: number
  next_offset: number | null
  articles: ScholarV2Article[]
}
