"use client"

import * as React from "react"
import { api, type CreditStatus } from "@/lib/api"
import { cn } from "@/lib/utils"

function Bar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs text-muted-foreground">{used} / {limit} credits</span>
      </div>
      <div className="h-2 rounded-full bg-muted overflow-hidden">
        <div
          className={cn("h-full rounded-full transition-all", pct >= 90 ? "bg-destructive" : pct >= 70 ? "bg-yellow-500" : "bg-primary")}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

/** Monthly + daily credit consumption against the caller's tier allowance, from
 *  GET /settings/usage's `credits` field. 1 credit = 1 outreach_ready lead returned. */
export function CreditsMeter() {
  const [credits, setCredits] = React.useState<CreditStatus | null>(null)
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    api.getUsage().then((r) => setCredits(r.credits)).catch(() => {}).finally(() => setLoading(false))
  }, [])

  if (loading) return <p className="text-sm text-muted-foreground">Loading credits…</p>
  if (!credits) return null

  return (
    <div className="grid gap-4">
      <Bar label="This month" used={credits.monthly_used} limit={credits.monthly_limit} />
      <Bar label="Today" used={credits.daily_used} limit={credits.daily_limit} />
      <p className="text-xs text-muted-foreground">
        1 credit = 1 outreach-ready lead returned. Billed when a run finishes, not when it starts.
      </p>
    </div>
  )
}
