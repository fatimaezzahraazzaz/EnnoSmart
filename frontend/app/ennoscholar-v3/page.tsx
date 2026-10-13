"use client"

import { useEffect, useState } from "react"
import AppShell from "@/components/ennosmart/app-shell"
import LoginPage from "@/components/ennosmart/login-page"
import { clearTokens, getAccessToken, getMe, type UserRead } from "@/lib/api"

export default function ScholarV3Page() {
  const [user, setUser] = useState<UserRead | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let active = true
    if (!getAccessToken()) { setLoading(false); return }
    getMe().then(current => { if (active) setUser(current) }).catch(() => { if (active) { clearTokens(); setUser(null) } }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])
  if (loading) return <div className="grid min-h-dvh place-items-center bg-background text-sm text-muted-foreground">Vérification de la session consultant…</div>
  if (!user) return <LoginPage onLogin={setUser} />
  return <AppShell user={user} onUserUpdated={setUser} onLogout={() => { clearTokens(); setUser(null) }} initialPage="scholar-v3" />
}
