import { useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { FileText, ImagePlus, Loader2 } from "lucide-react"
import { getListProjectsQueryKey, useCreateProject, useGenerate } from "@/api/endpoints/projects/projects"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

const SIDES = ["north", "east", "south", "west"] as const
type Side = (typeof SIDES)[number]

export function NewProjectForm() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const create = useCreateProject()
  const generate = useGenerate()
  const [name, setName] = useState("")
  const [plan, setPlan] = useState<File | null>(null)
  const [photos, setPhotos] = useState<Partial<Record<Side, File>>>({})
  const [error, setError] = useState<string | null>(null)

  const chosen = SIDES.filter((s) => photos[s])
  const canSubmit = name.trim().length > 0 && plan !== null && chosen.length > 0 && !create.isPending && !generate.isPending

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!plan) return
    setError(null)
    try {
      // OpenAPI describes file fields as `string` (format: binary); the generated
      // call builds a FormData, so passing File objects is what actually goes over the wire.
      const project = await create.mutateAsync({
        data: {
          name: name.trim(),
          plan: plan as unknown as string,
          photos: chosen.map((s) => photos[s]!) as unknown as string[],
          sides: chosen,
        },
      })
      await generate.mutateAsync({ projectId: project.id })
      await qc.invalidateQueries({ queryKey: getListProjectsQueryKey() })
      await navigate({ to: "/projects/$projectId", params: { projectId: project.id } })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Card>
      <CardContent className="p-5">
        <form onSubmit={submit} className="grid gap-4">
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Name</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Villa Rosemont" required />
          </label>

          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Plan (PDF)</span>
            <div
              className={cn(
                "flex items-center gap-3 rounded-md border border-dashed px-3 py-3 text-sm",
                plan ? "border-primary/50 bg-accent/40" : "border-input",
              )}
            >
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{plan ? plan.name : "Floor plans, elevations, site plan"}</span>
              <input type="file" accept="application/pdf" className="hidden" onChange={(e) => setPlan(e.target.files?.[0] ?? null)} />
              <span className="text-xs text-primary underline-offset-2 hover:underline">choose</span>
            </div>
          </label>

          <div className="grid gap-1.5 text-sm">
            <span className="font-medium">Photos, one per façade</span>
            <div className="grid grid-cols-2 gap-2">
              {SIDES.map((side) => {
                const f = photos[side]
                const url = f ? URL.createObjectURL(f) : null
                return (
                  <label
                    key={side}
                    className={cn(
                      "relative flex aspect-[4/3] cursor-pointer flex-col items-center justify-center overflow-hidden rounded-md border border-dashed text-xs",
                      f ? "border-primary/50" : "border-input hover:bg-accent/40",
                    )}
                  >
                    {url ? <img src={url} alt="" className="absolute inset-0 size-full object-cover" /> : <ImagePlus className="mb-1 size-4 text-muted-foreground" />}
                    <span className={cn("relative rounded bg-background/80 px-1.5 py-0.5 font-medium capitalize", f && "absolute bottom-1 left-1")}>
                      {side}
                    </span>
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      className="hidden"
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        setPhotos((p) => ({ ...p, [side]: file ?? undefined }))
                      }}
                    />
                  </label>
                )
              })}
            </div>
            <p className="text-xs text-muted-foreground">Label each photo by the façade it shows. Four sides give the best result.</p>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={!canSubmit}>
            {(create.isPending || generate.isPending) && <Loader2 className="animate-spin" />}
            Upload and generate
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}
