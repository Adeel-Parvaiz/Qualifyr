import { Search, Gauge, Sparkles, ShieldCheck, Receipt, BadgeCheck, TrendingUp } from "lucide-react"

const flow = [
  { icon: Search, title: "Find buyers", text: "Vendors and agencies rejected up front." },
  { icon: Gauge, title: "Score with reasons", text: "0 to 100, every point explained." },
  { icon: Sparkles, title: "AI drafts the proposal", text: "Only from facts we actually stored." },
  { icon: ShieldCheck, title: "You approve", text: "Nothing is sent or billed without you." },
  { icon: Receipt, title: "PayPal invoice", text: "Created and linked in your email." },
  { icon: BadgeCheck, title: "Webhook confirms", text: "Signature-verified payment events." },
  { icon: TrendingUp, title: "Scoring learns", text: "Weights tuned by who actually paid." },
]

export function LeadToCash() {
  return (
    <section id="lead-to-cash" className="py-16 sm:py-20">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <p className="text-center text-xs font-medium uppercase tracking-wide text-brand">New: Qualifyr x PayPal</p>
        <div className="mx-auto mt-3 max-w-2xl text-center">
          <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">A lead engine trained by real payments</h2>
          <p className="mt-4 text-muted-foreground">
            Most lead scores are guesses nobody ever checks. Qualifyr follows each lead all the way to a paid PayPal invoice, then
            uses what actually got paid to make the next score more accurate.
          </p>
        </div>

        <ol className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-7">
          {flow.map((s, i) => (
            <li key={s.title} className="relative flex flex-col items-center rounded-2xl border border-border/70 bg-card p-4 text-center shadow-sm">
              <span className="flex size-10 items-center justify-center rounded-xl bg-brand-muted text-brand">
                <s.icon className="size-5" />
              </span>
              <span className="mt-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Step {i + 1}</span>
              <h3 className="mt-1 text-sm font-semibold leading-snug">{s.title}</h3>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{s.text}</p>
            </li>
          ))}
        </ol>

        <p className="mx-auto mt-8 max-w-2xl text-center text-xs text-muted-foreground">
          Built for the PayPal AI Hackathon 2026. Runs entirely against the PayPal sandbox; no real money moves.
        </p>
      </div>
    </section>
  )
}
