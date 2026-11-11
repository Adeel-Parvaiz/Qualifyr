import { NextResponse, type NextRequest } from "next/server"

import { createAdminAuth } from "@/lib/firebase/admin"
import { SESSION_COOKIE_NAME, SESSION_DURATION_MS } from "@/lib/firebase/session"

export async function GET(request: NextRequest) {
  const headers = { "Cache-Control": "no-store" }
  const cookie = request.cookies.get(SESSION_COOKIE_NAME)?.value
  if (!cookie) return NextResponse.json({ error: "Sign-in required." }, { status: 401, headers })

  try {
    const auth = createAdminAuth()
    // A custom token can only be minted for the UID in a verified, unrevoked cookie.
    const session = await auth.verifySessionCookie(cookie, true)
    const customToken = await auth.createCustomToken(session.uid)
    return NextResponse.json({ customToken }, { headers })
  } catch (error) {
    const code = error && typeof error === "object" && "code" in error ? error.code : null
    if (typeof code === "string" && [
      "auth/argument-error", "auth/invalid-session-cookie", "auth/session-cookie-expired",
      "auth/session-cookie-revoked", "auth/user-disabled", "auth/user-not-found",
    ].includes(code)) {
      return NextResponse.json({ error: "Sign-in required." }, { status: 401, headers })
    }
    // Expose only a bounded error code, never credential paths, cookies or tokens.
    const safeCode = typeof code === "string" && /^[a-zA-Z0-9_/-]{1,100}$/.test(code)
      ? code : "server-error"
    console.error("Firebase session recovery failed:", safeCode)
    return NextResponse.json({ error: `Session recovery failed (${safeCode}).` }, { status: 503, headers })
  }
}

export async function POST(request: NextRequest) {
  const body: unknown = await request.json()
  if (!body || typeof body !== "object" || !("idToken" in body) ||
      typeof body.idToken !== "string" || !body.idToken) {
    return NextResponse.json({ error: "A Firebase ID token is required." }, { status: 400 })
  }

  const sessionCookie = await createAdminAuth().createSessionCookie(body.idToken, {
    expiresIn: SESSION_DURATION_MS,
  })
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } })
  response.cookies.set(SESSION_COOKIE_NAME, sessionCookie, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_DURATION_MS / 1000,
  })
  return response
}

export async function DELETE() {
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } })
  response.cookies.set(SESSION_COOKIE_NAME, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  })
  return response
}
