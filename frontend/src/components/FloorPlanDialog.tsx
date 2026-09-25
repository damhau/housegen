import { useCallback, useEffect, useState } from "react"
import "@fontsource/archivo/400.css"
import "@fontsource/archivo/600.css"
import "@fontsource/archivo/700.css"
import archivo400 from "@fontsource/archivo/files/archivo-latin-400-normal.woff2?url"
import archivo600 from "@fontsource/archivo/files/archivo-latin-600-normal.woff2?url"
import archivo700 from "@fontsource/archivo/files/archivo-latin-700-normal.woff2?url"
import { Download, Loader2, Map as MapIcon, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn, errorMessage } from "@/lib/utils"

/** A storey the scene can draw, as the scene page lists it. */
export type PlanStorey = { index: number; y: number; label: string }
/** The scene page's answer to a plan request (kit/plan2d.js through the runtime). */
export type PlanReply = { storeys: PlanStorey[]; index: number; svg: string | null; error: string | null }

type Content = "furnished" | "fittings"

/** The plan's font, embedded so a downloaded SVG or PNG looks the same everywhere. */
let fontCss: Promise<string> | null = null
function embeddedFonts(): Promise<string> {
  fontCss ??= Promise.all(
    [
      [archivo400, 400],
      [archivo600, 600],
      [archivo700, 700],
    ].map(async ([url, weight]) => {
      const bytes = new Uint8Array(await (await fetch(url as string)).arrayBuffer())
      let bin = ""
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
      return `@font-face{font-family:'Archivo';font-weight:${weight};src:url(data:font/woff2;base64,${btoa(bin)}) format('woff2')}`
    }),
  ).then((faces) => faces.join(""))
  return fontCss
}

async function standalone(svg: string): Promise<string> {
  const css = await embeddedFonts()
  return svg.replace(/^<svg([^>]*)>/, `<svg$1><defs><style>${css}</style></defs>`)
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function toPng(svg: string, longSide = 2800): Promise<Blob> {
  const w = Number(/width="(\d+)"/.exec(svg)?.[1] ?? 1200)
  const h = Number(/height="(\d+)"/.exec(svg)?.[1] ?? 900)
  const scale = longSide / Math.max(w, h)
  const img = new Image()
  img.src = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }))
  await img.decode()
  const canvas = document.createElement("canvas")
  canvas.width = Math.round(w * scale)
  canvas.height = Math.round(h * scale)
  const ctx = canvas.getContext("2d")
  if (!ctx) throw new Error("This browser cannot draw the picture")
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  URL.revokeObjectURL(img.src)
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("The picture could not be made"))), "image/png"),
  )
}

const slug = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase()

function Segmented<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex flex-wrap gap-0.5 rounded-lg border bg-background p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-md px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            o.value === value && "bg-secondary font-semibold text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/**
 * The 2D plan of the version on screen, drawn by the scene page from its own model: rooms coloured by
 * use, walls, doors and windows, furniture as plan symbols, each room's name and area. One storey at
 * a time, furnished or with the fittings only; downloads as SVG (vector) or PNG.
 */
