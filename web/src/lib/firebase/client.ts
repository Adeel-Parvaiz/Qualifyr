"use client"

import { getApp, getApps, initializeApp } from "firebase/app"
import { getAuth, signInWithCustomToken, type User } from "firebase/auth"
import { FIREBASE_CONFIG, firebaseConfigured } from "./config"

export function createClient() {
  if (!firebaseConfigured) {
    throw new Error(
      "Firebase web configuration is missing. Set the NEXT_PUBLIC_FIREBASE_* variables " +
        "and rebuild the app."
    )
  }
  const app = getApps().length ? getApp() : initializeApp(FIREBASE_CONFIG)
  return getAuth(app)
}

let restoringSession: Promise<User | null> | undefined

/** Recover browser auth from the verified HttpOnly session after a reload. */
export async function signedInUser(): Promise<User | null> {
  const auth = createClient()
  await auth.authStateReady()
  if (auth.currentUser) return auth.currentUser
  if (!restoringSession) {
    restoringSession = (async () => {
      const response = await fetch("/api/auth/session", { cache: "no-store" })
      if (response.status === 401) return null
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.error === "string"
          ? body.error
          : `Session recovery failed (HTTP ${response.status}). Please retry.`)
      }
      const { customToken } = await response.json()
      return (await signInWithCustomToken(auth, customToken)).user
    })().finally(() => { restoringSession = undefined })
  }
  return restoringSession
}
