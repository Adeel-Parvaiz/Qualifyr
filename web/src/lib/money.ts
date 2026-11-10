// Money is integer cents end to end; format only at the edge.
export function formatMoney(cents: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100)
  } catch {
    return `${currency} ${(cents / 100).toFixed(2)}`
  }
}

/** "120", "120.5" -> cents; null when it is not a valid amount with at most two decimals. */
export function parseMoney(input: string): number | null {
  const s = input.trim().replace(/,/g, "")
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null
  const [whole, frac = ""] = s.split(".")
  return Number(whole) * 100 + Number((frac + "00").slice(0, 2))
}
