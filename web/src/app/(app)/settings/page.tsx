"use client"

import * as React from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Ban, Trash2, Save, Plus, Key, BarChart3, FlaskConical, Eye, EyeOff, Power, Loader2, Wallet, Check, Mail } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, type MailboxState, type PricingTier, type Suppression, type UsageRow } from "@/lib/api"
import { useCampaign } from "@/components/campaign-context"
import { useTier } from "@/lib/use-tier"
import { TierBadge } from "@/components/tier-badge"
import { CreditsMeter } from "@/components/credits-meter"
import { cn } from "@/lib/utils"

const KEY_INFO: Record<string, { label: string; description: string; url: string }> = {
  brave: { label: "Brave Search", description: "Web search for company discovery. Falls back to DuckDuckGo without a key.", url: "https://brave.com/search/api/" },
  groq: { label: "Groq (LLM)", description: "AI-powered keyword generation, intent judging and relevance matching.", url: "https://console.groq.com/keys" },
  gemini: { label: "Google Gemini", description: "Alternative LLM provider. Used as fallback when Groq is unavailable.", url: "https://aistudio.google.com/apikey" },
  places: { label: "Google Places", description: "Rating, review count and opening hours enrichment. 1K free calls/month.", url: "https://console.cloud.google.com/apis/credentials" },
}

/** Shown while the server config is still being checked – so neither the "add" form nor the
 *  "not configured" warning flashes before we actually know the state. */
function CheckingConfig() {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" /> Checking server configuration…
    </div>
  )
}

/** Soft, informational notice when server-side encryption isn't set up. Replaces the old
 *  bare red line – only rendered after the config check completes and only when it's missing. */
function EncryptionNotice({ what }: { what: string }) {
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-700 dark:text-amber-400">
      <p className="font-medium">Encryption isn’t set up yet</p>
      <p className="mt-1 text-amber-700/90 dark:text-amber-400/90">
        {what} needs a server-side encryption key so your secrets are stored safely. Set{" "}
        <code className="font-mono text-xs">GTM_ENCRYPTION_KEY</code> in the server environment, then reload this page.
      </p>
    </div>
  )
}

