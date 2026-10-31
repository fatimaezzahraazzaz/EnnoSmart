"use client"

import { useState } from "react"
import dynamic from "next/dynamic"
const V4 = dynamic(() => import("./ennoscholar-v4-chat"), { ssr: false })
const V5 = dynamic(() => import("./v5/ennoscholar-v4-chat"), { ssr: false })

export default function ScholarEngineSelector(props: { userId: number; onOpenAppMenu: () => void }) {
  // Always open V4. Selection is explicit, per mounted workspace; no migration.
  const [engine, setEngine] = useState<"v4" | "v5">("v4")
  if (process.env.NEXT_PUBLIC_ENNOSCHOLAR_V5_TEST_ENABLED !== "1") return <V4 {...props} />
  return <div className="flex h-full min-h-0 flex-col">
    <div className="flex flex-wrap items-center gap-3 border-b bg-muted/40 px-4 py-2">
      <label htmlFor="scholar-test-engine" className="text-sm font-medium">Moteur de test</label>
      <select id="scholar-test-engine" className="min-h-11 rounded-md border bg-background px-3 text-sm" value={engine}
        onChange={e => setEngine(e.target.value as "v4" | "v5")}>
        <option value="v4">V4</option><option value="v5">V5 · Sol + LangGraph</option>
      </select>
      <span className="text-sm text-muted-foreground">Historiques séparés. Un changement de moteur conserve les données et laisse les exécutions en cours continuer.</span>
    </div>
    <div className="min-h-0 flex-1">{engine === "v5" ? <V5 key="v5" {...props} /> : <V4 key="v4" {...props} />}</div>
  </div>
}
