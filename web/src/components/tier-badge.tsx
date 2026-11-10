"use client"

import type { ReactNode } from "react"
import { Crown, Sparkles } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { useTier } from "@/lib/use-tier"

const TIER_STYLE: Record<string, { label: string; className: string; icon?: ReactNode }> = {
  free: { label: "Free", className: "border-border text-foreground" },
  pro: { label: "Pro", className: "border-transparent bg-brand text-brand-foreground", icon: <Sparkles className="size-3" /> },
  enterprise: { label: "Enterprise", className: "border-transparent bg-foreground text-background", icon: <Crown className="size-3" /> },
}

/** Free/Pro/Enterprise plan badge, sourced from GET /settings/limits (useTier). Local
 *  operators and master accounts (unlimited: true) show "Unlimited" instead of their
 *  underlying free-tier row, since that row is never actually enforced for them. */
export function TierBadge({ className }: { className?: string }) {
  const { tier, unlimited, loading } = useTier()
  if (loading || !tier) return null

  if (unlimited) {
    return <Badge variant="outline" className={cn("border-border text-muted-foreground", className)}>Unlimited</Badge>
  }

  const style = TIER_STYLE[tier] ?? TIER_STYLE.free
  return (
    <Badge variant="outline" className={cn(style.className, className)}>
      {style.icon}
      {style.label}
    </Badge>
  )
}
