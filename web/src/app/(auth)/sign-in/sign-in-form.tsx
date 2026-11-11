"use client"

import * as React from "react"
import { useSearchParams } from "next/navigation"
import { Eye, EyeOff, Loader2 } from "lucide-react"
import { browserLocalPersistence, setPersistence, sendEmailVerification, sendPasswordResetEmail, signInWithEmailAndPassword, signOut } from "firebase/auth"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { createClient } from "@/lib/firebase/client"
import { firebaseConfigured } from "@/lib/firebase/config"

export function SignInForm() {
  const params = useSearchParams()
  const [showPassword, setShowPassword] = React.useState(false)
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [resetMode, setResetMode] = React.useState(false)
  const [resetSent, setResetSent] = React.useState(false)

  async function onReset(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setPending(true)
    const form = new FormData(event.currentTarget)
    const email = String(form.get("email") ?? "").trim()
    if (!email) { setError("Enter your email address."); setPending(false); return }
    try {
      await sendPasswordResetEmail(createClient(), email, {
        url: `${window.location.origin}/auth/callback?next=/reset-password`,
        handleCodeInApp: true,
      })
      setResetSent(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the auth service.")
    } finally {
      setPending(false)
    }
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setPending(true)

    const form = new FormData(event.currentTarget)
    try {
      const auth = createClient()
      await setPersistence(auth, browserLocalPersistence)
      const credential = await signInWithEmailAndPassword(
        auth,
        String(form.get("email") ?? ""),
        String(form.get("password") ?? ""),
      )
      if (!credential.user.emailVerified) {
        try {
          await sendEmailVerification(credential.user, {
            url: `${window.location.origin}/auth/callback`,
            handleCodeInApp: true,
          })
        } finally {
          await signOut(auth)
        }
        setError("Verify your email using the link we just sent, then sign in again.")
        return
      }
      const idToken = await credential.user.getIdToken()
      const sessionResponse = await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idToken }),
      })
      if (!sessionResponse.ok) {
        await signOut(auth)
        throw new Error("Could not establish a secure session. Please try again.")
      }
      const requestedNext = params.get("next") || "/dashboard"
      const destination = new URL(requestedNext, window.location.origin)
      const next = destination.origin === window.location.origin
        ? `${destination.pathname}${destination.search}${destination.hash}`
        : "/dashboard"
      // A full navigation sends the new HttpOnly session cookie through the server proxy.
      window.location.replace(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the sign-in service.")
    } finally {
      setPending(false)
    }
  }

  if (!firebaseConfigured) {
    return (
      <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        Sign-in is not configured: the <code>NEXT_PUBLIC_FIREBASE_*</code> settings are missing
        from this build. On Vercel they are inlined at build time, so add them and redeploy.
      </p>
    )
  }

  if (resetMode) {
    if (resetSent) {
      return (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            Check your inbox for a password reset link. It may take a minute to arrive.
          </p>
          <button type="button" onClick={() => { setResetMode(false); setResetSent(false); setError(null) }} className="text-sm text-brand underline-offset-4 hover:underline self-start">
            Back to sign in
          </button>
        </div>
      )
    }
    return (
      <form onSubmit={onReset} className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <Label htmlFor="email">Email</Label>
          <Input id="email" name="email" type="email" autoComplete="email" placeholder="you@company.com" required className="h-10" />
        </div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button type="submit" disabled={pending} className="h-10 bg-brand text-brand-foreground hover:bg-brand/90">
          {pending && <Loader2 className="size-4 animate-spin" />}
          {pending ? "Sending…" : "Send reset link"}
        </Button>
        <button type="button" onClick={() => { setResetMode(false); setError(null) }} className="text-sm text-muted-foreground underline-offset-4 hover:underline self-start">
          Back to sign in
        </button>
      </form>
    )
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          required
          className="h-10"
        />
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="password">Password</Label>
          <button type="button" onClick={() => { setResetMode(true); setError(null) }} className="text-xs text-muted-foreground underline-offset-4 hover:underline">
            Forgot password?
          </button>
        </div>
        <div className="relative">
          <Input
            id="password"
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            placeholder="Your password"
            required
            className="h-10 pr-10"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "sign-in-error" : undefined}
          />
          <button
            type="button"
            onClick={() => setShowPassword(!showPassword)}
            className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-muted-foreground hover:text-foreground"
          >
            {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            <span className="sr-only">{showPassword ? "Hide" : "Show"} password</span>
          </button>
        </div>
      </div>

      {error && (
        <p id="sign-in-error" role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <Button
        type="submit"
        disabled={pending}
        className="h-10 bg-brand text-brand-foreground hover:bg-brand/90"
      >
        {pending && <Loader2 className="size-4 animate-spin" />}
        {pending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  )
}
