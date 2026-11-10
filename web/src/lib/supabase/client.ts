"use client"

import { createBrowserClient } from "@supabase/ssr"

import { SUPABASE_ANON_KEY, SUPABASE_URL, supabaseConfigured } from "./config"

/** Browser-side Supabase client.
 *
 * createBrowserClient memoises internally, so calling this per component is fine and keeps
 * one auth session (and one token refresh timer) for the tab.
 */
export function createClient() {
  if (!supabaseConfigured) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are not set. On Vercel " +
        "these are inlined at build time, so add them and redeploy - setting them alone " +
        "will not change an existing build."
    )
  }
  return createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY)
}
