import type { Metadata } from "next";
import Link from "next/link";

import { SignUpForm } from "./sign-up-form";

export const metadata: Metadata = {
  title: "Create account · Qualifyr",
  robots: { index: false, follow: false },
};

export default function SignUpPage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Create your account</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Start finding qualified buyers in a few minutes.
      </p>

      <div className="mt-8">
        <SignUpForm />
      </div>

      <p className="mt-8 text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/sign-in" className="text-brand underline-offset-4 hover:underline">
          Sign in
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
