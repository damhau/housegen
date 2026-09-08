import { useEffect, useRef, useState, type ReactNode } from "react"
import { Pause, Play, Sparkles } from "lucide-react"
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
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [look, setLook] = useState<Look>("final")
  const [effectsDropped, setEffectsDropped] = useState(false)
  const [progress, setProgress] = useState<{ count: number; total: number } | null>(null)
  const src = sceneUrl === null ? null : `${sceneUrl}?${lookQuery(look)}&view=southeast&r=${reloadKey ?? ""}`

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
    ref.current?.contentWindow?.postMessage({ type: "house:setView", view }, "*")
  }

  if (src === null) return <div className={cn(CHROME, className)}>{placeholder}</div>

  const status = error ? (
    <span className="text-destructive">scene error: {error}</span>
  ) : !ready ? (
    "loading…"
  ) : progress ? (
    `refining ${progress.count}/${progress.total}`
  ) : (
    "drag to orbit · scroll to zoom"
  )

  return (
    <div className={cn(CHROME, className)}>
      <iframe ref={ref} key={src} src={src} title="3D scene" className="size-full border-0" />
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
        </div>
        <span className="rounded-md bg-background/85 px-2 py-1 text-[11px] text-muted-foreground backdrop-blur">{status}</span>
      </div>
    </div>
  )
}
