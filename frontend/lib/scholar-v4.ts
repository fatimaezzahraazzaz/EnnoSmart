"use client"

import { scholarV4Request, scholarV4Blob } from "@/lib/api"
import type { ScholarV2Citation } from "@/lib/scholar-v2"

export type Citation = ScholarV2Citation
export type Claim = { statement: string; evidence_ids: string[]; origin?: "source_statement" | "inference"; basis?: "abstract" | "fulltext" }
export type Article = {
  id: string; title: string; authors: string[]; year: number | null
  doi: string; arxiv_id: string; url: string; pdf_url: string; abstract: string
  sources: string[]; verified: boolean; selected: boolean
  access_status: "metadata_only" | "abstract_only" | "fulltext_available" | "fulltext_extracted" | "fulltext_analyzed" | "access_blocked" | "extraction_failed"
  access_level: "metadata_only" | "abstract_only" | "fulltext"
  publisher_access: "open_access" | "paywalled" | "unknown"
  fulltext_available: boolean | null; analysis_basis: "metadata" | "abstract" | "fulltext" | null; fulltext_source: string
  analysis: { method?: Claim[]; results?: Claim[]; relevance?: Claim[]; limitations?: Claim[]
    problem?: Claim[]; objective?: Claim[]; contribution?: Claim[]; generalization?: Claim[]; open_questions?: Claim[]
    technical_mechanism?: Claim[]; equations?: Claim[]; parameters?: Claim[]; experimental_setup?: Claim[]; validation_protocol?: Claim[]
    datasets_materials?: Claim[]; metrics?: Claim[]; tables_figures?: Claim[]; comparisons?: Claim[]; conditions_of_validity?: Claim[]
    method_family?: string; generation_role?: string; analysis_limit?: string; fulltext_truncated?: boolean; reading_coverage?: string
    evidence?: { evidence_id: string; text?: string; exact_quote?: string; page?: number | null; page_number?: number | null; section: string; source_url: string; source_version?: string }[] }
  selection_override?: boolean | null
  metadata_errors: string[]
}
export type Message = { id: string; role: "user" | "assistant"; content: string; citations?: Citation[]; run_id?: string; partial?: boolean
  primary_article_ids?: string[] | null; displayed_article_ids?: string[] | null; complementary_count?: number | null; discovered_sources_count?: number | null; fallback?: boolean
  library_counts?: Record<string, number> | null; coverage_sufficient?: boolean | null; publication_accepted?: boolean | null; research_status?: "sufficient" | "incomplete" | "not_assessed" | null }
export type SessionSummary = { id: string; title: string; created_at: string; updated_at: string; active_run_id: string | null; articles_total: number; selected_count: number }
export type Session = SessionSummary & { messages: Message[]; articles: Article[]; recommended_article_ids: string[]; research_state: Record<string, unknown>; summary: string; writing_progress?: WritingProgress
  outline?: { sections?: { id: string; title: string }[] }; held_article_ids?: string[]; consultant_decisions?: ConsultantDecision[] }
export type ConsultantDecision = { article_id?: string; decision?: Decision; order?: string[]; origin: string; at: string; exact_quote?: string }
export type Decision = "accept" | "reject" | "hold" | "restore"
export type ArticlePage = { total: number; articles: Article[] }
export type Activity = { tool: string; status: string; at: string; query?: string; article_id?: string; family?: string; found?: number; reason?: string }
export type Run = { id: string; session_id: string; status: "queued" | "running" | "completed" | "partial" | "failed" | "cancelled"
  cancel_requested: boolean; activity: Activity[]; answer: string; citations: Citation[]; articles?: Article[]
  recommended_article_ids?: string[]; usage?: { estimated_cost_usd?: number; input_tokens?: number; output_tokens?: number; model?: string }
  duration_seconds?: number; reason?: string; partial?: boolean; tool_counts?: Record<string, number> }

const json = (body: unknown, method = "POST"): RequestInit => ({ method, body: JSON.stringify(body) })
export const listSessions = () => scholarV4Request<SessionSummary[]>("/sessions")
export const createSession = () => scholarV4Request<Session>("/sessions", json({}))
export const getSession = (id: string, signal?: AbortSignal) => scholarV4Request<Session>(`/sessions/${encodeURIComponent(id)}`, { signal })
export const sendMessage = (sessionId: string, message: string) => scholarV4Request<Run>("/chat", json({ session_id: sessionId, message }))
export const getRun = (id: string, signal?: AbortSignal) => scholarV4Request<Run>(`/runs/${encodeURIComponent(id)}`, { signal })
export const cancelRun = (id: string) => scholarV4Request<Run>(`/runs/${encodeURIComponent(id)}/cancel`, json({}))
export const selectArticle = (sid: string, aid: string, selected: boolean) => scholarV4Request<Article>(`/sessions/${encodeURIComponent(sid)}/articles/${encodeURIComponent(aid)}`, json({ selected }, "PATCH"))
export const decideArticle = (sid: string, aid: string, decision: Decision) => scholarV4Request<Article>(`/sessions/${encodeURIComponent(sid)}/articles/${encodeURIComponent(aid)}`, json({ decision }, "PATCH"))
export const orderArticles = (sid: string, articleIds: string[]) => scholarV4Request<{ recommended_article_ids: string[] }>(`/sessions/${encodeURIComponent(sid)}/articles/order`, json({ article_ids: articleIds }))
export const getArticle = (sid: string, aid: string, signal?: AbortSignal) => scholarV4Request<Article>(`/sessions/${encodeURIComponent(sid)}/articles/${encodeURIComponent(aid)}`, { signal })
export const outlineEdit = (action: string, fields: Record<string, unknown> = {}) => ({ action: "outline_edit", outline: {
  action, title: null, section_id: null, target_section_id: null, position: null, sections: null, article_ids: null, ...fields } })
