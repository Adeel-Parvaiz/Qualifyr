import { NextResponse, type NextRequest } from "next/server"

import { createAdminAuth } from "@/lib/firebase/admin"
import { firebaseConfigured } from "@/lib/firebase/config"
import { SESSION_COOKIE_NAME } from "@/lib/firebase/session"

const PROTECTED = ["/dashboard", "/campaigns", "/leads", "/outreach", "/revenue", "/settings"]
const AUTH_PAGES = ["/sign-in", "/sign-up", "/reset-password"]
const INVALID_SESSION_CODES = new Set([
  "auth/argument-error",
  "auth/id-token-expired",
  "auth/invalid-id-token",
  "auth/session-cookie-expired",
  "auth/session-cookie-revoked",
  "auth/invalid-session-cookie",
  "auth/user-disabled",
  "auth/user-not-found",
])

function isAuthVerificationFailure(error: unknown): boolean {
  if (!error || typeof error !== "object" || !("code" in error)) return false
  return typeof error.code === "string" && INVALID_SESSION_CODES.has(error.code)
}

function clearSession(response: NextResponse) {
  response.cookies.set(SESSION_COOKIE_NAME, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  })
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl
  const isProtected = PROTECTED.some((path) => pathname === path || pathname.startsWith(`${path}/`))
  const isAuthPage = AUTH_PAGES.includes(pathname)

  if (!firebaseConfigured || (!isProtected && !isAuthPage)) return NextResponse.next()

  let validSession = false
  let invalidSession = false
  const sessionCookie = request.cookies.get(SESSION_COOKIE_NAME)?.value
  if (sessionCookie) {
    try {
      await createAdminAuth().verifySessionCookie(sessionCookie)
      validSession = true
    } catch (error) {
      if (!isAuthVerificationFailure(error)) throw error
      invalidSession = true
    }
  }

  if (isProtected && !validSession) {
    const url = request.nextUrl.clone()
    url.pathname = "/sign-in"
    url.searchParams.set("next", pathname)
    const response = NextResponse.redirect(url)
    if (invalidSession) clearSession(response)
    return response
  }

  if (isAuthPage && validSession && pathname !== "/reset-password") {
    const url = request.nextUrl.clone()
    url.pathname = "/dashboard"
    url.search = ""
    return NextResponse.redirect(url)
  }

  const response = NextResponse.next()
  if (invalidSession) clearSession(response)
  return response
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
}
