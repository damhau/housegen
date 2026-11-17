import type { RestyleRequestStyle } from "@/api/model"

export type HouseStyle = RestyleRequestStyle

/** The house styles of kit/styles.js as the owner chooses them (#101); the first is the default. */
export const HOUSE_STYLES: { id: HouseStyle; label: string; blurb: string; swatches: string[] }[] = [
  {
    id: "scandinavian",
    label: "Scandinavian",
    blurb: "Light oak, white, grey and sand textiles; chrome taps, white tiles",
    swatches: ["#d8c19c", "#f4f2ee", "#b8b5ae", "#d9cbb1"],
  },
  {
    id: "contemporary",
    label: "Contemporary",
    blurb: "Dark accents on white walls; black fittings, grey marble, olive textiles",
    swatches: ["#262626", "#f2f1ee", "#9b9893", "#6b6d48"],
  },
]

export const styleLabel = (id: string | null | undefined) => HOUSE_STYLES.find((s) => s.id === id)?.label ?? id ?? ""

export function Swatches({ colors }: { colors: string[] }) {
  return (
    <span className="flex shrink-0 -space-x-1" aria-hidden>
      {colors.map((c) => (
        <span key={c} className="size-3.5 rounded-full border border-background ring-1 ring-border" style={{ background: c }} />
      ))}
    </span>
  )
}
