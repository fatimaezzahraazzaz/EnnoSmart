import * as React from "react"
import { Check } from "lucide-react"

import { cn } from "@/lib/utils"

type CheckboxProps = Omit<React.ComponentProps<"input">, "onChange" | "type"> & {
  onCheckedChange?: (checked: boolean) => void
}

function Checkbox({ className, checked, defaultChecked, disabled, onCheckedChange, ...props }: CheckboxProps) {
  return (
    <label className={cn("relative inline-flex size-5 shrink-0 items-center justify-center", disabled && "opacity-50", className)}>
      <input
        type="checkbox"
        checked={checked}
        defaultChecked={defaultChecked}
        disabled={disabled}
        onChange={(event) => onCheckedChange?.(event.target.checked)}
        className="peer absolute inset-0 cursor-pointer appearance-none rounded-[5px] border border-input bg-background transition-colors checked:border-primary checked:bg-primary focus-visible:ring-3 focus-visible:ring-ring/25 disabled:cursor-not-allowed"
        {...props}
      />
      <Check className="pointer-events-none relative size-3 text-primary-foreground opacity-0 peer-checked:opacity-100" strokeWidth={3} />
    </label>
  )
}

export { Checkbox }
