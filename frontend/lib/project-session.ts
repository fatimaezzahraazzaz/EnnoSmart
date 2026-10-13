"use client"

const CURRENT_PROJECT_ID_KEY = "ennosmart_current_project_id"
export const CURRENT_PROJECT_CHANGE_EVENT = "ennosmart:project-change"

function announceProjectChange(projectId: number | null) {
  window.dispatchEvent(
    new CustomEvent(CURRENT_PROJECT_CHANGE_EVENT, { detail: { projectId } }),
  )
}

export function setCurrentProjectId(projectId: number) {
  if (typeof window === "undefined") return
  localStorage.setItem(CURRENT_PROJECT_ID_KEY, String(projectId))
  announceProjectChange(projectId)
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
  announceProjectChange(null)
}
