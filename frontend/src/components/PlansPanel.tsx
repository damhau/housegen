import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { FileText, Loader2, Plus } from "lucide-react"
import { getGetProjectQueryKey, useAddPlanDocument } from "@/api/endpoints/projects/projects"
import type { ProjectOut } from "@/api/model"
import { Button } from "@/components/ui/button"
import { errorMessage, relTime } from "@/lib/utils"

/**
 * The plan documents of the project (#10) with their sheets, and a form to add one later
 * (the extension drawings, a survey): the next run sees it.
 */
export function PlansPanel({ project, busy }: { project: ProjectOut; busy: boolean }) {
  const qc = useQueryClient()
  const add = useAddPlanDocument()
  const [file, setFile] = useState<File | null>(null)
  const [label, setLabel] = useState("")
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (!file) return
    setError(null)
    try {
      await add.mutateAsync({ projectId: project.id, data: { plan: file as unknown as string, label: label.trim() } })
      setFile(null)
      setLabel("")
      await qc.invalidateQueries({ queryKey: getGetProjectQueryKey(project.id) })
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div className="space-y-4 p-3 text-sm">
      {project.plans.map((d) => (
        <section key={d.id}>
          <div className="flex items-baseline gap-2">
            <FileText className="size-3.5 self-center text-muted-foreground" />
            <span className="font-medium">
              {d.number}. {d.label}
            </span>
            <span className="text-xs text-muted-foreground">
              {d.pages} sheet{d.pages === 1 ? "" : "s"} · {d.original_name} · {relTime(d.created_at)}
            </span>
          </div>
          <div className="mt-1.5 grid grid-cols-3 gap-1.5">
            {d.page_urls.map((url, i) => (
              <a key={url} href={url} target="_blank" rel="noreferrer" className="group relative overflow-hidden rounded border bg-white">
                <img src={url} alt={`${d.label} sheet ${i + 1}`} className="aspect-[4/3] w-full object-contain" />
                <span className="absolute bottom-0.5 left-0.5 rounded bg-background/80 px-1 text-[10px]">
                  sheet {project.plans.slice(0, d.number - 1).reduce((n, x) => n + x.pages, 0) + i + 1}
                </span>
              </a>
            ))}
          </div>
        </section>
      ))}
      {project.plans.length > 1 && (
        <p className="text-xs text-muted-foreground">Where documents disagree, the builder trusts the most recent one (the last) for the house as it is today, unless the notes say otherwise.</p>
      )}
      <div className="space-y-2 rounded-md border border-dashed p-3">
        <div className="text-xs font-medium">Add a plan document</div>
        <label className="flex cursor-pointer items-center gap-2 text-xs">
          <span className="rounded-md border px-2 py-1 hover:bg-accent">choose a PDF</span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{file ? file.name : "the extension drawings, a survey…"}</span>
          <input type="file" accept="application/pdf" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <div className="flex gap-2">
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="label, e.g. 2024 survey" className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 text-xs" />
          <Button size="sm" onClick={() => void submit()} disabled={!file || add.isPending || busy} title={busy ? "Wait for the running job" : undefined}>
            {add.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Add
          </Button>
        </div>
        {error && <div className="text-xs text-destructive">{error}</div>}
      </div>
    </div>
  )
}
