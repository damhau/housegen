import type { JobOut, RunSummary } from "@/api/model"
import { cn } from "@/lib/utils"

/** "38 s", "2:10", "1 h 05" */
export function fmtMs(ms: number | undefined): string {
  const s = Math.round((ms ?? 0) / 1000)
  if (s < 90) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}:${String(s % 60).padStart(2, "0")}`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`
}

/** "1.2k", "160k", "2.3M" */
export function fmtTok(n: number | undefined): string {
  const v = n ?? 0
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 10_000) return `${Math.round(v / 1000)}k`
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`
  return String(v)
}

export function fmtUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "no price"
  return v < 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(1)}`
}

/** The five activities the wall time is split into, in display order, with their colours. */
export const ACTIVITIES = [
  { key: "thinking", label: "thinking", color: "bg-violet-400" },
  { key: "writing", label: "writing", color: "bg-sky-400" },
  { key: "tools", label: "tools", color: "bg-slate-400" },
  { key: "render", label: "rendering", color: "bg-amber-400" },
  { key: "critic", label: "critic", color: "bg-emerald-400" },
] as const
export type ActivityKey = (typeof ACTIVITIES)[number]["key"]

/** Milliseconds per activity, from a run summary. */
export function activityMs(m: RunSummary): Record<ActivityKey, number> {
  const b = m.builder ?? {}
  const c = m.critic ?? {}
  return {
    thinking: b.thinking_ms ?? 0,
    writing: b.writing_ms ?? 0,
    tools: Math.max(0, (b.tools_ms ?? 0) - (b.render_ms ?? 0)),
    render: b.render_ms ?? 0,
    critic: c.llm_ms ?? 0,
  }
}

