import { useMemo, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { FileText, ImagePlus, Images, Loader2, X } from "lucide-react"
import { getListProjectsQueryKey, useCreateProject, useGenerate, useIntake } from "@/api/endpoints/projects/projects"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn, errorMessage } from "@/lib/utils"

const SIDES = ["north", "east", "south", "west"] as const
type Side = (typeof SIDES)[number]

export function NewProjectForm() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const create = useCreateProject()
  const generate = useGenerate()
  const intake = useIntake()
  const [name, setName] = useState("")
  const [plan, setPlan] = useState<File | null>(null)
  const [facades, setFacades] = useState<Partial<Record<Side, File>>>({})
  const [extras, setExtras] = useState<File[]>([])
  const [notes, setNotes] = useState("")
  const [error, setError] = useState<string | null>(null)

  const labelled = SIDES.filter((s) => facades[s])
  const total = labelled.length + extras.length
  const busy = create.isPending || generate.isPending || intake.isPending
  const canSubmit = name.trim().length > 0 && plan !== null && !busy

  const extraUrls = useMemo(() => extras.map((f) => URL.createObjectURL(f)), [extras])

  function addExtras(files: FileList | null) {
    if (!files) return
    const picked = Array.from(files).filter((f) => f.type.startsWith("image/"))
    setExtras((prev) => [...prev, ...picked])
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!plan) return
    setError(null)
    try {
      const photos = [...labelled.map((s) => facades[s]!), ...extras]
      const sides = [...labelled, ...extras.map(() => "other")]
      // OpenAPI describes file fields as `string` (format: binary); the generated
      // call builds a FormData, so passing File objects is what actually goes over the wire.
      const project = await create.mutateAsync({
        data: {
          name: name.trim(),
          plan: plan as unknown as string,
          photos: photos as unknown as string[],
          sides,
          notes: notes.trim(),
        },
      })
      // without photos the agent reads the plans first and asks its questions; the build
      // starts from the conversation once they are answered
      if (photos.length === 0) await intake.mutateAsync({ projectId: project.id })
      else await generate.mutateAsync({ projectId: project.id, data: null })
      await qc.invalidateQueries({ queryKey: getListProjectsQueryKey() })
      await navigate({ to: "/projects/$projectId", params: { projectId: project.id } })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <Card>
      <CardContent className="p-5">
        <form onSubmit={submit} className="grid gap-5">
          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Name</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Villa Rosemont" required />
          </label>

          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">Plans (PDF)</span>
            <div
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-md border border-dashed px-3 py-3 text-sm",
                plan ? "border-primary/50 bg-accent/40" : "border-input hover:bg-accent/30",
              )}
            >
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{plan ? plan.name : "Floor plans, elevations, sections, site plan"}</span>
              <input type="file" accept="application/pdf" className="hidden" onChange={(e) => setPlan(e.target.files?.[0] ?? null)} />
              <span className="text-xs text-primary underline-offset-2 hover:underline">choose</span>
            </div>
          </label>

          <div className="grid gap-1.5 text-sm">
            <span className="font-medium">
              Photos <span className="font-normal text-muted-foreground">· optional</span>
            </span>
            <p className="text-xs text-muted-foreground">
              Add every photo you have of the house: each side, close-ups of details, the garden and the surroundings.
              The more the model sees, the more faithful it gets. Labelling the four sides helps the review compare like with like.
              No photo? The agent reads the plans first and asks you a few questions before building.
            </p>
            <div className="grid grid-cols-4 gap-2">
              {SIDES.map((side) => {
                const f = facades[side]
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
                      accept="image/*"
                      className="hidden"
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        setFacades((p) => ({ ...p, [side]: file ?? undefined }))
                      }}
                    />
                  </label>
                )
              })}
            </div>

            <label
              className="mt-1 flex cursor-pointer items-center gap-3 rounded-md border border-dashed border-input px-3 py-3 text-sm hover:bg-accent/30"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                addExtras(e.dataTransfer.files)
              }}
            >
              <Images className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                More photos <span className="text-muted-foreground">· details, other angles, garden, street. Drop or choose, any number.</span>
              </span>
              <input type="file" accept="image/*" multiple className="hidden" onChange={(e) => addExtras(e.target.files)} />
              <span className="text-xs text-primary underline-offset-2 hover:underline">choose</span>
            </label>
            {extras.length > 0 && (
              <div className="grid grid-cols-6 gap-1.5">
                {extras.map((f, i) => (
                  <div key={`${f.name}-${i}`} className="group relative aspect-square overflow-hidden rounded border">
                    <img src={extraUrls[i]} alt="" className="size-full object-cover" />
                    <button
                      type="button"
                      aria-label="Remove photo"
                      className="absolute right-0.5 top-0.5 hidden rounded-full bg-background/90 p-0.5 group-hover:block"
                      onClick={() => setExtras((prev) => prev.filter((_, k) => k !== i))}
                    >
                      <X className="size-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              {total === 0
                ? "No photo: the plans will be read first."
                : `${total} photo${total > 1 ? "s" : ""}: ${labelled.length} labelled façade${labelled.length === 1 ? "" : "s"}, ${extras.length} other.`}
            </p>
          </div>

          <label className="grid gap-1.5 text-sm">
            <span className="font-medium">
              Notes <span className="font-normal text-muted-foreground">· optional</span>
            </span>
            <Textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              maxLength={4000}
              placeholder="What the files cannot say: the photos are from 2015 and the east extension came later; the roof is now dark grey; the garage on the plan was never built…"
            />
          </label>

          {error && <p className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={!canSubmit}>
            {busy && <Loader2 className="animate-spin" />}
            {total === 0 ? "Upload and read the plans" : "Upload and generate"}
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}
