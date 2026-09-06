import { useState } from "react"
import { useSceneFiles } from "@/api/endpoints/projects/projects"
import { cn } from "@/lib/utils"

export function CodePanel({ projectId, version }: { projectId: string; version: number | null }) {
  const q = useSceneFiles(projectId, version === null ? undefined : { version })
  const [active, setActive] = useState<string | null>(null)
  const files = q.data?.files ?? []
  const file = files.find((f) => f.path === active) ?? files[0]
  if (q.isPending) return <p className="p-3 text-sm text-muted-foreground">Loading…</p>
  if (files.length === 0) return <p className="p-3 text-sm text-muted-foreground">No file.</p>
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap gap-1 border-b p-2">
        {files.map((f) => (
          <button
            key={f.path}
            onClick={() => setActive(f.path)}
            className={cn(
              "rounded px-2 py-0.5 font-mono text-xs hover:bg-accent",
              file?.path === f.path && "bg-accent text-accent-foreground",
            )}
          >
            {f.path.replace(/^src\//, "")}
          </button>
        ))}
      </div>
      <pre className="min-h-0 flex-1 overflow-auto p-3 font-mono text-[11px] leading-relaxed">{file?.content}</pre>
    </div>
  )
}
