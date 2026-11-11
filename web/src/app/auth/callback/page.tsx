"use client"

import * as React from "react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"

import { createClient } from "@/lib/firebase/client"

function CallbackContent() {
  const params = useSearchParams()
  const router = useRouter()
  const mode = params.get("mode")
  const code = params.get("oobCode")
  const started = React.useRef(false)
  const [message, setMessage] = React.useState("Checking your link…")
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (started.current) return
    started.current = true

    if (!code) return
    if (mode === "resetPassword") {
      router.replace(`/reset-password?oobCode=${encodeURIComponent(code)}`)
      return
    }

    if (mode !== "verifyEmail") return

    import("firebase/auth")
      .then(({ applyActionCode }) => applyActionCode(createClient(), code))
      .then(() => setMessage("Email verified. You can now sign in."))
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Could not verify this email.")
      })
  }, [code, mode, router])

  const visibleError = error ??
    (!code
      ? "This authentication link is invalid or has expired."
      : mode !== "verifyEmail" && mode !== "resetPassword"
        ? "This authentication link is not supported."
        : null)

  if (visibleError) {
    return (
      <div className="flex flex-col gap-3 text-sm">
        <p role="alert" className="text-destructive">{visibleError}</p>
        <Link href="/sign-in" className="text-brand underline-offset-4 hover:underline">
          Return to sign in
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 text-sm">
      <p>{message}</p>
      {message.startsWith("Email verified") && (
        <Link href="/sign-in" className="text-brand underline-offset-4 hover:underline">
          Sign in
        </Link>
      )}
    </div>
  )
}

export default function AuthCallbackPage() {
  return (
    <React.Suspense fallback={<p className="text-sm text-muted-foreground">Checking your link…</p>}>
      <CallbackContent />
    </React.Suspense>
  )
}
