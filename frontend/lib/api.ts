"use client"

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

const ACCESS_TOKEN_KEY = "ennosmart_access_token"
const REFRESH_TOKEN_KEY = "ennosmart_refresh_token"
let refreshPromise: Promise<string | null> | null = null

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
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
  localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token)
}

export function clearTokens() {
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

export async function getMe() {
  return apiRequest<UserRead>("/auth/me")
}

export async function getProjects() {
  return apiRequest<ProjectRead[]>("/projects")
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
  return apiRequest<ProjectRead>("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export async function getDocuments(projectId: number) {
  return apiRequest<DocumentRead[]>(`/projects/${projectId}/documents`)
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

  return apiRequest<DocumentRead>(
    `/projects/${projectId}/documents/upload${query}`,
    {
      method: "POST",
      body: formData,
    }
  )
}

export async function importExistingDocuments(projectId: number) {
  return apiRequest<DocumentRead[]>(
    `/projects/${projectId}/documents/import-existing`,
    {
      method: "POST",
    }
  )
}

export async function getVerrous(projectId: number) {
  return apiRequest<VerrouRead[]>(`/projects/${projectId}/verrous`)
}

export async function updateVerrouDecision(
  projectId: number,
  verrouId: number,
  consultant_status: "garde" | "rejete" | "reformuler" | "en_attente"
) {
  return apiRequest<VerrouRead>(
    `/projects/${projectId}/verrous/${verrouId}/decision`,
    {
      method: "PATCH",
      body: JSON.stringify({ consultant_status }),
    }
  )
}

export async function getArticles(projectId: number) {
  return apiRequest<ArticleRead[]>(`/projects/${projectId}/articles`)
}

export async function updateArticleDecision(
  projectId: number,
  articleId: number,
  consultant_status: "garde" | "rejete" | "en_attente"
) {
  return apiRequest<ArticleRead>(
    `/projects/${projectId}/articles/${articleId}/decision`,
    {
      method: "PATCH",
      body: JSON.stringify({ consultant_status }),
    }
  )
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

export async function getDiagnosticLatest(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/diagnostic/latest`)
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
  return apiRequest<any>(`/projects/${projectId}/diagnostic/import-existing`, {
    method: "POST",
  })
}

export async function runDiagnostic(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/diagnostic/run`, {
    method: "POST",
  })
}

export async function syncVerrous(projectId: number, runId: number) {
  return apiRequest<any>(
    `/projects/${projectId}/diagnostic/${runId}/sync-verrous`,
    {
      method: "POST",
    }
  )
}

export async function getScholarLatest(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/latest`)
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
}

function buildStateOfArtQuery(options: StateOfArtGenerationOptions = {}) {
  const params = new URLSearchParams()

  if (options.forcePhase3 !== undefined) params.set("force_phase3", options.forcePhase3 ? "true" : "false")
  if (options.forceArticleCards !== undefined) params.set("force_article_cards", options.forceArticleCards ? "true" : "false")
  if (options.enablePolish !== undefined && options.enablePolish !== null) params.set("enable_polish", options.enablePolish ? "true" : "false")
  if (options.enableNormalization !== undefined && options.enableNormalization !== null) params.set("enable_normalization", options.enableNormalization ? "true" : "false")
  if (options.fastMode !== undefined) params.set("fast_mode", options.fastMode ? "true" : "false")

  const qs = params.toString()
  return qs ? `?${qs}` : ""
}

export async function getLatestStateOfArt(projectId: number): Promise<StateOfArtLatestResponse> {
  return apiRequest<StateOfArtLatestResponse>(`/projects/${projectId}/scholar/state-of-art/latest`)
}

export async function getStateOfArtHistory(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/state-of-art/history`)
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
  return apiRequest<any>(`/projects/${projectId}/scholar/import-existing`, {
    method: "POST",
  })
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
) {
  const formData = new FormData()
  formData.append("file", file)
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
  const maxArticles = options.maxArticles ?? null
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
    "Accès et extraction depuis les liens déjà connus.",
    onProgress,
    () => fetchScholarSelectedFulltext(projectId, force, maxArticles),
  )

  await runPrepareStep(
    steps,
    "phase2b_direct_extract",
    "Récupération légale MCP des seuls échecs directs.",
    onProgress,
    () => recoverScholarSelectedFulltextLegally(projectId, force, maxArticles),
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
