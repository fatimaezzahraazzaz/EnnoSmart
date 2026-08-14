"use client"

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

const ACCESS_TOKEN_KEY = "ennosmart_access_token"
const REFRESH_TOKEN_KEY = "ennosmart_refresh_token"
let refreshPromise: Promise<string | null> | null = null
const readCache = new Map<string, { expiresAt: number; value: unknown }>()
const pendingReads = new Map<string, Promise<unknown>>()

function clearReadCache(prefix?: string) {
  if (!prefix) {
    readCache.clear()
    pendingReads.clear()
    return
  }
  for (const key of readCache.keys()) {
    if (key.startsWith(prefix)) readCache.delete(key)
  }
}

async function cachedRead<T>(key: string, ttlMs: number, loader: () => Promise<T>): Promise<T> {
  const cached = readCache.get(key)
  if (cached && cached.expiresAt > Date.now()) return cached.value as T

  const pending = pendingReads.get(key)
  if (pending) return pending as Promise<T>

  const request = loader()
  pendingReads.set(key, request)
  try {
    const value = await request
    readCache.set(key, { expiresAt: Date.now() + ttlMs, value })
    return value
  } finally {
    pendingReads.delete(key)
  }
}

export type UserRead = {
  id: number
  full_name: string
  email: string
  role: string
  is_active: boolean
  created_at: string
}

export type TokenResponse = {
  access_token: string
  refresh_token: string
  token_type: string
}

export type LoginRequest = {
  email: string
  password: string
}

export type RegisterRequest = {
  full_name: string
  email: string
  password: string
  company?: string
  job_title?: string
}

export type UserPreferences = {
  language: string
  timezone: string
  theme: "light" | "dark" | "system"
  compact_sidebar: boolean
  email_notifications: boolean
  project_notifications: boolean
  weekly_summary: boolean
  updated_at?: string | null
}

export type AccountRead = {
  user: UserRead
  profile: {
    job_title: string | null
    company: string | null
    phone: string | null
    bio: string | null
    avatar_url: string | null
    updated_at?: string | null
  }
  preferences: UserPreferences
}

export type AdminOverview = {
  users: { total: number; active: number; consultants: number; admins: number }
  projects: { total: number; completed: number; unassigned: number; by_stage: Record<string, number> }
  generated_at: string
}

export type AdminUser = {
  id: number
  full_name: string
  email: string
  role: "consultant" | "admin" | "superadmin"
  is_active: boolean
  created_at: string
  company: string | null
  job_title: string | null
  project_count: number
}

export type AdminProject = {
  id: number
  organisme: string
  project_name: string
  year: string
  domain_label: string | null
  status: string
  created_at: string
  consultant: { id: number; full_name: string; email: string } | null
  workflow: {
    stage: string
    progress_percent: number
    priority: string
    due_date: string | null
    notes: string | null
    updated_at: string | null
  }
  counts: { documents: number; diagnostics: number; scholar_runs: number }
}

export type AIModelSettings = {
  provider: "openai" | "ollama" | "openrouter" | "gemini"
  primary_model: string
  writer_model: string | null
  fallback_models: string[]
  allow_cross_provider_fallback: boolean
  default_temperature: number
  max_output_tokens_cap: number
  max_prompt_chars: number
  writer_max_prompt_chars: number
  monthly_budget_eur: number
  enabled_agents: Record<string, boolean>
  runtime_config?: string
  applied?: boolean
}

export type ProjectRead = {
  id: number
  consultant_id: number
  organisme: string
  project_name: string
  year: string
  domain_label: string | null
  status: string
  ai_folder: string | null
  created_at: string
}

export type ProjectOverview = {
  project: ProjectRead
  documents: { count: number; latest_at: string | null }
  diagnostic: {
    available: boolean
    latest_run: { id: number; status: string; created_at: string; completed_at: string | null } | null
    verrous: {
      count: number
      pending: number
      pertinent: number
      moyen: number
      average_score: number | null
      latest_at: string | null
    }
  }
  scholar: {
    available: boolean
    latest_run: { id: number; status: string; created_at: string; completed_at: string | null } | null
    articles: {
      count: number
      pending: number
      useful: number
      direct: number
      fondamental: number
      connexe: number
      hors_sujet: number
      latest_at: string | null
    }
  }
}

export type DocumentRead = {
  id: number
  project_id: number
  filename: string
  stored_filename: string
  file_path: string
  content_type: string | null
  file_size: number
  document_type: string | null
  upload_status: string
  created_at: string
  original_filename?: string | null
  storage_path?: string | null
  mime_type?: string | null
  size_bytes?: number | null
}

export type VerrouRead = {
  id: number
  diagnostic_run_id: number
  title: string
  tag_cir: string | null
  score: number | null
  consultant_status: string
  justification: string | null
  source_json: any
  created_at: string
}

