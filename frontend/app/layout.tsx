import { Analytics } from "@vercel/analytics/next"
import type { Metadata } from "next"

import "./globals.css"

export const metadata: Metadata = {
  title: "Ennoma — Plateforme CIR IA",
  description: "Pilotez, analysez et améliorez vos dossiers CIR avec une plateforme multi-agents sécurisée.",
  icons: { icon: "/ennoma-logo.png", apple: "/ennoma-logo.png" },
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="fr" className="bg-background">
      <body className="font-sans antialiased">
        {children}
        {process.env.NODE_ENV === "production" && <Analytics />}
      </body>
    </html>
  )
}
