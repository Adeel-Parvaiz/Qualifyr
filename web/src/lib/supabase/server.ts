import { createServerClient } from "@supabase/ssr"
import { cookies } from "next/headers"
import { SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL, supabaseConfigured } from "./config"

export async function createClient(cookieStore?: Awaited<ReturnType<typeof cookies>>) {
  if (!supabaseConfigured) {
    throw new Error("Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY.")
  }
  const store = cookieStore ?? await cookies()
  return createServerClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    cookies: {
      getAll: () => store.getAll(),
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) => store.set(name, value, options))
        } catch {
          // Server Components cannot write cookies; the request proxy refreshes them.
        }
      },
    },
  })
}