export type ArticleRead = {
  id: number
  scholar_run_id: number
  verrou_id: number | null
  title: string
  year: number | null
  source: string | null
  tag_article: string | null
  score: number | null
  url: string | null
  doi: string | null
  consultant_status: string
  source_json: any
  evidence_status?: string | null
  evidence_label?: string | null
  evidence_usable?: boolean | null
  fulltext_ready?: boolean | null
  candidate_only?: boolean | null
  access_check_status?: string | null
  evidence_reason_code?: string | null
  evidence_reason_detail?: string | null
  evidence_recommended_action?: string | null
  evidence_access_kind?: string | null
  created_at: string
}

export function getAccessToken() {
  if (typeof window === "undefined") return null
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

function getRefreshToken() {
  if (typeof window === "undefined") return null
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function setTokens(tokens: TokenResponse) {
  clearReadCache()
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
  localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token)
}

export function clearTokens() {
  clearReadCache()
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
}

async function renewAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise

  refreshPromise = (async () => {
    const refreshToken = getRefreshToken()
    if (!refreshToken) return null

    try {
      const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      })

      if (!response.ok) {
        clearTokens()
        return null
      }

      const tokens = (await response.json()) as TokenResponse
      if (!tokens.access_token || !tokens.refresh_token) {
        clearTokens()
        return null
      }

      setTokens(tokens)
      return tokens.access_token
    } catch {
      return null
    }
  })()

  try {
    return await refreshPromise
  } finally {
    refreshPromise = null
  }
}

async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  requireAuth = true
): Promise<T> {
  const headers = new Headers(options.headers)

  if (!headers.has("Content-Type") && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json")
  }

  if (requireAuth) {
    const token = getAccessToken()

    if (!token) {
      throw new Error("Utilisateur non authentifié.")
    }

    headers.set("Authorization", `Bearer ${token}`)
  }

  let response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })

  if (requireAuth && response.status === 401) {
    const renewedAccessToken = await renewAccessToken()

    if (renewedAccessToken) {
      headers.set("Authorization", `Bearer ${renewedAccessToken}`)
      response = await fetch(`${API_BASE_URL}${path}`, {
        ...options,
        headers,
      })
    }
  }

  let data: any = null

  try {
    data = await response.json()
  } catch {
    data = null
  }

  if (!response.ok) {
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : Array.isArray(data?.detail)
          ? data.detail.map((item: any) => item.msg || item.type).join(" | ")
          : "Erreur API."

    throw new Error(detail)
  }

  return data as T
}

async function apiBlobRequest(path: string): Promise<Blob> {
  const headers = new Headers()
  const token = getAccessToken()
  if (!token) throw new Error("Utilisateur non authentifié.")
  headers.set("Authorization", `Bearer ${token}`)

  let response = await fetch(`${API_BASE_URL}${path}`, { headers })
  if (response.status === 401) {
    const renewedAccessToken = await renewAccessToken()
    if (renewedAccessToken) {
      headers.set("Authorization", `Bearer ${renewedAccessToken}`)
      response = await fetch(`${API_BASE_URL}${path}`, { headers })
    }
  }
  if (!response.ok) {
    let detail = "Impossible d’ouvrir le PDF importé."
    try {
      const payload = await response.json()
      if (typeof payload?.detail === "string") detail = payload.detail
    } catch {
      // La réponse peut être binaire ou vide.
    }
    throw new Error(detail)
  }
  return response.blob()
}

export async function login(payload: LoginRequest) {
  const tokens = await apiRequest<TokenResponse>(
    "/auth/login",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    false
  )

  setTokens(tokens)
  return tokens
}

export async function register(payload: RegisterRequest) {
  return apiRequest<UserRead>(
    "/auth/register",
    { method: "POST", body: JSON.stringify(payload) },
    false,
  )
}

export async function forgotPassword(email: string) {
  return apiRequest<{
    status: string
    message: string
    email_sent?: boolean
    preview_token?: string
    reset_url?: string
  }>(
    "/auth/forgot-password",
    { method: "POST", body: JSON.stringify({ email }) },
    false,
  )
}

export async function resetPassword(token: string, password: string) {
  return apiRequest<{ status: string; message: string }>(
    "/auth/reset-password",
    { method: "POST", body: JSON.stringify({ token, password }) },
    false,
  )
}

export async function getMe() {
  return apiRequest<UserRead>("/auth/me")
}

export async function getAccount() {
  return apiRequest<AccountRead>("/auth/me/account")
}

export async function updateProfile(payload: Partial<AccountRead["profile"]> & {
  full_name?: string
  email?: string
}) {
  return apiRequest<AccountRead>("/auth/me/profile", {
    method: "PATCH",
    body: JSON.stringify(payload),
  })
}

export async function updatePreferences(payload: UserPreferences) {
  return apiRequest<AccountRead>("/auth/me/preferences", {
    method: "PUT",
    body: JSON.stringify(payload),
  })
}