export const getArticlePage = (sid: string, offset = 0, query = "", signal?: AbortSignal, excludeIds: string[] = [], category = "all") => scholarV4Request<ArticlePage>(`/sessions/${encodeURIComponent(sid)}/articles?offset=${offset}&limit=24&query=${encodeURIComponent(query)}&category=${encodeURIComponent(category)}${excludeIds.map(id => `&exclude_ids=${encodeURIComponent(id)}`).join("")}`, { signal })
export const uploadPdf = (sid: string, file: File, articleId = "") => {
  const body = new FormData(); body.append("file", file); body.append("article_id", articleId)
  return scholarV4Request<Article>(`/sessions/${encodeURIComponent(sid)}/pdf`, { method: "POST", body })
}
export const uploadDocument = (sid: string, file: File) => {
  const body = new FormData(); body.append("file", file)
  return scholarV4Request<Article>(`/sessions/${encodeURIComponent(sid)}/documents`, { method: "POST", body })
}
export const isRunning = (run: Run | null) => Boolean(run && ["queued", "running"].includes(run.status))
export type ScientificScope = { topic?: string; scientific_question?: string; technical_context?: string; objectives?: string[]; uncertainties?: string[]; experimental_conditions?: string[]; constraints?: string[]; unconfirmed_assumptions?: string[]; cir_year?: number | null; start_date?: string | null; reference_date?: string | null; work_type?: "exploration" | "plan" | "state_of_art" | "revision" }
export type WritingProgress = { status?: "awaiting_plan" | "running" | "plan_changed" | "draft_complete"; mode?: "full" | "section"; target_ids?: string[]; saved_draft_ids?: Record<string, string>; section_status?: Record<string, "pending" | "running" | "saved" | "failed">; approval_required?: boolean; errors?: {section_id: string; message: string}[] }
export type Draft = { id: string; text: string; raw_text?: string; article_ids: string[]; evidence_ids: string[]; paragraphs?: {id: string; text: string}[]; instructions: string }
export type ScientificSection = { id: string; title: string; description: string; article_ids: string[]; scientific_objective?: string; questions?: string[]; article_roles?: {article_id: string; role: string}[]; relation_ids?: string[]; experimental_results?: string[]; limitations?: string[]; evidence_ids?: string[]; missing_information?: string[]; project_connection?: string; figure_suggestions?: string[]; drafts: Draft[] }
export type ScientificFigure = { id: string; section_id: string; kind: "diagram" | "chart" | "original"; title: string; caption: string; origin: string; scientific_argument: string; recipe: Record<string, unknown>; evidence_ids: string[]; license_url?: string; source_url?: string }
export type ScientificDocument = {
  outline: { title?: string; version?: number; plan_revision?: number; status?: string; sections?: ScientificSection[]; approval?: {owner_id: number; origin: string} }
  document: { version_id?: string; revision?: number; review_stale?: boolean; approval?: {version_id: string}; writing_progress?: WritingProgress }
  scientific_scope: ScientificScope
  coverage: { scope: {knowledge_cutoff?: string; reference_date_confirmed?: boolean}; coverage_sufficient: boolean; articles: {article_id: string; title: string; temporal_status: string; first_verified_availability?: string; year?: number}[] }
  relations: {id: string; article_ids: string[]; kind: string; statement: string; conditions: string; uncertainties: string[]; origin: string}[]
  figures: ScientificFigure[]
  review: { integrity_passed?: boolean; scientific_assessment_current?: boolean; issues?: {code: string; message: string; section_id?: string}[]; scientific?: {conclusion: string; reasoning: string; all_current_text_examined?: boolean; issues: {statement: string; severity: string}[]} }
  versions: {id: string; revision: number; created_at: string; approval?: {origin: string}}[]
  articles: Article[]
  evidence: Record<string, {evidence_id: string; article_id: string; exact_quote: string; source_url: string; source_version?: string; version_status?: string; page_number?: number; printed_page?: string; section?: string; table?: {number?: string; unit?: string; dataset?: string; headers?: string[]; row?: string[]}}>
}
export const getDocument = (sid: string, signal?: AbortSignal) => scholarV4Request<ScientificDocument>(`/sessions/${encodeURIComponent(sid)}/document`, {signal})
export const changeDocument = (sid: string, payload: Record<string, unknown>) => scholarV4Request<Session>(`/sessions/${encodeURIComponent(sid)}/document`, json(payload))
export const getFigure = (sid: string, fid: string) => scholarV4Blob(`/sessions/${encodeURIComponent(sid)}/document/figures/${encodeURIComponent(fid)}`)
export const exportDocument = (sid: string, version: string, format: "docx" | "pdf") => scholarV4Blob(`/sessions/${encodeURIComponent(sid)}/document/export/${format}?version_id=${encodeURIComponent(version)}`)
export function safeUrl(value: string) {
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password && !value.includes("cookies_not_supported") ? url.href : "" } catch { return "" }
}
