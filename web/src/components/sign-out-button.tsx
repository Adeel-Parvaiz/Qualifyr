"use client"

import { useRouter } from "next/navigation"
import { LogOut } from "lucide-react"

import { Button } from "@/components/ui/button"
import { createClient } from "@/lib/supabase/client"
import { supabaseConfigured } from "@/lib/supabase/config"

export function SignOutButton() {
  const router = useRouter()

  // Nothing to sign out of on an unconfigured build; showing a dead button is worse than
  // showing none.
  if (!supabaseConfigured) return null

  async function signOut() {
    await createClient().auth.signOut()
    // replace(), not push(): the dashboard must not be reachable with the back button
    // after signing out. refresh() then re-runs the middleware against the cleared cookie.
    router.replace("/sign-in")
    router.refresh()
  }

  return (
    <Button variant="ghost" size="icon" onClick={signOut}>
      <LogOut className="size-5" />
      <span className="sr-only">Sign out</span>
    </Button>
  )
}
