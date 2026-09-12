import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2, X } from "lucide-react"
import { useModels } from "@/api/endpoints/meta/meta"
import { getGetProjectQueryKey, getRunEstimateQueryKey, useUpdateSettings } from "@/api/endpoints/projects/projects"
import type { ProjectOut, RunSettings, RunSettingsBuilderEffort, RunSettingsProvider, RunSettingsRenderQuality } from "@/api/model"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn, errorMessage } from "@/lib/utils"

type Effort = NonNullable<RunSettingsBuilderEffort>
type Provider = NonNullable<RunSettingsProvider>
type Quality = NonNullable<RunSettingsRenderQuality>

const EFFORTS: Effort[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
const PROVIDER_EFFORTS: Record<Provider, Effort[]> = {
  openai: ["none", "minimal", "low", "medium", "high", "xhigh"],
  anthropic: ["low", "medium", "high", "xhigh", "max"],
}
// shown while the provider's own list loads, or when it cannot be asked (no key, offline)
const KNOWN_MODELS: Record<Provider, string[]> = {
  anthropic: ["claude-opus-5", "claude-sonnet-5"],
  openai: ["gpt-6-astra"],
}
const OTHER = "__other__"

function monthOf(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 7) : ""
}
// the two presets (mirrors agent/run_settings.PRESETS); "custom" keeps whatever is set
const PRESETS: Record<"quick" | "full", Pick<RunSettings, "builder_effort" | "critic_rounds" | "max_steps">> = {
  quick: { builder_effort: "medium", critic_rounds: 1, max_steps: 30 },
  full: { builder_effort: "xhigh", critic_rounds: 2, max_steps: 60 },
}

function presetOf(s: RunSettings, effective: ProjectOut["effective_settings"]): "quick" | "full" | "custom" {
  const eff = s.builder_effort ?? effective.builder_effort
  const rounds = s.critic_rounds ?? effective.critic_rounds
  const steps = s.max_steps ?? effective.max_steps
  for (const [name, p] of Object.entries(PRESETS)) {
    if (p.builder_effort === eff && p.critic_rounds === rounds && p.max_steps === steps) return name as "quick" | "full"
  }
  return "custom"
}

/**
 * Per-project run settings (#18): provider and model, effort per role, critic rounds, step
 * budget, in-loop render quality. Unset = the server's .env default (shown as placeholder).
 * Saved on the project; the next job takes a snapshot, a running one is not affected.
 */
