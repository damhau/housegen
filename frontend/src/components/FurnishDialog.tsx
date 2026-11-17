import { useEffect, useState } from "react"
import { Loader2, Sofa, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { HOUSE_STYLES, Swatches, type HouseStyle } from "@/lib/styles"
import { cn, errorMessage } from "@/lib/utils"

const EXAMPLES = [
  "The whole house",
  "Ground floor only",
  "A home office in the smallest bedroom",
]

/**
 * Start the interior job (#33): the builder draws the rooms, partitions and doors from the floor
 * plans, checks them against the sheets, then furnishes and lights the rooms. The owner may say
 * which floor or flat, a use per room; empty = the whole house as the plans draw it. The house style
 * (#101) is kept in src/style.js: a Restyle switches it later without a run.
 */
export function FurnishDialog({
  estimate,
  onStart,
  onClose,
  initialMessage = "",
  initialStyle,
}: {
  estimate: string | null
  onStart: (message: string, style: HouseStyle) => Promise<void>
  onClose: () => void
  /** the request to start from (Furnish again, from the style picker) */
  initialMessage?: string
  /** the style of the house on screen, else the default */
  initialStyle?: HouseStyle | null
}) {
  const [message, setMessage] = useState(initialMessage)
  const [style, setStyle] = useState<HouseStyle>(initialStyle ?? "scandinavian")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [busy, onClose])

  async function start() {
    setBusy(true)
    setError(null)
    try {
      await onStart(message.trim(), style)
      onClose()
    } catch (e) {
      setError(errorMessage(e))
      setBusy(false)
    }
  }

  const add = (text: string) => setMessage((m) => (m.trim() ? `${m.trim()}. ${text}` : text))

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4"
      onClick={() => !busy && onClose()}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label="Furnish the interior"
        className="flex w-full max-w-lg flex-col gap-4 rounded-xl border bg-card p-5 text-sm shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="flex size-8 items-center justify-center rounded-lg bg-secondary">
              <Sofa className="size-4" />
            </span>
            <div>
              <h2 className="font-semibold">Furnish the interior</h2>
              <p className="text-xs text-muted-foreground">From the floor plans, on top of the current version</p>
            </div>
          </div>
          <Button size="sm" variant="ghost" className="h-7 px-2" onClick={onClose} disabled={busy} aria-label="Close">
            <X className="size-4" />
          </Button>
        </div>

        <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
          <li>Rooms, partitions and doors, checked against the plan sheets</li>
          <li>Furniture room by room, leaving room to walk and to open every door</li>
          <li>Lights, then pictures of every room: walk through them with the Walk button</li>
        </ol>

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-2 font-medium">Style</legend>
          <div role="radiogroup" aria-label="Style" className="grid gap-2 sm:grid-cols-2">
            {HOUSE_STYLES.map((s) => (
              <button
                key={s.id}
                type="button"
                role="radio"
                aria-checked={style === s.id}
                onClick={() => setStyle(s.id)}
                disabled={busy}
                className={cn(
                  "flex flex-col gap-1.5 rounded-lg border p-2.5 text-left transition-colors hover:bg-secondary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  style === s.id && "border-primary bg-secondary/60 ring-1 ring-primary",
                )}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="font-medium">{s.label}</span>
                  <Swatches colors={s.swatches} />
                </span>
                <span className="text-xs leading-snug text-muted-foreground">{s.blurb}</span>
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">Kept with the house: switch it later with Style in the viewer, without a new run.</p>
        </fieldset>

        <div className="flex flex-col gap-2">
          <label htmlFor="furnish-message" className="font-medium">
            Anything to specify? <span className="font-normal text-muted-foreground">(optional)</span>
          </label>
          <Textarea
            id="furnish-message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Which floor or flat, a use for a room… Leave empty for the whole house."
            rows={3}
            maxLength={4000}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void start()
            }}
          />
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((x) => (
              <button
                key={x}
                type="button"
                onClick={() => add(x)}
                className="rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
              >
                {x}
              </button>
            ))}
          </div>
        </div>

        {error && <p className="text-xs text-destructive">{error}</p>}

        <div className="flex items-center justify-between gap-3 border-t pt-4">
          <span className="text-xs text-muted-foreground">{estimate ? `Estimated ${estimate}` : ""}</span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => void start()} disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <Sofa />} Start
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
