import { Play } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { JobEvent } from "@/hooks/useJobStream"

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

/**
 * What the viewer shows while a project has no scene version yet: a heading, one line
 * saying what the agent is doing, and the latest render when there is one. Deliberately
 * static (no icons, spinners or counters): the live detail lives in the conversation.
 */
export function BuildingPlaceholder({
  running,
  kind,
  events,
  failedMessage,
  awaitingAnswers,
  hasPhotos,
  canGenerate,
  onGenerate,
}: {
  running: boolean
  /** kind of the running job (intake reads the plans, generate builds) */
  kind: string | null
  events: JobEvent[]
  failedMessage: string | null
  /** the plans were read; the build starts from the answers in the conversation */
  awaitingAnswers: boolean
  hasPhotos: boolean
  canGenerate: boolean
  onGenerate: () => void
}) {
  const thumb = latestRender(events)
  const phase = latestPhase(events)
  const reading = kind === "intake"

  return (
    <div className="absolute inset-0 grid place-items-center p-6">
      <div className="flex w-full max-w-md flex-col items-center gap-4 text-center">
        {running ? (
          <div>
            <h2 className="text-base font-semibold tracking-tight">{reading ? "Reading your plans" : "Building your house"}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{phase ?? "Starting the agent…"}</p>
          </div>
        ) : failedMessage ? (
          <>
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
            <div>
              <h2 className="text-base font-semibold tracking-tight">No scene yet</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {awaitingAnswers
                  ? "The plans are read. Answer the questions in the conversation to start the build."
                  : hasPhotos
                    ? "The agent reads the plans and the photos, then builds the house here."
                    : "The agent reads the plans and asks you a few questions, then builds the house here."}
              </p>
            </div>
            {canGenerate && !awaitingAnswers && (
              <Button size="sm" onClick={onGenerate}>
                <Play /> {hasPhotos ? "Generate" : "Read the plans"}
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
