import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { Columns2, Footprints, LogOut, Map as MapIcon, Mountain, Pause, Play, Sparkles } from "lucide-react"
import { useKits } from "@/api/endpoints/meta/meta"
import { useGetSurroundings } from "@/api/endpoints/surroundings/surroundings"
import type { KitInfo } from "@/api/model"
import { FloorPlanDialog, type PlanReply } from "@/components/FloorPlanDialog"
import { SurroundingsDialog, type SceneOutline } from "@/components/SurroundingsDialog"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const VIEWS = ["north", "east", "south", "west", "aerial"] as const

/**
 * How the scene is drawn. "fast" is the plain medium-quality path; "final" is the
 * presentation look (sky, sun, environment, horizon: the owner's picture, never what the
 * builder or the critic see); "ultra" adds progressive accumulation for soft shadows and
 * supersampled edges, converging while the camera rests.
 */
type Look = "fast" | "final" | "ultra"

const LOOKS: { id: Look; label: string; title: string }[] = [
  { id: "fast", label: "Fast", title: "Plain rendering: quickest to orbit" },
  { id: "final", label: "Final look", title: "Sky, sun and environment light, ambient occlusion" },
  { id: "ultra", label: "Ultra", title: "Final look plus soft shadows and supersampling, refined while the camera rests" },
]

function lookQuery(look: Look): string {
  return look === "fast" ? "quality=medium" : `look=${look === "final" ? "presentation" : "ultra"}`
}

const CHROME = "relative overflow-hidden rounded-xl border bg-[#d9e0e4]"

/** A room of the scene's floor plans, as the scene page announces it (walk mode). */
type Room = { name: string; use?: string; area?: number }

const TOUCH = typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)").matches

/**
 * Which renderer draws the scene: a snapshot of the kit (kit/versions/<name>, listed by the
 * API newest first) or "dev", the working copy. The build path is pinned to one snapshot;
 * the viewer shows the newest by default and can put two side by side on the same scene.
 */
function KitSelect({
  kits,
  value,
  onChange,
  className,
}: {
  kits: KitInfo[]
  value: string
  onChange: (name: string) => void
  className?: string
}) {
  const current = kits.find((k) => k.name === value)
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      title={current ? `${current.note}${current.pinned ? " · what the builder and the critic see" : ""}` : "renderer"}
      className={cn("h-7 max-w-44 rounded-md border bg-background px-1.5 text-[11px]", className)}
    >
      {kits.map((k) => (
        <option key={k.name} value={k.name}>
          {k.dev ? "dev (working copy)" : k.name}
          {k.pinned ? " · build" : ""}
        </option>
      ))}
    </select>
  )
}

/**
 * The interactive three.js scene in an iframe. With `sceneUrl === null` nothing is
 * loaded and `placeholder` is shown on the same chrome instead (project has no
 * version yet: the workspace only holds the kit's template box).
 */
