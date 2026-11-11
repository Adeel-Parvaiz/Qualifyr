import { NextResponse, type NextRequest } from "next/server"
import { createServerClient } from "@supabase/ssr"
import { SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL, supabaseConfigured } from "@/lib/supabase/config"

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl
  // Next.js dev requests can retain the internal localhost origin behind ngrok.
  // Trust only the exact tunnel hostname configured by the developer.
  const tunnelHost = process.env.NGROK_DOMAIN?.trim()
  const viaTunnel = tunnelHost && [request.headers.get("host"), request.headers.get("x-forwarded-host")]
    .some((host) => host?.split(",")[0].trim() === tunnelHost)
  const origin = viaTunnel ? `https://${tunnelHost}` : request.nextUrl.origin
  const code = searchParams.get("code")
  const destination = new URL(searchParams.get("next") ?? "/dashboard", origin)
  const next = destination.origin === origin ? destination.pathname + destination.search : "/dashboard"

  if (!supabaseConfigured || !code) {
    return NextResponse.redirect(`${origin}/sign-in`)
  }

  const response = NextResponse.redirect(`${origin}${next}`)
  const supabase = createServerClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookies, headers) => {
        cookies.forEach(({ name, value, options }) => response.cookies.set(name, value, options))
        Object.entries(headers).forEach(([key, value]) => response.headers.set(key, value))
      },
    },
  })

  const { error } = await supabase.auth.exchangeCodeForSession(code)
  if (error) {
    const failure = NextResponse.redirect(`${origin}/sign-in`)
    response.cookies.getAll().forEach((cookie) => failure.cookies.set(cookie))
    for (const header of ["cache-control", "expires", "pragma"]) {
      const value = response.headers.get(header)
      if (value) failure.headers.set(header, value)
    }
    return failure
  }

  return response
}