export function ActivityBar({ metrics, className }: { metrics: RunSummary; className?: string }) {
  const ms = activityMs(metrics)
  const total = Object.values(ms).reduce((a, b) => a + b, 0)
  if (total === 0) return null
  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted" title="time by activity">
        {ACTIVITIES.map((a) => (
          <div key={a.key} className={a.color} style={{ width: `${(ms[a.key] / total) * 100}%` }} title={`${a.label} ${fmtMs(ms[a.key])}`} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        {ACTIVITIES.filter((a) => ms[a.key] > 0).map((a) => (
          <span key={a.key} className="flex items-center gap-1">
            <span className={cn("inline-block size-2 rounded-sm", a.color)} /> {a.label} {fmtMs(ms[a.key])}
          </span>
        ))}
      </div>
    </div>
  )
}

/**
 * Where a run spent its time and tokens (#13): time by activity, tokens in/cached/out,
 * cost, turns, edits per turn, renders, and the two things worth a red flag (cache-miss
 * turns and single-edit turns).
 */
export function RunSummaryCard({ metrics, title = "Run summary", className }: { metrics: RunSummary; title?: string; className?: string }) {
  const b = metrics.builder ?? {}
  const c = metrics.critic ?? {}
  const inTok = (b.input_tokens ?? 0) + (c.input_tokens ?? 0)
  const cached = (b.cached_tokens ?? 0) + (c.cached_tokens ?? 0)
  const out = (b.output_tokens ?? 0) + (c.output_tokens ?? 0)
  const misses = b.cache_miss_turns ?? 0
  const singles = metrics.single_edit_turns ?? 0
  return (
    <div className={cn("rounded-md border bg-muted/40 p-2 text-xs", className)}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="font-medium">{title}</span>
        <span className="text-muted-foreground">
          {fmtMs(metrics.wall_ms)} · {metrics.turns ?? 0} turns · {fmtUsd(metrics.cost_usd)}
          {metrics.models && metrics.models.length > 0 ? ` · ${metrics.models.join(", ")}` : ""}
        </span>
      </div>
      <ActivityBar metrics={metrics} className="mt-1.5" />
      <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-muted-foreground">
        <dt>tokens</dt>
        <dd>
          {fmtTok(inTok)} in · {fmtTok(cached)} cached ({inTok > 0 ? Math.round((cached / inTok) * 100) : 0}%) · {fmtTok(out)} out
          {(b.reasoning_tokens ?? 0) + (c.reasoning_tokens ?? 0) > 0 ? ` (${fmtTok((b.reasoning_tokens ?? 0) + (c.reasoning_tokens ?? 0))} reasoning)` : ""}
        </dd>
        <dt>builder</dt>
        <dd>
          {b.turns ?? 0} turns · {metrics.edits ?? 0} edits ({metrics.edits_per_turn_avg ?? 0} per turn, max {metrics.edits_per_turn_max ?? 0}) · {b.renders ?? 0} renders in{" "}
          {fmtMs(b.render_ms)}
          {b.cost_usd != null ? ` · ${fmtUsd(b.cost_usd)}` : ""}
        </dd>
        {(c.turns ?? 0) > 0 && (
          <>
            <dt>critic</dt>
            <dd>
              {c.turns} calls · {fmtMs(c.llm_ms)} · {fmtTok(c.input_tokens)} in / {fmtTok(c.output_tokens)} out
              {c.cost_usd != null ? ` · ${fmtUsd(c.cost_usd)}` : ""}
            </dd>
          </>
        )}
        {(misses > 0 || singles > 0) && (
          <>
            <dt className="text-destructive">flags</dt>
            <dd className="text-destructive">
              {[misses > 0 && `${misses} cache-miss turn${misses > 1 ? "s" : ""}`, singles > 0 && `${singles} single-edit turn${singles > 1 ? "s" : ""}`]
                .filter(Boolean)
                .join(" · ")}
            </dd>
          </>
        )}
      </dl>
    </div>
  )
}

/** One metric row of the compare table: label, the two values, the delta. */
type Row = { label: string; a: number; b: number; fmt: (v: number) => string; lowerIsBetter?: boolean }

function rows(a: RunSummary, b: RunSummary): Row[] {
  const ma = activityMs(a)
  const mb = activityMs(b)
  const tok = (m: RunSummary, k: "input_tokens" | "cached_tokens" | "output_tokens") => (m.builder?.[k] ?? 0) + (m.critic?.[k] ?? 0)
  return [
    { label: "wall time", a: a.wall_ms ?? 0, b: b.wall_ms ?? 0, fmt: fmtMs, lowerIsBetter: true },
    { label: "cost", a: a.cost_usd ?? 0, b: b.cost_usd ?? 0, fmt: (v) => fmtUsd(v), lowerIsBetter: true },
    { label: "turns", a: a.turns ?? 0, b: b.turns ?? 0, fmt: String, lowerIsBetter: true },
    { label: "thinking", a: ma.thinking, b: mb.thinking, fmt: fmtMs, lowerIsBetter: true },
    { label: "writing", a: ma.writing, b: mb.writing, fmt: fmtMs, lowerIsBetter: true },
    { label: "tools", a: ma.tools, b: mb.tools, fmt: fmtMs, lowerIsBetter: true },
    { label: "rendering", a: ma.render, b: mb.render, fmt: fmtMs, lowerIsBetter: true },
    { label: "critic", a: ma.critic, b: mb.critic, fmt: fmtMs, lowerIsBetter: true },
    { label: "tokens in", a: tok(a, "input_tokens"), b: tok(b, "input_tokens"), fmt: fmtTok, lowerIsBetter: true },
    { label: "tokens cached", a: tok(a, "cached_tokens"), b: tok(b, "cached_tokens"), fmt: fmtTok },
    { label: "tokens out", a: tok(a, "output_tokens"), b: tok(b, "output_tokens"), fmt: fmtTok, lowerIsBetter: true },
    { label: "edits", a: a.edits ?? 0, b: b.edits ?? 0, fmt: String },
    { label: "edits per turn", a: a.edits_per_turn_avg ?? 0, b: b.edits_per_turn_avg ?? 0, fmt: (v) => v.toFixed(1) },
    { label: "single-edit turns", a: a.single_edit_turns ?? 0, b: b.single_edit_turns ?? 0, fmt: String, lowerIsBetter: true },
    { label: "cache-miss turns", a: a.builder?.cache_miss_turns ?? 0, b: b.builder?.cache_miss_turns ?? 0, fmt: String, lowerIsBetter: true },
    { label: "renders", a: a.builder?.renders ?? 0, b: b.builder?.renders ?? 0, fmt: String },
  ]
}

/** Two runs of the same project side by side, deltas highlighted (green = better). */
export function RunCompare({ a, b, labelA, labelB }: { a: JobOut; b: JobOut; labelA: string; labelB: string }) {
  if (!a.metrics || !b.metrics) return <p className="text-xs text-muted-foreground">Both runs need a summary (runs finished before this feature have none).</p>
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="py-1 font-normal"></th>
          <th className="py-1 font-normal">{labelA}</th>
          <th className="py-1 font-normal">{labelB}</th>
          <th className="py-1 font-normal">Δ</th>
        </tr>
      </thead>
      <tbody>
        {rows(a.metrics, b.metrics).map((r) => {
          const d = r.b - r.a
          const pct = r.a !== 0 ? Math.round((d / r.a) * 100) : null
          const better = d === 0 ? null : r.lowerIsBetter === undefined ? null : r.lowerIsBetter ? d < 0 : d > 0
          return (
            <tr key={r.label} className="border-t">
              <td className="py-0.5 text-muted-foreground">{r.label}</td>
              <td className="py-0.5 tabular-nums">{r.fmt(r.a)}</td>
              <td className="py-0.5 tabular-nums">{r.fmt(r.b)}</td>
              <td className={cn("py-0.5 tabular-nums", better === true && "text-success", better === false && "text-destructive")}>
                {d === 0 ? "=" : `${d > 0 ? "+" : "−"}${r.fmt(Math.abs(d))}${pct !== null && Math.abs(pct) < 1000 ? ` (${pct > 0 ? "+" : ""}${pct}%)` : ""}`}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
