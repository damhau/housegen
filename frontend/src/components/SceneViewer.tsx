import { useEffect, useRef, useState, type ReactNode } from "react"
import { Columns2, Pause, Play, Sparkles } from "lucide-react"
import { useKits } from "@/api/endpoints/meta/meta"
import type { KitInfo } from "@/api/model"
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
}: {
  sceneUrl: string | null
  reloadKey?: string | number
  className?: string
  placeholder?: ReactNode
  /** a job is running: the viewer follows the build (reloads after every error-free render) */
  live?: boolean
  autoReload?: boolean
  onToggleAutoReload?: () => void
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
  const [kit, setKit] = useState<string | null>(null)
  const [compare, setCompare] = useState<string | null>(null)
  const kitsQuery = useKits({ query: { staleTime: 60 * 60 * 1000 } })
  const kits = kitsQuery.data?.kits ?? []
  const kitsKnown = !kitsQuery.isLoading
  const kitA = kit ?? kitsQuery.data?.latest ?? null
  const frameSrc = (name: string | null) =>
    sceneUrl === null
      ? null
      : `${sceneUrl}?${lookQuery(look)}&view=southeast${name ? `&kit=${encodeURIComponent(name)}` : ""}&r=${reloadKey ?? ""}`
  // wait for the renderer list so the first load is already the right renderer
  const src = kitsKnown ? frameSrc(kitA) : null
  const srcB = kitsKnown && compare ? frameSrc(compare) : null

  useEffect(() => {
    setReady(false)
    setError(null)
    setEffectsDropped(false)
    setProgress(null)
    if (src === null) return
    const onMsg = (e: MessageEvent) => {
      if (e.source !== ref.current?.contentWindow) return
      const d = e.data as { type?: string; message?: string; enabled?: boolean; count?: number; total?: number }
      if (d?.type === "house:ready") setReady(true)
      if (d?.type === "house:error") setError(d.message ?? "error")
      if (d?.type === "house:effects" && d.enabled === false) setEffectsDropped(true)
      if (d?.type === "house:accumulate" && typeof d.count === "number" && typeof d.total === "number") {
        setProgress(d.count >= d.total ? null : { count: d.count, total: d.total })
      }
    }
    window.addEventListener("message", onMsg)
    return () => window.removeEventListener("message", onMsg)
  }, [src])

  function setView(view: string) {
    for (const frame of [ref, refB]) frame.current?.contentWindow?.postMessage({ type: "house:setView", view }, "*")
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
        <div className="pointer-events-auto flex gap-1 rounded-lg border bg-background/85 p-1 shadow-sm backdrop-blur">
          {VIEWS.map((v) => (
            <Button key={v} size="sm" variant="ghost" className="h-7 px-2 capitalize" onClick={() => setView(v)}>
              {v}
            </Button>
          ))}
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
          {kits.length > 1 && (
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
        <span className="rounded-md bg-background/85 px-2 py-1 text-[11px] text-muted-foreground backdrop-blur">{status}</span>
      </div>
    </div>
  )
}
