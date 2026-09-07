import { useEffect, useMemo, useState } from "react"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { Download, Loader2, Play, Trash2 } from "lucide-react"
import {
  getChatHistoryQueryKey,
  getGetProjectQueryKey,
  getListJobsQueryKey,
  getListProjectsQueryKey,
  useChatHistory,
  useDeleteProject,
  useGenerate,
  useGetProject,
  useIntake,
  useListJobs,
  useModify,
  useRestoreVersion,
  useFixVersion,
} from "@/api/endpoints/projects/projects"
import type { IntakeAnswer } from "@/api/model"
import { BuildingPlaceholder } from "@/components/BuildingPlaceholder"
import { CodePanel } from "@/components/CodePanel"
import { ComparePanel } from "@/components/ComparePanel"
import { ConversationPanel } from "@/components/ConversationPanel"
import { SceneViewer } from "@/components/SceneViewer"
import { VersionList } from "@/components/VersionList"
import { Button } from "@/components/ui/button"
import { useJobStream } from "@/hooks/useJobStream"
import { cn, errorMessage, parseIso } from "@/lib/utils"
import { StatusBadge } from "@/routes/index"

export const Route = createFileRoute("/projects/$projectId")({ component: ProjectPage })

type Tab = "conversation" | "compare" | "versions" | "code"

