import Link from "next/link"

export const metadata = {
  title: "Privacy Policy – Qualifyr",
}

const EFFECTIVE_DATE = "October 10, 2026"
const SUPPORT_EMAIL = "hello@grydin.co"

export default function PrivacyPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6 sm:py-20">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Privacy Policy</h1>
      <p className="mt-2 text-sm text-muted-foreground">Effective {EFFECTIVE_DATE}</p>

      <div className="mt-10 space-y-8 text-sm leading-relaxed text-muted-foreground [&_h2]:mb-2 [&_h2]:mt-8 [&_h2]:font-heading [&_h2]:text-base [&_h2]:font-medium [&_h2]:text-foreground [&_p]:mt-2 [&_li]:mt-1">
        <p>
          This Privacy Policy explains what information Qualifyr (&ldquo;the Service&rdquo;,
          &ldquo;we&rdquo;, &ldquo;us&rdquo;), operated by GrydIn, collects and how it&rsquo;s used.
        </p>

        <section>
          <h2>1. Information we collect</h2>
          <ul className="list-disc space-y-1 pl-5">
            <li>Account information: name, email, and authentication details when you sign up.</li>
            <li>
              Campaign input: what you describe wanting to find (your offer, target region, and
              any filters you set).
            </li>
            <li>
              API keys you choose to connect (e.g. your own Google Places key), stored encrypted
              and used only to run your campaigns.
            </li>
            <li>
              Usage data: which features you use, so we can keep the Service reliable and enforce
              plan limits.
            </li>
          </ul>
        </section>

        <section>
          <h2>2. Information we generate</h2>
          <p>
            Running a campaign produces leads built from public sources (maps, business listings,
            public web pages) and, optionally, third-party enrichment APIs you&rsquo;ve connected.
            This is business data about companies, not personal data about you.
          </p>
        </section>

        <section>
          <h2>3. How we use information</h2>
          <p>
            To run the Service you&rsquo;ve asked for (discovering, scoring, and exporting leads),
            to operate your account and billing, to improve relevance and scoring, and to
            communicate with you about your account. We don&rsquo;t sell your personal information.
          </p>
        </section>

        <section>
          <h2>4. Outreach you send</h2>
          <p>
            If you use the outreach feature, drafts are sent from your own connected mailbox after
            your explicit approval. We don&rsquo;t send anything automatically, and we don&rsquo;t
            use your outreach content for any purpose beyond providing the Service to you.
          </p>
        </section>

        <section>
          <h2>5. Data retention</h2>
          <p>
            Campaigns and leads are kept for as long as your account is active so that run history
            remains available to you. You can delete a campaign or your account at any time;
            deletion removes the associated data within a reasonable period.
          </p>
        </section>

        <section>
          <h2>6. Third parties</h2>
          <p>
            We use infrastructure providers (hosting, database, authentication) to run the
            Service, and – where you connect them – third-party data or enrichment APIs. These
            providers only process data as needed to deliver the Service.
          </p>
        </section>

        <section>
          <h2>7. Your rights</h2>
          <p>
            You can access, correct, export, or delete your account data at any time from
            Settings, or by contacting us directly.
          </p>
        </section>

        <section>
          <h2>8. Changes to this policy</h2>
          <p>
            We may update this Policy from time to time. Continued use of the Service after an
            update constitutes acceptance of the revised Policy.
          </p>
        </section>

        <section>
          <h2>9. Contact</h2>
          <p>
            Questions about this Policy?{" "}
            <a href={`mailto:${SUPPORT_EMAIL}`} className="text-brand underline-offset-4 hover:underline">
              {SUPPORT_EMAIL}
            </a>
          </p>
        </section>
      </div>

      <p className="mt-12 text-sm">
        <Link href="/terms" className="text-brand underline-offset-4 hover:underline">
          Terms of Service
        </Link>
      </p>
    </div>
  )
}