export async function changePassword(currentPassword: string, newPassword: string) {
  return apiRequest<{ status: string; message: string }>("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
}

export async function getAdminOverview() {
  return apiRequest<AdminOverview>("/admin/overview")
}

export async function getAdminUsers() {
  return apiRequest<AdminUser[]>("/admin/users")
}

export async function createAdminUser(payload: {
  full_name: string
  email: string
  password: string
  role: "consultant" | "admin" | "superadmin"
  company?: string
  job_title?: string
}) {
  return apiRequest<AdminUser>("/admin/users", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export async function updateAdminUser(
  userId: number,
  payload: { full_name?: string; role?: AdminUser["role"]; is_active?: boolean },
) {
  return apiRequest<AdminUser>(`/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  })
}

export async function getAdminProjects() {
  return apiRequest<AdminProject[]>("/admin/projects")
}

export async function assignAdminProject(projectId: number, consultantId: number) {
  return apiRequest<AdminProject>(`/admin/projects/${projectId}/assignment`, {
    method: "PATCH",
    body: JSON.stringify({ consultant_id: consultantId }),
  })
}

export async function updateAdminProjectWorkflow(
  projectId: number,
  payload: {
    stage: string
    progress_percent: number
    priority: string
    due_date?: string | null
    notes?: string | null
  },
) {
  return apiRequest<AdminProject>(`/admin/projects/${projectId}/workflow`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  })
}

export async function getAISettings() {
  return apiRequest<AIModelSettings>("/admin/ai-settings")
}

export async function updateAISettings(payload: AIModelSettings) {
  return apiRequest<AIModelSettings>("/admin/ai-settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  })
}

export async function getAdminAuditLog() {
  return apiRequest<Array<{
    id: number
    actor_user_id: number | null
    action: string
    entity_type: string
    entity_id: string | null
    metadata: Record<string, unknown> | null
    created_at: string
  }>>("/admin/audit-log")
}

export async function getProjects() {
  return cachedRead("projects", 10_000, () => apiRequest<ProjectRead[]>("/projects"))
}

export async function getProjectOverviews() {
  return cachedRead("project-overviews", 10_000, () =>
    apiRequest<ProjectOverview[]>("/projects/overview"),
  )
}

export async function getProject(projectId: number) {
  return apiRequest<ProjectRead>(`/projects/${projectId}`)
}

export async function createProject(payload: {
  organisme: string
  project_name: string
  year: string
  domain_label?: string
}) {
  const project = await apiRequest<ProjectRead>("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  })
  clearReadCache("project")
  return project
}

export async function getDocuments(projectId: number) {
  return cachedRead(`documents:${projectId}`, 10_000, () =>
    apiRequest<DocumentRead[]>(`/projects/${projectId}/documents`),
  )
}

export async function uploadDocument(
  projectId: number,
  file: File,
  documentType?: string
) {
  const formData = new FormData()
  formData.append("file", file)

  const query = documentType
    ? `?document_type=${encodeURIComponent(documentType)}`
    : ""

  const document = await apiRequest<DocumentRead>(
    `/projects/${projectId}/documents/upload${query}`,
    {
      method: "POST",
      body: formData,
    }
  )
  clearReadCache(`documents:${projectId}`)
  clearReadCache("project-overviews")
  return document
}

export async function importExistingDocuments(projectId: number) {
  const documents = await apiRequest<DocumentRead[]>(
    `/projects/${projectId}/documents/import-existing`,
    {
      method: "POST",
    }
  )
  clearReadCache(`documents:${projectId}`)
  clearReadCache("project-overviews")
  return documents
}

export async function getVerrous(projectId: number) {
  return apiRequest<VerrouRead[]>(`/projects/${projectId}/verrous`)
}

export async function updateVerrouDecision(
  projectId: number,
  verrouId: number,
  consultant_status: "garde" | "rejete" | "reformuler" | "en_attente"
) {
  const verrou = await apiRequest<VerrouRead>(
    `/projects/${projectId}/verrous/${verrouId}/decision`,
    {
      method: "PATCH",
      body: JSON.stringify({ consultant_status }),
    }
  )
  clearReadCache("project-overviews")
  return verrou
}

export async function getArticles(projectId: number, compact = false) {
  const query = compact ? "?compact=true" : ""
  return apiRequest<ArticleRead[]>(`/projects/${projectId}/articles${query}`)
}

export async function updateArticleDecision(
  projectId: number,
  articleId: number,
  consultant_status: "garde" | "rejete" | "en_attente"
) {
  const article = await apiRequest<ArticleRead>(
    `/projects/${projectId}/articles/${articleId}/decision`,
    {
      method: "PATCH",
      body: JSON.stringify({ consultant_status }),
    }
  )
  clearReadCache("project-overviews")
  return article
}

/**
 * Traduction FR de l'abstract à la demande.
 */
export async function translateArticleAbstract(
  projectId: number,
  articleId: number,
  force = false
) {
  const query = force ? "?force=true" : ""

  return apiRequest<ArticleRead>(
    `/projects/${projectId}/articles/${articleId}/translate-abstract${query}`,
    {
      method: "POST",
    }
  )
}

export async function getDiagnosticLatest(projectId: number, compact = true) {
  const query = compact ? "?compact=true" : ""
  return apiRequest<any>(`/projects/${projectId}/diagnostic/latest${query}`)
}

export async function compareCurrentWithPreviousCir(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/cir-previous/compare-current`, {
    method: "POST",
  })
}

export async function getPreviousCirComparisonLatest(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/cir-previous/comparison-latest`)
}

export async function importExistingDiagnostic(projectId: number) {
  const result = await apiRequest<any>(`/projects/${projectId}/diagnostic/import-existing`, {
    method: "POST",
  })
  clearReadCache("project-overviews")
  return result
}

export async function runDiagnostic(projectId: number) {
  const result = await apiRequest<any>(`/projects/${projectId}/diagnostic/run`, {
    method: "POST",
  })
  clearReadCache("project-overviews")
  return result
}

export async function syncVerrous(projectId: number, runId: number) {
  const result = await apiRequest<any>(
    `/projects/${projectId}/diagnostic/${runId}/sync-verrous`,
    {
      method: "POST",
    }
  )
  clearReadCache("project-overviews")
  return result
}

export async function getScholarLatest(projectId: number, compact = true) {
  const query = compact ? "?compact=true" : ""
  return apiRequest<any>(`/projects/${projectId}/scholar/latest${query}`)
}


// ============================================================
// EnnoScholar — State of Art V9 Unified Phase 5 / LLM
// ============================================================

export type MethodEvidenceChain = {
  citation_label?: string
  citation?: string
  concept?: string
  scientific_problem?: string
  why_this_method_exists?: string
  mechanism?: string
  training_pipeline?: string
  evaluation_protocol?: string
  experimental_results?: string
  remaining_limitations?: string
  transition_to_next_method?: string
  usage_type?: string
  priority_score?: number
}

export type StateOfArtVerrouView = {
  index?: number
  ok?: boolean
  verrou_id?: string
  verrou_title?: string
  required_citations?: string[]
  detected_citations?: string[]
  missing_citations?: string[]
  required_count?: number
  detected_count?: number
  draft_title?: string
  sections?: Record<string, any>
  method_evidence_chains?: MethodEvidenceChain[]
  method_evidence_chains_count?: number
  citations_used?: string[]
  references_utilisees?: any[]
  guard?: any
  polish?: any
  phase47_blueprint_used?: boolean
  methods_from_phase_4_5?: any[]
}

export type StateOfArtSummary = {
  phase5_ok?: boolean
  status?: string
  payload_type?: string
  markdown_chars?: number
  article_cards_count?: number
  raw_evidence_units_count?: number
  selected_main_citations_count?: number
  coverage_required_count?: number
  citations_detected_count?: number
  missing_required_citations_count?: number
  unknown_citations_count?: number
  verrous_written?: number
  verrou_sections_count?: number
  verrou_coverage_ok?: boolean
  strict_ok?: boolean
  single_unified_state_of_art?: boolean
  llm_used?: boolean
  llm_used_in_final?: boolean
  llm_generated?: boolean
  llm_reason?: string
  llm_provider?: string
  llm_model?: string
  final_source?: string
  accepted_sections?: number
  rejected_sections?: number
  anti_copy_guard_enabled?: boolean
  too_mechanical?: boolean
  too_repetitive?: boolean
  quality_score?: number
  forbidden_counts?: Record<string, number>
  [key: string]: any
}

export type StateOfArtView = {
  ok?: boolean
  payload_type?: string
  generated_at?: string
  project?: any
  summary?: StateOfArtSummary
  stats?: any
  guard?: any
  polish?: any
  llm?: any
  verrous?: StateOfArtVerrouView[]
  citations?: {
    required?: string[]
    detected?: string[]
    missing?: string[]
    unknown?: string[]
  }
  quality?: any
  markdown?: string
  paths?: Record<string, string>
  output_files?: any[]
  raw_payload?: any
}

export type StateOfArtLatestResponse = {
  ok: boolean
  report?: StateOfArtView
  state_of_art_view?: StateOfArtView
  markdown?: string
  payload?: any
  paths?: Record<string, string>
}

/**
 * Le frontend ne choisit jamais le fournisseur ni le modèle LLM.
 * Le routage est centralisé côté backend dans modules/LLM/llm_client.py
 * à partir de C:/EnnoSmart/.env et du request_name.
 */
export type StateOfArtGenerationOptions = {
  forcePhase3?: boolean
  forceArticleCards?: boolean
  enablePolish?: boolean | null
  enableNormalization?: boolean | null
  fastMode?: boolean
  guidedSessionId?: string | null
}

function buildStateOfArtQuery(options: StateOfArtGenerationOptions = {}) {
  const params = new URLSearchParams()

  if (options.forcePhase3 !== undefined) params.set("force_phase3", options.forcePhase3 ? "true" : "false")
  if (options.forceArticleCards !== undefined) params.set("force_article_cards", options.forceArticleCards ? "true" : "false")
  if (options.enablePolish !== undefined && options.enablePolish !== null) params.set("enable_polish", options.enablePolish ? "true" : "false")
  if (options.enableNormalization !== undefined && options.enableNormalization !== null) params.set("enable_normalization", options.enableNormalization ? "true" : "false")
  if (options.fastMode !== undefined) params.set("fast_mode", options.fastMode ? "true" : "false")
  if (options.guidedSessionId) params.set("guided_session_id", options.guidedSessionId)

  const qs = params.toString()
  return qs ? `?${qs}` : ""
}

export async function getLatestStateOfArt(projectId: number): Promise<StateOfArtLatestResponse> {
  return apiRequest<StateOfArtLatestResponse>(`/projects/${projectId}/scholar/state-of-art/latest`)
}

export async function getStateOfArtHistory(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/state-of-art/history`)
}

export async function getStateOfArtVisualBlob(
  projectId: number,
  visualId: string,
) {
  return apiBlobRequest(
    `/projects/${projectId}/scholar/state-of-art/visuals/${encodeURIComponent(visualId)}`,
  )
}

/**
 * Génération finale de l'état de l'art après sélection consultant.
 * Retourne : markdown final + stats citations/verrous + infos LLM + chemins.
 */
export async function generateStateOfArt(
  projectId: number,
  options: StateOfArtGenerationOptions = {},
) {
  return apiRequest<any>(
    `/projects/${projectId}/scholar/state-of-art/generate${buildStateOfArtQuery(options)}`,
    { method: "POST" },
  )
}

export async function runFullStateOfArt(
  projectId: number,
  options: StateOfArtGenerationOptions = {},
) {
  return apiRequest<any>(
    `/projects/${projectId}/scholar/state-of-art/run-full${buildStateOfArtQuery(options)}`,
    { method: "POST" },
  )
}


/**
 * Prévisualisation / construction du payload de sélection état de l'art.
 * Correspond à la Phase 1 de préparation rédaction : sélection consultant -> selection_payload.json.
 */
export async function getStateOfArtSelectionPreview(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/state-of-art/selection-preview`)
}

/**
 * Construction des Article Cards à partir des articles sélectionnés.
 * Correspond à la Phase 2 de préparation rédaction.
 */
export async function buildScholarArticleCards(
  projectId: number,
  mode = "auto",
  force = false
) {
  return apiRequest<any>(
    `/projects/${projectId}/scholar/state-of-art/article-cards/build?mode=${encodeURIComponent(mode)}&force=${force ? "true" : "false"}`,
    { method: "POST" }
  )
}

export async function getScholarArticleCards(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/state-of-art/article-cards`)
}

export async function getScholarFulltextStatus(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/fulltext/status`)
}

export async function fetchScholarSelectedFulltext(
  projectId: number,
  force = false,
  maxArticles?: number | null
) {
  const params = new URLSearchParams()
  params.set("force", force ? "true" : "false")

  // Règle métier : undefined/null/0 = traiter tous les articles sélectionnés.
  // On n'envoie max_articles que si l'utilisateur veut volontairement limiter.
  if (typeof maxArticles === "number" && maxArticles > 0) {
    params.set("max_articles", String(maxArticles))
  }

  return apiRequest<any>(
    `/projects/${projectId}/scholar/fulltext/fetch-selected?${params.toString()}`,
    { method: "POST" }
  )
}

export async function getScholarDirectExtractStatus(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/fulltext/direct-extract-status`)
}

