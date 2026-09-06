import type { PhotoOut, SceneVersionOut } from "@/api/model"

/** Photo vs render, side by side, per façade — the same pairs the critic sees. */
export function ComparePanel({ photos, version }: { photos: PhotoOut[]; version: SceneVersionOut | undefined }) {
  const sides = photos.filter((p) => p.side !== "other")
  if (sides.length === 0) return <p className="p-3 text-sm text-muted-foreground">No façade photo.</p>
  return (
    <div className="grid gap-3 p-3">
      {sides.map((p) => {
        const render = version?.render_urls[p.side]
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
    </div>
  )
}