function ApiKeys() {
  const { refreshKeys } = useCampaign()
  const [keys, setKeys] = React.useState<{ key_name: string; created_at: string }[]>([])
  const [encryptionAvailable, setEncryptionAvailable] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [inputs, setInputs] = React.useState<Record<string, string>>({})
  const [visible, setVisible] = React.useState<Record<string, boolean>>({})
  const [testing, setTesting] = React.useState<string | null>(null)
  const [testResult, setTestResult] = React.useState<Record<string, { ok: boolean; message: string }>>({})
  const [saving, setSaving] = React.useState<string | null>(null)

  const configured = React.useMemo(() => new Set(keys.map((k) => k.key_name)), [keys])

  const load = React.useCallback(() => {
    api.listApiKeys().then((r) => { setKeys(r.keys); setEncryptionAvailable(r.encryption_available) })
      .catch(() => {}).finally(() => setLoading(false))
  }, [])
  React.useEffect(() => { load() }, [load])

  const saveKey = async (name: string) => {
    const val = inputs[name]?.trim()
    if (!val) return
    setSaving(name)
    try {
      await api.saveApiKey(name, val)
      setInputs((p) => ({ ...p, [name]: "" }))
      load()
      refreshKeys()
    } catch { /* toast? */ } finally { setSaving(null) }
  }

  const deleteKey = async (name: string) => {
    if (!confirm(`Remove your ${KEY_INFO[name]?.label ?? name} API key?`)) return
    try { await api.deleteApiKey(name); load(); refreshKeys() } catch { /* */ }
  }

  const testKey = async (name: string) => {
    setTesting(name)
    setTestResult((p) => ({ ...p, [name]: { ok: false, message: "Testing..." } }))
    try {
      const r = await api.testApiKey(name)
      setTestResult((p) => ({ ...p, [name]: r }))
    } catch (e) {
      setTestResult((p) => ({ ...p, [name]: { ok: false, message: (e as Error).message } }))
    } finally { setTesting(null) }
  }

  return (
    <div className="grid gap-6">
      <Card>
        <CardHeader>
          <CardTitle>API Keys</CardTitle>
          <CardDescription>
            Add your own API keys to unlock premium features. All keys are encrypted at rest.
            Without keys, the engine uses free fallbacks (DuckDuckGo, MX-only verification, no LLM refinement).
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {loading ? <CheckingConfig /> : !encryptionAvailable ? <EncryptionNotice what="Saving your own API keys" /> :
          Object.entries(KEY_INFO).map(([name, info]) => (
            <div key={name} className="rounded-lg border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                <div className="flex items-center gap-2">
                  <Key className="h-4 w-4 text-muted-foreground" />
                  <span className="font-medium text-sm">{info.label}</span>
                  {configured.has(name)
                    ? <Badge className="text-xs">configured</Badge>
                    : <Badge variant="outline" className="text-xs">not configured</Badge>}
                </div>
                <a href={info.url} target="_blank" rel="noopener noreferrer" className="text-xs text-muted-foreground underline">
                  Get a key
                </a>
              </div>
              <p className="text-xs text-muted-foreground mb-3">{info.description}</p>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[200px] max-w-sm">
                  <Input
                    type={visible[name] ? "text" : "password"}
                    placeholder={configured.has(name) ? "••••••••" : "Paste your key"}
                    value={inputs[name] ?? ""}
                    onChange={(e) => setInputs((p) => ({ ...p, [name]: e.target.value }))}
                    disabled={!encryptionAvailable}
                    className="pr-8 font-mono text-xs"
                  />
                  <button
                    type="button"
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground"
                    onClick={() => setVisible((p) => ({ ...p, [name]: !p[name] }))}
                  >
                    {visible[name] ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                  </button>
                </div>
                <Button size="sm" onClick={() => saveKey(name)} disabled={!encryptionAvailable || !inputs[name]?.trim() || saving === name}>
                  <Save className="h-3.5 w-3.5 mr-1" /> Save
                </Button>
                {configured.has(name) && (
                  <>
                    <Button size="sm" variant="outline" onClick={() => testKey(name)} disabled={testing === name}>
                      <FlaskConical className="h-3.5 w-3.5 mr-1" /> Test
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => deleteKey(name)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </>
                )}
              </div>
              {testResult[name] && (
                <p className={cn("text-xs mt-2", testResult[name].ok ? "text-green-600 dark:text-green-400" : "text-destructive")}>
                  {testResult[name].message}
                </p>
              )}
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

const TIER_LABEL: Record<string, string> = { free: "Free", pro: "Pro", enterprise: "Enterprise" }
const UPGRADE_EMAIL = "hello@grydin.co"

function upgradeMailto(tierName: string) {
  const label = TIER_LABEL[tierName] ?? tierName
  return `mailto:${UPGRADE_EMAIL}?subject=${encodeURIComponent(`Upgrade to ${label}`)}&body=${encodeURIComponent(`Hi, I'd like to upgrade my Qualifyr plan to ${label}.`)}`
}

/** Plan badge + credits meter + upgrade CTA, shown atop the Usage tab. Local operators and
 *  master accounts (unlimited) skip the credits meter entirely - credit_status is computed
 *  against the free tier's allowance for them, which would read as a false "0/30" ceiling
 *  they never actually hit. */
function YourPlan() {
  const { tier, unlimited, loading } = useTier()
  if (loading) return null
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <CardTitle>Your plan</CardTitle>
            <TierBadge />
          </div>
          {!unlimited && tier && tier !== "enterprise" && (
            <a href={upgradeMailto(tier === "free" ? "pro" : "enterprise")}>
              <Button size="sm" variant="outline"><Mail className="h-3.5 w-3.5 mr-1" /> Upgrade</Button>
            </a>
          )}
        </div>
        <CardDescription>
          {unlimited
            ? "This account isn't metered - no credit limits apply."
            : "Credits are the unit of charge: 1 credit = 1 outreach-ready lead returned."}
        </CardDescription>
      </CardHeader>
      {!unlimited && (
        <CardContent>
          <CreditsMeter />
        </CardContent>
      )}
    </Card>
  )
}

function PlansComparison() {
  const [tiers, setTiers] = React.useState<PricingTier[]>([])
  const { tier: currentTier, unlimited } = useTier()
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    api.pricingTiers().then((r) => setTiers(r.tiers)).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const rows: { label: string; render: (t: PricingTier) => React.ReactNode }[] = [
    { label: "Price", render: (t) => t.price_usd_per_month === 0 ? "Free" : `$${t.price_usd_per_month}/mo` },
    { label: "Monthly credits", render: (t) => `${t.monthly_credits} credits` },
    { label: "Daily throttle", render: (t) => `${t.daily_credit_throttle}/day` },
    { label: "Leads per run", render: (t) => t.allowed_leads_per_run.join(", ") },
    { label: "Max campaigns", render: (t) => String(t.max_campaigns) },
    { label: "API keys", render: (t) => t.byok_only ? "Bring your own" : "Included" },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Plans</CardTitle>
        <CardDescription>
          1 credit = 1 outreach-ready lead returned, billed when a run finishes. Upgrading is
          handled manually for now &ndash; email us and we&rsquo;ll set it up.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? <CheckingConfig /> : (
          <div className="overflow-x-auto">
            <div className="grid min-w-[640px] grid-cols-4 gap-px overflow-hidden rounded-xl border bg-border">
              <div className="bg-card p-4" />
              {tiers.map((t) => {
                const isCurrent = !unlimited && currentTier === t.name
                return (
                  <div key={t.name} className={cn("flex flex-col gap-2 bg-card p-4", isCurrent && "bg-brand-muted/30")}>
                    <span className="text-sm font-semibold">{TIER_LABEL[t.name] ?? t.name}</span>
                    {isCurrent && (
                      <Badge className="w-fit border-transparent bg-brand text-brand-foreground">
                        <Check className="size-3" /> Current plan
                      </Badge>
                    )}
                    <span className="text-2xl font-bold">
                      {t.price_usd_per_month === 0 ? "Free" : `$${t.price_usd_per_month}`}
                      {t.price_usd_per_month > 0 && <span className="text-xs font-normal text-muted-foreground">/mo</span>}
                    </span>
                    {!isCurrent && (
                      <a href={upgradeMailto(t.name)}>
                        <Button size="sm" variant="outline" className="w-full"><Mail className="h-3.5 w-3.5 mr-1" /> Upgrade</Button>
                      </a>
                    )}
                  </div>
                )
              })}
              {rows.map((r) => (
                <React.Fragment key={r.label}>
                  <div className="bg-muted/30 p-4 text-sm font-medium text-muted-foreground">{r.label}</div>
                  {tiers.map((t) => (
                    <div key={`${r.label}-${t.name}`} className="bg-card p-4 text-sm">{r.render(t)}</div>
                  ))}
                </React.Fragment>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function UsageDashboard() {
  const [usage, setUsage] = React.useState<Record<string, UsageRow>>({})
  const [editing, setEditing] = React.useState<string | null>(null)
  const [editValue, setEditValue] = React.useState("")
  const [saving, setSaving] = React.useState(false)
  const [monthlyEditing, setMonthlyEditing] = React.useState<string | null>(null)
  const [monthlyEditValue, setMonthlyEditValue] = React.useState("")
  const [monthlySaving, setMonthlySaving] = React.useState(false)

  const load = React.useCallback(() => { api.getUsage().then((r) => setUsage(r.usage)).catch(() => {}) }, [])
  React.useEffect(() => { load() }, [load])

  const resources = [
    { key: "brave", label: "Brave Search", unit: "searches" },
    { key: "groq", label: "Groq LLM", unit: "calls" },
    { key: "places", label: "Google Places", unit: "lookups" },
  ]

  const monthlyResources = [
    { key: "runs", label: "Campaign runs", unit: "runs" },
  ]

  const startEdit = (key: string) => {
    setEditing(key)
    setEditValue(String(usage[key]?.limit ?? 50))
  }

  const saveLimit = async () => {
    if (!editing) return
    const val = parseInt(editValue, 10)
    if (!val || val < 1) return
    setSaving(true)
    try {
      await api.updateUsageLimit(editing, val)
      setEditing(null)
      load()
    } catch { /* */ } finally { setSaving(false) }
  }

  const startMonthlyEdit = (key: string) => {
    setMonthlyEditing(key)
    setMonthlyEditValue(String(usage[key]?.monthly_limit ?? 5))
  }

  const saveMonthlyLimit = async () => {
    if (!monthlyEditing) return
    const val = parseInt(monthlyEditValue, 10)
    if (!val || val < 1) return
    setMonthlySaving(true)
    try {
      await api.updateMonthlyUsageLimit(monthlyEditing, val)
      setMonthlyEditing(null)
      load()
    } catch { /* */ } finally { setMonthlySaving(false) }
  }

  return (
    <div className="grid gap-4">
    <Card>
      <CardHeader>
        <CardTitle>Daily usage</CardTitle>
        <CardDescription>
          Usage resets at midnight UTC each day. Limits keep the free tier sustainable.
          When a limit is reached, the engine falls back to free alternatives automatically.
          You can adjust limits to match your needs.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {resources.map((r) => {
          const u = usage[r.key] ?? { count: 0, limit: 50, default_limit: 50, max_limit: 500 }
          const pct = Math.min(100, Math.round((u.count / u.limit) * 100))
          const isEditing = editing === r.key
          return (
            <div key={r.key}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium">{r.label}</span>
                <div className="flex items-center gap-2">
                  {isEditing ? (
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min={1}
                        max={u.max_limit}
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="w-16 rounded border bg-transparent px-1.5 py-0.5 text-xs text-right"
                        autoFocus
                        onKeyDown={(e) => { if (e.key === "Enter") saveLimit(); if (e.key === "Escape") setEditing(null) }}
                      />
                      <span className="text-xs text-muted-foreground">/ {u.max_limit} max</span>
                      <Button size="sm" variant="ghost" className="h-6 px-1.5 text-xs" onClick={saveLimit} disabled={saving}>
                        <Save className="h-3 w-3" />
                      </Button>
                      <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setEditing(null)}>✕</button>
                    </div>
                  ) : (
                    <>
                      <span className="text-xs text-muted-foreground">{u.count} / {u.limit} {r.unit}</span>
                      <button
                        type="button"
                        className="text-xs text-muted-foreground underline hover:text-foreground"
                        onClick={() => startEdit(r.key)}
                      >
                        edit
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="h-2 rounded-full bg-muted overflow-hidden">
                <div
                  className={cn("h-full rounded-full transition-all", pct >= 90 ? "bg-destructive" : pct >= 70 ? "bg-yellow-500" : "bg-primary")}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {isEditing && u.limit !== u.default_limit && (
                <p className="text-[11px] text-muted-foreground mt-0.5">Default: {u.default_limit}</p>
              )}
            </div>
          )
        })}
      </CardContent>
    </Card>
    <Card>
      <CardHeader>
        <CardTitle>Monthly usage</CardTitle>
        <CardDescription>
          Resets on the 1st of each month (UTC). This is the predictable monthly cap, separate
          from the daily limits above.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {monthlyResources.map((r) => {
          const u = usage[r.key]
          if (!u || u.monthly_limit == null) return null
          const count = u.monthly_count ?? 0
          const limit = u.monthly_limit
          const pct = Math.min(100, Math.round((count / limit) * 100))
          const isEditing = monthlyEditing === r.key
          return (
            <div key={r.key}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium">{r.label}</span>
                <div className="flex items-center gap-2">
                  {isEditing ? (
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min={1}
                        max={u.monthly_max_limit}
                        value={monthlyEditValue}
                        onChange={(e) => setMonthlyEditValue(e.target.value)}
                        className="w-16 rounded border bg-transparent px-1.5 py-0.5 text-xs text-right"
                        autoFocus
                        onKeyDown={(e) => { if (e.key === "Enter") saveMonthlyLimit(); if (e.key === "Escape") setMonthlyEditing(null) }}
                      />
                      <span className="text-xs text-muted-foreground">/ {u.monthly_max_limit} max</span>
                      <Button size="sm" variant="ghost" className="h-6 px-1.5 text-xs" onClick={saveMonthlyLimit} disabled={monthlySaving}>
                        <Save className="h-3 w-3" />
                      </Button>
                      <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setMonthlyEditing(null)}>✕</button>
                    </div>
                  ) : (
                    <>
                      <span className="text-xs text-muted-foreground">{count} / {limit} {r.unit}</span>
                      <button
                        type="button"
                        className="text-xs text-muted-foreground underline hover:text-foreground"
                        onClick={() => startMonthlyEdit(r.key)}
                      >
                        edit
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="h-2 rounded-full bg-muted overflow-hidden">
                <div
                  className={cn("h-full rounded-full transition-all", pct >= 90 ? "bg-destructive" : pct >= 70 ? "bg-yellow-500" : "bg-primary")}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {isEditing && limit !== u.monthly_default_limit && (
                <p className="text-[11px] text-muted-foreground mt-0.5">Default: {u.monthly_default_limit}</p>
              )}
            </div>
          )
        })}
      </CardContent>
    </Card>
    </div>
  )
}

function Suppressions() {
  const [rows, setRows] = React.useState<Suppression[]>([])
  const [value, setValue] = React.useState("")
  const [reason, setReason] = React.useState("")
  const load = React.useCallback(() => { api.suppressions().then(setRows).catch(() => setRows([])) }, [])
  React.useEffect(() => { load() }, [load])
  const add = async () => {
    if (!value.trim()) return
    await api.addSuppression(value.trim(), reason.trim() || undefined)
    setValue(""); setReason(""); load()
  }
  const remove = async (v: string) => {
    if (!confirm(`Allow contacting ${v} again?`)) return
    await api.removeSuppression(v); load()
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Suppression list</CardTitle>
        <CardDescription>Addresses and domains that are never emailed. Bounces, unsubscribes and &quot;not interested&quot; replies land here automatically.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Input className="max-w-xs" placeholder="email or domain" value={value} onChange={(e) => setValue(e.target.value)} />
          <Input className="max-w-xs" placeholder="reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button onClick={add}><Ban data-icon="inline-start" /> Suppress</Button>
        </div>
        <Table>
          <TableHeader>
            <TableRow><TableHead>Value</TableHead><TableHead>Kind</TableHead><TableHead>Reason</TableHead><TableHead>Added</TableHead><TableHead /></TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.value}>
                <TableCell className="font-mono text-xs">{r.value}</TableCell>
                <TableCell><Badge variant="outline">{r.kind}</Badge></TableCell>
                <TableCell className="text-xs text-muted-foreground">{r.reason ?? ""}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{new Date(r.created_at).toLocaleDateString()}</TableCell>
                <TableCell><Button size="icon-xs" variant="ghost" onClick={() => remove(r.value)}><Trash2 /></Button></TableCell>
              </TableRow>
            ))}
            {rows.length === 0 && <TableRow><TableCell colSpan={5} className="text-center text-muted-foreground">Nothing suppressed.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function Mailboxes({ campaignId }: { campaignId: string | null }) {
  const [liveRows, setLiveRows] = React.useState<MailboxState[]>([])
  const [userRows, setUserRows] = React.useState<{ address: string; smtp_host: string; smtp_port: number; sender_name: string | null; daily_limit: number | null; enabled: boolean; created_at: string }[]>([])
  const [encryptionAvailable, setEncryptionAvailable] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [adding, setAdding] = React.useState(false)
  const [addr, setAddr] = React.useState("")
  const [pw, setPw] = React.useState("")
  const [smtpHost, setSmtpHost] = React.useState("smtp.gmail.com")
  const [smtpPort, setSmtpPort] = React.useState("587")
  const [senderName, setSenderName] = React.useState("")
  const [busy, setBusy] = React.useState<string | null>(null)
  const [msg, setMsg] = React.useState<{ ok: boolean; text: string } | null>(null)
  const [showPw, setShowPw] = React.useState(false)

  const loadLive = React.useCallback(() => {
    api.mailboxes(campaignId ?? undefined).then(setLiveRows).catch(() => setLiveRows([]))
  }, [campaignId])
  const loadUser = React.useCallback(() => {
    api.listUserMailboxes().then((r) => { setUserRows(r.mailboxes); setEncryptionAvailable(r.encryption_available) })
      .catch(() => {}).finally(() => setLoading(false))
  }, [])
  React.useEffect(() => { loadLive(); loadUser() }, [loadLive, loadUser])

  const testConn = async () => {
    setBusy("test"); setMsg(null)
    try {
      const r = await api.testUserMailbox({ address: addr, password: pw, smtp_host: smtpHost, smtp_port: Number(smtpPort) || 587 })
      setMsg({ ok: r.ok, text: r.message })
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(null) }
  }

  const save = async () => {
    if (!addr.trim() || !pw.trim()) { setMsg({ ok: false, text: "Email and password are required." }); return }
    setBusy("save"); setMsg(null)
    try {
      await api.saveUserMailbox({ address: addr.trim(), password: pw.trim(), smtp_host: smtpHost, smtp_port: Number(smtpPort) || 587, sender_name: senderName.trim() || undefined })
      setAddr(""); setPw(""); setSenderName(""); setSmtpHost("smtp.gmail.com"); setSmtpPort("587"); setAdding(false); setMsg(null)
      loadUser(); loadLive()
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) } finally { setBusy(null) }
  }

  const remove = async (address: string) => {
    if (!confirm(`Remove mailbox ${address}?`)) return
    try { await api.deleteUserMailbox(address); loadUser(); loadLive() } catch (e) { alert((e as Error).message) }
  }

  const toggle = async (address: string) => {
    try { await api.toggleUserMailbox(address); loadUser(); loadLive() } catch (e) { alert((e as Error).message) }
  }

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle>Your mailboxes</CardTitle>
              <CardDescription>Add your Gmail or SMTP account to send outreach emails. Credentials are encrypted at rest. For Gmail, use an <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer" className="underline">App Password</a> (not your regular password).</CardDescription>
            </div>
            {!loading && encryptionAvailable && !adding && (
              <Button size="sm" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5 mr-1" /> Add mailbox</Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="grid gap-4">
          {loading ? <CheckingConfig /> : !encryptionAvailable ? <EncryptionNotice what="Storing a mailbox" /> : (
          <>
          {adding && (
            <div className="rounded-lg border p-4 grid gap-3">
              <div className="grid gap-1.5">
                <label className="text-xs font-medium">Email address</label>
                <Input value={addr} onChange={(e) => setAddr(e.target.value)} placeholder="you@gmail.com" />
              </div>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium">App password</label>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <Input type={showPw ? "text" : "password"} value={pw} onChange={(e) => setPw(e.target.value)} placeholder="xxxx xxxx xxxx xxxx" />
                    <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                      {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="grid gap-1.5">
                  <label className="text-xs font-medium">SMTP host</label>
                  <Input value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} />
                </div>
                <div className="grid gap-1.5">
                  <label className="text-xs font-medium">Port</label>
                  <Input value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} />
                </div>
              </div>
              <div className="grid gap-1.5">
                <label className="text-xs font-medium">Sender name (optional)</label>
                <Input value={senderName} onChange={(e) => setSenderName(e.target.value)} placeholder="Your Name" />
              </div>
              {msg && <p className={cn("text-sm", msg.ok ? "text-green-600 dark:text-green-400" : "text-destructive")}>{msg.text}</p>}
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={testConn} disabled={!!busy}>
                  <FlaskConical className="h-3.5 w-3.5 mr-1" />{busy === "test" ? "Testing…" : "Test connection"}
                </Button>
                <Button size="sm" onClick={save} disabled={!!busy}>
                  <Save className="h-3.5 w-3.5 mr-1" />{busy === "save" ? "Saving…" : "Save mailbox"}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => { setAdding(false); setMsg(null) }}>Cancel</Button>
              </div>
            </div>
          )}
          {userRows.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow><TableHead>Address</TableHead><TableHead>SMTP</TableHead><TableHead>Sender</TableHead><TableHead>Status</TableHead><TableHead /></TableRow>
              </TableHeader>
              <TableBody>
                {userRows.map((m) => (
                  <TableRow key={m.address}>
                    <TableCell className="font-mono text-xs">{m.address}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{m.smtp_host}:{m.smtp_port}</TableCell>
                    <TableCell className="text-xs">{m.sender_name ?? "–"}</TableCell>
                    <TableCell>
                      <Badge variant={m.enabled ? "default" : "secondary"}>{m.enabled ? "active" : "paused"}</Badge>
                    </TableCell>
                    <TableCell className="flex gap-1 justify-end">
                      <Button size="icon-xs" variant="ghost" onClick={() => toggle(m.address)} title={m.enabled ? "Pause" : "Enable"}>
                        <Power className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="icon-xs" variant="ghost" onClick={() => remove(m.address)} className="text-muted-foreground hover:text-destructive">
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : !adding && (
            <p className="text-sm text-muted-foreground">No mailboxes added yet. Add one to start sending outreach emails.</p>
          )}
          </>
          )}
        </CardContent>
      </Card>
      {liveRows.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Live mailbox status</CardTitle>
            <CardDescription>Daily send counts, warm-up progress and bounce guard for all active mailboxes.</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow><TableHead>Address</TableHead><TableHead>Auth</TableHead><TableHead>Today</TableHead><TableHead>Warm-up</TableHead><TableHead>Bounced</TableHead><TableHead>State</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {liveRows.map((m) => (
                  <TableRow key={m.address} className={cn(m.paused_reason && "bg-destructive/5")}>
                    <TableCell className="font-mono text-xs">{m.address}</TableCell>
                    <TableCell><Badge variant="outline">{m.auth_mode}</Badge></TableCell>
                    <TableCell>{m.sent_today}/{m.cap}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{m.days_active ? `day ${m.days_active}` : "never sent"}</TableCell>
                    <TableCell>{m.bounced_today}</TableCell>
                    <TableCell>{m.paused_reason ? <Badge variant="destructive">{m.paused_reason}</Badge> : <Badge>{m.remaining} left</Badge>}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

const TAB_VALUES = ["api-keys", "usage", "plan", "mailboxes", "suppressions"] as const

function SettingsInner() {
  const { campaignId } = useCampaign()
  const router = useRouter()
  const searchParams = useSearchParams()
  const requestedTab = searchParams.get("tab")
  const initialTab = (TAB_VALUES as readonly string[]).includes(requestedTab ?? "") ? requestedTab! : "api-keys"
  const [tab, setTab] = React.useState(initialTab)

  const changeTab = (v: string) => {
    setTab(v)
    router.replace(`/settings?tab=${v}`, { scroll: false })
  }

  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground">Plan, API keys, usage limits, mailboxes and suppressions.</p>
      </div>
      <Tabs value={tab} onValueChange={(v) => changeTab(String(v))}>
        <TabsList>
          <TabsTrigger value="api-keys"><Key className="h-3.5 w-3.5 mr-1" />API Keys</TabsTrigger>
          <TabsTrigger value="usage"><BarChart3 className="h-3.5 w-3.5 mr-1" />Usage</TabsTrigger>
          <TabsTrigger value="plan"><Wallet className="h-3.5 w-3.5 mr-1" />Plan</TabsTrigger>
          <TabsTrigger value="mailboxes">Mailboxes</TabsTrigger>
          <TabsTrigger value="suppressions">Suppressions</TabsTrigger>
        </TabsList>
      </Tabs>
      {tab === "api-keys" && <ApiKeys />}
      {tab === "usage" && <UsageDashboard />}
      {tab === "plan" && (
        <div className="grid gap-4">
          <YourPlan />
          <PlansComparison />
        </div>
      )}
      {tab === "mailboxes" && <Mailboxes campaignId={campaignId} />}
      {tab === "suppressions" && <Suppressions />}
    </div>
  )
}

export default function SettingsPage() {
  return (
    // useSearchParams needs a Suspense boundary or the whole route opts out of static rendering.
    <React.Suspense fallback={<div className="h-64" />}>
      <SettingsInner />
    </React.Suspense>
  )
}
