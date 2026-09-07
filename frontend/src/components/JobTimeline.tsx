import { useEffect, useRef } from "react"
import { AlertTriangle, Camera, CheckCircle2, Eye, FileCode2, Hammer, Loader2, ScanSearch, Sparkles } from "lucide-react"
import type { JobEvent, LlmProgress } from "@/hooks/useJobStream"
import { LiveStatus } from "@/components/LiveStatus"
import { Markdown } from "@/components/Markdown"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

type P = Record<string, unknown>
const str = (v: unknown) => (typeof v === "string" ? v : "")
const num = (v: unknown) => (typeof v === "number" ? v : undefined)

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

  if (events.length === 0 && !live) return <p className="p-3 text-sm text-muted-foreground">No activity yet.</p>

  return (
    <div>
      <ol className="space-y-1.5 p-3 text-sm">
        {events.map((ev) => (
          <Row key={ev.seq} ev={ev} />
        ))}
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
      const issues = (p.issues as { severity: string; view: string; description: string }[] | undefined) ?? []
      const score = num(p.score) ?? 0
      return (
        <li className="ml-6 rounded-md border bg-muted/40 p-2">
          <div className="flex items-start gap-2">
            <ScoreBadge score={score} />
            <Markdown text={str(p.summary)} className="min-w-0" />
          </div>
          {issues.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              {issues.slice(0, 8).map((i, k) => (
                <li key={k}>
                  <span className={cn("font-medium", i.severity === "major" ? "text-destructive" : "")}>[{i.severity}]</span> {i.view}: {i.description}
                </li>
              ))}
            </ul>
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
    case "usage":
      return (
        <li className="ml-6 text-xs text-muted-foreground">
          tokens: {String(p.input_tokens)} in / {String(p.output_tokens)} out{num(p.cache_read_tokens) ? ` (${String(p.cache_read_tokens)} cached)` : ""}
          {num(p.critic_output_tokens) !== undefined && ` · critic ${String(p.critic_input_tokens)} in / ${String(p.critic_output_tokens)} out`}
        </li>
      )
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
