import { useEffect, useRef } from "react"
import { Brain, Eye, Hammer, PenLine, ScanSearch, Wrench } from "lucide-react"
import type { LlmProgress } from "@/hooks/useJobStream"
import { cn } from "@/lib/utils"

const ROLE_LABEL: Record<string, string> = {
  intake: "Reading the plan sheets",
  builder: "Builder",
  critic: "Critic comparing renders with the reference",
}

function fmtTokens(n: number, estimated: boolean) {
  const s = n.toLocaleString()
  return estimated ? `≈${s} tokens` : `${s} tokens`
}

function fmtElapsed(s: number) {
  const m = Math.floor(s / 60)
  const r = Math.floor(s % 60)
  return m > 0 ? `${m}:${String(r).padStart(2, "0")}` : `${r}s`
}

function AutoScroll({ text, className }: { text: string; className?: string }) {
  const ref = useRef<HTMLPreElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [text])
  return (
    <pre ref={ref} className={cn("mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md p-2 text-[11px] leading-relaxed", className)}>
      {text}
      <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-primary/60 align-middle" />
    </pre>
  )
}

/**
 * What the model is doing right now: role, phase, growing token counter, elapsed
 * time, the reasoning summary while it thinks, and (for the builder) the text it
 * writes as it streams in.
 */
export function LiveStatus({
  progress,
  liveText,
  liveThought,
}: {
  progress: LlmProgress | null
  liveText: string
  liveThought: string
}) {
  if (!progress) return null
  const role = ROLE_LABEL[progress.role] ?? progress.role
  const stepLabel = progress.role === "builder" && progress.step ? ` · step ${progress.step}` : ""
  let phaseLabel = "thinking…"
  let Icon = Brain
  if (progress.phase === "writing") {
    phaseLabel = progress.role === "builder" ? "writing…" : "writing the report…"
    Icon = PenLine
  } else if (progress.phase === "tool_call") {
    phaseLabel = `calling ${progress.tool_name ?? "a tool"}…`
    Icon = Wrench
  }
  const RoleIcon = progress.role === "critic" ? Eye : progress.role === "intake" ? ScanSearch : Hammer
  const thought = liveThought.trim()

  return (
    <div className="mx-3 mb-3 rounded-lg border border-primary/30 bg-accent/40 p-3 text-sm">
      <div className="flex items-center gap-2">
        <span className="relative grid size-6 place-items-center rounded-full bg-primary/15 text-primary">
          <RoleIcon className="size-3.5" />
          <span className="absolute inset-0 animate-ping rounded-full bg-primary/20" />
        </span>
        <span className="font-medium">
          {role}
          <span className="text-muted-foreground">{stepLabel}</span>
        </span>
        <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">{fmtElapsed(progress.elapsed_s)}</span>
      </div>
      <div className="mt-1.5 flex items-center gap-2 pl-8 text-xs text-muted-foreground">
        <Icon className={cn("size-3.5", progress.phase === "thinking" && "animate-pulse")} />
        <span>{phaseLabel}</span>
        {progress.output_tokens > 0 && (
          <span className="font-mono tabular-nums">· {fmtTokens(progress.output_tokens, progress.estimated)}</span>
        )}
      </div>
      {thought && (progress.phase === "thinking" || !liveText) && (
        <AutoScroll text={thought} className="bg-background/50 font-sans italic text-muted-foreground" />
      )}
      {liveText && <AutoScroll text={liveText} className="bg-background/70 font-mono text-muted-foreground" />}
    </div>
  )
}
