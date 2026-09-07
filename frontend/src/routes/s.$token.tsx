import { createFileRoute, Link } from "@tanstack/react-router"
import { Home } from "lucide-react"
import { useSharedProject } from "@/api/endpoints/shared/shared"
import { SceneViewer } from "@/components/SceneViewer"
import { errorMessage } from "@/lib/utils"

export const Route = createFileRoute("/s/$token")({ component: SharedPage })

/**
 * The read-only page behind a share link (#24): the 3D viewer of the shared scene (current
 * or pinned version), its renders and photos, a small header. No chat, versions, code or
 * actions: nothing here can change the project.
 */
function SharedPage() {
  const { token } = Route.useParams()
  const shared = useSharedProject(token, { query: { retry: false } })
  if (shared.isPending) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>
  if (shared.error || !shared.data)
    return (
      <div className="grid h-full place-items-center p-6 text-center text-sm">
        <div>
          <p className="font-medium">This link is not valid</p>
          <p className="mt-1 text-muted-foreground">{shared.error ? errorMessage(shared.error) : "It may have been revoked."}</p>
        </div>
      </div>
    )
  const s = shared.data
  const renders = Object.entries(s.render_urls)
  return (
    <div className="grid h-full grid-rows-[auto_1fr_auto] gap-3 p-3">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">{s.name}</h1>
        <span className="text-xs text-muted-foreground">
          version {s.version}
          {s.pinned ? " (pinned)" : ""} · read-only
        </span>
        <Link to="/" className="ml-auto flex items-center gap-1 text-xs text-muted-foreground hover:underline">
          <Home className="size-3.5" /> made with housegen
        </Link>
      </div>
      <SceneViewer sceneUrl={s.scene_url} reloadKey={`shared-${s.version}`} className="min-h-[420px]" />
      {(renders.length > 0 || s.photo_urls.length > 0) && (
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          {renders.map(([view, url]) => (
            <a key={view} href={url} target="_blank" rel="noreferrer" title={view} className="shrink-0">
              <img src={url} alt={view} className="h-20 rounded border object-cover" />
            </a>
          ))}
          {s.photo_urls.map((url) => (
            <a key={url} href={url} target="_blank" rel="noreferrer" title="photo" className="shrink-0">
              <img src={url} alt="photo" className="h-20 rounded border object-cover opacity-90" />
            </a>
          ))}
        </div>
      )}
    </div>
  )
}
