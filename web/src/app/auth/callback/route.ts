import { NextResponse, type NextRequest } from "next/server"
import { createServerClient } from "@supabase/ssr"
import { SUPABASE_ANON_KEY, SUPABASE_URL, supabaseConfigured } from "@/lib/supabase/config"

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
  const supabase = createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookies) => {
        cookies.forEach(({ name, value, options }) => response.cookies.set(name, value, options))
      },
    },
  })

  const { error } = await supabase.auth.exchangeCodeForSession(code)
  if (error) {
    return NextResponse.redirect(`${origin}/sign-in`)
  }

  return response
}
