import { useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, Camera, CheckCircle2, ChevronDown, ChevronRight, Eye, FileCode2, Hammer, Loader2, RefreshCw, ScanSearch, Sparkles } from "lucide-react"
import type { RunSummary } from "@/api/model"
import type { JobEvent, LlmProgress } from "@/hooks/useJobStream"
import { LiveStatus } from "@/components/LiveStatus"
import { Markdown } from "@/components/Markdown"
import { RunSummaryCard, fmtMs, fmtTok } from "@/components/RunSummaryCard"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

type P = Record<string, unknown>
const str = (v: unknown) => (typeof v === "string" ? v : "")
const num = (v: unknown) => (typeof v === "number" ? v : undefined)

/** A builder step: its text and tool rows, and (once the step ends) its `turn` record. */
interface TurnGroup {
  kind: "turn"
  step: number
  events: JobEvent[]
  turn: JobEvent | null
}
type Item = { kind: "event"; ev: JobEvent } | TurnGroup

/**
 * Group the builder's events by step (#13): builder_text/builder_step carry `step`, the
 * renders they trigger come right before their render_views row while the step is open,
 * and the `turn` event closes the step with its timing and tokens.
 */
export function groupByTurn(events: JobEvent[]): Item[] {
  const items: Item[] = []
  const open = (): TurnGroup | null => {
    const last = items[items.length - 1]
    return last && last.kind === "turn" && last.turn === null ? last : null
  }
  for (const ev of events) {
    const p = ev.payload as P
    const step = num(p.step)
    if ((ev.type === "builder_text" || ev.type === "builder_step") && step !== undefined) {
      let g = open()
      if (!g || g.step !== step) {
        g = { kind: "turn", step, events: [], turn: null }
        items.push(g)
      }
      g.events.push(ev)
    } else if (ev.type === "turn" && str(p.role) === "builder" && step !== undefined) {
      const g = open()
      if (g && g.step === step) g.turn = ev
      else items.push({ kind: "turn", step, events: [], turn: ev })
    } else if (ev.type === "render" && open()) {
      open()!.events.push(ev)
    } else if (ev.type === "turn") {
      continue // critic / intake turns: their card carries the numbers
    } else {
      items.push({ kind: "event", ev })
    }
  }
  return items
}

export function JobTimeline({
  events,
  live,
  progress = null,
  liveText = "",
  liveThought = "",
}: {
  events: JobEvent[]
  live: boolean
  progress?: LlmProgress | null
  liveText?: string
  liveThought?: string
}) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" })
  }, [events.length, progress?.phase, liveText.length > 0])

  const items = useMemo(() => groupByTurn(events), [events])
  const lastTurnSteps = items.filter((i): i is TurnGroup => i.kind === "turn").slice(-2).map((g) => g.step)

  if (events.length === 0 && !live) return <p className="p-3 text-sm text-muted-foreground">No activity yet.</p>

  return (
    <div>
      <ol className="space-y-1.5 p-3 text-sm">
        {items.map((it) =>
          it.kind === "event" ? (
            <Row key={it.ev.seq} ev={it.ev} />
          ) : (
            <TurnRows key={`turn-${it.step}`} group={it} recent={lastTurnSteps.includes(it.step)} />
          ),
        )}
        {live && !progress && (
          <li className="flex items-center gap-2 pl-1 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" /> working…
          </li>
        )}
      </ol>
      {live && <LiveStatus progress={progress} liveText={liveText} liveThought={liveThought} />}
      <div ref={endRef} />
    </div>
  )
}

const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [])

// a turn longer than this is worth a look
const SLOW_TURN_MS = 120_000

/** "edit_file ×4, render_views" */
function toolSummary(calls: { name: string }[]): string {
  const counts = new Map<string, number>()
  for (const c of calls) counts.set(c.name, (counts.get(c.name) ?? 0) + 1)
  return [...counts].map(([n, k]) => (k > 1 ? `${n} ×${k}` : n)).join(", ")
}

/**
 * One builder step: a collapsible header with the turn's numbers (duration, output tokens,
 * cache hit, tools), red when the prompt cache was lost, amber when the turn was slow. The
 * two most recent steps start open; older ones start collapsed.
 */
