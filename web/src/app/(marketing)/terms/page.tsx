import Link from "next/link"

export const metadata = {
  title: "Terms of Service – Qualifyr",
}

const EFFECTIVE_DATE = "October 10, 2026"
const SUPPORT_EMAIL = "hello@grydin.co"

export default function TermsPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6 sm:py-20">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Terms of Service</h1>
      <p className="mt-2 text-sm text-muted-foreground">Effective {EFFECTIVE_DATE}</p>

      <div className="mt-10 space-y-8 text-sm leading-relaxed text-muted-foreground [&_h2]:mb-2 [&_h2]:mt-8 [&_h2]:font-heading [&_h2]:text-base [&_h2]:font-medium [&_h2]:text-foreground [&_p]:mt-2">
        <p>
          These Terms of Service (&ldquo;Terms&rdquo;) govern your use of Qualifyr (&ldquo;the
          Service&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;), operated by GrydIn. By creating an
          account or using the Service, you agree to these Terms.
        </p>

        <section>
          <h2>1. The service</h2>
          <p>
            Qualifyr is a market-research and lead-discovery tool. It searches public sources,
            scores the results, and surfaces the evidence behind each score. It does not guarantee
            any specific outcome, response rate, or sale.
          </p>
        </section>

        <section>
          <h2>2. Your account</h2>
          <p>
            You&rsquo;re responsible for the accuracy of the information you provide and for
            keeping your account credentials secure. You must be authorized to use the Service on
            behalf of any company you represent.
          </p>
        </section>

        <section>
          <h2>3. Acceptable use</h2>
          <p>
            You may not use the Service to send unsolicited bulk communication, to scrape or
            resell the underlying data at scale, to violate the acceptable-use terms of any
            third-party source the Service relies on, or to break any applicable law. Outreach
            sent through the Service is sent from your own mailbox, under your own responsibility
            for compliance with anti-spam and data-protection law in your jurisdiction.
          </p>
        </section>

        <section>
          <h2>4. Plans, billing, and refunds</h2>
          <p>
            Some features are offered on paid plans. Fees are billed in advance for the period
            selected and are non-refundable, including for partial periods, unused usage, or early
            cancellation. You can cancel at any time; cancellation stops future billing but does
            not refund the current period. We may change pricing or plan features with notice
            posted on the Service.
          </p>
        </section>

        <section>
          <h2>5. Data sources and accuracy</h2>
          <p>
            Qualifyr draws on public sources (such as map data, business listings, and public web
            pages) and, where applicable, third-party APIs. We take reasonable care, but we don&rsquo;t
            warrant that any company, contact, or score returned by the Service is complete,
            current, or error-free. You&rsquo;re responsible for verifying anything before relying
            on it commercially.
          </p>
        </section>

        <section>
          <h2>6. Termination</h2>
          <p>
            We may suspend or terminate access for violation of these Terms. You may stop using
            the Service and delete your account at any time.
          </p>
        </section>

        <section>
          <h2>7. Disclaimer and limitation of liability</h2>
          <p>
            The Service is provided &ldquo;as is&rdquo;, without warranties of any kind. To the
            maximum extent permitted by law, we are not liable for indirect, incidental, or
            consequential damages, or for any amount exceeding what you paid us in the preceding
            three months.
          </p>
        </section>

        <section>
          <h2>8. Changes to these terms</h2>
          <p>
            We may update these Terms from time to time. Continued use of the Service after an
            update constitutes acceptance of the revised Terms.
          </p>
        </section>

        <section>
          <h2>9. Contact</h2>
          <p>
            Questions about these Terms?{" "}
            <a href={`mailto:${SUPPORT_EMAIL}`} className="text-brand underline-offset-4 hover:underline">
              {SUPPORT_EMAIL}
            </a>
          </p>
        </section>
      </div>

      <p className="mt-12 text-sm">
        <Link href="/privacy" className="text-brand underline-offset-4 hover:underline">
          Privacy Policy
        </Link>
      </p>
    </div>
  )
}
