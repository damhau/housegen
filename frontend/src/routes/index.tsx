import { createFileRoute, Link } from "@tanstack/react-router"
import { useListProjects } from "@/api/endpoints/projects/projects"
import { NewProjectForm } from "@/components/NewProjectForm"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { errorMessage, relTime } from "@/lib/utils"

export const Route = createFileRoute("/")({ component: HomePage })

function HomePage() {
  const projects = useListProjects()
  return (
    <div className="mx-auto grid max-w-6xl gap-8 p-6 lg:grid-cols-[420px_1fr]">
      <section>
        <h1 className="mb-1 text-xl font-semibold tracking-tight">New house</h1>
        <p className="mb-4 text-sm text-muted-foreground">
          Upload the plan set as one or more PDFs and, if you have them, photos of the façades. The agent reads the plans, builds a
          three.js scene, renders it, compares it with your photos (or with the elevation drawings) and fixes what differs.
          Without photos it first asks you a few questions the drawings cannot answer.
        </p>
        <NewProjectForm />
      </section>
      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground">Projects</h2>
        {projects.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
        {projects.error ? <p className="text-sm text-destructive">{errorMessage(projects.error)}</p> : null}
        {projects.data?.length === 0 && <p className="text-sm text-muted-foreground">No project yet.</p>}
        <div className="grid gap-3 sm:grid-cols-2">
          {projects.data?.map((p) => (
            <Link key={p.id} to="/projects/$projectId" params={{ projectId: p.id }} className="group">
              <Card className="overflow-hidden transition-shadow group-hover:shadow-md">
                <div className="aspect-[16/10] bg-muted">
                  {p.thumbnail_url ? (
                    <img src={p.thumbnail_url} alt="" className="size-full object-cover" />
                  ) : (
                    <div className="grid size-full place-items-center text-xs text-muted-foreground">no render yet</div>
                  )}
                </div>
                <CardContent className="flex items-center justify-between gap-2 p-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{p.name}</div>
                    <div className="text-xs text-muted-foreground">
                      v{p.current_version} · {relTime(p.created_at)}
                    </div>
                  </div>
                  <StatusBadge status={p.status} />
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </section>
    </div>
  )
}

export function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "ready" ? "success" : status === "generating" ? "warning" : status === "failed" ? "destructive" : "secondary"
  return <Badge variant={variant}>{status}</Badge>
}
