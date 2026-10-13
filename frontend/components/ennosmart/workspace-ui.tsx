import type { ReactNode } from "react"
import type { LucideIcon } from "lucide-react"
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Circle,
  Clock3,
  FileCheck2,
  Loader2,
  Sparkles,
  XCircle,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

type Tone = "brand" | "neutral" | "success" | "warning" | "danger" | "info"

const toneClasses: Record<Tone, { icon: string; surface: string; value: string }> = {
  brand: {
    icon: "bg-brand/10 text-brand",
    surface: "border-brand/20 bg-brand/[0.035]",
    value: "text-brand",
  },
  neutral: {
    icon: "bg-muted text-muted-foreground",
    surface: "border-border bg-card",
    value: "text-foreground",
  },
  success: {
    icon: "bg-success/10 text-success",
    surface: "border-success/20 bg-success/[0.035]",
    value: "text-success",
  },
  warning: {
    icon: "bg-warning/12 text-warning-foreground",
    surface: "border-warning/25 bg-warning/[0.06]",
    value: "text-warning-foreground",
  },
  danger: {
    icon: "bg-destructive/10 text-destructive",
    surface: "border-destructive/20 bg-destructive/[0.035]",
    value: "text-destructive",
  },
  info: {
    icon: "bg-info/10 text-info",
    surface: "border-info/20 bg-info/[0.035]",
    value: "text-info",
  },
}

export function PageHeader({
  eyebrow,
  title,
  description,
  icon: Icon,
  context,
  actions,
  backAction,
  className,
}: {
  eyebrow?: string
  title: ReactNode
  description?: ReactNode
  icon?: LucideIcon
  context?: ReactNode
  actions?: ReactNode
  backAction?: { label: string; onClick: () => void }
  className?: string
}) {
  return (
    <header className={cn("workspace-page-header", className)}>
      <div className="relative z-[1] min-w-0 flex-1">
        {backAction && (
          <Button
            variant="ghost"
            size="sm"
            onClick={backAction.onClick}
            className="-ml-2 mb-2 h-8 text-muted-foreground"
          >
            <span aria-hidden="true">←</span>
            {backAction.label}
          </Button>
        )}
        {eyebrow && (
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-brand">
            {eyebrow}
          </p>
        )}
        <div className="flex min-w-0 items-start gap-3">
          {Icon && (
            <span className="workspace-header-icon grid size-9 shrink-0 place-items-center rounded-lg border border-brand/15 bg-brand/8 text-brand">
              <Icon className="size-[18px]" aria-hidden="true" />
            </span>
          )}
          <div className="min-w-0">
            <h1 className="text-balance text-2xl font-semibold leading-tight tracking-[-0.025em] text-foreground sm:text-[1.75rem]">
              {title}
            </h1>
            {description && (
              <p className="mt-1.5 max-w-3xl text-sm leading-6 text-muted-foreground">
                {description}
              </p>
            )}
            {context && <div className="mt-3 flex flex-wrap items-center gap-2">{context}</div>}
          </div>
        </div>
      </div>
      {actions && (
        <div className="relative z-[1] flex w-full flex-wrap items-center gap-2 sm:w-auto sm:justify-end">
          {actions}
        </div>
      )}
    </header>
  )
}

export function ContextBadge({
  children,
  label,
  value,
  className,
}: {
  children?: ReactNode
  label?: ReactNode
  value?: ReactNode
  className?: string
}) {
  return (
    <Badge variant="outline" className={cn("h-6 border-border bg-background/80 px-2.5 text-[11px] text-muted-foreground", className)}>
      {children ?? <><span className="font-medium text-muted-foreground">{label}</span><span className="max-w-56 truncate text-foreground">{value}</span></>}
    </Badge>
  )
}

export function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "neutral",
  className,
}: {
  label: ReactNode
  value: ReactNode
  detail?: ReactNode
  icon?: LucideIcon
  tone?: Tone
  className?: string
}) {
  const styles = toneClasses[tone]
  return (
    <Card className={cn("min-w-0 shadow-none", styles.surface, className)}>
      <CardContent className="flex min-h-[108px] items-start justify-between gap-4 p-4 sm:p-5">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{label}</p>
          <p className={cn("mt-2 text-2xl font-semibold tracking-[-0.03em] tabular-nums", styles.value)}>{value}</p>
          {detail && <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>}
        </div>
        {Icon && (
          <span className={cn("grid size-9 shrink-0 place-items-center rounded-lg", styles.icon)}>
            <Icon className="size-[18px]" aria-hidden="true" />
          </span>
        )}
      </CardContent>
    </Card>
  )
}

export type StatusState =
  | "suggestion"
  | "processing"
  | "evidence"
  | "review"
  | "validated"
  | "rejected"
  | "attention"
  | "failed"
  | "neutral"