/** "m:ss" since an ISO timestamp, ticking every second; null when no timestamp. */
function useElapsed(sinceIso: string | null) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!sinceIso) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [sinceIso])
  if (!sinceIso) return null
  const s = Math.max(0, Math.floor((now - parseIso(sinceIso).getTime()) / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
}

function ProjectPage() {
  const { projectId } = Route.useParams()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const project = useGetProject(projectId)
  const jobs = useListJobs(projectId)
  const chat = useChatHistory(projectId)
  const generate = useGenerate()
  const intake = useIntake()
  const modify = useModify()
  const restore = useRestoreVersion()
  const fix = useFixVersion()
  const del = useDeleteProject()

  const [tab, setTab] = useState<Tab>("conversation")
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  // the job to follow: the most recent one that is still running, else the most recent one at all
  const activeJob = useMemo(() => jobs.data?.find((j) => j.status === "running" || j.status === "queued") ?? null, [jobs.data])
  const latestJob = jobs.data?.[0] ?? null
  const followed = activeJob ?? latestJob

  const refreshAll = () => {
    void qc.invalidateQueries({ queryKey: getGetProjectQueryKey(projectId) })
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
    void qc.invalidateQueries({ queryKey: getChatHistoryQueryKey(projectId) })
    void qc.invalidateQueries({ queryKey: getListProjectsQueryKey() })
    setReloadKey((k) => k + 1)
  }
  const { events, live, progress, liveText, liveThought } = useJobStream(projectId, followed?.id, refreshAll)
  const elapsed = useElapsed(activeJob?.created_at ?? null)

  // reload the viewer whenever a version lands mid-job
  const versionEvents = events.filter((e) => e.type === "version").length
  useEffect(() => {
    if (versionEvents > 0) {
      setReloadKey((k) => k + 1)
      void qc.invalidateQueries({ queryKey: getGetProjectQueryKey(projectId) })
    }
  }, [versionEvents, projectId, qc])

  // the viewer follows the build: reload after every error-free render (never on a broken scene),
  // unless the user paused it to keep orbiting a given state
  const [autoReload, setAutoReload] = useState(true)
  // (only renders taken after the builder's first write count: before that the working copy is
  // still the kit's template box, which must never be shown as "the house")
  const lastGoodRender = useMemo(() => {
    let seq = 0
    let edited = false
    for (const e of events) {
      const p = e.payload as { errors?: unknown[]; tool?: string } | null
      if (e.type === "builder_step" && (p?.tool === "write_file" || p?.tool === "edit_file" || p?.tool === "apply_patch")) edited = true
      if (e.type === "render" && edited && Array.isArray(p?.errors) && p.errors.length === 0) seq = e.seq
    }
    return seq
  }, [events])
  useEffect(() => {
    if (lastGoodRender > 0 && autoReload && Boolean(activeJob)) setReloadKey((k) => k + 1)
  }, [lastGoodRender, autoReload]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (activeJob) setTab("conversation")
  }, [activeJob?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  if (project.isPending) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>
  if (project.error || !project.data)
    return <div className="p-6 text-sm text-destructive">{project.error ? errorMessage(project.error) : "not found"}</div>
  const p = project.data
  const busy = Boolean(activeJob) || generate.isPending || intake.isPending || modify.isPending
  const hasPhotos = p.photos.length > 0
  // no photos: the plans are read first (intake job); the build starts from the answers
  const needsIntake = !hasPhotos && p.intake === null
  const awaitingAnswers = !hasPhotos && p.intake !== null && p.current_version === 0 && latestJob?.kind === "intake" && latestJob.status === "done"
  const version = selectedVersion === null ? p.versions.find((v) => v.number === p.current_version) : p.versions.find((v) => v.number === selectedVersion)
  const sceneUrl = selectedVersion === null || !version ? p.scene_url : version.scene_url
  // no version yet: the working copy is only the kit's template box, don't show it as "the house" —
  // until the running job has produced an error-free render of it (then the viewer follows the build)
  const hasScene = p.current_version > 0 || p.versions.length > 0 || (Boolean(activeJob) && lastGoodRender > 0)
  const failedMessage = !activeJob && latestJob?.status === "failed" ? (latestJob.error ?? "the run failed") : null

  async function onSend(text: string) {
    await modify.mutateAsync({ projectId, data: { message: text } })
    setSelectedVersion(null)
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
    void qc.invalidateQueries({ queryKey: getChatHistoryQueryKey(projectId) })
  }
  async function onGenerate() {
    if (needsIntake) await intake.mutateAsync({ projectId })
    else await generate.mutateAsync({ projectId, data: null })
    setSelectedVersion(null)
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
  }
  async function onAnswer(answers: IntakeAnswer[], notes: string) {
    await generate.mutateAsync({ projectId, data: { answers, notes } })
    setSelectedVersion(null)
    void qc.invalidateQueries({ queryKey: getGetProjectQueryKey(projectId) })
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
    void qc.invalidateQueries({ queryKey: getChatHistoryQueryKey(projectId) })
  }
  async function onFix(n: number) {
    await fix.mutateAsync({ projectId, number: n })
    setSelectedVersion(null)
    setTab("conversation")
    void qc.invalidateQueries({ queryKey: getListJobsQueryKey(projectId) })
    void qc.invalidateQueries({ queryKey: getChatHistoryQueryKey(projectId) })
  }
  async function onRestore(n: number) {
    await restore.mutateAsync({ projectId, number: n })
    setSelectedVersion(null)
    refreshAll()
  }
  async function onDelete() {
    if (!confirm(`Delete “${p.name}” and all its versions?`)) return
    await del.mutateAsync({ projectId })
    void qc.invalidateQueries({ queryKey: getListProjectsQueryKey() })
    await navigate({ to: "/" })
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "conversation", label: "Conversation" },
    { id: "compare", label: hasPhotos ? "Photo vs render" : "Plan vs render" },
    { id: "versions", label: `Versions (${p.versions.length})` },
    { id: "code", label: "Code" },
  ]

  return (
    <div className="grid h-full grid-rows-[auto_1fr] gap-3 p-3">
      <div className="flex items-center gap-3">
        <Link to="/" className="text-sm text-muted-foreground hover:underline">
          Projects
        </Link>
        <span className="text-muted-foreground">/</span>
        <h1 className="text-lg font-semibold tracking-tight">{p.name}</h1>
        <StatusBadge status={p.status} />
        <span className="text-xs text-muted-foreground">
          {selectedVersion === null ? `current v${p.current_version}` : `viewing v${selectedVersion}`}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {p.current_version === 0 && !busy && !awaitingAnswers && (
            <Button size="sm" onClick={() => void onGenerate()}>
              <Play /> {needsIntake ? "Read the plans" : "Generate"}
            </Button>
          )}
          {p.current_version > 0 && !busy && (
            <Button size="sm" variant="outline" onClick={() => void onGenerate()} title="Rebuild from scratch">
              <Play /> Regenerate
            </Button>
          )}
          {busy && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin" /> agent running
              {elapsed && <span className="font-mono tabular-nums">· {elapsed}</span>}
            </span>
          )}
          {p.current_version > 0 && (
            <a href={`/api/v1/projects/${projectId}/export${selectedVersion !== null ? `?version=${selectedVersion}` : ""}`}>
              <Button size="sm" variant="outline">
                <Download /> Export
              </Button>
            </a>
          )}
          <Button size="icon" variant="ghost" className="text-muted-foreground" onClick={() => void onDelete()} disabled={busy} title="Delete project">
            <Trash2 />
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 gap-3 lg:grid-cols-[1fr_420px]">
        <SceneViewer
          sceneUrl={hasScene ? sceneUrl : null}
          reloadKey={`${reloadKey}-${selectedVersion ?? "c"}`}
          className="min-h-[420px]"
          live={Boolean(activeJob) && selectedVersion === null}
          autoReload={autoReload}
          onToggleAutoReload={() => setAutoReload((v) => !v)}
          placeholder={
            <BuildingPlaceholder
              running={Boolean(activeJob)}
              kind={activeJob?.kind ?? null}
              events={events}
              progress={progress}
              elapsed={elapsed}
              failedMessage={failedMessage}
              awaitingAnswers={awaitingAnswers}
              hasPhotos={hasPhotos}
              canGenerate={!busy}
              onGenerate={() => void onGenerate()}
            />
          }
        />
        <aside className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card">
          <div className="flex shrink-0 gap-0.5 overflow-x-auto border-b p-1">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cn(
                  "whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                  tab === t.id && "bg-accent text-accent-foreground",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {tab === "conversation" && (
              <ConversationPanel
                projectId={projectId}
                jobs={jobs.data ?? []}
                messages={chat.data ?? []}
                versions={p.versions}
                currentVersion={p.current_version}
                hasPhotos={hasPhotos}
                intake={p.intake}
                liveJob={{ jobId: followed?.id ?? null, events, live, progress, liveText, liveThought, elapsed }}
                busy={busy}
                disabled={p.current_version === 0}
                onSend={onSend}
                onFix={(n) => void onFix(n)}
                onAnswer={onAnswer}
              />
            )}
            {tab === "compare" && <ComparePanel photos={p.photos} version={version} planPageUrls={p.plan_page_urls} intake={p.intake} />}
            {tab === "versions" && (
              <VersionList
                versions={p.versions}
                current={p.current_version}
                selected={selectedVersion}
                onSelect={setSelectedVersion}
                onRestore={(n) => void onRestore(n)}
                onFix={(n) => void onFix(n)}
                busy={restore.isPending || fix.isPending || busy}
              />
            )}
            {tab === "code" && <CodePanel projectId={projectId} version={selectedVersion} />}
          </div>
        </aside>
      </div>
    </div>
  )
}
