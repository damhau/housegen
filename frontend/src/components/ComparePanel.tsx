import type { IntakeOut, PhotoOut, SceneVersionOut } from "@/api/model"

/**
 * Reference vs render, side by side, per façade — the same pairs the critic sees: the photo
 * and the photo-like render, or, without photos, the elevation sheet and the straight-on
 * elevation render — plus the other photos.
 */
export function ComparePanel({
  photos,
  version,
  planPageUrls,
  intake,
}: {
  photos: PhotoOut[]
  version: SceneVersionOut | undefined
  planPageUrls: string[]
  intake: IntakeOut | null
}) {
  const sides = photos.filter((p) => p.side !== "other")
  const others = photos.filter((p) => p.side === "other")
  if (photos.length === 0) return <ElevationPairs version={version} planPageUrls={planPageUrls} intake={intake} />
  return (
    <div className="grid gap-3 p-3">
      {sides.map((p) => {
        // the photo-like render shares the photographer's viewpoint; older versions only have the elevated view
        const render = version?.render_urls[`${p.side}-photo`] ?? version?.render_urls[p.side]
        return (
          <Pair key={p.id} title={p.side} left={p.url} leftAlt={`${p.side} photo`} right={render} rightAlt={`${p.side} render`} />
        )
      })}
      <Aerial version={version} />
      {others.length > 0 && (
        <div className="grid gap-1">
          <div className="text-xs font-medium text-muted-foreground">other photos ({others.length})</div>
          <div className="grid grid-cols-4 gap-1.5">
            {others.map((p) => (
              <a key={p.id} href={p.url} target="_blank" rel="noreferrer">
                <img src={p.url} alt={p.original_name} className="aspect-square w-full rounded border object-cover" />
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** No photos: each façade's elevation sheet (as identified by the intake) next to its elevation render. */
function ElevationPairs({ version, planPageUrls, intake }: { version: SceneVersionOut | undefined; planPageUrls: string[]; intake: IntakeOut | null }) {
  const pairs: { side: string; page: number; url: string }[] = []
  for (const sheet of intake?.sheets ?? []) {
    const url = planPageUrls[sheet.page - 1]
    if (url === undefined) continue
    for (const side of sheet.elevations ?? []) {
      if (!pairs.some((p) => p.side === side)) pairs.push({ side, page: sheet.page, url })
    }
  }
  if (pairs.length === 0) {
    return (
      <p className="p-3 text-sm text-muted-foreground">
        No photo. {intake ? "The intake found no elevation sheet to compare the renders with." : "Once the plans are read, the elevation sheets are compared with the renders here."}
      </p>
    )
  }
  return (
    <div className="grid gap-3 p-3">
      {pairs.map(({ side, page, url }) => (
        <Pair
          key={side}
          title={`${side} · sheet ${page}`}
          left={url}
          leftAlt={`sheet ${page}`}
          right={version?.render_urls[`${side}-elevation`] ?? version?.render_urls[side]}
          rightAlt={`${side} elevation render`}
          contain
        />
      ))}
      <Aerial version={version} />
    </div>
  )
}

function Pair({ title, left, leftAlt, right, rightAlt, contain = false }: { title: string; left: string; leftAlt: string; right: string | undefined; rightAlt: string; contain?: boolean }) {
  const fit = contain ? "object-contain bg-white" : "object-cover"
  return (
    <div className="grid gap-1">
      <div className="text-xs font-medium capitalize text-muted-foreground">{title}</div>
      <div className="grid grid-cols-2 gap-2">
        <a href={left} target="_blank" rel="noreferrer">
          <img src={left} alt={leftAlt} className={`aspect-[4/3] w-full rounded-md border ${fit}`} />
        </a>
        {right ? (
          <a href={right} target="_blank" rel="noreferrer">
            <img src={right} alt={rightAlt} className={`aspect-[4/3] w-full rounded-md border ${fit}`} />
          </a>
        ) : (
          <div className="grid aspect-[4/3] place-items-center rounded-md border text-xs text-muted-foreground">no render</div>
        )}
      </div>
    </div>
  )
}

function Aerial({ version }: { version: SceneVersionOut | undefined }) {
  if (!version?.render_urls.aerial) return null
  return (
    <div className="grid gap-1">
      <div className="text-xs font-medium text-muted-foreground">aerial</div>
      <img src={version.render_urls.aerial} alt="aerial render" className="w-full rounded-md border" />
    </div>
  )
}
