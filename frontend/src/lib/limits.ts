export const LIMITS = [
  { key: "concurrent_jobs", label: "Jobs at once", hint: "queued or running", money: false },
  { key: "jobs_per_day", label: "Jobs per day", hint: "started since midnight UTC", money: false },
  { key: "listing_usd_per_month", label: "Listing photos per month", hint: "spent this month (UTC)", money: true },
] as const

export type LimitKey = (typeof LIMITS)[number]["key"]

export function amount(value: number, money: boolean): string {
  return money ? `$${value.toFixed(2)}` : String(value)
}