const statusConfig: Record<StatusState, { className: string; icon: LucideIcon }> = {
  suggestion: { className: "border-brand/25 bg-brand/8 text-brand", icon: Sparkles },
  processing: { className: "border-info/25 bg-info/8 text-info", icon: Loader2 },
  evidence: { className: "border-info/25 bg-info/8 text-info", icon: FileCheck2 },
  review: { className: "border-warning/30 bg-warning/10 text-warning-foreground", icon: Clock3 },
  validated: { className: "border-success/25 bg-success/8 text-success", icon: CheckCircle2 },
  rejected: { className: "border-destructive/25 bg-destructive/8 text-destructive", icon: XCircle },
  attention: { className: "border-warning/30 bg-warning/10 text-warning-foreground", icon: AlertCircle },
  failed: { className: "border-destructive/25 bg-destructive/8 text-destructive", icon: AlertCircle },
  neutral: { className: "border-border bg-muted/60 text-muted-foreground", icon: Circle },
}

export function StatusChip({
  state,
  children,
  live = false,
  className,
}: {
  state: StatusState
  children: ReactNode
  live?: boolean
  className?: string
}) {
  const config = statusConfig[state]
  const Icon = config.icon
  return (
    <span
      className={cn("inline-flex min-h-6 w-fit items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold leading-none", config.className, className)}
      role={live ? "status" : undefined}
      aria-atomic={live || undefined}
    >
      <Icon className={cn("size-3.5", state === "processing" && "animate-spin motion-reduce:animate-none")} aria-hidden="true" />
      {children}
    </span>
  )
}

export function StatusNotice({
  tone = "info",
  state,
  title,
  children,
  description,
  actions,
  action,
  live = false,
  className,
}: {
  tone?: Tone
  state?: StatusState
  title: ReactNode
  children?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  action?: ReactNode
  live?: boolean
  className?: string
}) {
  const resolvedTone: Tone = state === "failed" || state === "rejected" ? "danger" : state === "validated" ? "success" : state === "attention" || state === "review" ? "warning" : state === "suggestion" ? "brand" : tone
  const styles = toneClasses[resolvedTone]
  const Icon = resolvedTone === "success" ? CheckCircle2 : resolvedTone === "danger" ? AlertCircle : resolvedTone === "warning" ? Clock3 : resolvedTone === "brand" ? Sparkles : FileCheck2
  return (
    <div className={cn("flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-start", styles.surface, className)} role={live ? "status" : undefined} aria-atomic={live || undefined}>
      <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg", styles.icon)}>
        <Icon className="size-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {(children || description) && <div className="mt-1 text-xs leading-5 text-muted-foreground">{children ?? description}</div>}
      </div>
      {(actions || action) && <div className="flex shrink-0 flex-wrap gap-2">{actions ?? action}</div>}
    </div>
  )
}

export function LoadingState({ label, detail }: { label: string; detail?: string }) {
  return (
    <div className="workspace-page grid min-h-[52vh] place-items-center" role="status" aria-live="polite">
      <div className="flex max-w-md flex-col items-center text-center">
        <span className="grid size-11 place-items-center rounded-xl border border-brand/15 bg-brand/8 text-brand">
          <Loader2 className="size-5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        </span>
        <p className="mt-4 text-sm font-semibold text-foreground">{label}</p>
        {detail && <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>}
      </div>
    </div>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("rounded-xl border border-dashed border-border bg-muted/20 px-6 py-10 text-center", className)}>
      <span className="mx-auto grid size-10 place-items-center rounded-lg bg-muted text-muted-foreground">
        <Icon className="size-5" aria-hidden="true" />
      </span>
      <p className="mt-4 text-sm font-semibold text-foreground">{title}</p>
      {description && <p className="mx-auto mt-1 max-w-lg text-xs leading-5 text-muted-foreground">{description}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  )
}

export function SectionHeader({
  id,
  title,
  description,
  actions,
  action,
  className,
}: {
  id?: string
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div>
        <h2 id={id} className="text-base font-semibold tracking-[-0.01em] text-foreground">{title}</h2>
        {description && <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>}
      </div>
      {(actions || action) && <div className="flex flex-wrap items-center gap-2">{actions ?? action}</div>}
    </div>
  )
}

export function WorkflowSteps({
  steps,
  ariaLabel = "Progression du workflow",
  className,
}: {
  steps: Array<{
    label: string
    detail?: string
    description?: string
    status?: "complete" | "current" | "upcoming" | "attention"
    icon?: LucideIcon
    onClick?: () => void
  }>
  ariaLabel?: string
  className?: string
}) {
  return (
    <ol className={cn("workflow-steps", className)} aria-label={ariaLabel}>
      {steps.map((step, index) => {
        const complete = step.status === "complete"
        const current = step.status === "current"
        const attention = step.status === "attention"
        const Icon = step.icon
        const content = (
          <>
            <span className="workflow-step-index" aria-hidden="true">
              {complete ? <Check className="size-3.5" /> : Icon ? <Icon className="size-3.5" /> : index + 1}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-xs font-semibold text-foreground">{step.label}</span>
              {(step.detail || step.description) && <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">{step.detail ?? step.description}</span>}
            </span>
          </>
        )
        return (
          <li key={`${index}-${step.label}`} className={cn("workflow-step", current && "is-current", complete && "is-complete", attention && "is-attention")} aria-current={current ? "step" : undefined}>
            {step.onClick ? (
              <button type="button" onClick={step.onClick} className="flex w-full items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25">
                {content}
              </button>
            ) : content}
          </li>
        )
      })}
    </ol>
  )
}
