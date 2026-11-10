"use client"

import * as React from "react"
import { Sparkles, ShieldCheck, Receipt, ExternalLink, RefreshCw, Loader2, CheckCircle2, FlaskConical } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { api, type Invoice, type LineItem, type PayPalCheckout, type Proposal } from "@/lib/api"
import { formatMoney, parseMoney } from "@/lib/money"
import { InvoiceStatusBadge } from "@/components/invoice-status-badge"

interface Props {
  leadId: string
  locked: boolean
  /** Save the proposal text into the outreach email draft (so the link lands in the real email). */
  onUseText: (subject: string, body: string) => Promise<void>
  /** Reload the outreach draft after the server appended the PayPal link. */
  onAttached: () => Promise<void>
}

type Row = { name: string; description: string; quantity: string; price: string }

const toRows = (items: LineItem[]): Row[] =>
  items.map((i) => ({ name: i.name, description: i.description, quantity: String(i.quantity), price: (i.unit_amount_cents / 100).toFixed(2) }))

/** Lead -> proposal -> human approval -> PayPal invoice, in the outreach editor. */
export function PayPalInvoicePanel({ leadId, locked, onUseText, onAttached }: Props) {
  const [loading, setLoading] = React.useState(true)
  const [proposal, setProposal] = React.useState<Proposal | null>(null)
  const [invoice, setInvoice] = React.useState<Invoice | null>(null)
  const [checkout, setCheckout] = React.useState<PayPalCheckout | null>(null)
  const [subject, setSubject] = React.useState("")
  const [body, setBody] = React.useState("")
  const [rows, setRows] = React.useState<Row[]>([])
  const [currency, setCurrency] = React.useState("USD")
  const [basePrice, setBasePrice] = React.useState("500")
  const [useAsEmail, setUseAsEmail] = React.useState(true)
  const [busy, setBusy] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [note, setNote] = React.useState<string | null>(null)

  const hydrate = React.useCallback((p: Proposal | null) => {
    setProposal(p)
    if (p) { setSubject(p.subject); setBody(p.body); setRows(toRows(p.line_items)); setCurrency(p.currency) }
  }, [])

  React.useEffect(() => {
    let alive = true
    api.getProposal(leadId)
      .then((r) => { if (alive) { hydrate(r.proposal); setInvoice(r.invoice); setCheckout(r.checkout) } })
      .catch((e) => { if (alive) setError((e as Error).message) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [leadId, hydrate])

  const parsedItems = React.useMemo(() => {
    const out: LineItem[] = []
    for (const r of rows) {
      const cents = parseMoney(r.price)
      const qty = Number(r.quantity)
      if (cents === null || !Number.isInteger(qty) || qty < 1 || !r.name.trim()) return null
      out.push({ name: r.name.trim(), description: r.description, quantity: qty, unit_amount_cents: cents })
    }
    return out.length ? out : null
  }, [rows])
  const total = parsedItems?.reduce((s, i) => s + i.unit_amount_cents * i.quantity, 0) ?? 0

  const dirty = !!proposal && (
    subject !== proposal.subject || body !== proposal.body || currency !== proposal.currency ||
    JSON.stringify(parsedItems) !== JSON.stringify(proposal.line_items)
  )
  const outOfRange = !!proposal && parsedItems !== null && (total < proposal.range_min_cents || total > proposal.range_max_cents)

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError(null); setNote(null)
    try { await fn() } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }

  const draft = () => run("draft", async () => {
    const price = parseMoney(basePrice)
    if (price === null || price < 100) throw new Error("Enter your base price, e.g. 500 or 499.99")
    const r = await api.createProposal(leadId, { base_price: price / 100, currency })
    hydrate(r.proposal)
  })

  const save = async (): Promise<Proposal> => {
    if (!proposal || !parsedItems) throw new Error("Check the line items: every row needs a name, a whole quantity and a valid price.")
    const r = await api.editProposal(leadId, proposal.proposal_id, { subject, body, currency, line_items: parsedItems })
    hydrate(r.proposal)
    return r.proposal
  }

  const approve = () => run("approve", async () => {
    if (!proposal) return
    const p = dirty ? await save() : proposal
    const r = await api.approveProposal(leadId, p.proposal_id)
    hydrate(r.proposal)
    setNote("Terms approved. Nothing has been billed yet.")
  })

  const createInvoice = () => run("invoice", async () => {
    if (!proposal || proposal.status !== "approved") return
    if (useAsEmail) await onUseText(proposal.subject, proposal.body)
    const r = await api.createInvoice(leadId, proposal.proposal_id)
    setInvoice(r.invoice)
    if (r.link_attached) await onAttached()
    setNote(r.link_attached
      ? "Invoice created in PayPal. The payment link was added to the email below; review it and approve the email."
      : "Invoice created. There was no pending email draft to attach the link to; copy the link below.")
    const fresh = await api.getProposal(leadId)
    hydrate(fresh.proposal); setCheckout(fresh.checkout)
  })

  const createOrder = () => run("order", async () => {
    if (!proposal || proposal.status !== "approved") return
    const r = await api.createPayPalOrder(leadId, proposal.proposal_id)
    setCheckout(r.checkout)
  })

  const refreshOrder = () => run("order-status", async () => {
    if (!checkout) return
    const r = await api.refreshPayPalOrder(checkout.checkout_id)
    setCheckout(r.checkout)
  })

  const refresh = () => run("refresh", async () => {
    if (!invoice) return
    const r = await api.refreshInvoice(invoice.invoice_id)
    setInvoice(r.invoice)
  })

  const setRow = (i: number, patch: Partial<Row>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  return (
    <div className="rounded-xl border border-border/70 bg-muted/30 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Receipt className="size-4 text-brand" /> PayPal invoice
          <Badge variant="outline" className="gap-1 text-amber-700 dark:text-amber-400"><FlaskConical /> Sandbox</Badge>
        </div>
        {proposal && <span className="text-xs text-muted-foreground">
          {proposal.source === "llm" ? "AI-drafted, checked against stored evidence" : "Drafted from stored evidence"}
        </span>}
      </div>

      {loading ? (
        <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading…</p>
      ) : invoice ? (
        <div className="mt-3 grid gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <InvoiceStatusBadge status={invoice.status} />
            <span className="text-lg font-semibold tabular-nums">{formatMoney(invoice.amount_cents, invoice.currency)}</span>
            <span className="font-mono text-xs text-muted-foreground">{invoice.invoice_number}</span>
            <span className="text-xs text-muted-foreground">Score at send: {invoice.score_at_send}</span>
          </div>
          {invoice.payer_url && (
            <a href={invoice.payer_url} target="_blank" rel="noreferrer" className="inline-flex w-fit items-center gap-1.5 text-sm underline underline-offset-2">
              <ExternalLink className="size-3.5" /> Open the payer page (what your client sees)
            </a>
          )}
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={refresh} disabled={!!busy}>
              {busy === "refresh" ? <Loader2 className="animate-spin" /> : <RefreshCw />} Check status
            </Button>
          </div>
        </div>
      ) : checkout ? (
        <div className="mt-3 grid gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant={checkout.status === "PAID" ? "default" : "outline"}>{checkout.status.replaceAll("_", " ")}</Badge>
            <span className="text-lg font-semibold tabular-nums">{formatMoney(checkout.amount_cents, checkout.currency)}</span>
          </div>
          {checkout.approval_url && checkout.status !== "PAID" && (
            <a href={checkout.approval_url} target="_blank" rel="noreferrer" className="inline-flex w-fit items-center gap-1.5 text-sm underline underline-offset-2">
              <ExternalLink className="size-3.5" /> Continue to PayPal Sandbox
            </a>
          )}
          <Button variant="outline" size="sm" onClick={refreshOrder} disabled={!!busy}>
            {busy === "order-status" ? <Loader2 className="animate-spin" /> : <RefreshCw />} Check payment status
          </Button>
          <p className="text-xs text-muted-foreground">Checkout is tied to these approved terms. Qualifyr confirms payment with PayPal before marking it paid.</p>
        </div>
      ) : !proposal ? (
        <div className="mt-3 grid gap-3">
          <p className="text-sm text-muted-foreground">
            The agent drafts a proposal and invoice from this lead&apos;s stored evidence. Tell it your base price; nothing is sent or billed until you approve.
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1">
              <label className="text-xs text-muted-foreground">Your base price</label>
              <Input value={basePrice} onChange={(e) => setBasePrice(e.target.value)} className="w-32" inputMode="decimal" />
            </div>
            <div className="grid gap-1">
              <label className="text-xs text-muted-foreground">Currency</label>
              <select value={currency} onChange={(e) => setCurrency(e.target.value)} className="h-8 rounded-lg border border-input bg-background px-2 text-sm">
                {["USD", "EUR", "GBP"].map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <Button onClick={draft} disabled={locked || !!busy}>
              {busy === "draft" ? <Loader2 className="animate-spin" /> : <Sparkles />} Draft proposal with AI
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 grid gap-3">
          <div className="grid gap-1">
            <label className="text-xs text-muted-foreground">Proposal subject</label>
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} disabled={proposal.status === "invoiced" || !!proposal.payment_method} />
          </div>
          <div className="grid gap-1">
            <label className="text-xs text-muted-foreground">Proposal email</label>
            <Textarea value={body} onChange={(e) => setBody(e.target.value)} rows={8} className="text-[13px]" disabled={proposal.status === "invoiced" || !!proposal.payment_method} />
          </div>

          <div className="grid gap-2">
            <label className="text-xs text-muted-foreground">Invoice line items</label>
            {rows.map((r, i) => (
              <div key={i} className="grid grid-cols-[1fr_4rem_7rem] gap-2">
                <Input value={r.name} onChange={(e) => setRow(i, { name: e.target.value })} aria-label="Item name" disabled={!!proposal.payment_method} />
                <Input value={r.quantity} onChange={(e) => setRow(i, { quantity: e.target.value })} aria-label="Quantity" inputMode="numeric" disabled={!!proposal.payment_method} />
                <Input value={r.price} onChange={(e) => setRow(i, { price: e.target.value })} aria-label="Unit price" inputMode="decimal" disabled={!!proposal.payment_method} />
              </div>
            ))}
            <div className="flex flex-wrap items-center justify-between text-sm">
              <span className="text-muted-foreground">Suggested range {formatMoney(proposal.range_min_cents, proposal.currency)} – {formatMoney(proposal.range_max_cents, proposal.currency)}</span>
              <span className="font-semibold tabular-nums">Total {parsedItems ? formatMoney(total, currency) : "—"}</span>
            </div>
            {outOfRange && <p className="text-xs text-amber-700 dark:text-amber-400">This total is outside the suggested range. You can still approve it; just confirm it is what you mean to bill.</p>}
          </div>
          <p className="text-xs text-muted-foreground">{proposal.rationale}</p>

          {proposal.status === "approved" && (
            <p className="flex items-center gap-1.5 text-sm text-green-700 dark:text-green-400">
              <CheckCircle2 className="size-4" /> Terms approved. Any edit will require approving again.
            </p>
          )}
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input type="checkbox" checked={useAsEmail} onChange={(e) => setUseAsEmail(e.target.checked)} />
            Use this proposal as the email text (the PayPal link is added to it)
          </label>
          <div className="flex flex-wrap gap-2">
            {proposal.status !== "approved" || dirty ? (
              <Button onClick={approve} disabled={!!busy || !parsedItems || proposal.status === "invoiced" || !!proposal.payment_method}>
                {busy === "approve" ? <Loader2 className="animate-spin" /> : <ShieldCheck />} {dirty ? "Save & approve terms" : "Approve terms"}
              </Button>
            ) : (
              <>
                <Button onClick={createInvoice} disabled={locked || !!busy}>
                  {busy === "invoice" ? <Loader2 className="animate-spin" /> : <Receipt />} Create PayPal invoice &amp; attach link
                </Button>
                <Button variant="outline" onClick={createOrder} disabled={!!busy}>
                  {busy === "order" ? <Loader2 className="animate-spin" /> : <ExternalLink />} One-time PayPal checkout
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {note && <p className="mt-3 text-sm text-green-700 dark:text-green-400">{note}</p>}
      {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
    </div>
  )
}
