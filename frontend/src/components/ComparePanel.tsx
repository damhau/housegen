import type { PhotoOut, SceneVersionOut } from "@/api/model"

/** Photo vs render, side by side, per façade — the same pairs the critic sees — plus the other photos. */
export function ComparePanel({ photos, version }: { photos: PhotoOut[]; version: SceneVersionOut | undefined }) {
  const sides = photos.filter((p) => p.side !== "other")
  const others = photos.filter((p) => p.side === "other")
  if (photos.length === 0) return <p className="p-3 text-sm text-muted-foreground">No photo.</p>
  return (
    <div className="grid gap-3 p-3">
      {sides.map((p) => {
        // the photo-like render shares the photographer's viewpoint; older versions only have the elevated view
        const render = version?.render_urls[`${p.side}-photo`] ?? version?.render_urls[p.side]
        return (
          <div key={p.id} className="grid gap-1">
            <div className="text-xs font-medium capitalize text-muted-foreground">{p.side}</div>
            <div className="grid grid-cols-2 gap-2">
              <img src={p.url} alt={`${p.side} photo`} className="aspect-[4/3] w-full rounded-md border object-cover" />
              {render ? (
                <img src={render} alt={`${p.side} render`} className="aspect-[4/3] w-full rounded-md border object-cover" />
              ) : (
                <div className="grid aspect-[4/3] place-items-center rounded-md border text-xs text-muted-foreground">no render</div>
              )}
            </div>
          </div>
        )
      })}
      {version?.render_urls.aerial && (
        <div className="grid gap-1">
          <div className="text-xs font-medium text-muted-foreground">aerial</div>
          <img src={version.render_urls.aerial} alt="aerial render" className="w-full rounded-md border" />
        </div>
      )}
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