export async function extractScholarArticleFulltextOnDemand(
  projectId: number,
  articleId: number,
) {
  return apiRequest<ArticleRead>(
    `/projects/${projectId}/scholar/articles/${articleId}/fulltext/extract-on-demand`,
    { method: "POST" },
  )
}

export async function extractScholarSelectedFulltextDirect(
  projectId: number,
  force = false,
  maxArticles?: number | null
) {
  const params = new URLSearchParams()
  params.set("force", force ? "true" : "false")

  // Règle métier : undefined/null/0 = traiter tous les articles sélectionnés.
  if (typeof maxArticles === "number" && maxArticles > 0) {
    params.set("max_articles", String(maxArticles))
  }

  return apiRequest<any>(
    `/projects/${projectId}/scholar/fulltext/extract-direct-selected?${params.toString()}`,
    { method: "POST" }
  )
}

export async function recoverScholarSelectedFulltextLegally(
  projectId: number,
  forceRefresh = false,
  maxArticles?: number | null,
) {
  const params = new URLSearchParams()
  params.set("force_refresh", forceRefresh ? "true" : "false")
  // Arrêt dès la première copie légale vérifiée ; les autres fournisseurs
  // restent des fallbacks si les premiers ne trouvent rien.
  params.set("search_all", "false")

  if (typeof maxArticles === "number" && maxArticles > 0) {
    params.set("max_articles", String(maxArticles))
  }

  return apiRequest<any>(
    `/projects/${projectId}/scholar/fulltext/recover-legal-problems?${params.toString()}`,
    { method: "POST" },
  )
}

