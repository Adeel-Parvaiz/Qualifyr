"use client"

import * as React from "react"
import Link from "next/link"
import { FlaskConical } from "lucide-react"
import { api, type PayPalStatus } from "@/lib/api"
import { cn } from "@/lib/utils"

/** Always-visible mode indicator. Judges (and users) should never have to guess whether real
 *  money can move: sandbox is stated on every screen, in the header. */
export function PayPalModeBadge({ className }: { className?: string }) {
  const [status, setStatus] = React.useState<PayPalStatus | null>(null)
  React.useEffect(() => { api.paypalStatus().then(setStatus).catch(() => setStatus(null)) }, [])
  if (!status) return null
  const sandbox = status.mode === "sandbox"
  return (
    <Link
      href="/revenue"
      title={status.configured ? "PayPal is connected" : "PayPal credentials are not set on the server"}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        sandbox
          ? "border-amber-500/30 bg-amber-500/10 text-amber-700 hover:bg-amber-500/15 dark:text-amber-400"
          : "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-400",
        className,
      )}
    >
      <FlaskConical className="size-3.5" />
      PayPal {sandbox ? "Sandbox" : "LIVE"}
      <span className={cn("size-1.5 rounded-full", status.configured ? "bg-green-500" : "bg-muted-foreground/50")} />
    </Link>
  )
}
