import type { ResolvedRunSettings, RunSettings } from "@/api/model"

// the two presets (mirrors agent/run_settings.PRESETS); "custom" keeps whatever is set
export const PRESETS: Record<"quick" | "full", Pick<RunSettings, "builder_effort" | "critic_rounds" | "max_steps">> = {
  quick: { builder_effort: "medium", critic_rounds: 1, max_steps: 30 },
  full: { builder_effort: "xhigh", critic_rounds: 2, max_steps: 60 },
}

export const PRESET_LABEL = { quick: "Quick draft", full: "Full quality", custom: "Custom" } as const

export function presetOf(s: RunSettings, effective: ResolvedRunSettings): keyof typeof PRESET_LABEL {
  const eff = s.builder_effort ?? effective.builder_effort
  const rounds = s.critic_rounds ?? effective.critic_rounds
  const steps = s.max_steps ?? effective.max_steps
  for (const [name, p] of Object.entries(PRESETS)) {
    if (p.builder_effort === eff && p.critic_rounds === rounds && p.max_steps === steps) return name as "quick" | "full"
  }
  return "custom"
}

/** The fields that differ from the server defaults: unset ones are left out. */
export function chosenSettings(s: RunSettings): RunSettings {
  const body: RunSettings = {}
  for (const [k, v] of Object.entries(s)) if (v !== null && v !== undefined && v !== "") (body as Record<string, unknown>)[k] = v
  return body
}
