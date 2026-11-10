import { Suspense } from "react";
import type { Metadata } from "next";
import Link from "next/link";

import { SignInForm } from "./sign-in-form";

export const metadata: Metadata = {
  title: "Sign in · Qualifyr",
  // Nothing here should ever appear in a search result.
  robots: { index: false, follow: false },
};

export default function SignInPage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Welcome back</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Sign in to your account to continue.
      </p>

      <div className="mt-8">
        {/* useSearchParams needs a Suspense boundary or the whole route opts out of
            static rendering. */}
        <Suspense fallback={<div className="h-64" />}>
          <SignInForm />
        </Suspense>
      </div>

      <p className="mt-8 text-sm text-muted-foreground">
        Don&rsquo;t have an account?{" "}
        <Link href="/sign-up" className="text-brand underline-offset-4 hover:underline">
          Sign up
        </Link>
        .
      </p>

      <Link
        href="/"
        className="mt-6 inline-block text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        ← Back to home
      </Link>
    </div>
  );
}
