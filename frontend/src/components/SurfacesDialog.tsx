import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import "@fontsource/archivo/400.css"
import "@fontsource/archivo/500.css"
import "@fontsource/archivo/600.css"
import "@fontsource/archivo/700.css"
import "@fontsource/ibm-plex-mono/400.css"
import "@fontsource/ibm-plex-mono/500.css"
import archivo400 from "@fontsource/archivo/files/archivo-latin-400-normal.woff2?url"
import archivo500 from "@fontsource/archivo/files/archivo-latin-500-normal.woff2?url"
import archivo600 from "@fontsource/archivo/files/archivo-latin-600-normal.woff2?url"
import archivo700 from "@fontsource/archivo/files/archivo-latin-700-normal.woff2?url"
import plex400 from "@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2?url"
import plex500 from "@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2?url"
import { Download, FileSpreadsheet, Loader2, Ruler, Settings2, X } from "lucide-react"
import { getGetReportQueryKey, putReport, useGetReport } from "@/api/endpoints/report/report"
import type { Prices, ReportSettings } from "@/api/model"
import { Button } from "@/components/ui/button"
import { ReportPages } from "@/components/report/ReportPages"
import { buildReport, chf, csv, n2, tables } from "@/components/report/compute"
import type { QuantitiesReply } from "@/components/report/types"
import reportCss from "@/components/report/report.css?raw"
import { cn, errorMessage } from "@/lib/utils"

/** The report's fonts, embedded so the PDF printed by the backend looks the same. */
let fontCss: Promise<string> | null = null
function embeddedFonts(): Promise<string> {
  fontCss ??= Promise.all(
    (
      [
        [archivo400, "Archivo", 400],
        [archivo500, "Archivo", 500],
        [archivo600, "Archivo", 600],
        [archivo700, "Archivo", 700],
        [plex400, "IBM Plex Mono", 400],
        [plex500, "IBM Plex Mono", 500],
      ] as const
    ).map(async ([url, family, weight]) => {
      const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer())
      let bin = ""
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
      return `@font-face{font-family:'${family}';font-weight:${weight};src:url(data:font/woff2;base64,${btoa(bin)}) format('woff2')}`
    }),
  ).then((faces) => faces.join(""))
  return fontCss
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const slug = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase()

/** A settings field: text or a number (empty = the default, shown as the placeholder). */
function SettingInput({ label, value, placeholder, onChange, number, suffix, className }: {
  label: string
  value: string | number | null | undefined
  placeholder?: string
  onChange: (v: string | number | null) => void
  number?: boolean
  suffix?: string
  className?: string
}) {
  const [text, setText] = useState(value == null ? "" : String(value))
  useEffect(() => setText(value == null ? "" : String(value)), [value])
  return (
    <label className={cn("grid gap-1 text-xs", className)}>
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1">
        <input
          className="h-7 w-full rounded-md border bg-background px-2 text-xs"
          type={number ? "number" : "text"}
          step={number ? "any" : undefined}
          inputMode={number ? "decimal" : undefined}
          value={text}
          placeholder={placeholder}
          onChange={(e) => {
            setText(e.target.value)
            if (!number) return onChange(e.target.value.trim() || null)
            const v = parseFloat(e.target.value)
            onChange(Number.isFinite(v) ? v : null)
          }}
        />
        {suffix && <span className="shrink-0 text-muted-foreground">{suffix}</span>}
      </span>
    </label>
  )
}

/**
 * "Surfaces et volumes": the SIA 416 report of the version on screen (#47, #48), page for page as the
 * mockup, from the figures the scene page measures on its own model (kit/quantities.js). The estimate's
 * prices and the report's settings are saved on the project; the report downloads as PDF (printed by
 * the backend) and as CSV, one file per table.
 */