function TurnRows({ group, recent }: { group: TurnGroup; recent: boolean }) {
  const [manual, setManual] = useState<boolean | null>(null)
  const open = manual ?? (recent || group.turn === null)
  const t = (group.turn?.payload ?? null) as P | null
  const calls = (t?.tool_calls as { name: string }[] | undefined) ?? []
  const miss = t?.cache_miss === true
  const slow = (num(t?.duration_ms) ?? 0) + (num(t?.tools_ms) ?? 0) > SLOW_TURN_MS
  const hit = num(t?.cache_hit)
  return (
    <li className="ml-0">
      <button
        type="button"
        className={cn(
          "flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left font-mono text-[11px] hover:bg-accent/40",
          miss ? "text-destructive" : slow ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground",
        )}
        onClick={() => setManual(!open)}
        aria-expanded={open}
        title={t ? `model ${str(t.model)} · thinking ${fmtMs(num(t.thinking_ms))} · tools ${fmtMs(num(t.tools_ms))} (renders ${fmtMs(num(t.render_ms))}) · ${fmtTok(num(t.input_tokens))} in` : "step in progress"}
      >
        {open ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}
        <span className="font-semibold">step {group.step}</span>
        {t ? (
          <span className="truncate">
            · {fmtMs((num(t.duration_ms) ?? 0) + (num(t.tools_ms) ?? 0))} · {fmtTok(num(t.output_tokens))} out · cache {hit !== undefined ? `${Math.round(hit * 100)}%` : "–"}
            {miss ? " (lost)" : ""}
            {calls.length > 0 ? ` · ${toolSummary(calls)}` : ""}
          </span>
        ) : (
          <span>· running…</span>
        )}
      </button>
      {open && (
        <ol className="mt-1 space-y-1.5">
          {group.events.map((ev) => (
            <Row key={ev.seq} ev={ev} />
          ))}
        </ol>
      )}
    </li>
  )
}

