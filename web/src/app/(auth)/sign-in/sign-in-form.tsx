"use client"

import * as React from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Eye, EyeOff, Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { createClient } from "@/lib/supabase/client"
import { supabaseConfigured } from "@/lib/supabase/config"

export function SignInForm() {
  const router = useRouter()
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
      const { error } = await createClient().auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth/callback?next=/reset-password`,
      })
      if (error) { setError(error.message); return }
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
      const { error } = await createClient().auth.signInWithPassword({
        email: String(form.get("email") ?? ""),
        password: String(form.get("password") ?? ""),
      })
      if (error) {
        // Supabase already says "Invalid login credentials" without revealing whether the
        // address exists, which is the behaviour we want; don't rewrite it into something
        // more specific.
        setError(error.message)
        return
      }
      // refresh() so the middleware re-runs with the new session cookie before we land.
      router.replace(params.get("next") || "/dashboard")
      router.refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the sign-in service.")
    } finally {
      setPending(false)
    }
  }

  if (!supabaseConfigured) {
    return (
      <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        Sign-in is not configured: <code>NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
        <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> are missing from this build. On Vercel
        they are inlined at build time, so add them and redeploy.
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
