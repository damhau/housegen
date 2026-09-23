import { useEffect, useState } from "react"
import { Loader2, Sofa, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { errorMessage } from "@/lib/utils"

const EXAMPLES = [
  "The whole house",
  "Ground floor only",
  "Scandinavian, light oak and white",
  "A home office in the smallest bedroom",
]

/**
 * Start the interior job (#33): the builder draws the rooms, partitions and doors from the floor
 * plans, checks them against the sheets, then furnishes and lights the rooms. The owner may say
 * which floor or flat, a style, a use per room; empty = the whole house as the plans draw it.
 */
export function FurnishDialog({
  estimate,
  onStart,
  onClose,
}: {
  estimate: string | null
  onStart: (message: string) => Promise<void>
  onClose: () => void
}) {
  const [message, setMessage] = useState("")
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
      await onStart(message.trim())
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

        <div className="flex flex-col gap-2">
          <label htmlFor="furnish-message" className="font-medium">
            Anything to specify? <span className="font-normal text-muted-foreground">(optional)</span>
          </label>
          <Textarea
            id="furnish-message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Which floor or flat, a style, a use for a room… Leave empty for the whole house."
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
