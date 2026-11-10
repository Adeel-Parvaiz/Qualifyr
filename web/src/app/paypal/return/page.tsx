import { PayPalCheckoutReturn } from "@/components/paypal-checkout-return"

export const metadata = { referrer: "no-referrer" }

export default async function PayPalReturnPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const query = await searchParams
  const value = (key: string) => {
    const item = query[key]
    return Array.isArray(item) ? item[0] : item
  }
  return (
    <main className="mx-auto max-w-xl p-8">
      <PayPalCheckoutReturn
        checkoutId={value("checkout_id") ?? ""}
        capability={value("capability") ?? ""}
        paypalOrderId={value("token") ?? ""}
        cancelled={value("cancelled") === "1"}
      />
    </main>
  )
}
