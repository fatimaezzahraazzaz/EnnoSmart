"use client"

import { scholarV31Request } from "@/lib/api"
import type { ScholarV2Citation } from "@/lib/scholar-v2"

export type Citation = ScholarV2Citation
export type Claim = { statement: string; evidence_ids: string[]; origin?: "source_statement" | "inference"; basis?: "abstract" | "fulltext" }
export type Article = {
  id: string; title: string; authors: string[]; year: number | null
  doi: string; arxiv_id: string; url: string; pdf_url: string; abstract: string
  sources: string[]; verified: boolean; selected: boolean
  access_level: "metadata_only" | "abstract_only" | "fulltext"
  publisher_access: "open_access" | "paywalled" | "unknown"
  fulltext_available: boolean | null; analysis_basis: "metadata" | "abstract" | "fulltext" | null; fulltext_source: string
  relevance_tag: "DIRECT" | "CONNEXE" | "FONDAMENTAL" | "HORS_SUJET" | null
  analysis: { method?: Claim[]; results?: Claim[]; relevance?: Claim[]; limitations?: Claim[]
    problem?: Claim[]; objective?: Claim[]; contribution?: Claim[]; generalization?: Claim[]; open_questions?: Claim[]
    method_family?: string; generation_role?: string; analysis_limit?: string; fulltext_truncated?: boolean
    evidence?: { evidence_id: string; text: string; page: number | null; section: string; source_url: string; source_version?: string }[] }
  metadata_errors: string[]
}
export type Message = { id: string; role: "user" | "assistant"; content: string; citations?: Citation[]; run_id?: string; partial?: boolean }
export type SessionSummary = { id: string; title: string; created_at: string; updated_at: string; active_run_id: string | null; articles_total: number; selected_count: number }
export type Session = SessionSummary & { messages: Message[]; articles: Article[]; recommended_article_ids: string[]; research_state: Record<string, unknown>; summary: string }
export type ArticlePage = { total: number; articles: Article[] }
export type Activity = { tool: string; status: string; at: string; query?: string; article_id?: string; family?: string; found?: number; reason?: string }
export type Run = { id: string; session_id: string; status: "queued" | "running" | "completed" | "partial" | "failed" | "cancelled"
  cancel_requested: boolean; activity: Activity[]; answer: string; citations: Citation[]; articles?: Article[]
  recommended_article_ids?: string[]; usage?: { estimated_cost_usd?: number; input_tokens?: number; output_tokens?: number; model?: string }
  duration_seconds?: number; reason?: string; partial?: boolean; tool_counts?: Record<string, number> }

const json = (body: unknown, method = "POST"): RequestInit => ({ method, body: JSON.stringify(body) })
export const listSessions = () => scholarV31Request<SessionSummary[]>("/sessions")
export const createSession = () => scholarV31Request<Session>("/sessions", json({}))
export const getSession = (id: string, signal?: AbortSignal) => scholarV31Request<Session>(`/sessions/${encodeURIComponent(id)}`, { signal })
export const sendMessage = (sessionId: string, message: string) => scholarV31Request<Run>("/chat", json({ session_id: sessionId, message }))
export const getRun = (id: string, signal?: AbortSignal) => scholarV31Request<Run>(`/runs/${encodeURIComponent(id)}`, { signal })
export const cancelRun = (id: string) => scholarV31Request<Run>(`/runs/${encodeURIComponent(id)}/cancel`, json({}))
export const selectArticle = (sid: string, aid: string, selected: boolean) => scholarV31Request<Article>(`/sessions/${encodeURIComponent(sid)}/articles/${encodeURIComponent(aid)}`, json({ selected }, "PATCH"))
export const getArticlePage = (sid: string, offset = 0, query = "", signal?: AbortSignal) => scholarV31Request<ArticlePage>(`/sessions/${encodeURIComponent(sid)}/articles?offset=${offset}&limit=24&query=${encodeURIComponent(query)}`, { signal })
export const uploadPdf = (sid: string, file: File, articleId = "") => {
  const body = new FormData(); body.append("file", file); body.append("article_id", articleId)
  return scholarV31Request<Article>(`/sessions/${encodeURIComponent(sid)}/pdf`, { method: "POST", body })
}
export const isRunning = (run: Run | null) => Boolean(run && ["queued", "running"].includes(run.status))
export function safeUrl(value: string) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : "" } catch { return "" }
}
