import { useState } from "react"
import { History, RotateCcw, Wrench } from "lucide-react"
import type { JobOut, SceneVersionOut } from "@/api/model"
import { Button } from "@/components/ui/button"
import { ScoreBadge } from "@/components/JobTimeline"
import { RunCompare, RunSummaryCard, fmtMs, fmtUsd } from "@/components/RunSummaryCard"
import { cn, relTime } from "@/lib/utils"

const KIND_LABEL: Record<string, string> = { intake: "plans read", generate: "generation", modify: "modification" }

function jobLabel(j: JobOut): string {
  return `${KIND_LABEL[j.kind] ?? j.kind}${j.result_version != null ? ` → v${j.result_version}` : ""} · ${relTime(j.created_at)}`
}

/** Two finished runs of the project side by side (#13). */
function CompareRuns({ jobs }: { jobs: JobOut[] }) {
  const done = jobs.filter((j) => j.metrics)
  const [a, setA] = useState<string>(done[1]?.id ?? "")
  const [b, setB] = useState<string>(done[0]?.id ?? "")
  if (done.length < 2) return null
  const ja = done.find((j) => j.id === a)
  const jb = done.find((j) => j.id === b)
  const select = (v: string, set: (s: string) => void) => (
    <select value={v} onChange={(e) => set(e.target.value)} className="h-7 max-w-[180px] rounded-md border bg-background px-1 text-xs">
      {done.map((j) => (
        <option key={j.id} value={j.id}>
          {jobLabel(j)}
        </option>
      ))}
    </select>
  )
  return (
    <div className="space-y-2 border-t p-3">
      <div className="text-xs font-medium">Compare two runs</div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {select(a, setA)} <span className="text-muted-foreground">vs</span> {select(b, setB)}
      </div>
      {ja && jb && <RunCompare a={ja} b={jb} labelA="A" labelB="B" />}
    </div>
  )
}

export function VersionList({
  versions,
  jobs = [],
  current,
  selected,
  onSelect,
  onRestore,
  onFix,
  busy,
}: {
  versions: SceneVersionOut[]
  /** the project's jobs: the one that produced a version gives its run summary */
  jobs?: JobOut[]
  current: number
  selected: number | null
  onSelect: (n: number | null) => void
  onRestore: (n: number) => void
  onFix: (n: number) => void
  busy: boolean
}) {
  const [details, setDetails] = useState<number | null>(null)
  if (versions.length === 0) return <p className="p-3 text-sm text-muted-foreground">No version yet.</p>
  const jobOf = (v: SceneVersionOut) => jobs.find((j) => j.result_version === v.number && (j.metrics || j.settings))
  return (
    <div>
    <ul className="divide-y">
      {[...versions].reverse().map((v) => {
        const isCurrent = v.number === current
        const isSelected = selected === v.number || (selected === null && isCurrent)
        const issues = v.critique?.issues.length ?? 0
        const suggestions = v.suggestions?.length ?? 0
        const job = jobOf(v)
        return (
          <li
            key={v.id}
            className={cn("cursor-pointer px-3 py-2 text-sm hover:bg-accent/40", isSelected && "bg-accent/60")}
            onClick={() => onSelect(isCurrent ? null : v.number)}
          >
            <div className="flex items-center gap-3">
              {v.render_urls.aerial ? (
                <img src={v.render_urls.aerial} alt="" className="h-10 w-16 rounded border object-cover" />
              ) : (
                <div className="grid h-10 w-16 place-items-center rounded border text-muted-foreground">
                  <History className="size-3.5" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">v{v.number}</span>
                  <span className="truncate text-muted-foreground">{v.label}</span>
                  {isCurrent && <span className="text-[10px] uppercase text-primary">current</span>}
                </div>
                <div className="text-xs text-muted-foreground">
                  {v.kind} · {relTime(v.created_at)}
                  {issues > 0 && ` · ${issues} finding${issues > 1 ? "s" : ""}`}
                  {suggestions > 0 && ` · ${suggestions} suggestion${suggestions > 1 ? "s" : ""}`}
                </div>
                {job?.settings && (
                  <div className="text-[11px] text-muted-foreground" title="the settings this version was made with">
                    {job.settings.model} · {job.settings.builder_effort} · {job.settings.critic_rounds} round{job.settings.critic_rounds === 1 ? "" : "s"} · {job.settings.max_steps} steps
                  </div>
                )}
                {job?.metrics && (
                  <button
                    type="button"
                    className="text-[11px] text-muted-foreground underline-offset-2 hover:underline"
                    onClick={(e) => {
                      e.stopPropagation()
                      setDetails(details === v.number ? null : v.number)
                    }}
                  >
                    {fmtMs(job.metrics.wall_ms)} · {job.metrics.turns ?? 0} turns · {fmtUsd(job.metrics.cost_usd)}
                    {job.metrics.models?.length ? ` · ${job.metrics.models[0]}` : ""}
                  </button>
                )}
              </div>
              {v.critic_score != null && <ScoreBadge score={v.critic_score} />}
              {!isCurrent && (
                <Button
                  size="icon"
                  variant="ghost"
                  className="size-7"
                  title="Restore this version"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation()
                    onRestore(v.number)
                  }}
                >
                  <RotateCcw className="size-3.5" />
                </Button>
              )}
            </div>
            {details === v.number && job?.metrics && <RunSummaryCard metrics={job.metrics} title="Run" className="mt-2" />}
            {isCurrent && issues > 0 && !busy && (
              <div className="mt-2 pl-[76px]">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={(e) => {
                    e.stopPropagation()
                    onFix(v.number)
                  }}
                >
                  <Wrench /> Apply the review's {issues} finding{issues > 1 ? "s" : ""}
                </Button>
              </div>
            )}
          </li>
        )
      })}
    </ul>
    <CompareRuns jobs={jobs} />
    </div>
  )
}
