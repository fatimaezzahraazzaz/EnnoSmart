"use client"

import { useEffect, useState } from "react"
import LoginPage from "@/components/ennosmart/login-page"
import AppShell from "@/components/ennosmart/app-shell"
import { clearTokens, getAccessToken, getMe, type UserRead } from "@/lib/api"

export default function Page() {
  const [user, setUser] = useState<UserRead | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = getAccessToken()

    if (!token) {
      setLoading(false)
      return
    }

    getMe()
      .then((currentUser) => {
        setUser(currentUser)
      })
      .catch(() => {
        clearTokens()
        setUser(null)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const handleLogout = () => {
    clearTokens()
    setUser(null)
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-2">
          <p className="text-sm font-medium text-foreground">
            Chargement EnnoSmart...
          </p>
          <p className="text-xs text-muted-foreground">
            Vérification de la session consultant
          </p>
        </div>
      </div>
    )
  }

  if (!user) {
    return <LoginPage onLogin={(connectedUser) => setUser(connectedUser)} />
  }

  return <AppShell user={user} onLogout={handleLogout} onUserUpdated={setUser} />
}
