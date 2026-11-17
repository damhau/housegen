import { useState } from "react"
import { Check, Loader2, Palette, Sofa } from "lucide-react"
import { useRestyleVersion, useVersionStyle } from "@/api/endpoints/projects/projects"
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/components/ui/menu"
import { HOUSE_STYLES, Swatches, styleLabel, type HouseStyle } from "@/lib/styles"
import { errorMessage } from "@/lib/utils"

/** What a Furnish run is asked when a house cannot be restyled: the same house, finishes left to the style. */
export const FURNISH_AGAIN_MESSAGE =
  "Keep the rooms, partitions, doors and furniture where they are; leave the finishes and colours to the house style."

/**
 * The house style of the version on screen (#101, #75): another style is saved as a new version
 * with no run (the same rooms and pieces, the style's finishes, fittings, textiles and decor), and
 * Restore brings the old one back. A house furnished before styles, or whose pieces name their own
 * finishes, says so and offers a Furnish run instead.
 */
export function StylePicker({
  projectId,
  version,
  busy,
  onRestyled,
  onFurnishAgain,
}: {
  projectId: string
  version: number
  /** a job is running: the scene cannot change now */
  busy: boolean
  onRestyled: () => void
  onFurnishAgain: (style: HouseStyle | null) => void
}) {
  const state = useVersionStyle(projectId, version, { query: { enabled: version > 0, staleTime: Infinity } })
  const restyle = useRestyleVersion()
  const [error, setError] = useState<string | null>(null)
  const s = state.data
  if (!s?.interior) return null
  const current = (s.style ?? null) as HouseStyle | null

  async function pick(style: HouseStyle, close: () => void) {
    setError(null)
    try {
      await restyle.mutateAsync({ projectId, number: version, data: { style } })
      close()
      onRestyled()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Menu
      side="top"
      align="start"
      label="House style"
      className="h-7 whitespace-nowrap px-2 text-xs font-medium"
      button={
        <>
          <Palette className="size-3.5" />
          {current ? styleLabel(current) : "Style"}
        </>
      }
    >
      {(close) => (
        <div className="w-72 max-w-[calc(100vw-2rem)]">
          <MenuLabel>House style · v{version}</MenuLabel>
          {s.restylable ? (
            <>
              {HOUSE_STYLES.map((x) => (
                <MenuItem
                  key={x.id}
                  disabled={busy || restyle.isPending || x.id === current}
                  onClick={() => void pick(x.id, close)}
                  className="items-start py-2 disabled:opacity-100"
                >
                  <span className="mt-0.5 flex size-4 items-center justify-center">
                    {x.id === current ? <Check /> : restyle.isPending && restyle.variables?.data.style === x.id ? <Loader2 className="animate-spin" /> : null}
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-medium">{x.label}</span>
                      <Swatches colors={x.swatches} />
                    </span>
                    <span className="text-xs leading-snug text-muted-foreground">{x.blurb}</span>
                  </span>
                </MenuItem>
              ))}
              <MenuSeparator />
              <p className="px-2 py-1.5 text-xs leading-snug text-muted-foreground">
                {busy
                  ? "A job is running: restyle when it is done."
                  : "Saved as a new version with the same rooms and pieces, no run. Restore brings this one back."}
              </p>
            </>
          ) : (
            <>
              <p className="px-2 py-1.5 text-xs leading-snug text-muted-foreground">{s.reason}</p>
              {s.literals.length > 0 && <MenuLabel className="pb-1">Pieces that name their own finish</MenuLabel>}
              {s.literals.length > 0 && (
                <ul className="mx-2 mb-1.5 space-y-0.5 rounded-md bg-muted/60 px-2 py-1.5 font-mono text-[11px] text-muted-foreground">
                  {s.literals.slice(0, 4).map((l) => (
                    <li key={`${l.file}:${l.line}:${l.text}`} className="truncate" title={`${l.file}:${l.line} · ${l.text}`}>
                      {l.file.replace(/^src\//, "")}:{l.line} {l.text}
                    </li>
                  ))}
                  {s.literal_count > 4 && <li>and {s.literal_count - 4} more</li>}
                </ul>
              )}
              <MenuSeparator />
              <MenuItem
                disabled={busy}
                onClick={() => {
                  close()
                  onFurnishAgain(current)
                }}
              >
                <Sofa /> Furnish again…
              </MenuItem>
            </>
          )}
          {error && <p className="px-2 py-1.5 text-xs text-destructive">{error}</p>}
        </div>
      )}
    </Menu>
  )
}