export function SurfacesDialog({
  request,
  projectId,
  projectName,
  version,
  onClose,
}: {
  request: () => Promise<QuantitiesReply>
  projectId: string
  projectName: string
  version: number | null
  onClose: () => void
}) {
  const [reply, setReply] = useState<QuantitiesReply | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [panel, setPanel] = useState(false)
  const [busy, setBusy] = useState<"pdf" | null>(null)
  const [settings, setSettings] = useState<ReportSettings | null>(null)
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error" | null>(null)
  const report = useGetReport(projectId)
  const queryClient = useQueryClient()
  const sheets = useRef<HTMLDivElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let live = true
    request()
      .then((r) => {
        if (!live) return
        if (r.error) setError(r.error)
        else if (!r.quantities) setError("This version has no building to measure.")
        setReply(r)
      })
      .catch((e) => live && setError(errorMessage(e)))
    return () => {
      live = false
    }
  }, [request])

  useEffect(() => {
    if (report.data && settings === null) setSettings(report.data.settings)
  }, [report.data, settings])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  /** Change the settings and save them on the project a moment later. */
  const update = useCallback(
    (patch: Partial<ReportSettings>) => {
      setSettings((cur) => {
        const next = { ...(cur ?? {}), ...patch } as ReportSettings
        if (timer.current) clearTimeout(timer.current)
        setSaveState("saving")
        timer.current = setTimeout(() => {
          putReport(projectId, next)
            .then((out) => {
              setSaveState("saved")
              queryClient.setQueryData(getGetReportQueryKey(projectId), out)
            })
            .catch(() => setSaveState("error"))
        }, 700)
        return next
      })
    },
    [projectId, queryClient],
  )
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), [])

  const q = reply?.quantities ?? null
  const R = useMemo(
    () =>
      q && settings && report.data
        ? buildReport(q, settings, report.data.defaults, { project: projectName, version, date: new Date() })
        : null,
    [q, settings, report.data, projectName, version],
  )

  const base = `${slug(projectName) || "projet"}${version ? `-v${version}` : ""}-sia416`

  async function downloadPdf() {
    if (!sheets.current) return
    setBusy("pdf")
    setError(null)
    try {
      const copy = sheets.current.cloneNode(true) as HTMLElement
      // the fields as their values (what the owner typed, not an input box)
      for (const input of copy.querySelectorAll<HTMLInputElement>("input")) {
        const span = document.createElement("span")
        span.className = "printed"
        const v = input.dataset.value ? parseFloat(input.dataset.value) : NaN
        span.textContent = Number.isFinite(v) ? (Number.isInteger(v) ? chf(v) : n2(v)) : "—"
        input.replaceWith(span)
      }
      // one A4 page per sheet: each sheet laid out 860 px wide (the screen's), scaled to fit 210 × 297 mm
      const probe = document.createElement("div")
      probe.className = "sia-report"
      probe.style.cssText = "position:absolute;left:-20000px;top:0;width:860px;padding:0"
      probe.innerHTML = copy.innerHTML
      document.body.appendChild(probe)
      const fit = [...probe.querySelectorAll<HTMLElement>(".sheet")].map((el) => {
        el.style.minHeight = "0"
        el.style.maxWidth = "860px"
        return Math.min(794 / 860, 1118 / el.getBoundingClientRect().height)
      })
      probe.remove()
      copy.querySelectorAll<HTMLElement>(".sheet").forEach((el, i) => el.style.setProperty("--z", String(fit[i] ?? 794 / 860)))
      const html = `<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>${projectName} · SIA 416</title><style>${await embeddedFonts()}${reportCss}</style></head><body><div class="sia-report">${copy.innerHTML}</div></body></html>`
      const res = await fetch(`/api/v1/projects/${projectId}/report/pdf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ html, filename: `${base}.pdf` }),
      })
      if (!res.ok) throw new Error(`The PDF could not be made (${res.status})`)
      save(await res.blob(), `${base}.pdf`)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  function downloadCsv(name: string) {
    if (!R) return
    const t = tables(R).find((x) => x.name === name)
    if (t) save(new Blob([csv(t)], { type: "text/csv;charset=utf-8" }), `${base}-${t.name}.csv`)
  }

  const flatNames = R?.flats.map((F) => F.flat) ?? []
  const defaults = report.data?.defaults

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/35 p-3 sm:p-6" onClick={onClose} role="presentation">
      <div
        role="dialog"
        aria-label="Surfaces et volumes SIA 416"
        className="flex h-[min(94vh,1200px)] w-full max-w-[1000px] flex-col overflow-hidden rounded-xl border bg-card text-sm shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-4">
          <div className="flex items-center gap-2.5">
            <span className="flex size-8 items-center justify-center rounded-lg bg-secondary">
              <Ruler className="size-4" />
            </span>
            <div>
              <h2 className="font-semibold">Surfaces et volumes SIA 416</h2>
              <p className="text-xs text-muted-foreground">
                Measured on this version's model. Prices and settings are saved on the project
                {saveState === "saving" ? " · saving…" : saveState === "saved" ? " · saved" : saveState === "error" ? " · not saved" : ""}.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <Button size="sm" variant={panel ? "secondary" : "outline"} className="h-7 px-2.5" disabled={!R} onClick={() => setPanel((v) => !v)} title="Building names, slab, weights, terraces and gardens">
              <Settings2 className="size-3.5" />
              Réglages
            </Button>
            <Button size="sm" variant="outline" className="h-7 px-2.5" disabled={!R || busy !== null} onClick={() => void downloadPdf()} title="The whole report, printed to A4 by the server">
              {busy === "pdf" ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
              PDF
            </Button>
            <label className={cn("inline-flex h-7 items-center gap-1 rounded-md border bg-background px-2 text-xs", !R && "opacity-50")} title="One CSV file per table">
              <FileSpreadsheet className="size-3.5" />
              <select
                className="bg-transparent text-xs outline-none"
                value=""
                disabled={!R}
                onChange={(e) => {
                  if (e.target.value) downloadCsv(e.target.value)
                }}
              >
                <option value="">CSV…</option>
                {R && tables(R).map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.title}
                  </option>
                ))}
              </select>
            </label>
            <Button size="sm" variant="ghost" className="h-7 px-2" onClick={onClose} aria-label="Close">
              <X className="size-4" />
            </Button>
          </div>
        </div>

        {panel && R && settings && (
          <div className="grid max-h-[40vh] gap-3 overflow-auto border-y bg-muted/30 px-5 py-3 sm:grid-cols-3">
            <SettingInput label="Projet (description)" value={settings.description} placeholder={R.meta.description} onChange={(v) => update({ description: v as string | null })} />
            <SettingInput label="Parcelle" value={settings.parcel} placeholder={defaults?.parcel ?? "BF n° …, commune"} onChange={(v) => update({ parcel: v as string | null })} />
            <SettingInput label="Altitude du ±0.00" number suffix="m" value={settings.datum} placeholder={defaults?.datum != null ? String(defaults.datum) : "—"} onChange={(v) => update({ datum: v as number | null })} />
            {R.buildings.map((b, i) => (
              <SettingInput key={b.key} label={`Objet ${i + 1}`} value={settings.building_names?.[b.key]} placeholder={b.name}
                onChange={(v) => {
                  const next = { ...(settings.building_names ?? {}) }
                  if (v) next[b.key] = v as string
                  else delete next[b.key]
                  update({ building_names: next })
                }} />
            ))}
            {R.main?.slabAssumed && (
              <SettingInput label="Radier sous le niveau le plus bas (pas dans la maquette)" number suffix="m" value={settings.slab_thickness} placeholder="0.25" onChange={(v) => update({ slab_thickness: v as number | null })} />
            )}
            {R.exterior.plot === null && (
              <SettingInput label="Surface de la parcelle" number suffix="m²" value={settings.plot_area} placeholder="—" onChange={(v) => update({ plot_area: v as number | null })} />
            )}
            {(["balcony", "terrace", "garden"] as const).map((k) => (
              <SettingInput key={k} label={`Pondération : ${{ balcony: "balcons", terrace: "terrasses", garden: "jardins" }[k]}`} number suffix="%"
                value={settings.weights?.[k] != null ? Math.round((settings.weights[k] as number) * 1000) / 10 : null}
                placeholder={String({ balcony: 50, terrace: 33, garden: 10 }[k])}
                onChange={(v) => update({ weights: { balcony: 0.5, terrace: 0.33, garden: 0.1, ...(settings.weights ?? {}), [k]: v == null ? { balcony: 0.5, terrace: 0.33, garden: 0.1 }[k] : (v as number) / 100 } })} />
            ))}
            {R.terraces.map((t) => (
              <label key={t.index} className="grid gap-1 text-xs">
                <span className="text-muted-foreground">
                  {t.name} ({n2(t.area)} m²)
                </span>
                <select className="h-7 rounded-md border bg-background px-1.5 text-xs" value={settings.terraces?.[t.index] ?? ""}
                  onChange={(e) => {
                    const next = { ...(settings.terraces ?? {}) }
                    if (e.target.value) next[t.index] = e.target.value
                    else delete next[t.index]
                    update({ terraces: next })
                  }}>
                  <option value="">non attribuée</option>
                  {flatNames.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            {flatNames.map((f) => (
              <SettingInput key={f} label={`Jardin privatif, ${f}`} number suffix="m²" value={settings.gardens?.[f]} placeholder="—"
                onChange={(v) => {
                  const next = { ...(settings.gardens ?? {}) }
                  if (v == null) delete next[f]
                  else next[f] = v as number
                  update({ gardens: next })
                }} />
            ))}
          </div>
        )}

        <div className="relative min-h-0 flex-1 overflow-auto">
          <style>{reportCss}</style>
          {R && settings ? (
            <div className="sia-report" ref={sheets}>
              <ReportPages R={R} settings={settings} onPrices={(prices: Prices) => update({ prices })} />
            </div>
          ) : (
            <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
              {error ? (
                <span className="max-w-md text-center text-destructive">{error}</span>
              ) : (
                <>
                  <Loader2 className="size-4 animate-spin" /> Measuring the model…
                </>
              )}
            </div>
          )}
          {R && error && <p className="sticky bottom-2 text-center text-xs text-destructive">{error}</p>}
        </div>
      </div>
    </div>
  )
}
