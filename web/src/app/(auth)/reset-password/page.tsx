import { Suspense } from "react"
import type { Metadata } from "next"
import Link from "next/link"

import { ResetPasswordForm } from "./reset-password-form"

export const metadata: Metadata = {
  title: "Reset password - Qualifyr",
  robots: { index: false, follow: false },
}

export default function ResetPasswordPage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Set new password</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Choose a new password for your account.
      </p>

      <div className="mt-8">
        <Suspense fallback={<div className="h-64" />}>
          <ResetPasswordForm />
        </Suspense>
      </div>

      <Link
        href="/sign-in"
        className="mt-6 inline-block text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        &larr; Back to sign in
      </Link>
    </div>
  )
}
