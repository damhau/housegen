import { useState } from "react"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { Building2, Gauge } from "lucide-react"
import { useMe } from "@/api/endpoints/accounts/accounts"
import {
  getAdminOrganisationsQueryKey,
  getAdminProjectsQueryKey,
  useAdminOrganisations,
  useAdminProjects,
  useMoveProject,
  useSetOrganisationLimits,
} from "@/api/endpoints/admin/admin"
import { getListProjectsQueryKey } from "@/api/endpoints/projects/projects"
import type { AdminOrganisationOut, AdminProjectOut, LimitOut, LimitsIn } from "@/api/model"
import { Avatar } from "@/components/accounts/Avatar"
import { CenteredCard } from "@/components/accounts/CenteredCard"
import { LimitsList } from "@/components/accounts/Limits"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { JOB_LABEL, StatusBadge } from "@/routes/index"
import { LIMITS, type LimitKey } from "@/lib/limits"
import { errorMessage, formatDate, formatDay, relTime } from "@/lib/utils"

export const Route = createFileRoute("/admin")({ component: AdminPage })

/** The administrator's view (#108): every organisation with its members, projects and limits (#107), and moving projects between them. */
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
          Every organisation on this server, with its members, projects and limits. Moving a project changes who sees it; its
          files, versions, listing photos and share link stay as they are.
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
        {organisation && <LimitsSection organisation={organisation} />}
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

type Choice = "default" | "own" | "unlimited"

function choiceOf(limit: LimitOut): { choice: Choice; value: string } {
  if (limit.override === null) return { choice: "default", value: String(limit.default) }
  if (limit.override === "unlimited") return { choice: "unlimited", value: String(limit.default) }
  return { choice: "own", value: String(limit.override) }
}

function LimitsSection({ organisation }: { organisation: AdminOrganisationOut }) {
  const [editing, setEditing] = useState(false)
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Limits</h4>
        {!editing && (
          <Button variant="ghost" size="sm" onClick={() => setEditing(true)} aria-label={`Edit the limits of ${organisation.name}`}>
            <Gauge />
            Edit limits
          </Button>
        )}
      </div>
      {editing ? (
        <LimitsEditor organisation={organisation} onDone={() => setEditing(false)} />
      ) : (
        <LimitsList limits={organisation.limits} showSource />
      )}
    </section>
  )
}

function LimitsEditor({ organisation, onDone }: { organisation: AdminOrganisationOut; onDone: () => void }) {
  const qc = useQueryClient()
  const save = useSetOrganisationLimits()
  const [draft, setDraft] = useState<Record<LimitKey, { choice: Choice; value: string }>>(() => ({
    concurrent_jobs: choiceOf(organisation.limits.concurrent_jobs),
    jobs_per_day: choiceOf(organisation.limits.jobs_per_day),
    listing_usd_per_month: choiceOf(organisation.limits.listing_usd_per_month),
  }))
  const [error, setError] = useState<string | null>(null)
  const invalid = LIMITS.filter(({ key, money }) => {
    const { choice, value } = draft[key]
    if (choice !== "own") return false
    const n = Number(value)
    return value.trim() === "" || !Number.isFinite(n) || n < 0 || (!money && !Number.isInteger(n))
  })

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (invalid.length > 0) return
    setError(null)
    const body: LimitsIn = {}
    for (const { key } of LIMITS) {
      const { choice, value } = draft[key]
      body[key] = choice === "default" ? null : choice === "unlimited" ? "unlimited" : Number(value)
    }
    try {
      await save.mutateAsync({ organisationId: organisation.id, data: body })
      await qc.invalidateQueries({ queryKey: getAdminOrganisationsQueryKey() })
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="grid gap-3 rounded-md border p-3">
      {LIMITS.map(({ key, label, money }) => {
        const { choice, value } = draft[key]
        const set = (next: Partial<{ choice: Choice; value: string }>) => setDraft((prev) => ({ ...prev, [key]: { ...prev[key], ...next } }))
        const fallback = organisation.limits[key].default
        return (
          <div key={key} className="grid gap-1.5 sm:grid-cols-[1fr_auto_auto] sm:items-center sm:gap-2">
            <span className="text-sm font-medium">{label}</span>
            <select
              aria-label={`${label}: which limit`}
              value={choice}
              onChange={(e) => set({ choice: e.target.value as Choice })}
              className="h-8 rounded-md border bg-background px-2 text-sm sm:w-48"
            >
              <option value="default">Default ({money ? `$${fallback.toFixed(2)}` : fallback})</option>
              <option value="own">Own limit</option>
              <option value="unlimited">No limit</option>
            </select>
            <div className="flex items-center gap-1 sm:w-32">
              {money && <span className="text-sm text-muted-foreground">$</span>}
              <Input
                type="number"
                inputMode={money ? "decimal" : "numeric"}
                min={0}
                step={money ? 0.01 : 1}
                aria-label={`${label}: own limit`}
                value={choice === "own" ? value : ""}
                placeholder={choice === "own" ? "" : "—"}
                disabled={choice !== "own"}
                onChange={(e) => set({ value: e.target.value })}
                className="h-8"
              />
            </div>
          </div>
        )
      })}
      {invalid.length > 0 && (
        <p className="text-sm text-destructive">
          {invalid.map((l) => l.label).join(", ")}: {invalid.some((l) => !l.money) ? "a whole number, 0 or more" : "an amount, 0 or more"}.
        </p>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      <p className="text-xs text-muted-foreground">
        Jobs already started go on; the next job is checked against the new limits. A limit of 0 lets none start.
      </p>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={save.isPending || invalid.length > 0}>
          {save.isPending ? "Saving…" : "Save limits"}
        </Button>
      </div>
    </form>
  )
}
