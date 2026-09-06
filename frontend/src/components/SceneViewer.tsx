import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const VIEWS = ["north", "east", "south", "west", "aerial"] as const

export function SceneViewer({ sceneUrl, reloadKey, className }: { sceneUrl: string; reloadKey?: string | number; className?: string }) {
  const ref = useRef<HTMLIFrameElement>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const src = `${sceneUrl}?quality=medium&view=southeast&r=${reloadKey ?? ""}`

  useEffect(() => {
    setReady(false)
    setError(null)
    const onMsg = (e: MessageEvent) => {
      if (e.source !== ref.current?.contentWindow) return
      const d = e.data as { type?: string; message?: string }
      if (d?.type === "house:ready") setReady(true)
      if (d?.type === "house:error") setError(d.message ?? "error")
    }
    window.addEventListener("message", onMsg)
    return () => window.removeEventListener("message", onMsg)
  }, [src])

  function setView(view: string) {
    ref.current?.contentWindow?.postMessage({ type: "house:setView", view }, "*")
  }

  return (
    <div className={cn("relative overflow-hidden rounded-xl border bg-[#dfe6ea]", className)}>
      <iframe ref={ref} key={src} src={src} title="3D scene" className="size-full border-0" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between p-3">
        <div className="pointer-events-auto flex gap-1 rounded-lg border bg-background/85 p-1 shadow-sm backdrop-blur">
          {VIEWS.map((v) => (
            <Button key={v} size="sm" variant="ghost" className="h-7 px-2 capitalize" onClick={() => setView(v)}>
              {v}
            </Button>
          ))}
        </div>
        <span className="rounded-md bg-background/85 px-2 py-1 text-[11px] text-muted-foreground backdrop-blur">
          {error ? <span className="text-destructive">scene error: {error}</span> : ready ? "drag to orbit · scroll to zoom" : "loading…"}
        </span>
      </div>
    </div>
  )
}
