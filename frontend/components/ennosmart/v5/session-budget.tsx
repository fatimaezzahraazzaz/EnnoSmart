"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { getResourceBudget, setResourceBudget, type ResourceBudget } from "@/lib/scholar-v5"

const dollars = (value: number) => value.toLocaleString("fr-FR", { minimumFractionDigits: 4, maximumFractionDigits: 4 })

export default function SessionBudget({ sid, value, onChanged }: { sid: string; value?: ResourceBudget; onChanged: () => Promise<void> }) {
  const [budget, setBudget] = useState(value)
  const [cap, setCap] = useState(value?.limit_usd?.toString() ?? "")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  useEffect(() => {
    let current = true
    if (value) { setBudget(value); return }
    getResourceBudget(sid).then(next => { if (current) { setBudget(next); setCap(next.limit_usd?.toString() ?? "") } })
      .catch(() => { if (current) setError("Consommation indisponible. Actualisez la conversation.") })
    return () => { current = false }
  }, [sid, value])
  const configuredLimit = value?.limit_usd
  useEffect(() => { setCap(configuredLimit?.toString() ?? "") }, [sid, configuredLimit])
  async function save() {
    if (!budget) return
    const limit = cap.trim() ? Number(cap.replace(",", ".")) : null
    if (limit !== null && (!Number.isFinite(limit) || limit <= 0)) {
      setError("Indiquez un montant positif en dollars, ou laissez le champ vide."); return
    }
    setPending(true); setError(""); setNotice("")
    try {
      const next = await setResourceBudget(sid, limit, budget.revision)
      setBudget(next); setNotice(limit === null ? "Plafond de conversation désactivé." : "Plafond de conversation enregistré.")
      await onChanged()
    } catch (problem) { setError(problem instanceof Error ? problem.message : "Échec de sauvegarde. Actualisez puis réessayez.") }
    finally { setPending(false) }
  }
  return <details className="mx-auto mt-5 max-w-3xl rounded-xl border px-3 py-2 text-sm">
    <summary className="cursor-pointer rounded focus-visible:outline-2 focus-visible:outline-primary">
      Consommation de la conversation : <span className="tabular-nums">{budget ? `${dollars(budget.committed_usd)} $ engagés` : "chargement…"}</span>
      {budget?.limit_usd != null ? ` sur ${dollars(budget.limit_usd)} $` : budget ? " · sans plafond de conversation" : ""}
    </summary>
    {budget && <p className="mt-3 tabular-nums">{dollars(budget.confirmed_usd)} $ confirmés · {dollars(budget.reserved_usd)} $ réservés · {dollars(budget.uncertain_usd)} $ incertains.</p>}
    {budget && <p className="mt-1 tabular-nums">{budget.tokens.toLocaleString("fr-FR")} tokens engagés, consommations et réservations comprises.</p>}
    <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); void save() }}>
      <div className="min-w-0 flex-1"><label className="mb-1 block font-medium" htmlFor="v5-session-budget">Plafond cumulé de la conversation ($)</label>
        <input id="v5-session-budget" inputMode="decimal" className="min-h-11 w-full rounded-md border bg-background px-3 focus-visible:outline-2 focus-visible:outline-primary" value={cap} disabled={pending || !budget} onChange={event => setCap(event.target.value)} aria-invalid={Boolean(error)} aria-describedby="v5-budget-help v5-budget-error" />
      </div><Button type="submit" className="min-h-11" variant="outline" disabled={pending || !budget}>{pending ? "Enregistrement…" : "Enregistrer le plafond"}</Button>
    </form>
    <p id="v5-budget-help" className="mt-2 text-muted-foreground">Champ vide : aucun plafond de conversation. Les limites par exécution et par mission restent actives. Les montants proviennent des consommations attestées et des réservations, sans garantie de facture finale.</p>
    <p id="v5-budget-error" role={error ? "alert" : undefined} className="mt-2 text-destructive">{error}</p>
    {notice && <p role="status" className="mt-2">{notice}</p>}
  </details>
}
