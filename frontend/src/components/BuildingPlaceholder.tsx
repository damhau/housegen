import { AlertTriangle, Brain, Eye, Hammer, Loader2, PenLine, Play, Wrench } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { JobEvent, LlmProgress } from "@/hooks/useJobStream"

const THUMB_VIEWS = ["south", "aerial", "north", "east", "west"]

function latestRender(events: JobEvent[]): { view: string; url: string } | null {
  for (const e of [...events].reverse()) {
    if (e.type !== "render") continue
    const renders = (e.payload.renders as Record<string, string> | undefined) ?? {}
    const view = THUMB_VIEWS.find((v) => renders[v]) ?? Object.keys(renders)[0]
    const url = view === undefined ? undefined : renders[view]
    if (view !== undefined && url !== undefined) return { view, url }
  }
  return null
}

function latestPhase(events: JobEvent[]): string | null {
  for (const e of [...events].reverse()) {
    if (e.type === "phase") return (e.payload.message as string | undefined) ?? null
  }
  return null
}

function progressLine(p: LlmProgress): { Icon: typeof Brain; text: string } {
  const who = p.role === "critic" ? "Critic" : "Builder"
  const step = p.role === "builder" && p.step ? ` (step ${p.step})` : ""
  if (p.phase === "writing") return { Icon: PenLine, text: `${who}${step} is writing…` }
  if (p.phase === "tool_call") return { Icon: Wrench, text: `${who}${step} is calling ${p.tool_name ?? "a tool"}…` }
  return { Icon: p.role === "critic" ? Eye : Brain, text: `${who}${step} is thinking…` }
}

/**
 * What the viewer shows while a project has no scene version yet: a clear
 * "in progress" state fed by the job's events instead of the kit's placeholder box.
 */
export function BuildingPlaceholder({
  running,
  events,
  progress,
  elapsed,
  failedMessage,
  canGenerate,
  onGenerate,
}: {
  running: boolean
  events: JobEvent[]
  progress: LlmProgress | null
  elapsed: string | null
  failedMessage: string | null
  canGenerate: boolean
  onGenerate: () => void
}) {
  const thumb = latestRender(events)
  const phase = latestPhase(events)
  const live = progress && running ? progressLine(progress) : null

  return (
    <div className="absolute inset-0 grid place-items-center p-6">
      <div className="flex w-full max-w-md flex-col items-center gap-4 text-center">
        {running ? (
          <>
            <span className="relative grid size-12 place-items-center rounded-full bg-primary/15 text-primary">
              <Hammer className="size-5" />
              <span className="absolute inset-0 animate-ping rounded-full bg-primary/20" />
            </span>
            <div>
              <h2 className="text-base font-semibold tracking-tight">Building your house</h2>
              <p className="mt-1 text-sm text-muted-foreground">{phase ?? "Starting the agent…"}</p>
            </div>
            {live && (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <live.Icon className="size-3.5" />
                {live.text}
              </p>
            )}
            {elapsed && (
              <p className="flex items-center gap-1.5 font-mono text-xs tabular-nums text-muted-foreground">
                <Loader2 className="size-3 animate-spin" /> {elapsed}
              </p>
            )}
          </>
        ) : failedMessage ? (
          <>
            <span className="grid size-12 place-items-center rounded-full bg-destructive/10 text-destructive">
              <AlertTriangle className="size-5" />
            </span>
            <div>
              <h2 className="text-base font-semibold tracking-tight">The run did not finish</h2>
              <p className="mt-1 max-w-sm break-words text-sm text-muted-foreground">{failedMessage}</p>
            </div>
            {canGenerate && (
              <Button size="sm" onClick={onGenerate}>
                <Play /> Try again
              </Button>
            )}
          </>
        ) : (
          <>
            <span className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground">
              <Hammer className="size-5" />
            </span>
            <div>
              <h2 className="text-base font-semibold tracking-tight">No scene yet</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                The agent reads the plan and the photos, then builds the house here.
              </p>
            </div>
            {canGenerate && (
              <Button size="sm" onClick={onGenerate}>
                <Play /> Generate
              </Button>
            )}
          </>
        )}
        {thumb && (
          <figure className="mt-2 w-full">
            <img src={thumb.url} alt={`Latest render, ${thumb.view} view`} className="mx-auto max-h-48 rounded-lg border object-contain shadow-sm" />
            <figcaption className="mt-1 text-[11px] text-muted-foreground">
              {running ? "Latest render, " : "Last render before it stopped, "}
              {thumb.view} view
            </figcaption>
          </figure>
        )}
      </div>
    </div>
  )
}