export function SettingsSheet({ project, onClose }: { project: ProjectOut; onClose: () => void }) {
  const qc = useQueryClient()
  const update = useUpdateSettings()
  const [s, setS] = useState<RunSettings>({ ...project.settings })
  const [error, setError] = useState<string | null>(null)
  const eff = project.effective_settings
  const provider: Provider = s.provider ?? eff.provider
  const preset = presetOf(s, eff)
  // the provider's models, newest first (cached server-side; a failure leaves the list empty)
  const modelsQuery = useModels({ provider }, { query: { staleTime: 10 * 60 * 1000 } })
  const models = modelsQuery.data?.models ?? []
  const modelIds = models.length > 0 ? models.map((m) => m.id) : KNOWN_MODELS[provider]
  // "other…" switches to a free-text field; a stored model the list does not have stays selectable
  const [otherModel, setOtherModel] = useState(false)
  const modelListed = !s.model || modelIds.includes(s.model)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const set = <K extends keyof RunSettings>(k: K, v: RunSettings[K]) => setS((prev) => ({ ...prev, [k]: v }))
  const mapped = (e: Effort | null | undefined): string | null => {
    if (!e || PROVIDER_EFFORTS[provider].includes(e)) return null
    const allowed = PROVIDER_EFFORTS[provider]
    const i = EFFORTS.indexOf(e)
    const below = allowed.filter((x) => EFFORTS.indexOf(x) < i)
    const nearest = below.length > 0 ? below[below.length - 1] : allowed[0]
    return `${e} is not available on ${provider}: ${nearest} will be used`
  }

  async function save() {
    setError(null)
    try {
      const body: RunSettings = {}
      for (const [k, v] of Object.entries(s)) if (v !== null && v !== undefined && v !== "") (body as Record<string, unknown>)[k] = v
      await update.mutateAsync({ projectId: project.id, data: body })
      await qc.invalidateQueries({ queryKey: getGetProjectQueryKey(project.id) })
      await qc.invalidateQueries({ queryKey: getRunEstimateQueryKey(project.id) })
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const field = "grid gap-1 text-xs"
  const select = "h-8 rounded-md border bg-background px-2 text-xs"

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose} role="presentation">
      <aside
        role="dialog"
        aria-label="Run settings"
        className="flex h-full w-full max-w-sm flex-col gap-4 overflow-y-auto border-l bg-card p-4 text-sm shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Run settings</h2>
          <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close">
            <X />
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          For this project only; the next run takes a snapshot, a running one is not affected. Empty = the server default.
        </p>

        <div className="grid grid-cols-3 gap-1.5">
          {(["quick", "full", "custom"] as const).map((name) => (
            <button
              key={name}
              type="button"
              className={cn("rounded-md border px-2 py-1.5 text-xs hover:bg-accent", preset === name && "border-primary bg-primary/10 text-primary")}
              onClick={() => {
                if (name !== "custom") setS((prev) => ({ ...prev, ...PRESETS[name] }))
              }}
              aria-pressed={preset === name}
            >
              <div className="font-medium">{name === "quick" ? "Quick draft" : name === "full" ? "Full quality" : "Custom"}</div>
              <div className="text-[10px] text-muted-foreground">
                {name === "quick" ? "medium · 1 round · 30 steps" : name === "full" ? "xhigh · 2 rounds · 60 steps" : "whatever is set"}
              </div>
            </button>
          ))}
        </div>

        <label className={field}>
          <span className="font-medium">Provider</span>
          <select className={select} value={s.provider ?? ""} onChange={(e) => set("provider", (e.target.value || null) as RunSettingsProvider)}>
            <option value="">default ({eff.provider})</option>
            <option value="anthropic">anthropic</option>
            <option value="openai">openai</option>
          </select>
        </label>
        <label className={field}>
          <span className="font-medium">Model</span>
          <select
            className={select}
            value={otherModel ? OTHER : (s.model ?? "")}
            onChange={(e) => {
              if (e.target.value === OTHER) {
                setOtherModel(true)
                set("model", null)
              } else {
                setOtherModel(false)
                set("model", e.target.value || null)
              }
            }}
          >
            <option value="">default ({eff.model})</option>
            {models.length > 0
              ? models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id}
                    {m.created_at ? ` · ${monthOf(m.created_at)}` : ""}
                  </option>
                ))
              : KNOWN_MODELS[provider].map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
            {s.model && !modelListed && !otherModel && <option value={s.model}>{s.model}</option>}
            <option value={OTHER}>other…</option>
          </select>
          {otherModel && (
            <Input
              autoFocus
              value={s.model ?? ""}
              placeholder="model id"
              onChange={(e) => set("model", e.target.value || null)}
              className="h-8 text-xs"
            />
          )}
          <span className="text-muted-foreground">
            {modelsQuery.isPending
              ? `Asking ${provider} for its models…`
              : modelsQuery.data?.error
                ? `Could not list ${provider}'s models: ${modelsQuery.data.error}`
                : models.length > 0
                  ? `${models.length} models from ${provider}, newest first.`
                  : `No model list from ${provider}.`}
          </span>
        </label>
        {(["builder_effort", "critic_effort"] as const).map((k) => (
          <label key={k} className={field}>
            <span className="font-medium">{k === "builder_effort" ? "Builder effort" : "Critic effort"}</span>
            <select className={select} value={s[k] ?? ""} onChange={(e) => set(k, (e.target.value || null) as RunSettingsBuilderEffort)}>
              <option value="">default ({k === "builder_effort" ? eff.builder_effort : eff.critic_effort})</option>
              {EFFORTS.map((e) => (
                <option key={e} value={e}>
                  {e}
                  {PROVIDER_EFFORTS[provider].includes(e) ? "" : " (mapped)"}
                </option>
              ))}
            </select>
            {mapped(s[k]) && <span className="text-amber-700 dark:text-amber-300">{mapped(s[k])}</span>}
          </label>
        ))}
        <div className="grid grid-cols-2 gap-2">
          <label className={field}>
            <span className="font-medium">Critic rounds</span>
            <Input
              type="number"
              min={0}
              max={5}
              value={s.critic_rounds ?? ""}
              placeholder={String(eff.critic_rounds)}
              onChange={(e) => set("critic_rounds", e.target.value === "" ? null : Number(e.target.value))}
              className="h-8 text-xs"
            />
          </label>
          <label className={field}>
            <span className="font-medium">Step budget</span>
            <Input
              type="number"
              min={5}
              max={200}
              value={s.max_steps ?? ""}
              placeholder={String(eff.max_steps)}
              onChange={(e) => set("max_steps", e.target.value === "" ? null : Number(e.target.value))}
              className="h-8 text-xs"
            />
          </label>
        </div>
        <label className={field}>
          <span className="font-medium">In-loop render quality</span>
          <select className={select} value={s.render_quality ?? ""} onChange={(e) => set("render_quality", (e.target.value || null) as RunSettingsRenderQuality)}>
            <option value="">default ({eff.render_quality})</option>
            {(["low", "medium", "high"] as Quality[]).map((q) => (
              <option key={q} value={q}>
                {q}
              </option>
            ))}
          </select>
          <span className="text-muted-foreground">The builder's own renders; every saved version is rendered at high.</span>
        </label>
        {(eff.notes ?? []).length > 0 && <div className="text-xs text-amber-700 dark:text-amber-300">{(eff.notes ?? []).join(" · ")}</div>}
        {error && <div className="text-xs text-destructive">{error}</div>}
        <div className="mt-auto flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={update.isPending}>
            {update.isPending && <Loader2 className="animate-spin" />} Save
          </Button>
        </div>
      </aside>
    </div>
  )
}
