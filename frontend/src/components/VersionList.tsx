import { History, RotateCcw, Wrench } from "lucide-react"
import type { SceneVersionOut } from "@/api/model"
import { Button } from "@/components/ui/button"
import { ScoreBadge } from "@/components/JobTimeline"
import { cn, relTime } from "@/lib/utils"

export function VersionList({
  versions,
  current,
  selected,
  onSelect,
  onRestore,
  onFix,
  busy,
}: {
  versions: SceneVersionOut[]
  current: number
  selected: number | null
  onSelect: (n: number | null) => void
  onRestore: (n: number) => void
  onFix: (n: number) => void
  busy: boolean
}) {
  if (versions.length === 0) return <p className="p-3 text-sm text-muted-foreground">No version yet.</p>
  return (
    <ul className="divide-y">
      {[...versions].reverse().map((v) => {
        const isCurrent = v.number === current
        const isSelected = selected === v.number || (selected === null && isCurrent)
        const issues = v.critique?.issues.length ?? 0
        const suggestions = v.suggestions?.length ?? 0
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
  )
}