function Row({ ev }: { ev: JobEvent }) {
  const p = ev.payload as P
  switch (ev.type) {
    case "phase":
      return (
        <li className="mt-3 flex items-center gap-2 font-medium first:mt-0">
          <PhaseIcon name={str(p.name)} />
          {str(p.message)}
        </li>
      )
    case "intake": {
      const sheets = (p.sheets as { page: number; kind: string; label: string; elevations?: string[] }[] | undefined) ?? []
      const questions = (p.questions as { question: string }[] | undefined) ?? []
      return (
        <li className="ml-6 rounded-md border bg-muted/40 p-2">
          <Markdown text={str(p.summary)} />
          {sheets.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              {sheets.map((s) => (
                <li key={s.page}>
                  sheet {s.page}: {s.kind.replace("_", " ")}
                  {s.elevations && s.elevations.length > 0 ? ` (${s.elevations.join(", ")})` : ""} · {s.label}
                </li>
              ))}
            </ul>
          )}
          {questions.length > 0 && (
            <div className="mt-1 text-xs text-muted-foreground">
              {questions.length} question{questions.length > 1 ? "s" : ""} for you, answered below
            </div>
          )}
        </li>
      )
    }
    case "builder_text":
      return (
        <li className="ml-6 text-muted-foreground">
          <Markdown text={str(p.text)} />
        </li>
      )
    case "builder_step": {
      const tool = str(p.tool)
      const isErr = p.is_error === true
      return (
        <li className={cn("ml-6 flex items-start gap-2 font-mono text-xs", isErr && "text-destructive")}>
          <ToolIcon tool={tool} />
          <span className="min-w-0">
            <span className="font-semibold">{tool}</span> {str(p.args)}
            {tool === "finish" || isErr ? <span className="block text-muted-foreground">{str(p.result)}</span> : null}
          </span>
        </li>
      )
    }
    case "render": {
      const renders = (p.renders as Record<string, string> | undefined) ?? {}
      const errors = (p.errors as string[] | undefined) ?? []
      return (
        <li className="ml-6">
          <div className="flex flex-wrap gap-1">
            {Object.entries(renders).map(([view, url]) => (
              <a key={view} href={url} target="_blank" rel="noreferrer" title={view}>
                <img src={url} alt={view} className="h-14 rounded border object-cover" />
              </a>
            ))}
          </div>
          {errors.length > 0 && <div className="mt-1 text-xs text-destructive">{errors.join(" · ")}</div>}
        </li>
      )
    }
    case "critic": {
      const issues = (p.issues as { severity: string; kind?: string; view: string; description: string }[] | undefined) ?? []
      const score = num(p.score) ?? 0
      // fidelity (the model vs the reference) and plausibility (physically impossible) apart
      const fidelity = issues.filter((i) => i.kind !== "plausibility")
      const plausibility = issues.filter((i) => i.kind === "plausibility")
      return (
        <li className="ml-6 rounded-md border bg-muted/40 p-2">
          <div className="flex items-start gap-2">
            <ScoreBadge score={score} />
            <Markdown text={str(p.summary)} className="min-w-0" />
          </div>
          {fidelity.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              {fidelity.slice(0, 8).map((i, k) => (
                <li key={k}>
                  <span className={cn("font-medium", i.severity === "major" ? "text-destructive" : "")}>[{i.severity}]</span> {i.view}: {i.description}
                </li>
              ))}
            </ul>
          )}
          {plausibility.length > 0 && (
            <div className="mt-1.5 text-xs text-muted-foreground">
              <div className="font-medium text-amber-700 dark:text-amber-300">Not physically plausible</div>
              <ul className="space-y-0.5">
                {plausibility.slice(0, 8).map((i, k) => (
                  <li key={k}>
                    <span className="font-medium text-destructive">[{i.severity}]</span> {i.view}: {i.description}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </li>
      )
    }
    case "version":
      return (
        <li className="ml-6 flex items-center gap-2">
          <Sparkles className="size-3.5 text-primary" />
          Version {String(p.number)} saved <span className="text-muted-foreground">· {str(p.label)}</span>
        </li>
      )
    case "usage": {
      const metrics = p.metrics as RunSummary | undefined
      if (metrics) {
        return (
          <li className="ml-6">
            <RunSummaryCard metrics={metrics} />
          </li>
        )
      }
      return (
        <li className="ml-6 text-xs text-muted-foreground">
          tokens: {String(p.input_tokens)} in / {String(p.output_tokens)} out{num(p.cache_read_tokens) ? ` (${String(p.cache_read_tokens)} cached)` : ""}
          {num(p.critic_output_tokens) !== undefined && ` · critic ${String(p.critic_input_tokens)} in / ${String(p.critic_output_tokens)} out`}
        </li>
      )
    }
    case "done": {
      const suggestions = strList(p.suggestions).length
      const questions = strList(p.questions).length
      const hint = [
        suggestions > 0 && `${suggestions} optional addition${suggestions > 1 ? "s" : ""}`,
        questions > 0 && `${questions} question${questions > 1 ? "s" : ""} for you`,
      ]
        .filter(Boolean)
        .join(", ")
      return (
        <li className="mt-2 flex flex-wrap items-center gap-2 font-medium text-success">
          <CheckCircle2 className="size-4" /> Done{num(p.score) !== undefined ? ` · score ${String(p.score)}` : ""}
          {hint && <span className="text-xs font-normal text-muted-foreground">{hint} below</span>}
        </li>
      )
    }
    case "error":
      return (
        <li className="mt-2 flex items-start gap-2 text-destructive">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" /> {str(p.message)}
        </li>
      )
    case "resumed":
      return (
        <li className="mt-3 flex items-center gap-2 text-muted-foreground">
          <RefreshCw className="size-4" /> Resumed after a server restart
          {num(p.attempt) !== undefined && num(p.attempt)! > 1 ? ` (${String(p.attempt)}× so far)` : ""}
        </li>
      )
    default:
      return null
  }
}

function PhaseIcon({ name }: { name: string }) {
  const cls = "size-4 text-primary"
  if (name === "analyst" || name === "intake") return <ScanSearch className={cls} />
  if (name === "builder") return <Hammer className={cls} />
  if (name === "critic") return <Eye className={cls} />
  return <Camera className={cls} />
}

function ToolIcon({ tool }: { tool: string }) {
  const cls = "mt-0.5 size-3.5 shrink-0 text-muted-foreground"
  if (tool === "render_views" || tool === "check_scene") return <Camera className={cls} />
  if (tool === "finish") return <CheckCircle2 className={cls} />
  return <FileCode2 className={cls} />
}

export function ScoreBadge({ score }: { score: number }) {
  const variant = score >= 80 ? "success" : score >= 60 ? "warning" : "destructive"
  return <Badge variant={variant}>{score}/100</Badge>
}
