"use client"

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

const ACCESS_TOKEN_KEY = "ennosmart_access_token"
const REFRESH_TOKEN_KEY = "ennosmart_refresh_token"

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

export function setTokens(tokens: TokenResponse) {
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
  localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token)
}

export function clearTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
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

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })

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

export async function getStateOfArtHistory(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/state-of-art/history`)
}

export async function importExistingScholar(projectId: number) {
  return apiRequest<any>(`/projects/${projectId}/scholar/import-existing`, {
    method: "POST",
  })
}

export function logout() {
  clearTokens()
}