export function FloorPlanDialog({
  request,
  fileBase,
  onClose,
}: {
  request: (index: number, furnished: boolean) => Promise<PlanReply>
  fileBase: string
  onClose: () => void
}) {
  const [storeys, setStoreys] = useState<PlanStorey[]>([])
  const [index, setIndex] = useState<number | null>(null)
  const [content, setContent] = useState<Content>("furnished")
  const [plans, setPlans] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<"svg" | "png" | null>(null)

  const key = index === null ? null : `${index}-${content}`
  const svg = key ? plans[key] : undefined

  // first ask: the storeys and the plan the scene starts with (the one nearest the ground)
  useEffect(() => {
    let live = true
    request(0, true)
      .then((r) => {
        if (!live) return
        if (!r.storeys.length) return setError("This version has no interior to draw: furnish it first.")
        setStoreys(r.storeys)
        const ground = r.storeys.reduce<PlanStorey | null>((a, s) => (a && Math.abs(a.y) <= Math.abs(s.y) ? a : s), null)
        setIndex(ground?.index ?? 0)
        if (r.svg) setPlans((p) => ({ ...p, [`${r.index}-furnished`]: r.svg as string }))
      })
      .catch((e) => live && setError(errorMessage(e)))
    return () => {
      live = false
    }
  }, [request])

  useEffect(() => {
    if (index === null || !key || plans[key]) return
    let live = true
    setError(null)
    request(index, content === "furnished")
      .then((r) => {
        if (!live) return
        if (r.error || !r.svg) return setError(r.error ?? "The plan could not be drawn")
        setPlans((p) => ({ ...p, [key]: r.svg as string }))
      })
      .catch((e) => live && setError(errorMessage(e)))
    return () => {
      live = false
    }
  }, [index, content, key, plans, request])

  const step = useCallback(
    (d: number) => setIndex((i) => (i === null ? i : Math.max(0, Math.min(storeys.length - 1, i + d)))),
    [storeys.length],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
      if (e.key === "ArrowRight" || e.key === "ArrowUp") step(1)
      if (e.key === "ArrowLeft" || e.key === "ArrowDown") step(-1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose, step])

  const storey = storeys.find((s) => s.index === index)
  const name = `${slug(fileBase) || "plan"}-${slug(storey?.label ?? "plan")}${content === "fittings" ? "-fittings" : ""}`

  async function download(kind: "svg" | "png") {
    if (!svg) return
    setSaving(kind)
    try {
      const full = await standalone(svg)
      save(kind === "svg" ? new Blob([full], { type: "image/svg+xml" }) : await toPng(full), `${name}.${kind}`)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/35 p-3 sm:p-6" onClick={onClose} role="presentation">
      <div
        role="dialog"
        aria-label="Floor plan"
        className="flex h-[min(94vh,1000px)] w-full max-w-6xl flex-col overflow-hidden rounded-xl border bg-card text-sm shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-4">
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-lg bg-secondary">
              <MapIcon className="size-4" />
            </span>
            <div>
              <h2 className="font-semibold">Floor plan</h2>
              <p className="text-xs text-muted-foreground">
                Drawn from this version's model. Areas marked ≈ are measured on it; the others are read from the plans.
              </p>
            </div>
          </div>
          <Button size="sm" variant="ghost" className="h-7 px-2" onClick={onClose} aria-label="Close">
            <X className="size-4" />
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-y bg-muted/30 px-5 py-2.5">
          {storeys.length > 0 && index !== null && (
            <Segmented
              label="Storey"
              options={storeys.map((s) => ({ value: s.index, label: s.label }))}
              value={index}
              onChange={setIndex}
            />
          )}
          <Segmented<Content>
            label="Content"
            options={[
              { value: "furnished", label: "Furnished" },
              { value: "fittings", label: "Fittings only" },
            ]}
            value={content}
            onChange={setContent}
          />
          <div className="ml-auto flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Download</span>
            {(["svg", "png"] as const).map((k) => (
              <Button
                key={k}
                size="sm"
                variant="outline"
                className="h-7 px-2.5"
                disabled={!svg || saving !== null}
                onClick={() => void download(k)}
                title={k === "svg" ? "Vector: sharp at any size, opens in Illustrator or Inkscape" : "Picture, 2800 px on its long side"}
              >
                {saving === k ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
                {k.toUpperCase()}
              </Button>
            ))}
          </div>
        </div>

        <div className="relative min-h-0 flex-1 overflow-auto bg-[#eef0ee] p-3 sm:p-6 dark:bg-muted/40">
          {svg ? (
            <div
              className="mx-auto h-full w-fit max-w-full rounded-lg bg-white p-2 shadow-[0_1px_3px_rgba(0,0,0,0.08),0_8px_24px_rgba(0,0,0,0.08)] [&_svg]:block [&_svg]:h-full [&_svg]:max-h-full [&_svg]:w-auto [&_svg]:max-w-full"
              // the scene page's own drawing (kit/plan2d.js escapes every text it writes)
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          ) : (
            <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
              {error ? (
                <span className="max-w-md text-center text-destructive">{error}</span>
              ) : (
                <>
                  <Loader2 className="size-4 animate-spin" /> Drawing the plan…
                </>
              )}
            </div>
          )}
          {svg && error && (
            <p className="absolute inset-x-0 bottom-2 text-center text-xs text-destructive">{error}</p>
          )}
        </div>
      </div>
    </div>
  )
}
