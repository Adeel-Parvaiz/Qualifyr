"use client"

import * as React from "react"
import { Wallet, Receipt, TrendingUp, Sparkles, RefreshCw, FlaskConical, ExternalLink, Loader2, Check, X, Trash2 } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { api, type CalibrationRun, type Invoice, type PayPalStatus, type RevenueSummary } from "@/lib/api"
import { formatMoney } from "@/lib/money"
import { useCampaign } from "@/components/campaign-context"
import { InvoiceStatusBadge } from "@/components/invoice-status-badge"
import { cn } from "@/lib/utils"

const chartConfig = {
  rate: { label: "Paid rate", theme: { light: "var(--chart-1)", dark: "var(--chart-1)" } },
}

function Kpi({ title, value, hint, icon: Icon, highlight }: {
  title: string; value: string; hint: string; icon: React.ElementType; highlight?: boolean
}) {
  return (
    <Card className={cn("transition-shadow hover:shadow-md", highlight && "border-brand/30 bg-brand-muted/40")}>
      <CardContent className="p-5">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-muted-foreground">{title}</span>
          <span className={cn("flex size-8 items-center justify-center rounded-lg", highlight ? "bg-brand text-brand-foreground" : "bg-muted text-muted-foreground")}>
            <Icon className="size-4" />
          </span>
        </div>
        <div className="mt-4 text-3xl font-semibold tracking-tight tabular-nums">{value}</div>
        <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{hint}</p>
      </CardContent>
    </Card>
  )
}

