"use client"

import * as React from "react"
import { api } from "@/lib/api"

export function PayPalCheckoutReturn({
  checkoutId,
  capability,
  paypalOrderId,
  cancelled,
}: {
  checkoutId: string
  capability: string
  paypalOrderId: string
  cancelled: boolean
}) {
  const [status, setStatus] = React.useState("Checking payment with PayPal…")
  const [busy, setBusy] = React.useState(true)

  React.useEffect(() => {
    let active = true
    const check = async () => {
      try {
        const result = cancelled || !paypalOrderId
          ? await api.paypalOrderStatus({ checkout_id: checkoutId, capability })
          : await api.returnFromPayPalOrder({
              checkout_id: checkoutId, capability, paypal_order_id: paypalOrderId,
            })
        if (active) {
          setStatus(result.checkout.paid
            ? "Payment confirmed. Thank you."
            : cancelled && result.checkout.status === "PENDING"
              ? "Checkout cancelled. No payment was confirmed."
              : `Payment status: ${result.checkout.status.replaceAll("_", " ")}. Qualifyr will not mark this checkout paid until PayPal confirms the capture.`)
        }
      } catch {
        if (active) setStatus("We could not confirm this checkout. Contact the seller and do not pay again until they check its status.")
      } finally {
        if (active) setBusy(false)
      }
    }
    if (!checkoutId || !capability) {
      setStatus("Checkout link is invalid or incomplete.")
      setBusy(false)
    } else {
      void check()
    }
    return () => { active = false }
  }, [checkoutId, capability, paypalOrderId, cancelled])

  return (
    <section className="grid gap-3 rounded-xl border p-6">
      <h1 className="text-xl font-semibold">PayPal checkout</h1>
      <p role="status" aria-live="polite">{busy ? "Verifying…" : status}</p>
    </section>
  )
}