export async function importExistingScholar(projectId: number) {
  const result = await apiRequest<any>(`/projects/${projectId}/scholar/import-existing`, {
    method: "POST",
  })
  clearReadCache("project-overviews")
  return result
}

export function logout() {
  clearTokens()
}

export async function uploadAndExtractArticlePdf(
  projectId: number,
  articleId: number,
  file: File,
  sourceUrl?: string | null
) {
  const formData = new FormData()
  formData.append("file", file)

  if (sourceUrl) {
    formData.append("source_url", sourceUrl)
  }

  return apiRequest<any>(
    `/projects/${projectId}/scholar/articles/${articleId}/fulltext/upload-and-extract`,
    {
      method: "POST",
      body: formData,
    }
  )
}

export async function uploadNewScholarSource(
  projectId: number,
  file: File,
  guidedSessionId?: string | null,
) {
  const formData = new FormData()
  formData.append("file", file)
  if (guidedSessionId) formData.append("guided_session_id", guidedSessionId)
  return apiRequest<{
    ok: boolean
    article: ArticleRead
    extraction: Record<string, any>
    phase_1: Record<string, any>
    phase_2: Record<string, any>
  }>(
    `/projects/${projectId}/scholar/articles/upload-source`,
    {
      method: "POST",
      body: formData,
    },
  )
}

