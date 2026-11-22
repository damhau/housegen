import { useEffect, useState, type ReactNode } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Camera, ChevronLeft, ChevronRight, Download, ImageOff, Loader2, Plus, RefreshCw, Square, Undo2, X } from "lucide-react"
import { getGetListingQueryKey, useBringBackListingPhoto, useGetListing, useStartListing } from "@/api/endpoints/listing/listing"
import { getListJobsQueryKey, useCancelJob } from "@/api/endpoints/projects/projects"
import { ListingOutExteriors, ListingOutStaging, type JobOut, type ListingOut, type ListingPhotoOut, type ListingRoomOut } from "@/api/model"
import { fmtUsd } from "@/components/RunSummaryCard"
import { Button } from "@/components/ui/button"
import { errorMessage, relTime } from "@/lib/utils"

const running = (j: JobOut) => j.status === "running" || j.status === "queued" || j.status === "interrupted"

/** What a listing job asked: `{"version": n, "reuse": bool, …, "room"?: name}` (a room shot alone, #142). */
function jobRequest(j: JobOut): { version: number | null; room: string | null } {
  try {
    const r = JSON.parse(j.request_text) as { version?: number; room?: string }
    return { version: typeof r.version === "number" ? r.version : null, room: typeof r.room === "string" ? r.room : null }
  } catch {
    return { version: null, room: null }
  }
}

/** The views from outside, shot alone as one unit (#142). */
const EXTERIOR = "exterior"

/** A unit of the set as the panel names it: a room's caption (with its flat), or the views from outside. */
function unitLabel(set: ListingOut | null, room: string): string {
  if (room === EXTERIOR) return "the views from outside"
  const p = set?.photos.find((x) => x.room === room) ?? set?.rooms?.find((x) => x.room === room)
  if (!p) return room
  return p.unit ? `${p.unit} · ${p.caption}` : p.caption
}

/** How much of the furnishing the rooms show (#94); the exteriors are shot as they are. */
const STAGINGS: { value: ListingOutStaging; label: string; title: string }[] = [
  { value: "furnished", label: "Furnished", title: "The rooms dressed and staged, as agencies publish them" },
  { value: "light", label: "Light", title: "The furniture without the decor: no accessories, art or plants" },
  { value: "empty", label: "Empty", title: "The architecture alone: finishes and light, the fitted kitchen and bathrooms, the fixed lights" },
]

/** The set's photos from outside (#97). */
const EXTERIORS: { value: ListingOutExteriors; label: string; title: string }[] = [
  { value: "surroundings", label: "Surroundings", title: "The house in its surroundings: the surveyed ground, the neighbours, the trees, the land to the horizon" },
  { value: "house", label: "House alone", title: "The house alone on its plot, with its own garden and no neighbours" },
  { value: "none", label: "No exteriors", title: "Interior photos only" },
]

type Option<T> = { value: T; label: string; title: string; disabled?: boolean }

