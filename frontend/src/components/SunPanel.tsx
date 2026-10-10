import { useCallback, useEffect, useRef, useState } from "react"
import { Loader2, Pause, Play, Sun, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

/** The scene page's answer to house:sun (kit/sunstudy.js): Swiss local hours, degrees. */
export type SunReply = {
  sun: {
    date: string
    hour: number
    elevation: number
    azimuth: number
    horizon: number
    lit: boolean
    sunrise: number | null
    noon: number
    sunset: number | null
    firstSun: number | null
    lastSun: number | null
  } | null
  error: string | null
}

/** The scene page's answer to house:sunHours: hours of direct sun a day (a season: on average). */
export type SunHoursReply = {
  result: { span: "day" | "season"; date: string; from: string; to: string; max: number; best: number; mean: number; steps: number; ms: number } | null
  error: string | null
}

// the map's colours, from no sun to all the day's (kit/sunstudy.js RAMP)
const RAMP = ["#2b3a8f", "#2f7fc1", "#3fb39b", "#9bd15a", "#f2d64b", "#f08a3a", "#d6402b"]
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

const clock = (h: number | null | undefined) => {
  if (h === null || h === undefined || !Number.isFinite(h)) return "—"
  const m = Math.round(h * 60)
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`
}
const dayLabel = (iso: string) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString("fr-CH", { day: "numeric", month: "long", timeZone: "UTC" })

/**
 * The sun study (#49): the real sun over the plot for a day and an hour, its shadows, and the
 * sunshine-hours map of a day or a season. The scene page does the work (house:sun, house:sunHours);
 * closing the panel gives the page its own sun back.
 */
export function SunPanel({
  ask,
  post,
  progress,
  readyKey,
  onClose,
}: {
  ask: <T>(type: string, payload?: Record<string, unknown>, timeoutMs?: number) => Promise<T>
  post: (message: Record<string, unknown>) => void
  progress: { done: number; total: number } | null
  /** changes when the scene page loads again: the panel sets its sun again */
  readyKey: string
  onClose: () => void
}) {
  const year = new Date().getFullYear()
  const presets = [
    { label: "21 déc.", date: `${year}-12-21` },
    { label: "20 mars", date: `${year}-03-20` },
    { label: "21 juin", date: `${year}-06-21` },
    { label: "23 sept.", date: `${year}-09-23` },
  ]
  const [date, setDate] = useState(`${year}-06-21`)
  const [hour, setHour] = useState(15)
  const [sun, setSun] = useState<SunReply["sun"]>(null)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [map, setMap] = useState<SunHoursReply["result"]>(null)
  const [mapping, setMapping] = useState<"day" | "season" | null>(null)
  const busy = useRef(false)
  const wanted = useRef<{ date: string; hour: number } | null>(null)

  // one request at a time; the latest wanted sun wins (a slider sends many)
  const send = useCallback(
    async (d: string, h: number) => {
      wanted.current = { date: d, hour: h }
      if (busy.current) return
      busy.current = true
      try {
        while (wanted.current) {
          const w = wanted.current
          wanted.current = null
          const r = await ask<SunReply>("house:sun", { date: w.date, hour: w.hour })
          if (r.error) setError(r.error)
          else {
            setError(null)
            setSun(r.sun)
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        busy.current = false
      }
    },
    [ask],
  )

  useEffect(() => {
    void send(date, hour)
  }, [date, hour, send, readyKey])

  // closing the panel: the page's own sun and sky again, no map
  useEffect(() => () => post({ type: "house:sun", off: true }), [post])

  // play the day: from the first sun over the hills to the last, ten minutes a step
  useEffect(() => {
    if (!playing || !sun) return
    const from = sun.firstSun ?? sun.sunrise ?? 6
    const to = sun.lastSun ?? sun.sunset ?? 20
    const t = setInterval(() => {
      setHour((h) => {
        const next = h + 1 / 6
        if (next > to) {
          setPlaying(false)
          return to
        }
        return next < from ? from : next
      })
    }, 250)
    return () => clearInterval(t)
  }, [playing, sun])

  async function runMap(span: "day" | "season") {
    setMapping(span)
    setError(null)
    try {
      const r = await ask<SunHoursReply>("house:sunHours", { date, span }, 300000)
      if (r.error) setError(r.error)
      setMap(r.result)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setMapping(null)
    }
  }

  function hideMap() {
    post({ type: "house:sunHours", off: true, id: -1 })
    setMap(null)
  }

  const lo = Math.floor(sun?.sunrise ?? 5)
  const hi = Math.ceil(sun?.sunset ?? 22)
  const where = sun ? COMPASS[Math.round(((sun.azimuth % 360) + 360) / 45) % 8] : ""

  return (
    <div className="absolute right-3 top-14 z-20 w-[300px] rounded-lg border bg-card/95 p-3 text-xs shadow-lg backdrop-blur">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5 font-semibold">
          <Sun className="size-3.5" /> Sun
        </div>
        <Button size="sm" variant="ghost" className="h-6 px-1.5" onClick={onClose} aria-label="Close the sun study">
          <X className="size-3.5" />
        </Button>
      </div>

      <div className="flex flex-wrap gap-1">
        {presets.map((p) => (
          <Button key={p.date} size="sm" variant={date === p.date ? "secondary" : "outline"} className="h-6 px-2 text-[11px]" onClick={() => setDate(p.date)}>
            {p.label}
          </Button>
        ))}
        <input
          type="date"
          value={date}
          onChange={(e) => e.target.value && setDate(e.target.value)}
          className="h-6 rounded-md border bg-background px-1.5 text-[11px]"
          aria-label="Day"
        />
      </div>

      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" variant="outline" className="h-7 w-7 p-0" onClick={() => setPlaying((v) => !v)} aria-label={playing ? "Pause" : "Play the day"}>
          {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
        </Button>
        <input
          type="range"
          min={lo}
          max={hi}
          step={1 / 12}
          value={hour}
          onChange={(e) => {
            setPlaying(false)
            setHour(Number(e.target.value))
          }}
          className="flex-1"
          aria-label="Hour"
        />
        <span className="w-10 text-right font-mono tabular-nums">{clock(hour)}</span>
      </div>

      <div className="mt-2 space-y-0.5 text-muted-foreground">
        {sun ? (
          <>
            <div className="text-foreground">
              {sun.lit
                ? `Sun ${sun.elevation.toFixed(1)}° high, ${where} (${sun.azimuth.toFixed(0)}°)`
                : sun.elevation > 0
                  ? `The sun is behind the hills (${sun.elevation.toFixed(1)}°, the relief ${sun.horizon.toFixed(1)}°)`
                  : "The sun is down"}
            </div>
            <div>
              Sunrise {clock(sun.sunrise)} · sunset {clock(sun.sunset)}
            </div>
            <div>
              Sun over the hills {clock(sun.firstSun)} – {clock(sun.lastSun)}
            </div>
          </>
        ) : (
          <div className="flex items-center gap-1.5">
            <Loader2 className="size-3 animate-spin" /> Placing the sun…
          </div>
        )}
      </div>

      <div className="mt-3 border-t pt-2">
        <div className="mb-1 font-medium">Hours of sun</div>
        <div className="flex gap-1">
          <Button size="sm" variant="outline" className="h-6 flex-1 px-2 text-[11px]" disabled={Boolean(mapping)} onClick={() => void runMap("day")}>
            {mapping === "day" ? <Loader2 className="size-3 animate-spin" /> : null} This day
          </Button>
          <Button size="sm" variant="outline" className="h-6 flex-1 px-2 text-[11px]" disabled={Boolean(mapping)} onClick={() => void runMap("season")}>
            {mapping === "season" ? <Loader2 className="size-3 animate-spin" /> : null} The season
          </Button>
          {map && (
            <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]" onClick={hideMap}>
              Hide
            </Button>
          )}
        </div>
        {mapping && progress && (
          <div className="mt-1.5 h-1 overflow-hidden rounded bg-secondary">
            <div className="h-full bg-primary transition-all" style={{ width: `${(100 * progress.done) / Math.max(progress.total, 1)}%` }} />
          </div>
        )}
        {map && (
          <div className="mt-2">
            <div className="h-2.5 rounded" style={{ background: `linear-gradient(to right, ${RAMP.join(", ")})` }} />
            <div className="mt-0.5 flex justify-between font-mono text-[10px] tabular-nums text-muted-foreground">
              <span>0 h</span>
              <span>{(map.max / 2).toFixed(1)} h</span>
              <span>{map.max.toFixed(1)} h</span>
            </div>
            <p className={cn("mt-1 leading-snug text-muted-foreground")}>
              {map.span === "day"
                ? `Hours of direct sun on ${dayLabel(map.date)}; a spot open to the sky gets ${map.max.toFixed(1)} h (the hills taken off). Lines every hour.`
                : `Hours of direct sun a day, on average from ${dayLabel(map.from)} to ${dayLabel(map.to)}; ${map.max.toFixed(1)} h at most. Lines every hour.`}
            </p>
          </div>
        )}
      </div>
      {error && <p className="mt-2 text-destructive">{error}</p>}
    </div>
  )
}