export async function getUploadedScholarArticlePdf(
  projectId: number,
  articleId: number,
) {
  return apiBlobRequest(
    `/projects/${projectId}/scholar/articles/${articleId}/uploaded-pdf`,
  )
}
// ============================================================
// EnnoScholar — Préparation complète état de l'art
// Phase 1 -> accès/extraction directs -> récupération légale MCP
// -> Phase 2D Article Cards -> Refresh
// IMPORTANT : ce bloc reste dans lib/api.ts et n'importe JAMAIS depuis '@/lib/api'.
// ============================================================

export type PrepareStepKey =
  | "phase1_selection_payload"
  | "phase2a_fulltext_resolve"
  | "phase2b_direct_extract"
  | "phase2d_article_cards"
  | "refresh_status"

export type PrepareStepStatus = "pending" | "running" | "done" | "error"

export type PrepareProgressEvent = {
  key: PrepareStepKey
  status: PrepareStepStatus
  detail?: string
  data?: any
}

export type PrepareStateOfArtOptions = {
  force?: boolean
  maxArticles?: number | null
  articleCardMode?: "auto" | "llm" | "template" | string
  onProgress?: (event: PrepareProgressEvent) => void
}

export type PrepareStateOfArtResult = {
  ok: boolean
  steps: Record<PrepareStepKey, PrepareProgressEvent>
  finalStatus: {
    selectionPreview: any
    fulltextStatus: any
    directExtractStatus: any
    articleCards: any
  }
}

const PREPARE_STEP_KEYS: PrepareStepKey[] = [
  "phase1_selection_payload",
  "phase2a_fulltext_resolve",
  "phase2b_direct_extract",
  "phase2d_article_cards",
  "refresh_status",
]

function makePrepareInitialSteps(): Record<PrepareStepKey, PrepareProgressEvent> {
  return PREPARE_STEP_KEYS.reduce((acc, key) => {
    acc[key] = { key, status: "pending" }
    return acc
  }, {} as Record<PrepareStepKey, PrepareProgressEvent>)
}

function normalizePrepareError(error: any): string {
  return error?.message || error?.detail || String(error || "Erreur inconnue")
}

async function runPrepareStep<T>(
  steps: Record<PrepareStepKey, PrepareProgressEvent>,
  key: PrepareStepKey,
  detail: string,
  onProgress: ((event: PrepareProgressEvent) => void) | undefined,
  action: () => Promise<T>,
): Promise<T> {
  const running: PrepareProgressEvent = { key, status: "running", detail }
  steps[key] = running
  onProgress?.(running)

  try {
    const data = await action()
    const done: PrepareProgressEvent = { key, status: "done", detail: "OK", data }
    steps[key] = done
    onProgress?.(done)
    return data
  } catch (error: any) {
    const failed: PrepareProgressEvent = {
      key,
      status: "error",
      detail: normalizePrepareError(error),
      data: error,
    }
    steps[key] = failed
    onProgress?.(failed)
    throw error
  }
}

export async function uploadScholarArticlePdf(
  projectId: number,
  articleId: number,
  file: File,
  sourceUrl?: string,
): Promise<any> {
  return uploadAndExtractArticlePdf(projectId, articleId, file, sourceUrl || null)
}

