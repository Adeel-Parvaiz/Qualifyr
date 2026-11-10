import { Badge } from "@/components/ui/badge"
import { api, type CompanyType, type SequenceStatus } from "@/lib/api"

export function TypeBadge({ type }: { type: CompanyType }) {
  const variant = type === "BUYER" ? "default" : type === "VENDOR" ? "destructive" : "outline"
  return <Badge variant={variant}>{type}</Badge>
}

export function ScoreBadge({ score }: { score: number }) {
  const variant = score >= 80 ? "default" : score >= 70 ? "secondary" : "outline"
  return <Badge variant={variant} className="tabular-nums">{score}</Badge>
}

const STATUS_LABEL: Record<SequenceStatus, string> = {
  not_queued: "Not queued", queued: "Queued", email_1_sent: "Email 1 sent",
  followup_1_sent: "Follow-up 1 sent", followup_2_sent: "Sequence done", completed: "Done",
  replied: "Replied", bounced: "Bounced", unsubscribed: "Unsubscribed", suppressed: "Suppressed",
}

export function StatusBadge({ status }: { status: SequenceStatus }) {
  const variant =
    status === "replied" ? "default"
    : status === "bounced" || status === "unsubscribed" || status === "suppressed" ? "destructive"
    : status === "not_queued" ? "outline" : "secondary"
  return <Badge variant={variant}>{STATUS_LABEL[status] ?? status}</Badge>
}

const REPLY_LABEL: Record<string, { text: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  interested: { text: "Interested", variant: "default" },
  not_interested: { text: "Not interested", variant: "destructive" },
  out_of_office: { text: "Out of office", variant: "secondary" },
  wrong_person: { text: "Wrong person", variant: "secondary" },
  unsubscribe: { text: "Unsubscribed", variant: "destructive" },
  auto_reply: { text: "Auto-reply", variant: "outline" },
  reply: { text: "Replied", variant: "default" },
}

export function ReplyLabelBadge({ label }: { label: string | null }) {
  if (!label) return null
  const cfg = REPLY_LABEL[label] ?? { text: label, variant: "outline" as const }
  return <Badge variant={cfg.variant}>{cfg.text}</Badge>
}

export function ReviewButtons({ leadId, verdict, onChange }: { leadId: string; verdict: string | null; onChange: (v: string | null) => void }) {
  const opts: [string, string][] = [["correct", "Correct"], ["wrong_company", "Wrong company"], ["wrong_person", "Wrong person"], ["wrong_email", "Wrong email"]]
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="mr-1 text-xs text-muted-foreground">Reviewer:</span>
      {opts.map(([v, label]) => (
        <button
          key={v}
          onClick={async () => { const next = verdict === v ? "clear" : v; const r = await api.review(leadId, next); onChange(r.review_verdict) }}
          className={
            "rounded-full border px-2 py-0.5 text-xs transition-colors " +
            (verdict === v ? (v === "correct" ? "border-primary bg-primary text-primary-foreground" : "border-destructive bg-destructive/10 text-destructive") : "border-border text-muted-foreground hover:bg-muted")
          }
        >{label}</button>
      ))}
    </div>
  )
}

export function EmailStatusBadge({ status }: { status: string }) {
  const variant = status === "deliverable" || status === "mx_valid" ? "default"
    : status === "generic" ? "secondary"
    : status === "invalid" || status === "risky" ? "destructive" : "outline"
  return <Badge variant={variant}>{status.replace("_", " ")}</Badge>
}
