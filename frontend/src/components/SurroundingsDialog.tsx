import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2, MapPin, Mountain, RotateCcw, RotateCw, Search, Trash2, X } from "lucide-react"
import {
  alignSurroundings,
  deleteSurroundings,
  fetchSurroundings,
  getGetSurroundingsQueryKey,
  searchPlaces,
  useGetSurroundings,
} from "@/api/endpoints/surroundings/surroundings"
import type { PlaceOut } from "@/api/model"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn, errorMessage } from "@/lib/utils"

/** The scene from above, as the scene page reports it (scene metres: x east, z south). */
type Quad = [number, number, number, number]
export type SceneOutline = { walls: Quad[]; slabs: [number, number][][]; terrain: Quad[] }

type Alignment = { x: number; z: number; rotation: number; ground: number }

const KINDS: Record<string, string> = { parcel: "Parcel", address: "Address", gg25: "Commune", zipcode: "Postcode" }

/**
 * The real surroundings of the house (#39): terrain, aerial photo and neighbouring buildings from
 * swisstopo, 200 m around it, shown by the viewer in the Final and Ultra looks. First find the place
 * (the plans' title block usually names the parcel), then set where the house stands on it.
 */
export function SurroundingsDialog({
  projectId,
  requestOutline,
  onChanged,
  onClose,
}: {
  projectId: string
  requestOutline: () => Promise<SceneOutline>
  onChanged: () => void
  onClose: () => void
}) {
  const qc = useQueryClient()
  const state = useGetSurroundings(projectId)
  const data = state.data
  const [searching, setSearching] = useState(false)
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<PlaceOut[] | null>(null)
  const [fetching, setFetching] = useState<PlaceOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [changePlace, setChangePlace] = useState(false)

  const exists = Boolean(data?.exists) && !changePlace

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !fetching) onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [fetching, onClose])

  // the plans' parcel, searched at once
  const suggestion = data?.suggestion ?? null
  useEffect(() => {
    if (!data || data.exists || !suggestion || query) return
    setQuery(suggestion)
    void run(suggestion)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, suggestion])

  async function run(q: string) {
    if (q.trim().length < 2) return
    setSearching(true)
    setError(null)
    try {
      setResults(await searchPlaces({ q: q.trim() }))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setSearching(false)
    }
  }

  async function fetchPlace(place: PlaceOut) {
    setFetching(place)
    setError(null)
    try {
      const out = await fetchSurroundings(projectId, { place, radius: 200 })
      qc.setQueryData(getGetSurroundingsQueryKey(projectId), out)
      setChangePlace(false)
      onChanged()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setFetching(null)
    }
  }

  async function remove() {
    setError(null)
    try {
      await deleteSurroundings(projectId)
      await qc.invalidateQueries({ queryKey: getGetSurroundingsQueryKey(projectId) })
      onChanged()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/35 p-3 sm:p-6"
      onClick={() => !fetching && onClose()}
      role="presentation"
    >
      <div
        role="dialog"
        aria-label="Surroundings"
        className="flex max-h-[94vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border bg-card text-sm shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-4">
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-lg bg-secondary">
              <Mountain className="size-4" />
            </span>
            <div>
              <h2 className="font-semibold">Surroundings</h2>
              <p className="text-xs text-muted-foreground">
                The real terrain, aerial photo and neighbouring buildings, 200 m around the house (swisstopo). Shown in the
                Final and Ultra looks.
              </p>
            </div>
          </div>
          <Button size="sm" variant="ghost" className="h-7 px-2" onClick={onClose} disabled={Boolean(fetching)} aria-label="Close">
            <X className="size-4" />
          </Button>
        </div>

        {state.isLoading ? (
          <div className="flex items-center justify-center gap-2 border-t p-10 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading…
          </div>
        ) : exists && data ? (
          <AlignEditor
            projectId={projectId}
            photoUrl={data.photo_url ?? ""}
            radius={data.radius ?? 200}
            place={data.place ?? null}
            initial={data.alignment ?? null}
            requestOutline={requestOutline}
            onSaved={(out) => {
              qc.setQueryData(getGetSurroundingsQueryKey(projectId), out)
              onChanged()
            }}
            onChangePlace={() => {
              setChangePlace(true)
              setQuery(data.place?.label ?? "")
              setResults(null)
            }}
            onRemove={() => void remove()}
            credits={data.credits ?? []}
          />
        ) : (
          <div className="flex flex-col gap-3 border-t px-5 py-4">
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                void run(query)
              }}
            >
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="surroundings-search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="An address, or a parcel number and its commune: “3013 Mont-sur-Lausanne”"
                  className="pl-8"
                  autoFocus
                />
              </div>
              <Button type="submit" size="sm" variant="outline" className="h-9" disabled={searching || query.trim().length < 2}>
                {searching ? <Loader2 className="animate-spin" /> : <Search />} Search
              </Button>
            </form>
            {suggestion && !results && !searching && (
              <p className="text-xs text-muted-foreground">The plans name {suggestion}.</p>
            )}
            {results && (
              <ul className="flex flex-col divide-y rounded-lg border">
                {results.length === 0 && <li className="px-3 py-3 text-muted-foreground">Nothing found. Try the street and number, or the parcel and its commune.</li>}
                {results.map((p) => (
                  <li key={`${p.kind}-${p.e}-${p.n}`} className="flex items-center gap-3 px-3 py-2.5">
                    <MapPin className="size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium">{p.label}</div>
                      <div className="text-xs text-muted-foreground tabular-nums">
                        {KINDS[p.kind] ?? p.kind} · E {Math.round(p.e).toLocaleString("de-CH")} · N {Math.round(p.n).toLocaleString("de-CH")}
                      </div>
                    </div>
                    <Button size="sm" onClick={() => void fetchPlace(p)} disabled={Boolean(fetching)}>
                      {fetching === p ? <Loader2 className="animate-spin" /> : <Mountain />}
                      {fetching === p ? "Fetching…" : "Use this place"}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            {fetching && (
              <p className="text-xs text-muted-foreground">
                Fetching the terrain, the aerial photo and the buildings around {fetching.label}: 10 to 30 seconds.
              </p>
            )}
            {changePlace && (
              <div>
                <Button size="sm" variant="ghost" onClick={() => setChangePlace(false)} disabled={Boolean(fetching)}>
                  Keep the current place
                </Button>
              </div>
            )}
          </div>
        )}
        {error && <p className="border-t px-5 py-2 text-xs text-destructive">{error}</p>}
      </div>
    </div>
  )
}

const DEG = Math.PI / 180

/** Scene metres → the surroundings' local metres (x east, z south): the alignment's turn and shift. */
function toLocal(a: Alignment, x: number, z: number): [number, number] {
  const c = Math.cos(a.rotation * DEG), s = Math.sin(a.rotation * DEG)
  return [a.x + c * x - s * z, a.z + s * x + c * z]
}

function AlignEditor({
  projectId,
  photoUrl,
  radius,
  place,
  initial,
  requestOutline,
  onSaved,
  onChangePlace,
  onRemove,
  credits,
}: {
  projectId: string
  photoUrl: string
  radius: number
  place: PlaceOut | null
  initial: (Alignment & { set: boolean }) | null
  requestOutline: () => Promise<SceneOutline>
  onSaved: (out: Awaited<ReturnType<typeof alignSurroundings>>) => void
  onChangePlace: () => void
  onRemove: () => void
  credits: string[]
}) {
  const [a, setA] = useState<Alignment>({ x: initial?.x ?? 0, z: initial?.z ?? 0, rotation: initial?.rotation ?? 0, ground: initial?.ground ?? 0 })
  const [saved, setSaved] = useState<Alignment>(a)
  const [outline, setOutline] = useState<SceneOutline | null>(null)
  const [outlineError, setOutlineError] = useState<string | null>(null)
  const [half, setHalf] = useState(45) // metres from the centre of the view to its edge
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const svg = useRef<SVGSVGElement>(null)
  const drag = useRef<{ mode: "move" | "turn"; from: [number, number]; start: Alignment } | null>(null)
  const [center] = useState<[number, number]>([a.x, a.z])

  useEffect(() => {
    requestOutline()
      .then(setOutline)
      .catch((e) => setOutlineError(errorMessage(e)))
  }, [requestOutline])

  // how far the house reaches from its origin: the turning handle sits just beyond it
  const reach = useMemo(() => {
    let r = 6
    for (const [x0, z0, x1, z1] of outline?.walls ?? []) r = Math.max(r, Math.hypot(x0, z0), Math.hypot(x1, z1))
    return r + 3
  }, [outline])

  const dirty = a.x !== saved.x || a.z !== saved.z || a.rotation !== saved.rotation || a.ground !== saved.ground

  function point(e: { clientX: number; clientY: number }): [number, number] {
    const el = svg.current
    const m = el?.getScreenCTM()
    if (!el || !m) return [0, 0]
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse())
    return [p.x, p.y]
  }

  function down(mode: "move" | "turn", e: ReactPointerEvent) {
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    drag.current = { mode, from: point(e), start: a }
  }

  function move(e: ReactPointerEvent) {
    const d = drag.current
    if (!d) return
    const [px, pz] = point(e)
    if (d.mode === "move") {
      setA({ ...d.start, x: round(d.start.x + px - d.from[0], 2), z: round(d.start.z + pz - d.from[1], 2) })
    } else {
      // the handle points to the scene's north: its bearing from the house's origin is the turn
      const bearing = Math.atan2(px - d.start.x, -(pz - d.start.z)) / DEG
      setA({ ...d.start, rotation: round(e.shiftKey ? bearing : Math.round(bearing * 2) / 2, 1) })
    }
  }

  function nudge(dx: number, dz: number, dr = 0) {
    setA((v) => ({ ...v, x: round(v.x + dx, 2), z: round(v.z + dz, 2), rotation: round(norm(v.rotation + dr), 1) }))
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const out = await alignSurroundings(projectId, a)
      setSaved(a)
      onSaved(out)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const handle = toLocal(a, 0, -reach)
  const origin = [a.x, a.z]
  const view = `${center[0] - half} ${center[1] - half} ${2 * half} ${2 * half}`

  return (
    <div className="grid min-h-0 flex-1 border-t md:grid-cols-[1fr_280px]">
      <div className="relative min-h-[360px] bg-[#1d2124]">
        <svg
          ref={svg}
          viewBox={view}
          className="absolute inset-0 size-full touch-none select-none"
          onPointerMove={move}
          onPointerUp={() => (drag.current = null)}
          onPointerLeave={() => (drag.current = null)}
          onWheel={(e) => setHalf((h) => Math.min(radius, Math.max(12, h * (e.deltaY > 0 ? 1.15 : 1 / 1.15))))}
          tabIndex={0}
          aria-label="The house on the aerial photo: drag to move it, drag the round handle to turn it"
          onKeyDown={(e) => {
            const s = e.shiftKey ? 1 : 0.1
            if (e.key === "ArrowLeft") nudge(-s, 0)
            else if (e.key === "ArrowRight") nudge(s, 0)
            else if (e.key === "ArrowUp") nudge(0, -s)
            else if (e.key === "ArrowDown") nudge(0, s)
            else if (e.key === "[") nudge(0, 0, e.shiftKey ? -5 : -0.5)
            else if (e.key === "]") nudge(0, 0, e.shiftKey ? 5 : 0.5)
            else return
            e.preventDefault()
          }}
        >
          <image href={photoUrl} x={-radius} y={-radius} width={2 * radius} height={2 * radius} preserveAspectRatio="none" />
          <g transform={`translate(${a.x} ${a.z}) rotate(${a.rotation})`} onPointerDown={(e) => down("move", e)} className="cursor-move">
            {(outline?.terrain ?? []).map(([x0, z0, x1, z1], i) => (
              <rect key={`t${i}`} x={x0} y={z0} width={x1 - x0} height={z1 - z0} fill="rgba(255,255,255,0.12)" stroke="#fff" strokeWidth={0.25} strokeDasharray="1 0.7" />
            ))}
            {(outline?.slabs ?? []).map((poly, i) => (
              <polygon key={`s${i}`} points={poly.map((p) => p.join(",")).join(" ")} fill="rgba(255,214,120,0.35)" stroke="#ffd678" strokeWidth={0.15} />
            ))}
            {(outline?.walls ?? []).map(([x0, z0, x1, z1], i) => (
              <line key={`w${i}`} x1={x0} y1={z0} x2={x1} y2={z1} stroke="#ff4d3d" strokeWidth={0.45} strokeLinecap="square" />
            ))}
            {!outline?.walls?.length && <rect x={-5} y={-5} width={10} height={10} fill="rgba(255,77,61,0.3)" stroke="#ff4d3d" strokeWidth={0.3} />}
            <circle r={0.6} fill="#fff" stroke="#1d2124" strokeWidth={0.2} />
          </g>
          <line x1={origin[0]} y1={origin[1]} x2={handle[0]} y2={handle[1]} stroke="#fff" strokeWidth={0.2} strokeDasharray="0.8 0.5" />
          <g transform={`translate(${handle[0]} ${handle[1]})`} onPointerDown={(e) => down("turn", e)} className="cursor-grab">
            <circle r={Math.max(1.4, half / 28)} fill="#fff" stroke="#1d2124" strokeWidth={0.25} />
            <text textAnchor="middle" dy={Math.max(0.5, half / 80)} fontSize={Math.max(1.4, half / 28)} fontWeight={700} fill="#1d2124">
              N
            </text>
          </g>
        </svg>
        <div className="pointer-events-none absolute left-2 top-2 rounded-md bg-black/55 px-2 py-1 text-[11px] text-white">
          Drag the house · drag <b>N</b> to turn it (Shift: free angle) · wheel to zoom · arrows and [ ] to fine-tune
        </div>
        {outlineError && (
          <div className="absolute inset-x-2 bottom-2 rounded-md bg-black/60 px-2 py-1 text-[11px] text-white">
            The house's outline could not be read from the scene ({outlineError}): a 10 m square stands in for it.
          </div>
        )}
      </div>

      <div className="flex flex-col gap-4 overflow-y-auto p-4">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Place</div>
          <div className="mt-1 font-medium">{place?.label ?? "—"}</div>
          <Button size="sm" variant="link" className="h-auto p-0 text-xs" onClick={onChangePlace}>
            Change place
          </Button>
        </div>

        <div className="flex flex-col gap-2">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">House on the site</div>
          <Field label="East of the place" unit="m" value={a.x} step={0.1} onChange={(v) => setA({ ...a, x: v })} />
          <Field label="South of the place" unit="m" value={a.z} step={0.1} onChange={(v) => setA({ ...a, z: v })} />
          <div className="flex items-end gap-1.5">
            <Field label="Turned" unit="°" value={a.rotation} step={0.5} onChange={(v) => setA({ ...a, rotation: norm(v) })} />
            <Button size="sm" variant="outline" className="h-8 px-2" title="Turn 90° anticlockwise" onClick={() => nudge(0, 0, -90)}>
              <RotateCcw className="size-3.5" />
            </Button>
            <Button size="sm" variant="outline" className="h-8 px-2" title="Turn 90° clockwise" onClick={() => nudge(0, 0, 90)}>
              <RotateCw className="size-3.5" />
            </Button>
          </div>
          <Field label="Ground floor at (±0.00)" unit="m" value={a.ground} step={0.05} onChange={(v) => setA({ ...a, ground: v })} />
          <p className="text-xs text-muted-foreground">
            The plans give the ground floor's altitude as ±0.00 (for example 667.60). The survey's ground at the place is the starting value.
          </p>
        </div>

        <div className="mt-auto flex flex-col gap-2 border-t pt-3">
          {error && <p className="text-xs text-destructive">{error}</p>}
          <Button onClick={() => void save()} disabled={saving || (!dirty && Boolean(initial?.set))}>
            {saving ? <Loader2 className="animate-spin" /> : null}
            {dirty || !initial?.set ? "Save and show in the viewer" : "Saved"}
          </Button>
          <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={onRemove}>
            <Trash2 className="size-3.5" /> Remove the surroundings
          </Button>
          <p className="text-[11px] leading-snug text-muted-foreground">{credits.join(" · ")}</p>
        </div>
      </div>
    </div>
  )
}

function Field({ label, unit, value, step, onChange }: { label: string; unit: string; value: number; step: number; onChange: (v: number) => void }) {
  const id = `f-${label.replace(/\W+/g, "-").toLowerCase()}`
  const [text, setText] = useState(String(value))
  useEffect(() => setText(String(value)), [value])
  return (
    <label htmlFor={id} className="flex flex-1 flex-col gap-1 text-xs text-muted-foreground">
      {label}
      <span className="flex items-center gap-1.5">
        <Input
          id={id}
          type="number"
          step={step}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            const v = Number(e.target.value)
            if (e.target.value !== "" && Number.isFinite(v)) onChange(v)
          }}
          className={cn("h-8 tabular-nums text-foreground")}
        />
        <span className="w-4 text-foreground">{unit}</span>
      </span>
    </label>
  )
}

const round = (v: number, d: number) => Math.round(v * 10 ** d) / 10 ** d
const norm = (deg: number) => ((((deg + 180) % 360) + 360) % 360) - 180