export async function prepareStateOfArtPhase1And2(
  projectId: number,
  options: PrepareStateOfArtOptions = {},
): Promise<PrepareStateOfArtResult> {
  const force = options.force ?? false
  const articleCardMode = options.articleCardMode || "auto"
  const onProgress = options.onProgress
  const steps = makePrepareInitialSteps()

  await runPrepareStep(
    steps,
    "phase1_selection_payload",
    "Construction du payload de sélection consultant.",
    onProgress,
    () => getStateOfArtSelectionPreview(projectId),
  )

  await runPrepareStep(
    steps,
    "phase2a_fulltext_resolve",
    "Lecture des extractions déjà terminées pendant EnnoScholar.",
    onProgress,
    () => getScholarDirectExtractStatus(projectId),
  )

  await runPrepareStep(
    steps,
    "phase2b_direct_extract",
    "Lecture des statuts MCP déjà qualifiés pendant EnnoScholar.",
    onProgress,
    () => getScholarFulltextStatus(projectId),
  )

  await runPrepareStep(
    steps,
    "phase2d_article_cards",
    "Construction des Article Cards à partir des textes disponibles.",
    onProgress,
    () => buildScholarArticleCards(projectId, articleCardMode, force),
  )

  const finalStatus = await runPrepareStep(
    steps,
    "refresh_status",
    "Relecture des statuts finaux.",
    onProgress,
    async () => {
      const [
        selectionPreview,
        fulltextStatus,
        directExtractStatus,
        articleCards,
      ] = await Promise.all([
        getStateOfArtSelectionPreview(projectId),
        getScholarFulltextStatus(projectId),
        getScholarDirectExtractStatus(projectId),
        getScholarArticleCards(projectId),
      ])

      return {
        selectionPreview,
        fulltextStatus,
        directExtractStatus,
        articleCards,
      }
    },
  )

  return {
    ok: true,
    steps,
    finalStatus,
  }
}

// ============================================================
// EnnoScholar — chat du plan consultant
// ============================================================
export type GuidedResearchConversationTurn = {
  message_id?: string
  role: "consultant" | "assistant" | "system" | string
  content: string
  intent?: string | null
  metadata?: Record<string, any>
  created_at?: string
}

export type GuidedResearchSession = {
  session_id: string
  project_id: number
  state: string
  ready_to_write: boolean
  messages: GuidedResearchConversationTurn[]
  context?: Record<string, any>
  brief?: Record<string, any> | null
  title?: string
  preview?: string
  message_count?: number
  created_at?: string
  updated_at?: string
}

export async function listGuidedResearchSessions(projectId: number) {
  return apiRequest<{ ok: boolean; sessions: GuidedResearchSession[] }>(
    `/api/projects/${projectId}/guided-research/sessions`,
  )
}

export async function createGuidedResearchSession(projectId: number) {
  return apiRequest<{ ok: boolean; session: GuidedResearchSession }>(
    `/api/projects/${projectId}/guided-research/sessions`,
    {
      method: "POST",
      body: JSON.stringify({ target_mode: "global", entry_module: "ennoscholar" }),
    },
  )
}

export async function deleteGuidedResearchSession(
  projectId: number,
  sessionId: string,
) {
  return apiRequest<{ ok: boolean; session_id: string }>(
    `/api/projects/${projectId}/guided-research/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  )
}

export async function getGuidedResearchSession(projectId: number, sessionId: string) {
  return apiRequest<{
    ok: boolean
    session: GuidedResearchSession
    artifacts?: Record<string, any>
  }>(`/api/projects/${projectId}/guided-research/sessions/${encodeURIComponent(sessionId)}`)
}

export async function sendGuidedResearchMessage(
  projectId: number,
  sessionId: string,
  message: string,
) {
  return apiRequest<any>(
    `/api/projects/${projectId}/guided-research/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: "POST",
      body: JSON.stringify({ message }),
    },
  )
}

// ============================================================
// EnnoAmelioration — conversations et versions contrôlées
// ============================================================
export type ImprovementMessage = {
  message_id: string
  role: "consultant" | "assistant" | string
  content: string
  intent?: string | null
  metadata?: Record<string, any>
  created_at?: string | null
}

export type ImprovementSection = {
  section_id: string
  title: string
  level: number
  start: number
  end: number
  content: string
}

export type ImprovementVersion = {
  version_id: string
  version_number: number
  status: "original" | "candidate" | "accepted" | "rejected" | "superseded" | string
  content: string
  parent_version_id?: string | null
  instruction?: string | null
  diff?: {
    unified?: string
    changes?: Array<{ operation: string; before: string; after: string }>
    similarity?: number
    before_chars?: number
    after_chars?: number
  }
  audit?: { findings?: Array<Record<string, any>> }
  evidence?: Record<string, any>
  generation?: Record<string, any>
  created_at?: string | null
  decided_at?: string | null
  is_active?: boolean
}

export type ImprovementProjectContext = {
  project: {
    id: number
    organisme: string
    project_name: string
    year: string
    domain_label?: string | null
    status: string
  }
  documents: { available: boolean; count: number }
  diagnostic: { available: boolean; latest_run_id?: number | null; status?: string | null }
  scholar: {
    available: boolean
    latest_run_id?: number | null
    status?: string | null
    accepted_article_count: number
  }
  cir_memory: {
    available: boolean
    source_memory_available: boolean
    policy: string
  }
  last_improvement?: {
    session_id: string
    title: string
    state: string
    updated_at?: string | null
  } | null
}