export function SceneViewer({
  sceneUrl,
  reloadKey,
  className,
  placeholder,
  live = false,
  autoReload = true,
  onToggleAutoReload,
  planName = "plan",
  projectId,
  contextUrl,
}: {
  sceneUrl: string | null
  reloadKey?: string | number
  className?: string
  placeholder?: ReactNode
  /** a job is running: the viewer follows the build (reloads after every error-free render) */
  live?: boolean
  autoReload?: boolean
  onToggleAutoReload?: () => void
  /** start of the downloaded plans' file names (the project's name) */
  planName?: string
  /** the project (its surroundings can be fetched and aligned here) */
  projectId?: string
  /** surroundings to show without a project (the share page) */
  contextUrl?: string | null
}) {
  const ref = useRef<HTMLIFrameElement>(null)
  const refB = useRef<HTMLIFrameElement>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [look, setLook] = useState<Look>("final")
  const [effectsDropped, setEffectsDropped] = useState(false)
  const [progress, setProgress] = useState<{ count: number; total: number } | null>(null)
  // renderer: null = the newest snapshot once the list is known; compare = a second renderer
  // drawn next to it on the same scene, null = off
  // walk mode: the rooms the scene announced (none: no interior, no Walk button), the credit lines of
  // attribution-licensed models it uses, whether the visitor is walking and in which room
  const [rooms, setRooms] = useState<Room[]>([])
  const [credits, setCredits] = useState<string[]>([])
  const [walking, setWalking] = useState(false)
  const [room, setRoom] = useState("")
  const [kit, setKit] = useState<string | null>(null)
  const [compare, setCompare] = useState<string | null>(null)
  const [planOpen, setPlanOpen] = useState(false)
  const [surroundingsOpen, setSurroundingsOpen] = useState(false)
  // the real surroundings (#39): drawn by the scene page in the Final and Ultra looks only
  const surroundings = useGetSurroundings(projectId ?? "", { query: { enabled: Boolean(projectId) } })
  const ctx = projectId ? (surroundings.data?.exists ? surroundings.data : null) : null
  const ctxUrl = projectId ? (ctx?.url ?? null) : (contextUrl ?? null)
  const ctxKey = ctx ? `${ctx.fetched_at}-${ctx.alignment?.x}-${ctx.alignment?.z}-${ctx.alignment?.rotation}-${ctx.alignment?.ground}` : ""
  // requests waiting for the scene page's answer (plans, the outline), by id
  const planWaiting = useRef(new Map<number, (r: never) => void>())
  const planSeq = useRef(0)
  // the scene page answers once it is ready: requests made while it loads wait for it
  const frameReady = useRef(false)
  const readyWaiters = useRef<(() => void)[]>([])
  const kitsQuery = useKits({ query: { staleTime: 60 * 60 * 1000 } })
  const kits = kitsQuery.data?.kits ?? []
  const kitsKnown = !kitsQuery.isLoading
  const kitA = kit ?? kitsQuery.data?.latest ?? null
  const frameSrc = (name: string | null) =>
    sceneUrl === null
      ? null
      : `${sceneUrl}?${lookQuery(look)}&view=southeast${name ? `&kit=${encodeURIComponent(name)}` : ""}${
          look !== "fast" && ctxUrl ? `&context=${encodeURIComponent(ctxUrl)}&cv=${encodeURIComponent(ctxKey)}` : ""
        }&r=${reloadKey ?? ""}`
  // wait for the renderer list so the first load is already the right renderer
  // and for the surroundings, so the page loads once with them (not once without, then again)
  const ctxKnown = !projectId || !surroundings.isLoading
  const src = kitsKnown && ctxKnown ? frameSrc(kitA) : null
  const srcB = kitsKnown && compare ? frameSrc(compare) : null

  useEffect(() => {
    setReady(false)
    setError(null)
    setEffectsDropped(false)
    setProgress(null)
    setRooms([])
    setCredits([])
    setWalking(false)
    setRoom("")
    setPlanOpen(false)
    setSurroundingsOpen(false)
    frameReady.current = false
    if (src === null) return
    const onMsg = (e: MessageEvent) => {
      if (e.source !== ref.current?.contentWindow) return
      const d = e.data as {
        type?: string
        message?: string
        enabled?: boolean
        count?: number
        total?: number
        on?: boolean
        rooms?: Room[]
        credits?: string[]
        id?: number
      }
      if (d?.type === "house:ready") {
        setReady(true)
        frameReady.current = true
        for (const w of readyWaiters.current.splice(0)) w()
        setRooms(Array.isArray(d.rooms) ? d.rooms : [])
        setCredits(Array.isArray(d.credits) ? d.credits : [])
      }
      if (d?.type === "house:walking") setWalking(!!d.on)
      if ((d?.type === "house:plan2d" || d?.type === "house:outline") && typeof d.id === "number") {
        planWaiting.current.get(d.id)?.(d as never)
        planWaiting.current.delete(d.id)
      }
      if (d?.type === "house:error") setError(d.message ?? "error")
      if (d?.type === "house:effects" && d.enabled === false) setEffectsDropped(true)
      if (d?.type === "house:accumulate" && typeof d.count === "number" && typeof d.total === "number") {
        setProgress(d.count >= d.total ? null : { count: d.count, total: d.total })
      }
    }
    window.addEventListener("message", onMsg)
    return () => window.removeEventListener("message", onMsg)
  }, [src])

  const ask = useCallback(async <T,>(type: string, payload: Record<string, unknown> = {}) => {
    if (!frameReady.current) await new Promise<void>((r) => readyWaiters.current.push(r))
    const frame = ref.current?.contentWindow
    if (!frame) throw new Error("The scene is not loaded")
    const id = ++planSeq.current
    return new Promise<T>((resolve, reject) => {
      planWaiting.current.set(id, resolve as (r: never) => void)
      frame.postMessage({ type, id, ...payload }, "*")
      setTimeout(() => {
        if (planWaiting.current.delete(id)) reject(new Error("The scene did not answer: reload it and try again"))
      }, 20000)
    })
  }, [])
  const requestPlan = useCallback((index: number, furnished: boolean) => ask<PlanReply>("house:plan2d", { index, furnished }), [ask])
  const requestOutline = useCallback(() => ask<SceneOutline>("house:outline"), [ask])

  function setView(view: string) {
    for (const frame of [ref, refB]) frame.current?.contentWindow?.postMessage({ type: "house:setView", view }, "*")
  }

  function walk(on: boolean, into?: string) {
    ref.current?.contentWindow?.postMessage({ type: "house:walk", on, room: into }, "*")
    if (!on) setRoom("")
  }

  function goToRoom(name: string) {
    setRoom(name)
    ref.current?.contentWindow?.postMessage({ type: "house:jumpTo", room: name }, "*")
    ref.current?.focus() // the keys (W A S D) go to the scene
  }

  function toggleCompare() {
    if (compare) {
      setCompare(null)
      return
    }
    // the other renderer worth a look: the working copy next to a snapshot, the newest
    // snapshot next to the working copy
    const other = kits.find((k) => k.name !== kitA && (k.dev || k.name === kitsQuery.data?.latest))
    setCompare(other?.name ?? kits.find((k) => k.name !== kitA)?.name ?? null)
  }

  if (sceneUrl === null) return <div className={cn(CHROME, className)}>{placeholder}</div>

  const status = error ? (
    <span className="text-destructive">scene error: {error}</span>
  ) : !ready ? (
    "loading…"
  ) : progress ? (
    `refining ${progress.count}/${progress.total}`
  ) : walking ? (
    TOUCH ? "drag to look · tap the floor to go there" : "click to look with the mouse · W A S D to walk · Esc to release"
  ) : effectsDropped && look !== "fast" ? (
    "effects off: this machine renders them too slowly"
  ) : (
    "drag to orbit · scroll to zoom"
  )

  const frameChip = (name: string | null, onChange: (n: string) => void) =>
    kits.length > 1 && name ? (
      <div className="pointer-events-auto absolute right-3 top-3 flex items-center gap-1 rounded-lg border bg-background/85 p-1 shadow-sm backdrop-blur">
        <KitSelect kits={kits} value={name} onChange={onChange} />
      </div>
    ) : null

  return (
    <div className={cn(CHROME, className)}>
      {srcB ? (
        <div className="grid size-full grid-cols-2 gap-0.5">
          <div className="relative">
            {src && <iframe ref={ref} key={src} src={src} title="3D scene" className="size-full border-0" />}
            {frameChip(kitA, setKit)}
          </div>
          <div className="relative">
            <iframe ref={refB} key={srcB} src={srcB} title="3D scene, second renderer" className="size-full border-0" />
            {frameChip(compare, setCompare)}
          </div>
        </div>
      ) : (
        <>
          {src && <iframe ref={ref} key={src} src={src} title="3D scene" className="size-full border-0" />}
          {frameChip(kitA, setKit)}
        </>
      )}
      {live && (
        <div className="absolute left-3 top-3 flex items-center gap-1 rounded-lg border bg-background/85 p-1 shadow-sm backdrop-blur">
          <span className="flex items-center gap-1.5 px-1.5 text-[11px] font-medium">
            <span className={cn("size-2 rounded-full", autoReload ? "animate-pulse bg-emerald-500" : "bg-muted-foreground")} />
            {autoReload ? "live" : "paused"}
          </span>
          {onToggleAutoReload && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2"
              title={autoReload ? "Pause auto-reload to keep orbiting this state" : "Resume following the build"}
              onClick={onToggleAutoReload}
            >
              {autoReload ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
              {autoReload ? "Pause" : "Follow"}
            </Button>
          )}
        </div>
      )}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between p-3">
        <div className="pointer-events-auto flex flex-wrap gap-1 rounded-lg border bg-background/85 p-1 shadow-sm backdrop-blur">
          {walking ? (
            <>
              <select
                value={room}
                onChange={(e) => goToRoom(e.target.value)}
                title="Go to a room"
                className="h-7 max-w-48 rounded-md border bg-background px-1.5 text-[12px]"
              >
                <option value="" disabled>
                  Go to a room…
                </option>
                {rooms.map((r) => (
                  <option key={r.name} value={r.name}>
                    {r.name}
                    {r.area ? ` · ${r.area.toFixed(1)} m²` : ""}
                  </option>
                ))}
              </select>
              <Button size="sm" variant="ghost" className="h-7 px-2" title="Back to the view from outside" onClick={() => walk(false)}>
                <LogOut className="size-3.5" />
                Exit walk
              </Button>
            </>
          ) : (
            <>
              {VIEWS.map((v) => (
                <Button key={v} size="sm" variant="ghost" className="h-7 px-2 capitalize" onClick={() => setView(v)}>
                  {v}
                </Button>
              ))}
              {rooms.length > 0 && !compare && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2"
                  title="Walk through the rooms at eye height"
                  onClick={() => walk(true)}
                >
                  <Footprints className="size-3.5" />
                  Walk
                </Button>
              )}
              {rooms.length > 0 && !compare && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2"
                  title="The 2D plan of each storey, furnished or not, to download"
                  onClick={() => setPlanOpen(true)}
                >
                  <MapIcon className="size-3.5" />
                  Plan
                </Button>
              )}
              {projectId && ready && !compare && (
                <Button
                  size="sm"
                  variant={ctx ? "secondary" : "ghost"}
                  className="h-7 px-2"
                  title={
                    ctx
                      ? "The real surroundings (terrain, aerial photo, neighbours): shown in Final look and Ultra. Click to align or change them"
                      : "Add the real surroundings from Swiss public geodata"
                  }
                  onClick={() => {
                    setSurroundingsOpen(true)
                    if (look === "fast" && ctx) setLook("final")
                  }}
                >
                  <Mountain className="size-3.5" />
                  Surroundings
                </Button>
              )}
            </>
          )}
          <span className="mx-1 w-px self-stretch bg-border" />
          {LOOKS.map((l) => (
            <Button
              key={l.id}
              size="sm"
              variant={look === l.id ? "secondary" : "ghost"}
              className="h-7 px-2"
              title={
                l.id !== "fast" && effectsDropped && look === l.id
                  ? "Effects were disabled: this machine renders them too slowly"
                  : l.title
              }
              onClick={() => setLook(l.id)}
            >
              {l.id === "final" && <Sparkles className="size-3.5" />}
              {l.label}
            </Button>
          ))}
          {kits.length > 1 && !walking && (
            <>
              <span className="mx-1 w-px self-stretch bg-border" />
              <Button
                size="sm"
                variant={compare ? "secondary" : "ghost"}
                className="h-7 px-2"
                title="Draw the same scene with a second renderer next to this one"
                onClick={toggleCompare}
              >
                <Columns2 className="size-3.5" />
                Compare
              </Button>
            </>
          )}
        </div>
        <div className="flex flex-col items-end gap-1">
          {credits.length > 0 && (
            <span
              className="pointer-events-auto max-w-80 truncate rounded-md bg-background/85 px-2 py-0.5 text-[10px] text-muted-foreground backdrop-blur"
              title={credits.join("\n")}
            >
              Models: {credits.join(" · ")}
            </span>
          )}
          <span className="rounded-md bg-background/85 px-2 py-1 text-[11px] text-muted-foreground backdrop-blur">{status}</span>
        </div>
      </div>
      {planOpen && <FloorPlanDialog request={requestPlan} fileBase={planName} onClose={() => setPlanOpen(false)} />}
      {surroundingsOpen && projectId && (
        <SurroundingsDialog
          projectId={projectId}
          requestOutline={requestOutline}
          onChanged={() => {
            if (look === "fast") setLook("final")
          }}
          onClose={() => setSurroundingsOpen(false)}
        />
      )}
    </div>
  )
}
