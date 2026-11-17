import { useState } from "react"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { Building2 } from "lucide-react"
import { useMe } from "@/api/endpoints/accounts/accounts"
import {
  getAdminOrganisationsQueryKey,
  getAdminProjectsQueryKey,
  useAdminOrganisations,
  useAdminProjects,
  useMoveProject,
} from "@/api/endpoints/admin/admin"
import { getListProjectsQueryKey } from "@/api/endpoints/projects/projects"
import type { AdminOrganisationOut, AdminProjectOut } from "@/api/model"
import { Avatar } from "@/components/accounts/Avatar"
import { CenteredCard } from "@/components/accounts/CenteredCard"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { JOB_LABEL, StatusBadge } from "@/routes/index"
import { errorMessage, formatDate, formatDay, relTime } from "@/lib/utils"

export const Route = createFileRoute("/admin")({ component: AdminPage })

/** The administrator's view (#108): every organisation with its members and projects, and moving projects between them. */
function AdminPage() {
  const me = useMe()
  if (me.isPending) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>
  if (!me.data?.is_admin)
    return (
      <CenteredCard title="Not available" description="This page is for the administrator of this server.">
        <Link to="/" className="text-sm underline underline-offset-4">
          Back to projects
        </Link>
      </CenteredCard>
    )
  return <AdminView />
}

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`
}

function AdminView() {
  const organisations = useAdminOrganisations()
  // refreshed while a job works on a project, so its badge clears on its own
  const projects = useAdminProjects({ query: { refetchInterval: (q) => (q.state.data?.some((p) => p.job) ? 5000 : false) } })
  const error = organisations.error ?? projects.error
  if (error) return <p className="p-6 text-sm text-destructive">{errorMessage(error)}</p>
  if (!organisations.data || !projects.data) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>

  const orgs = organisations.data
  const known = new Set(orgs.map((o) => o.id))
  const byOrganisation = new Map<string, AdminProjectOut[]>()
  const unassigned: AdminProjectOut[] = []
  for (const p of projects.data) {
    if (p.organisation_id && known.has(p.organisation_id)) {
      byOrganisation.set(p.organisation_id, [...(byOrganisation.get(p.organisation_id) ?? []), p])
    } else {
      unassigned.push(p)
    }
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-6 sm:p-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Admin</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Every organisation on this server, with its members and projects. Moving a project changes who sees it; its files,
          versions, listing photos and share link stay as they are.
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          {plural(orgs.length, "organisation", "organisations")} · {plural(projects.data.length, "project", "projects")}
        </p>
      </div>
      {orgs.map((o) => (
        <OrganisationCard key={o.id} organisation={o} projects={byOrganisation.get(o.id) ?? []} organisations={orgs} />
      ))}
      {unassigned.length > 0 && <OrganisationCard organisation={null} projects={unassigned} organisations={orgs} />}
    </div>
  )
}

function OrganisationCard({
  organisation,
  projects,
  organisations,
}: {
  organisation: AdminOrganisationOut | null
  projects: AdminProjectOut[]
  organisations: AdminOrganisationOut[]
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Building2 className="size-4 shrink-0 text-muted-foreground" />
          <CardTitle className="min-w-0 break-words text-base">{organisation?.name ?? "No organisation"}</CardTitle>
          {organisation?.is_default && <Badge variant="secondary">default</Badge>}
        </div>
        <CardDescription>
          {organisation
            ? `${plural(organisation.project_count, "project", "projects")} · created ${formatDay(organisation.created_at)}`
            : "Projects that belong to no organisation: nobody but the administrator sees them."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {organisation && (
          <section className="flex flex-col gap-1">
            <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Members</h4>
            {organisation.members.length === 0 && <p className="text-sm text-muted-foreground">Nobody has joined yet.</p>}
            {organisation.members.map((m) => (
              <div key={m.user_id} className="flex items-center gap-3 py-1">
                <Avatar name={m.name} email={m.email} picture={m.picture} className="size-7" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{m.name || m.email}</div>
                  {m.name && m.email && <div className="truncate text-xs text-muted-foreground">{m.email}</div>}
                </div>
                <Badge variant={m.role === "owner" ? "default" : "secondary"}>{m.role}</Badge>
              </div>
            ))}
          </section>
        )}
        <section className="flex flex-col gap-2">
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Projects</h4>
          {projects.length === 0 && <p className="text-sm text-muted-foreground">No project.</p>}
          {projects.map((p) => (
            <ProjectRow key={p.id} project={p} from={organisation} organisations={organisations} />
          ))}
        </section>
      </CardContent>
    </Card>
  )
}

function ProjectRow({
  project,
  from,
  organisations,
}: {
  project: AdminProjectOut
  from: AdminOrganisationOut | null
  organisations: AdminOrganisationOut[]
}) {
  const qc = useQueryClient()
  const move = useMoveProject()
  const [error, setError] = useState<string | null>(null)
  const targets = organisations.filter((o) => o.id !== from?.id)
  const fromName = from?.name ?? "no organisation"

  async function onMove(targetId: string) {
    const target = targets.find((o) => o.id === targetId)
    if (!target) return
    const question =
      `Move "${project.name}" from ${fromName} to ${target.name}?\n\n` +
      `The members of ${target.name} will see and edit it${from ? `; those of ${from.name} will no longer see it` : ""}. ` +
      "Its files, versions, listing photos and share link stay as they are."
    if (!confirm(question)) return
    setError(null)
    try {
      await move.mutateAsync({ projectId: project.id, data: { organisation_id: target.id } })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      await Promise.all([
        qc.invalidateQueries({ queryKey: getAdminOrganisationsQueryKey() }),
        qc.invalidateQueries({ queryKey: getAdminProjectsQueryKey() }),
        qc.invalidateQueries({ queryKey: getListProjectsQueryKey() }),
      ])
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border px-3 py-2">
      <div className="min-w-0 flex-1 basis-40">
        <div className="truncate text-sm font-medium">{project.name}</div>
        <div className="text-xs text-muted-foreground" title={formatDate(project.updated_at)}>
          updated {relTime(project.updated_at)}
        </div>
      </div>
      {project.job ? <Badge variant="warning">{JOB_LABEL[project.job] ?? "working"}</Badge> : <StatusBadge status={project.status} />}
      <select
        aria-label={`Move ${project.name} to another organisation`}
        value=""
        disabled={move.isPending || targets.length === 0}
        onChange={(e) => void onMove(e.target.value)}
        className="h-8 w-full rounded-md border bg-background px-2 text-sm disabled:opacity-50 sm:w-56"
      >
        <option value="" disabled>
          {move.isPending ? "Moving…" : "Move to…"}
        </option>
        {targets.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
      {error && <p className="basis-full text-sm text-destructive">{error}</p>}
    </div>
  )
}