export type ImprovementSession = {
  session_id: string
  project_id: number
  title: string
  state: string
  target_scope: string
  target_section_id?: string | null
  target_section_title?: string | null
  source_document_id?: number | null
  active_version_id?: string | null
  active_version_number?: number | null
  candidate_count: number
  message_count: number
  preview?: string
  context?: { sections?: ImprovementSection[]; [key: string]: any }
  messages?: ImprovementMessage[]
  versions?: ImprovementVersion[]
  created_at?: string | null
  updated_at?: string | null
}

export async function getImprovementProjectContext(projectId: number) {
  return apiRequest<{ ok: boolean; context: ImprovementProjectContext }>(
    `/api/projects/${projectId}/improvements/context`,
  )
}

export async function listImprovementSessions(projectId: number) {
  return apiRequest<{ ok: boolean; sessions: ImprovementSession[] }>(
    `/api/projects/${projectId}/improvements/sessions`,
  )
}

export async function createImprovementSession(
  projectId: number,
  payload: {
    title?: string
    source_text?: string
    source_document_id?: number
    target_scope?: "selection" | "paragraph" | "section" | "multi_section" | "full_document"
    target_section_id?: string
    target_section_title?: string
  },
) {
  return apiRequest<{ ok: boolean; session: ImprovementSession }>(
    `/api/projects/${projectId}/improvements/sessions`,
    { method: "POST", body: JSON.stringify(payload) },
  )
}

export async function getImprovementSession(projectId: number, sessionId: string) {
  return apiRequest<{ ok: boolean; session: ImprovementSession }>(
    `/api/projects/${projectId}/improvements/sessions/${encodeURIComponent(sessionId)}`,
  )
}

export async function getImprovementSourceDocument(
  projectId: number,
  documentId: number,
) {
  const token = getAccessToken()
  const response = await fetch(
    `${API_BASE_URL}/projects/${projectId}/source-documents/${documentId}/open`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  )
  if (!response.ok) {
    const detail = await response.text().catch(() => "")
    throw new Error(detail || `Impossible d'ouvrir le document source (HTTP ${response.status}).`)
  }
  return response.blob()
}

export async function deleteImprovementSession(projectId: number, sessionId: string) {
  return apiRequest<{ ok: boolean; session_id: string }>(
    `/api/projects/${projectId}/improvements/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  )
}

export async function sendImprovementMessage(
  projectId: number,
  sessionId: string,
  payload: {
    message: string
    selected_text?: string
    target_scope?: "selection" | "paragraph" | "section" | "multi_section" | "full_document"
    target_section_id?: string
    target_section_title?: string
  },
) {
  return apiRequest<{
    ok: boolean
    session: ImprovementSession
    candidate_version_id?: string | null
  }>(
    `/api/projects/${projectId}/improvements/sessions/${encodeURIComponent(sessionId)}/messages`,
    { method: "POST", body: JSON.stringify(payload) },
  )
}

export async function decideImprovementVersion(
  projectId: number,
  sessionId: string,
  versionId: string,
  decision: "accepted" | "rejected",
  reason = "",
) {
  return apiRequest<{ ok: boolean; session: ImprovementSession }>(
    `/api/projects/${projectId}/improvements/sessions/${encodeURIComponent(sessionId)}/versions/${encodeURIComponent(versionId)}/decision`,
    { method: "POST", body: JSON.stringify({ decision, reason }) },
  )
}

export async function restoreImprovementVersion(
  projectId: number,
  sessionId: string,
  versionId: string,
  reason = "",
) {
  return apiRequest<{ ok: boolean; session: ImprovementSession }>(
    `/api/projects/${projectId}/improvements/sessions/${encodeURIComponent(sessionId)}/versions/${encodeURIComponent(versionId)}/restore`,
    { method: "POST", body: JSON.stringify({ reason }) },
  )
}

export async function decideImprovementSources(
  projectId: number,
  sessionId: string,
  candidateIds: string[],
  decision: "accepted" | "rejected",
  reason = "",
) {
  return apiRequest<{ ok: boolean; session: ImprovementSession }>(
    `/api/projects/${projectId}/improvements/sessions/${encodeURIComponent(sessionId)}/sources/decision`,
    {
      method: "POST",
      body: JSON.stringify({ candidate_ids: candidateIds, decision, reason }),
    },
  )
}

export async function decideGuidedResearchSources(
  projectId: number,
  sessionId: string,
  candidateIds: string[],
  decision: "accepted" | "rejected",
  reason = "",
) {
  return apiRequest<any>(
    `/api/projects/${projectId}/guided-research/sessions/${encodeURIComponent(sessionId)}/sources/decision`,
    {
      method: "POST",
      body: JSON.stringify({
        candidate_ids: candidateIds,
        decision,
        reason,
        prepare_after_acceptance: true,
      }),
    },
  )
}
