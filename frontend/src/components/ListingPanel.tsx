import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Camera, ChevronLeft, ChevronRight, Download, ImageOff, Loader2, RefreshCw, Square, X } from "lucide-react"
import { getGetListingQueryKey, useGetListing, useStartListing } from "@/api/endpoints/listing/listing"
import { getListJobsQueryKey, useCancelJob } from "@/api/endpoints/projects/projects"
import type { JobOut, ListingOut, ListingPhotoOut } from "@/api/model"
import { fmtUsd } from "@/components/RunSummaryCard"
import { Button } from "@/components/ui/button"
import { errorMessage, relTime } from "@/lib/utils"

const running = (j: JobOut) => j.status === "running" || j.status === "queued" || j.status === "interrupted"

/** The version a listing job shoots (its request is `{"version": n, "reuse": bool}`). */
function jobVersion(j: JobOut): number | null {
  try {
    const v = (JSON.parse(j.request_text) as { version?: number }).version
    return typeof v === "number" ? v : null
  } catch {
    return null
  }
}

/** The photos grouped by unit, in listing order. */
function byUnit(photos: ListingPhotoOut[]): [string | null, ListingPhotoOut[]][] {
  const groups: [string | null, ListingPhotoOut[]][] = []
  for (const p of photos) {
    const unit = p.unit ?? null
    const last = groups[groups.length - 1]
    if (last && last[0] === unit) last[1].push(p)
    else groups.push([unit, [p]])
  }
  return groups
}

/**
 * The listing photo sets (#91): a saved version's interiors shot in Blender Cycles on the photo
 * service, one photo per room, grouped by unit, with their captions.
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
  const cancel = useCancelJob()
  const [open, setOpen] = useState<number | null>(null)

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
    await start.mutateAsync({ projectId, data: { version, reuse } })
    setShown(version)
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
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
  const jobOn = job ? jobVersion(job) : null
  const shootingThis = job !== null && set !== null && jobOn === set.version
  const previewsDone = set?.previews_done ?? 0
  const previewsTotal = set?.previews_total ?? 0
  const previewing = shootingThis && previewsTotal > 0 && previewsDone < previewsTotal && ready.length === 0
  const viewedHasSet = data?.sets.includes(version) ?? false

  if (listing.isPending) return <div className="p-3 text-sm text-muted-foreground">Loading…</div>
  if (listing.error) return <div className="p-3 text-sm text-destructive">{errorMessage(listing.error)}</div>

  return (
    <div className="space-y-3 p-3 text-sm">
      <p className="text-xs text-muted-foreground">
        Photos of the interior for a listing: each flat's living room, dining room or kitchen, bedrooms and bathroom, rendered in
        Blender Cycles with the project's finishes. The app picks the rooms; for each it previews several viewpoints, a picture
        editor (the project's critic model) keeps one, and that one is shot at full size.
      </p>
      {!data?.available && <p className="text-xs text-amber-700 dark:text-amber-300">No photo service is configured on this server.</p>}
      <div className="flex flex-wrap items-center gap-2">
        {!job && (
          <Button
            size="sm"
            onClick={() => void shoot(false)}
            disabled={!data?.available || start.isPending}
            title={`Shoot a listing set of version ${version} (a furnished version: the set photographs its rooms)`}
          >
            {start.isPending ? <Loader2 className="animate-spin" /> : <Camera />} Shoot v{version}
          </Button>
        )}
        {!job && viewedHasSet && (
          <Button size="sm" variant="outline" onClick={() => void shoot(true)} disabled={!data?.available || start.isPending} title="Shoot the set again from the same cameras">
            <RefreshCw /> Shoot again
          </Button>
        )}
        {job && (
          <>
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              {previewing
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
      {set ? <SetView set={set} currentVersion={currentVersion} live={shootingThis} onOpen={setOpen} /> : !job && <p className="text-xs text-muted-foreground">No set yet.</p>}
      {set && open !== null && <PhotoViewer photos={ready} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />}
    </div>
  )
}

function SetView({ set, currentVersion, live, onOpen }: { set: ListingOut; currentVersion: number; live: boolean; onOpen: (i: number) => void }) {
  const ready = set.photos.filter((p) => p.url)
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        v{set.version} · {ready.length} photo{ready.length === 1 ? "" : "s"} · {set.width}×{set.height}
        {set.created_at && ` · ${relTime(set.created_at)}`} · ≈ {fmtUsd(set.cost_usd)}
      </p>
      {set.out_of_date && (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
          Shot on v{set.version}; the project is at v{currentVersion} now. Shoot again to show the current state.
        </p>
      )}
      {byUnit(set.photos).map(([unit, photos]) => (
        <section key={unit ?? "house"} className="space-y-1.5">
          {unit && <h3 className="text-xs font-semibold">{unit}</h3>}
          <div className="grid grid-cols-2 gap-2">
            {photos.map((p) => (
              <figure key={p.key} className="space-y-0.5">
                {p.url ? (
                  <button onClick={() => onOpen(ready.indexOf(p))} className="block w-full overflow-hidden rounded border">
                    <img src={p.url} alt={p.caption} loading="lazy" className="aspect-video w-full object-cover" />
                  </button>
                ) : (
                  <div className="grid aspect-video w-full place-items-center rounded border bg-muted text-muted-foreground" title={p.error ?? undefined}>
                    {p.error ? <ImageOff className="size-4" /> : live ? <Loader2 className="size-4 animate-spin" /> : <ImageOff className="size-4" />}
                  </div>
                )}
                <figcaption className="truncate text-xs" title={p.error ? `${p.caption}: ${p.error}` : p.why ? `${p.caption}. ${p.why}` : p.caption}>
                  {p.caption}
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      ))}
    </div>
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
    <div role="dialog" aria-label={p.caption} className="fixed inset-0 z-50 grid grid-rows-[1fr_auto] bg-black/90 p-4" onClick={onClose}>
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
        <button onClick={onClose} aria-label="Close" className="rounded p-1 hover:bg-white/10">
          <X />
        </button>
      </div>
    </div>
  )
}
