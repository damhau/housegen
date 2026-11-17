import type { LimitOut, LimitsOut } from "@/api/model"
import { amount, LIMITS, type LimitKey } from "@/lib/limits"
import { cn, formatDay } from "@/lib/utils"

function resets(key: LimitKey, limit: LimitOut): string | null {
  if (!limit.resets_at) return null
  return key === "jobs_per_day" ? "resets at midnight UTC" : `resets on ${formatDay(limit.resets_at)}`
}

function source(limit: LimitOut, money: boolean): string {
  if (limit.override === null) return "the server's default"
  if (limit.override === "unlimited") return `lifted (default ${amount(limit.default, money)})`
  return `own limit (default ${amount(limit.default, money)})`
}

/** An organisation's limits next to its use of them: a bar per limit, fuller as the use nears it. */
export function LimitsList({ limits, showSource = false }: { limits: LimitsOut; showSource?: boolean }) {
  return (
    <div className="grid gap-3">
      {LIMITS.map(({ key, label, hint, money }) => {
        const limit = limits[key]
        const share = limit.limit === null ? null : limit.limit === 0 ? 1 : Math.min(1, limit.used / limit.limit)
        const reset = resets(key, limit)
        return (
          <div key={key} className="grid gap-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="text-sm font-medium">{label}</span>
              <span className="text-sm tabular-nums">
                {amount(limit.used, money)}
                <span className="text-muted-foreground">
                  {limit.limit === null ? " · no limit" : ` of ${amount(limit.limit, money)}`}
                </span>
              </span>
            </div>
            {share !== null && (
              <div
                className="h-1.5 overflow-hidden rounded-full bg-muted"
                role="meter"
                aria-label={label}
                aria-valuemin={0}
                aria-valuemax={limit.limit ?? undefined}
                aria-valuenow={limit.used}
              >
                <div
                  className={cn("h-full rounded-full", share >= 1 ? "bg-destructive" : share >= 0.8 ? "bg-warning" : "bg-primary")}
                  style={{ width: `${Math.max(share * 100, limit.used > 0 ? 3 : 0)}%` }}
                />
              </div>
            )}
            <div className="text-xs text-muted-foreground">
              {hint}
              {reset && limit.limit !== null ? ` · ${reset}` : ""}
              {showSource ? ` · ${source(limit, money)}` : ""}
            </div>
          </div>
        )
      })}
    </div>
  )
}
