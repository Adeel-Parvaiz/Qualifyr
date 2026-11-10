import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { InvoiceStatus } from "@/lib/api"

const STYLE: Record<InvoiceStatus, { label: string; cls: string }> = {
  PENDING: { label: "Creating", cls: "bg-muted text-muted-foreground" },
  DRAFT: { label: "Draft", cls: "bg-muted text-muted-foreground" },
  SENT: { label: "Awaiting payment", cls: "bg-blue-500/10 text-blue-700 dark:text-blue-400" },
  PAID: { label: "Paid", cls: "bg-green-500/10 text-green-700 dark:text-green-400" },
  CANCELLED: { label: "Cancelled", cls: "bg-red-500/10 text-red-700 dark:text-red-400" },
  REFUNDED: { label: "Refunded", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-400" },
}

export function InvoiceStatusBadge({ status }: { status: InvoiceStatus }) {
  const s = STYLE[status] ?? STYLE.DRAFT
  return <Badge variant="secondary" className={cn("border-0", s.cls)}>{s.label}</Badge>
}
