import { createServerClient } from "@supabase/ssr"
import { NextResponse, type NextRequest } from "next/server"

import { SUPABASE_ANON_KEY, SUPABASE_URL, supabaseConfigured } from "@/lib/supabase/config"

/** Routes that require a session. Everything else - the landing page, /sign-in, static
 *  assets - is public. */
const PROTECTED = ["/dashboard", "/campaigns", "/leads", "/outreach", "/settings"]

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl
  const isProtected = PROTECTED.some((p) => pathname === p || pathname.startsWith(`${p}/`))

  const isAuthPage = pathname === "/sign-in" || pathname === "/sign-up" || pathname === "/reset-password"

  // Nothing to check, and nothing we could check with. Note this only affects *navigation*:
  // the API refuses unauthenticated requests regardless, so an unconfigured deploy shows an
  // empty dashboard rather than leaking anything.
  if (!supabaseConfigured || (!isProtected && !isAuthPage)) {
    return NextResponse.next()
  }

  // Supabase refreshes the session by setting cookies, so the response has to be built
  // first and handed to the client - otherwise a refreshed token is dropped and the user
  // is silently signed out when the old one expires.
  let response = NextResponse.next({ request })

  const supabase = createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookies) => {
        cookies.forEach(({ name, value }) => request.cookies.set(name, value))
        response = NextResponse.next({ request })
        cookies.forEach(({ name, value, options }) => response.cookies.set(name, value, options))
      },
    },
  })

  // getUser(), not getSession(): getSession trusts whatever is in the cookie, while getUser
  // verifies it with Supabase. A forged cookie must not get past the redirect.
  const {
    data: { user },
  } = await supabase.auth.getUser()

  if (isProtected && !user) {
    const url = request.nextUrl.clone()
    url.pathname = "/sign-in"
    // Come back to where they were aiming once they are in.
    url.searchParams.set("next", pathname)
    return NextResponse.redirect(url)
  }

  if (isAuthPage && user && pathname !== "/reset-password") {
    const url = request.nextUrl.clone()
    url.pathname = "/dashboard"
    url.search = ""
    return NextResponse.redirect(url)
  }

  return response
}

export const config = {
  // Skip static assets and image optimisation: running an auth check on every .svg is
  // latency for nothing.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
}
