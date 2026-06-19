"use client"

const CURRENT_PROJECT_ID_KEY = "ennosmart_current_project_id"

export function setCurrentProjectId(projectId: number) {
  if (typeof window === "undefined") return
  localStorage.setItem(CURRENT_PROJECT_ID_KEY, String(projectId))
}

export function getCurrentProjectId() {
  if (typeof window === "undefined") return null

  const value = localStorage.getItem(CURRENT_PROJECT_ID_KEY)
  if (!value) return null

  const id = Number(value)
  return Number.isFinite(id) ? id : null
}

export function clearCurrentProjectId() {
  if (typeof window === "undefined") return
  localStorage.removeItem(CURRENT_PROJECT_ID_KEY)
}
