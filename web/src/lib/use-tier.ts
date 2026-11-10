"use client"

import * as React from "react"
import { api } from "@/lib/api"

/** The caller's resolved tier + per-run dropdown choices, from GET /settings/limits.
 *  One shared hook so the sidebar badge, Settings "Your plan" card and the Campaigns
 *  page all read the same cheap call instead of each firing their own fetch. */
export function useTier() {
  const [tier, setTier] = React.useState<string | null>(null)
  const [unlimited, setUnlimited] = React.useState(false)
  const [allowedLeadsPerRun, setAllowedLeadsPerRun] = React.useState<number[]>([])
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    api.myLimits()
      .then((l) => {
        setTier(l.tier)
        setUnlimited(l.unlimited)
        setAllowedLeadsPerRun(l.allowed_leads_per_run)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return { tier, unlimited, allowedLeadsPerRun, loading }
}
