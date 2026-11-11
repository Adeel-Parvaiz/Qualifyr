import { applicationDefault, cert, getApps, initializeApp } from "firebase-admin/app"
import { getAuth } from "firebase-admin/auth"
import { readFileSync } from "node:fs"

// Use a distinct app so development hot reload cannot retain the old ADC signer.
const APP_NAME = "qualifyr-local-signing-v1"

export function createAdminAuth() {
  const projectId = process.env.FIREBASE_PROJECT_ID || process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID
  if (!projectId) throw new Error("FIREBASE_PROJECT_ID is not configured.")

  const existing = getApps().find((app) => app.name === APP_NAME)
  if (existing) {
    if (existing.options.projectId !== projectId) {
      throw new Error("Firebase Admin project does not match FIREBASE_PROJECT_ID.")
    }
    return getAuth(existing)
  }

  const rawServiceAccount = process.env.FIREBASE_SERVICE_ACCOUNT_JSON
  const credentialFile = process.env.GOOGLE_APPLICATION_CREDENTIALS
  const serviceAccount = process.env.FIREBASE_AUTH_EMULATOR_HOST
    ? null
    : rawServiceAccount
      ? JSON.parse(rawServiceAccount)
      : credentialFile
        ? JSON.parse(readFileSync(credentialFile, "utf8"))
        : null
  const credential = process.env.FIREBASE_AUTH_EMULATOR_HOST
    ? undefined
    : serviceAccount?.type === "service_account"
      ? cert(serviceAccount)
      : applicationDefault()
  const app = initializeApp({ credential, projectId }, APP_NAME)
  return getAuth(app)
}
