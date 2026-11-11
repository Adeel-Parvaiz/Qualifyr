"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { LogOut } from "lucide-react"
import { signOut as firebaseSignOut } from "firebase/auth"

import { Button } from "@/components/ui/button"
import { createClient } from "@/lib/firebase/client"
import { firebaseConfigured } from "@/lib/firebase/config"

export function SignOutButton() {
  const router = useRouter()
  const [error, setError] = React.useState<string | null>(null)

  // Nothing to sign out of on an unconfigured build; showing a dead button is worse than
  // showing none.
  if (!firebaseConfigured) return null

  async function signOut() {
    setError(null)
    try {
      const response = await fetch("/api/auth/session", { method: "DELETE" })
      if (!response.ok) throw new Error("Could not clear the server session.")
      await firebaseSignOut(createClient())
      // replace(), not push(): the dashboard must not be reachable with the back button.
      router.replace("/sign-in")
      router.refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not sign out.")
    }
  }

  return (
    <>
      <Button variant="ghost" size="icon" onClick={signOut} aria-label="Sign out">
        <LogOut className="size-5" />
      </Button>
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
    </>
  )
}