function Choice<T extends string>({ label, options, value, onChange }: { label: string; options: Option<T>[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex items-center rounded-md border p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          title={o.title}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
          className={`rounded px-2 py-0.5 hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent ${value === o.value ? "bg-accent text-accent-foreground" : "text-muted-foreground"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** The photos grouped by unit, in listing order; the exteriors lead as the building. */
function byUnit(photos: ListingPhotoOut[]): [string | null, ListingPhotoOut[]][] {
  const groups: [string | null, ListingPhotoOut[]][] = []
  for (const p of photos) {
    const unit = p.unit ?? (p.use === "exterior" ? "Building" : null)
    const last = groups[groups.length - 1]
    if (last && last[0] === unit) last[1].push(p)
    else groups.push([unit, [p]])
  }
  return groups
}

/**
 * The listing photo sets (#91): a saved version shot in Blender Cycles on the photo service, the
 * building from outside first (#93), then one photo per room, grouped by unit, with their captions.
 */
export function ListingPanel({
  projectId,
  jobs,
  version,
  currentVersion,
}: {
  projectId: string
  /** the project's listing jobs, newest first */
  jobs: JobOut[]
  /** the version being viewed (the current one when none is picked) */
  version: number
  currentVersion: number
}) {
  const qc = useQueryClient()
  const job = jobs.find(running) ?? null
  const lastJob = jobs[0] ?? null
  const [shown, setShown] = useState<number | null>(null)
  const listing = useGetListing(projectId, shown !== null ? { version: shown } : undefined, {
    query: { refetchInterval: job ? 4000 : false },
  })
  const start = useStartListing()
  const back = useBringBackListingPhoto()
  const cancel = useCancelJob()
  const [open, setOpen] = useState<number | null>(null)
  const [picked, setPicked] = useState<ListingOutStaging | null>(null)
  const [pickedOutside, setPickedOutside] = useState<ListingOutExteriors | null>(null)

  const jobId = job?.id ?? null
  useEffect(() => {
    if (jobId === null) {
      void qc.invalidateQueries({ queryKey: getGetListingQueryKey(projectId) })
      return
    }
    // the page refreshes the job list for scene jobs only: notice this one's end here
    const id = setInterval(() => void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) }), 5000)
    return () => clearInterval(id)
  }, [jobId, projectId, qc])

  async function shoot(reuse: boolean) {
    await start.mutateAsync({ projectId, data: { version, reuse, staging, exteriors } })
    setShown(version)
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
    void qc.invalidateQueries({ queryKey: getGetListingQueryKey(projectId) })
  }
  /** One room of the shown set, or its views from outside, shot alone with the set's own settings (#142). */
  async function shootRoom(room: string) {
    if (!set) return
    await start.mutateAsync({ projectId, data: { version: set.version, room } })
    setShown(set.version)
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
  }
  async function bringBack(room: string) {
    if (!set) return
    await back.mutateAsync({ projectId, version: set.version, data: { room } })
    void qc.invalidateQueries({ queryKey: getGetListingQueryKey(projectId) })
  }
  async function stop() {
    if (!job) return
    await cancel.mutateAsync({ projectId, jobId: job.id })
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
  }

  const data = listing.data
  const set = data?.listing ?? null
  const photos = set?.photos ?? []
  const ready = photos.filter((p) => p.url)
  const { version: jobOn, room: jobRoom } = job ? jobRequest(job) : { version: null, room: null }
  const shootingThis = job !== null && set !== null && jobOn === set.version
  // a room shot alone leaves the set as it is until its photo joins it
  const reshooting = shootingThis && jobRoom !== null ? jobRoom : null
  const previewsDone = set?.previews_done ?? 0
  const previewsTotal = set?.previews_total ?? 0
  const previewing = shootingThis && reshooting === null && previewsTotal > 0 && previewsDone < previewsTotal && ready.length === 0
  const viewedHasSet = data?.sets.includes(version) ?? false
  // a set shot again keeps its staging unless another is picked
  const staging = picked ?? set?.staging ?? ListingOutStaging.furnished
  // and its exteriors; the surroundings need the project's (the house alone without)
  const surroundings = data?.surroundings ?? false
  const wanted = pickedOutside ?? set?.exteriors ?? ListingOutExteriors.surroundings
  const exteriors = wanted === ListingOutExteriors.surroundings && !surroundings ? ListingOutExteriors.house : wanted

  if (listing.isPending) return <div className="p-3 text-sm text-muted-foreground">Loading…</div>
  if (listing.error) return <div className="p-3 text-sm text-destructive">{errorMessage(listing.error)}</div>

  return (
    <div className="space-y-3 p-3 text-sm">
      <p className="text-xs text-muted-foreground">
        Photos for a listing: the building from outside, then each flat's living room, dining room or kitchen, bedrooms and
        bathroom, rendered in Blender Cycles with the project's finishes. The app previews several viewpoints of the building
        and of each room; a picture editor (the project's critic model) keeps the best, and those are shot at full size. The
        staging sets how much of the furnishing the rooms show: furnished, light (no decor) or empty (the architecture and its
        finishes). The exteriors show the house in its surroundings, alone on its plot, or not at all. A room can be shot
        again alone (its viewpoints, the editor's pick and its photo, with the set's settings), any other room of the
        house can be shot and added, and the photo a room's new shot replaced can be brought back.
      </p>
      {!data?.available && <p className="text-xs text-amber-700 dark:text-amber-300">No photo service is configured on this server.</p>}
      <div className="flex flex-wrap items-center gap-2">
        {!job && (
          <Button
            size="sm"
            onClick={() => void shoot(false)}
            disabled={!data?.available || start.isPending}
            title={`Shoot a listing set of version ${version} (a furnished version: the set photographs the building and its rooms)`}
          >
            {start.isPending ? <Loader2 className="animate-spin" /> : <Camera />} Shoot v{version}
          </Button>
        )}
        {!job && viewedHasSet && (
          <Button size="sm" variant="outline" onClick={() => void shoot(true)} disabled={!data?.available || start.isPending} title="Shoot the set again from the same cameras">
            <RefreshCw /> Shoot again
          </Button>
        )}
        {!job && (
          <>
            <Choice label="Staging" options={STAGINGS} value={staging} onChange={setPicked} />
            <Choice
              label="Exteriors"
              options={EXTERIORS.map((o) =>
                o.value === ListingOutExteriors.surroundings && !surroundings
                  ? { ...o, disabled: true, title: "This project has no surroundings" }
                  : o,
              )}
              value={exteriors}
              onChange={setPickedOutside}
            />
          </>
        )}
        {job && (
          <>
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              {jobRoom !== null
                ? `Shooting ${unitLabel(shootingThis ? set : null, jobRoom)} alone on v${jobOn ?? "?"}…`
                : previewing
                  ? `Previewing v${jobOn}: ${previewsDone} of ${previewsTotal} viewpoints`
                  : shootingThis
                    ? `Shooting v${jobOn}: ${ready.length} of ${photos.length} photos`
                    : `Preparing v${jobOn ?? "?"} in Blender…`}
            </span>
            <Button size="sm" variant="outline" onClick={() => void stop()} disabled={cancel.isPending || job.status === "interrupted"}>
              <Square /> Stop
            </Button>
          </>
        )}
        {set && ready.length > 0 && (
          <a href={`/api/v1/projects/${projectId}/listing/${set.version}/zip`} className="ml-auto">
            <Button size="sm" variant="outline">
              <Download /> ZIP
            </Button>
          </a>
        )}
      </div>
      {start.error && <p className="text-xs text-destructive">{errorMessage(start.error)}</p>}
      {back.error && <p className="text-xs text-destructive">{errorMessage(back.error)}</p>}
      {!job && lastJob?.status === "failed" && <p className="text-xs text-destructive">The last shoot failed: {lastJob.error}</p>}
      {(data?.sets.length ?? 0) > 1 && (
        <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
          Sets:
          {data?.sets.map((n) => (
            <button
              key={n}
              onClick={() => setShown(n)}
              className={`rounded px-1.5 py-0.5 hover:bg-accent ${set?.version === n ? "bg-accent text-accent-foreground" : ""}`}
            >
              v{n}
            </button>
          ))}
        </div>
      )}
      {set ? (
        <SetView
          set={set}
          currentVersion={currentVersion}
          live={shootingThis && reshooting === null}
          reshooting={reshooting}
          busy={job !== null || !data?.available || start.isPending || back.isPending}
          onOpen={setOpen}
          onShoot={(room) => void shootRoom(room)}
          onBack={(room) => void bringBack(room)}
        />
      ) : (
        !job && <p className="text-xs text-muted-foreground">No set yet.</p>
      )}
      {set && open !== null && <PhotoViewer photos={ready} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />}
    </div>
  )
}

/** A small icon action next to a photo or a group (#142). */
function UnitAction({ icon, title, disabled, onClick }: { icon: ReactNode; title: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-3.5"
    >
      {icon}
    </button>
  )
}

function SetView({
  set,
  currentVersion,
  live,
  reshooting,
  busy,
  onOpen,
  onShoot,
  onBack,
}: {
  set: ListingOut
  currentVersion: number
  /** the whole set is being shot: its missing photos are on their way */
  live: boolean
  /** the room (or "exterior") a running job shoots alone */
  reshooting: string | null
  /** a listing job runs or the service is missing: no action can start */
  busy: boolean
  onOpen: (i: number) => void
  onShoot: (room: string) => void
  onBack: (room: string) => void
}) {
  const ready = set.photos.filter((p) => p.url)
  const rooms = set.rooms ?? []
  const reshoots = [...(set.reshoots ?? [])].reverse().slice(0, 3)
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        v{set.version} · {STAGINGS.find((o) => o.value === (set.staging ?? "furnished"))?.label} ·{" "}
        {EXTERIORS.find((o) => o.value === (set.exteriors ?? "surroundings"))?.label} · {ready.length} photo
        {ready.length === 1 ? "" : "s"} · {set.width}×{set.height}
        {set.created_at && ` · ${relTime(set.created_at)}`} · ≈ {fmtUsd(set.cost_usd)}
      </p>
      {reshoots.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Shot alone:{" "}
          {reshoots.map((r, i) => (
            <span key={r.job_id}>
              {i > 0 && " · "}
              {r.room === EXTERIOR ? "views from outside" : r.unit ? `${r.unit} · ${r.caption}` : r.caption}
              {r.joined ? " (added)" : ""} ≈ {fmtUsd(r.usd)}, {relTime(r.at)}
            </span>
          ))}
        </p>
      )}
      {set.out_of_date && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
          Shot on v{set.version}; the project is at v{currentVersion} now. Shoot again to show the current state.
        </p>
      )}
      {rooms.length > 0 && <RoomPicker rooms={rooms} reshooting={reshooting} busy={busy} onShoot={onShoot} />}
      {byUnit(set.photos).map(([unit, photos]) => {
        const outside = photos[0]?.use === "exterior"
        return (
          <section key={unit ?? "house"} className="space-y-1.5">
            {(unit || outside) && (
              <div className="flex items-center gap-1">
                <h3 className="flex-1 text-xs font-semibold">{unit}</h3>
                {outside && (
                  <>
                    {reshooting === EXTERIOR && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
                    <UnitAction
                      icon={<RefreshCw />}
                      title="Shoot the views from outside again: their viewpoints, the editor's choice and the photos. The ones they replace are kept"
                      disabled={busy}
                      onClick={() => onShoot(EXTERIOR)}
                    />
                    {photos.some((p) => p.previous) && (
                      <UnitAction
                        icon={<Undo2 />}
                        title="Bring back the views from outside these replaced (these are kept: bring them back the same way)"
                        disabled={busy}
                        onClick={() => onBack(EXTERIOR)}
                      />
                    )}
                  </>
                )}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              {photos.map((p) => {
                const again = reshooting !== null && reshooting === p.room
                return (
                  <figure key={p.key} className="space-y-0.5">
                    <div className="relative">
                      {p.url ? (
                        <button onClick={() => onOpen(ready.indexOf(p))} className="block w-full overflow-hidden rounded border">
                          <img src={p.url} alt={p.caption} loading="lazy" className="aspect-video w-full object-cover" />
                        </button>
                      ) : (
                        <div className="grid aspect-video w-full place-items-center rounded border bg-muted text-muted-foreground" title={p.error ?? undefined}>
                          {p.error ? <ImageOff className="size-4" /> : live ? <Loader2 className="size-4 animate-spin" /> : <ImageOff className="size-4" />}
                        </div>
                      )}
                      {(again || (outside && reshooting === EXTERIOR)) && (
                        <div className="pointer-events-none absolute inset-0 grid place-items-center rounded bg-black/40 text-white">
                          <Loader2 className="size-4 animate-spin" />
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-0.5">
                      <figcaption className="min-w-0 flex-1 truncate text-xs" title={p.error ? `${p.caption}: ${p.error}` : p.why ? `${p.caption}. ${p.why}` : p.caption}>
                        {p.caption}
                      </figcaption>
                      {!outside && (
                        <>
                          <UnitAction
                            icon={<RefreshCw />}
                            title={`Shoot ${p.title} again alone: its viewpoints, the editor's pick and the photo. The photo it replaces is kept`}
                            disabled={busy}
                            onClick={() => onShoot(p.room)}
                          />
                          {p.previous && (
                            <UnitAction
                              icon={<Undo2 />}
                              title="Bring back the photo this one replaced (this one is kept: bring it back the same way)"
                              disabled={busy}
                              onClick={() => onBack(p.room)}
                            />
                          )}
                        </>
                      )}
                    </div>
                  </figure>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}

/** Every room of the house, floor by floor from the lowest: any one shot alone (#142). */
function RoomPicker({
  rooms,
  reshooting,
  busy,
  onShoot,
}: {
  rooms: ListingRoomOut[]
  reshooting: string | null
  busy: boolean
  onShoot: (room: string) => void
}) {
  const [room, setRoom] = useState("")
  const floors = [...new Set(rooms.map((r) => r.storey ?? 0))]
  const chosen = rooms.find((r) => r.room === room)
  const name = (r: ListingRoomOut) => (r.unit ? `${r.unit} · ${r.caption}` : r.caption)
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-semibold">Shoot one room</h3>
      <div className="flex flex-wrap items-center gap-1.5">
        <select
          value={room}
          onChange={(e) => setRoom(e.target.value)}
          disabled={busy}
          aria-label="Room to shoot alone"
          className="h-7 min-w-0 max-w-[280px] flex-1 rounded-md border bg-background px-1 text-xs"
        >
          <option value="">Choose a room…</option>
          {floors.map((f) => (
            <optgroup key={f} label={floors.length > 1 ? `Level ${f + 1}${f === 0 ? " (lowest)" : ""}` : "Rooms"}>
              {rooms
                .filter((r) => (r.storey ?? 0) === f)
                .map((r) => (
                  <option key={r.room} value={r.room}>
                    {name(r)}
                    {r.in_set ? " · in the set" : ""}
                  </option>
                ))}
            </optgroup>
          ))}
        </select>
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2"
          disabled={busy || !chosen}
          onClick={() => chosen && onShoot(chosen.room)}
          title={
            chosen?.in_set
              ? `Shoot ${chosen.title} again alone: its viewpoints, the editor's pick and the photo. The photo it replaces is kept`
              : chosen
                ? `Shoot ${chosen.title} and add it to the set in listing order`
                : "Choose a room first"
          }
        >
          {reshooting && reshooting === room ? <Loader2 className="animate-spin" /> : chosen?.in_set ? <RefreshCw /> : <Plus />}
          {chosen?.in_set ? "Shoot again" : "Shoot and add"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">About 10 minutes and $1 a room; the rest of the set stays as it is.</p>
    </section>
  )
}

/** A photo at full size over the page: arrows or ← → for the others, Esc or × to close. */
export function PhotoViewer({
  photos,
  index,
  onIndex,
  onClose,
}: {
  photos: ListingPhotoOut[]
  index: number
  onIndex: (i: number) => void
  onClose: () => void
}) {
  const n = photos.length
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
      if (e.key === "ArrowRight") onIndex((index + 1) % n)
      if (e.key === "ArrowLeft") onIndex((index - 1 + n) % n)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [index, n, onIndex, onClose])
  const p = photos[index]
  if (!p?.url) return null
  return (
    <div role="dialog" aria-label={p.caption} className="fixed inset-0 z-50 grid grid-rows-[minmax(0,1fr)_auto] bg-black/90 p-4" onClick={onClose}>
      <button
        onClick={onClose}
        aria-label="Close"
        title="Close (Esc)"
        className="absolute right-3 top-3 z-10 rounded-full bg-black/60 p-2 text-white ring-1 ring-white/30 hover:bg-white/20"
      >
        <X className="size-6" />
      </button>
      <img src={p.url} alt={p.caption} className="m-auto max-h-full max-w-full object-contain" onClick={(e) => e.stopPropagation()} />
      <div className="flex items-center justify-center gap-3 pt-2 text-sm text-white" onClick={(e) => e.stopPropagation()}>
        <button onClick={() => onIndex((index - 1 + n) % n)} aria-label="Previous" className="rounded p-1 hover:bg-white/10">
          <ChevronLeft />
        </button>
        <span>
          {p.unit ? `${p.unit} · ` : ""}
          {p.caption} <span className="opacity-60">({index + 1}/{n})</span>
        </span>
        <button onClick={() => onIndex((index + 1) % n)} aria-label="Next" className="rounded p-1 hover:bg-white/10">
          <ChevronRight />
        </button>
        <a href={p.url} target="_blank" rel="noreferrer" className="rounded p-1 text-xs underline-offset-2 hover:underline">
          full size
        </a>
      </div>
    </div>
  )
}
