import { Badge } from "@/components/ui/badge"
import type { ArticleTag, CirTag } from "@/lib/mock-data"

export function CirTagBadge({ tag }: { tag: CirTag }) {
  const className =
    tag === "PERTINENT POUR CIR"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : tag === "MOYEN POUR CIR"
        ? "bg-amber-50 text-amber-700 border-amber-200"
        : "bg-slate-50 text-slate-600 border-slate-200"
  return <Badge variant="outline" className={className}>{tag}</Badge>
}

export function ArticleTagBadge({ tag }: { tag: ArticleTag }) {
  const className =
    tag === "DIRECTEMENT LIÉ AU VERROU"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : tag === "JUSTE CONCEPT SCIENTIFIQUE"
        ? "bg-blue-50 text-blue-700 border-blue-200"
        : "bg-rose-50 text-rose-700 border-rose-200"
  return <Badge variant="outline" className={className}>{tag}</Badge>
}

export function RiskBadge({ risk }: { risk: string }) {
  const className =
    risk === "Faible"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : risk === "Moyen"
        ? "bg-amber-50 text-amber-700 border-amber-200"
        : "bg-rose-50 text-rose-700 border-rose-200"
  return <Badge variant="outline" className={className}>{risk}</Badge>
}

export function StatusBadge({ status }: { status: string }) {
  const className = status.includes("prêt") || status.includes("terminée")
    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
    : status.includes("Validation")
      ? "bg-amber-50 text-amber-700 border-amber-200"
      : "bg-violet-50 text-violet-700 border-violet-200"
  return <Badge variant="outline" className={className}>{status}</Badge>
}