function WeightBar({ label, from, to }: { label: string; from: number; to: number }) {
  const delta = to - from
  return (
    <div className="grid gap-1">
      <div className="flex items-center justify-between text-sm">
        <span>{label}</span>
        <span className="tabular-nums text-muted-foreground">
          {from}{delta !== 0 && <> → <span className={cn("font-semibold", delta > 0 ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400")}>{to}</span></>}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-brand transition-all" style={{ width: `${Math.min(100, to * 3)}%` }} />
      </div>
    </div>
  )
}

function CalibrationCard({ campaignId, onChanged }: { campaignId: string; onChanged: () => void }) {
  const [runs, setRuns] = React.useState<CalibrationRun[]>([])
  const [busy, setBusy] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const load = React.useCallback(() => {
    api.calibrationRuns(campaignId).then((r) => setRuns(r.runs)).catch((e) => setError((e as Error).message))
  }, [campaignId])
  React.useEffect(() => { load() }, [load])

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setError(null)
    try { await fn(); load(); onChanged() } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }
  const latest = runs[0]
  const res = latest?.result
  const labels = Object.fromEntries((res?.dimension_stats ?? []).map((d) => [d.dimension, d.label]))

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><TrendingUp className="size-4 text-brand" /> Score calibration</CardTitle>
            <CardDescription>Learns which score dimensions actually predict a paid invoice, and proposes new weights. Never applied without your OK.</CardDescription>
          </div>
          <Button onClick={() => act("run", () => api.runCalibration(campaignId))} disabled={!!busy}>
            {busy === "run" ? <Loader2 className="animate-spin" /> : <Sparkles />} Run calibration
          </Button>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!latest ? (
          <p className="text-sm text-muted-foreground">No calibration yet. Run it once you have resolved invoices (paid, cancelled, or unpaid for 14+ days).</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant={latest.status === "proposed" ? "default" : "secondary"}>{latest.status.replace("_", " ")}</Badge>
              <span>{latest.result.sample_size} resolved invoices · {latest.result.paid_count} paid · {(latest.result.base_rate * 100).toFixed(0)}% paid</span>
              {latest.includes_synthetic ? <Badge variant="outline" className="gap-1 text-amber-700 dark:text-amber-400"><FlaskConical /> includes synthetic demo data</Badge> : null}
            </div>
            <p className="rounded-lg bg-muted/50 p-3 text-sm leading-relaxed">{latest.explanation}</p>
            {latest.result.status === "proposed" && (
              <div className="grid gap-3 sm:grid-cols-2">
                {Object.keys(latest.result.current_weights).map((k) => (
                  <WeightBar key={k} label={labels[k] ?? k} from={latest.result.current_weights[k]} to={latest.result.proposed_weights[k]} />
                ))}
              </div>
            )}
            {latest.status === "proposed" && (
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => act("apply", () => api.applyCalibration(latest.run_id))} disabled={!!busy}>
                  {busy === "apply" ? <Loader2 className="animate-spin" /> : <Check />} Accept new weights
                </Button>
                <Button variant="outline" onClick={() => act("reject", () => api.rejectCalibration(latest.run_id))} disabled={!!busy}>
                  <X /> Reject
                </Button>
                <span className="self-center text-xs text-muted-foreground">Applies to future scoring runs. Past leads and the invoice ledger keep their original scores.</span>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

export default function RevenuePage() {
  const { campaignId } = useCampaign()
  const [status, setStatus] = React.useState<PayPalStatus | null>(null)
  const [summary, setSummary] = React.useState<RevenueSummary | null>(null)
  const [invoices, setInvoices] = React.useState<Invoice[]>([])
  const [loading, setLoading] = React.useState(true)
  const [busy, setBusy] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const load = React.useCallback(async () => {
    if (!campaignId) return
    const [st, rev, inv] = await Promise.allSettled([api.paypalStatus(), api.revenue(campaignId), api.invoices(campaignId)])
    if (st.status === "fulfilled") setStatus(st.value)
    if (rev.status === "fulfilled") setSummary(rev.value)
    if (inv.status === "fulfilled") setInvoices(inv.value.invoices)
    const failed = [rev, inv].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined
    setError(failed ? (failed.reason as Error).message : null)
    setLoading(false)
  }, [campaignId])
  // This loader only updates state after awaiting the API response.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  React.useEffect(() => { void load() }, [load])
  React.useEffect(() => {
    const timer = setInterval(() => { void load() }, 15000)
    const refresh = () => { void load() }
    window.addEventListener("focus", refresh)
    return () => { clearInterval(timer); window.removeEventListener("focus", refresh) }
  }, [load])

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setError(null)
    try { await fn(); await load() } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }

  const paidMoney = summary
    ? Object.entries(summary.paid_cents_by_currency).map(([c, v]) => formatMoney(v, c)).join(" + ") || formatMoney(0, "USD")
    : "—"
  const open = summary ? ["SENT", "DRAFT", "CREATING", "PENDING", "APPROVED", "CAPTURING", "CAPTURE_PENDING"].reduce((n, status) => n + (summary.by_status[status] ?? 0), 0) : 0
  const bandData = (summary?.score_bands ?? []).map((b) => ({ band: b.band, rate: b.rate === null ? 0 : Math.round(b.rate * 100), n: b.n }))
  const hasBands = bandData.some((b) => b.n > 0)

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Revenue</h1>
          <p className="text-muted-foreground">Track invoices and one-time checkouts. Revenue includes only payments confirmed with PayPal.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {status && (
            <Badge variant="outline" className={cn("gap-1", status.mode === "sandbox" ? "text-amber-700 dark:text-amber-400" : "text-red-700")}>
              <FlaskConical /> PayPal {status.mode}
            </Badge>
          )}
          <Button variant="outline" onClick={() => act("refresh", async () => undefined)} disabled={!!busy}><RefreshCw data-icon="inline-start" /> Refresh</Button>
        </div>
      </div>

      {status && !status.configured && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardContent className="p-4 text-sm">
            <span className="font-medium">PayPal is not connected on the server.</span> Set <code className="font-mono">GTM_PAYPAL_CLIENT_ID</code> and <code className="font-mono">GTM_PAYPAL_CLIENT_SECRET</code> (sandbox app credentials) and redeploy. See docs/PAYPAL.md.
          </CardContent>
        </Card>
      )}
      {status && status.configured && !status.webhook_configured && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardContent className="p-4 text-sm">
            Webhook not configured: payments will not update automatically. Set <code className="font-mono">GTM_PAYPAL_WEBHOOK_ID</code>, or use <span className="font-medium">Check status</span> on an invoice.
          </CardContent>
        </Card>
      )}
      {error && <p className="text-sm text-destructive">API error: {error}</p>}

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32" />)}</div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Kpi title="Revenue collected" value={paidMoney} hint="Paid invoices and one-time checkouts confirmed with PayPal." icon={Wallet} highlight />
          <Kpi title="Invoices & checkouts" value={String(summary?.total_payments ?? summary?.total_invoices ?? 0)} hint="Created from human-approved proposals." icon={Receipt} />
          <Kpi title="Awaiting payment" value={String(open)} hint="Sent, not yet paid or cancelled." icon={RefreshCw} />
          <Kpi title="Paid rate" value={`${((summary?.paid_rate ?? 0) * 100).toFixed(0)}%`} hint={`${summary?.paid_payments ?? summary?.paid_invoices ?? 0} paid of ${summary?.total_payments ?? summary?.total_invoices ?? 0}.`} icon={TrendingUp} />
        </div>
      )}

      <Card>
        <CardHeader><CardTitle>One-time checkouts</CardTitle></CardHeader>
        <CardContent>
          {!summary?.checkouts?.length ? <p className="text-sm text-muted-foreground">No checkouts for this campaign yet.</p> : (
            <Table>
              <TableHeader><TableRow><TableHead>Checkout</TableHead><TableHead>Status</TableHead><TableHead>Amount</TableHead><TableHead>Paid</TableHead></TableRow></TableHeader>
              <TableBody>{summary.checkouts.map((checkout) => (
                <TableRow key={checkout.checkout_id}>
                  <TableCell className="font-mono text-xs">{checkout.checkout_id}</TableCell>
                  <TableCell><Badge variant={checkout.status === "PAID" ? "default" : "outline"}>{checkout.status.replaceAll("_", " ")}</Badge></TableCell>
                  <TableCell>{formatMoney(checkout.amount_cents, checkout.currency)}</TableCell>
                  <TableCell>{checkout.paid_at ? new Date(checkout.paid_at).toLocaleString() : "Awaiting confirmation"}</TableCell>
                </TableRow>
              ))}</TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {summary && summary.synthetic_count > 0 && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
            <span><FlaskConical className="mr-1.5 inline size-4" /><span className="font-medium">{summary.synthetic_count} synthetic demo invoices</span> are included in these numbers. They are labelled below and never touched PayPal.</span>
            <Button variant="outline" size="sm" onClick={() => act("clear", () => api.clearDemo(campaignId!))} disabled={!!busy}><Trash2 /> Remove demo data</Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Score vs paid</CardTitle>
            <CardDescription>Share of invoices that got paid, by the lead&apos;s score when the invoice was sent.</CardDescription>
          </CardHeader>
          <CardContent>
            {hasBands ? (
              <ChartContainer config={chartConfig} className="h-[220px] w-full">
                <BarChart data={bandData} margin={{ left: -16, top: 4 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis dataKey="band" tickLine={false} axisLine={false} />
                  <YAxis tickLine={false} axisLine={false} unit="%" domain={[0, 100]} />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Bar dataKey="rate" fill="var(--color-rate)" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ChartContainer>
            ) : (
              <p className="py-10 text-center text-sm text-muted-foreground">No resolved invoices yet. Create one from the Outreach tab, or load demo data to see the loop.</p>
            )}
            {campaignId && (summary?.total_invoices ?? 0) === 0 && (
              <div className="mt-3 flex justify-center">
                <Button variant="outline" size="sm" onClick={() => act("seed", () => api.seedDemo(campaignId))} disabled={!!busy}>
                  {busy === "seed" ? <Loader2 className="animate-spin" /> : <FlaskConical />} Load synthetic demo data
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
        {campaignId && <CalibrationCard campaignId={campaignId} onChanged={load} />}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Invoices</CardTitle>
          <CardDescription>Each row keeps the score it had at send time; later re-scoring never rewrites it.</CardDescription>
        </CardHeader>
        <CardContent>
          {invoices.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">No invoices yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Invoice</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Amount</TableHead>
                  <TableHead className="text-right">Score at send</TableHead><TableHead>Created</TableHead><TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {invoices.slice(0, 50).map((inv) => (
                  <TableRow key={inv.invoice_id}>
                    <TableCell className="font-mono text-xs">
                      {inv.invoice_number ?? inv.invoice_id}
                      {inv.is_synthetic ? <Badge variant="outline" className="ml-2 text-amber-700 dark:text-amber-400">synthetic</Badge> : null}
                    </TableCell>
                    <TableCell><InvoiceStatusBadge status={inv.status} /></TableCell>
                    <TableCell className="text-right tabular-nums">{formatMoney(inv.amount_cents, inv.currency)}</TableCell>
                    <TableCell className="text-right tabular-nums">{inv.score_at_send}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{new Date(inv.created_at).toLocaleDateString()}</TableCell>
                    <TableCell className="text-right">
                      {inv.payer_url && (
                        <a href={inv.payer_url} target="_blank" rel="noreferrer" aria-label="Open payer page" className="inline-flex text-muted-foreground hover:text-foreground">
                          <ExternalLink className="size-4" />
                        </a>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
