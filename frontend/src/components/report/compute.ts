/**
 * The surfaces and volumes report (SIA 416, #47, #48) from the scene's figures and the owner's settings:
 * totals, volumes with the slab, the finishes take-off, living areas, habitability, the estimate. Laid
 * out by ReportPages; the CSV tables come from here too.
 */
import type { ReportDefaults, ReportSettings } from "@/api/model"
import type { Pt, QBuilding, QFacade, QRoom, QStorey, Quantities } from "./types"

// ---- formatting (Swiss: apostrophe thousands, point decimals, a true minus)
export const n2 = (v: number) => {
  const [i = "0", d = "00"] = Math.abs(v).toFixed(2).split(".")
  return (v < 0 ? "−" : "") + i.replace(/\B(?=(\d{3})+(?!\d))/g, "'") + "." + d
}
export const pct = (a: number, b: number) => {
  const p = ((a - b) / b) * 100
  return (p > 0 ? "+" : p < 0 ? "−" : "") + Math.abs(p).toFixed(1) + " %"
}
export const chf = (v: number) => {
  const s = Math.round(Math.abs(v))
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, "'")
  return (v < 0 ? "−" : "") + s
}
export const sum = <T,>(xs: T[], f: (x: T) => number) => xs.reduce((a, x) => a + f(x), 0)
const lvl = (v: number) => (Math.abs(v) < 0.005 ? "±0.00" : (v > 0 ? "+" : "−") + Math.abs(v).toFixed(2))
const words = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} et ${xs.at(-1)}`)

export const CLASS = { SUP: "sup", SUS: "sus", SD: "sd", SI: "si" } as const

// ---- compass: the side a façade or a balcony faces, from the scene's turn on the map (the surroundings)
const SHORT = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
const LONG = ["nord", "nord-est", "est", "sud-est", "sud", "sud-ouest", "ouest", "nord-ouest"]
/** True azimuth (0 = north, clockwise) of a scene direction [x, z], the scene turned `north` degrees clockwise. */
export const azimuth = ([x, z]: Pt, north: number) => (((Math.atan2(x, -z) * 180) / Math.PI + north) % 360 + 360) % 360
const sector = (az: number) => Math.round(az / 45) % 8

export type Facade = QFacade & { name: string; side: string; az: number }

/** The material of a floor finish, for the take-off. */
const FINISH: Record<string, string> = {
  oak: "parquet chêne",
  "oak-light": "parquet chêne clair",
  "oak-smoked": "parquet chêne fumé",
  herringbone: "parquet chêne à bâtons rompus",
  tile: "carrelage grès cérame",
  "tile-dark": "carrelage grès cérame foncé",
  terrazzo: "terrazzo",
  marble: "marbre",
}

export type Line = { group?: string; eccc?: string; cfc?: string; what?: string; where?: string; formula?: string; unit?: string; qty?: number }

export type Flat = {
  flat: string
  living: (QRoom & { storey: string; counted: number })[]
  stairs: QRoom[]
  annex: (QRoom & { storeyN: number })[]
  balc: { name: string; area: number }[]
  terr: { name: string; area: number }[]
  garden: number
  hab: number
  total: number
}

export type EstimateLine = { key: string; cfc: string; what: string; qtyText: string; qty: number | null; unit: string; label: string; price: number | null; step?: string }

export type Building = QBuilding & { name: string; slabUsed: number; slabAssumed: boolean; vb: number; vbUnder: number; bandsV: number[]; formulas: string[] }

export type Report = ReturnType<typeof buildReport>

const DEFAULT_RATE = { main: 850, annex: 600 }
export const PRESETS = [
  { label: "Simple", rate: 675 },
  { label: "Moyen", rate: 850 },
  { label: "Élevé", rate: 1075 },
]

const dims = (d: [number, number] | null, area: number) => (d ? `${d[0].toFixed(2)} × ${d[1].toFixed(2)}` : `SB ${n2(area)}`)

export function buildReport(
  q: Quantities,
  s: ReportSettings,
  d: ReportDefaults,
  meta: { project: string; version: number | null; date: Date },
) {
  const north = d.north ?? 0
  const S = q.storeys
  const allRooms = S.flatMap((st) => st.rooms.map((r) => ({ ...r, storey: st.label, storeyIndex: st.index, storeyN: st.n })))
  const T = {
    sp: sum(S, (x) => x.sp),
    sn: sum(S, (x) => x.sn),
    sc: sum(S, (x) => x.sc),
    SUP: sum(S, (x) => x.SUP),
    SUS: sum(S, (x) => x.SUS),
    SD: sum(S, (x) => x.SD),
    SI: sum(S, (x) => x.SI),
  }

  // ---- flats: those the rooms' names give, else the whole house is one dwelling
  const flatNames = q.flats.length ? q.flats.slice().sort() : ["Logement"]
  const flatOf = (r: { flat: string | null }) => (q.flats.length ? r.flat : r.flat === "Commun" ? null : "Logement")

  // ---- buildings: names, the slab under the lowest storey, volumes per band
  const nameOf = (b: QBuilding, i: number) => {
    const set = s.building_names?.[b.key]
    if (set) return set
    if (b.main) return "Villa"
    if (b.rooms?.length && b.rooms.every((r) => /garage/i.test(r.name) || r.use === "garage")) return "Garage"
    return `Annexe ${i}`
  }
  const buildings: Building[] = q.buildings.map((b, i) => {
    const slabAssumed = b.slab == null
    const slabUsed = b.slab ?? s.slab_thickness ?? 0.25
    const bandsV = b.bands.map((band, k) => band.v + (k === 0 ? band.area * slabUsed : 0))
    const formulas = b.bands.map((band, k) => {
      const base = dims(band.dims, band.area)
      const radier = k === 0 ? ` + ${slabUsed.toFixed(2)} radier` : ""
      if (band.top) {
        const mean = band.area ? band.v / band.area : 0
        return k === 0 ? `${base} × (${mean.toFixed(2)}${radier}), hauteur moyenne mesurée` : `${base} × ${mean.toFixed(2)}, hauteur moyenne mesurée`
      }
      return k === 0 ? `${base} × (${(band.h ?? 0).toFixed(2)}${radier})` : `${base} × ${(band.h ?? 0).toFixed(2)}`
    })
    return {
      ...b,
      name: nameOf(b, i),
      slabUsed,
      slabAssumed,
      vb: sum(bandsV, (v) => v),
      vbUnder: sum(bandsV.filter((_, k) => b.bands[k]?.under), (v) => v),
      bandsV,
      formulas,
    }
  })
  const main = buildings.find((b) => b.main) ?? null
  const annexes = buildings.filter((b) => !b.main)
  const vb = main?.vb ?? 0
  const vbUnder = main?.vbUnder ?? 0

  // ---- façades named by the side they face, in order round the house from the north
  const facades: Facade[] = q.facades
    .map((f) => {
      const az = azimuth(f.n, north)
      return { ...f, az, name: SHORT[sector(az)] ?? "N", side: LONG[sector(az)] ?? "nord" }
    })
    .sort((a, b) => ((a.az + 67.5) % 360) - ((b.az + 67.5) % 360))

  // ---- rooms off the plan (the kit's plan check, #70): flagged over 5 %
  const withPlan = allRooms.filter((r) => r.plan)
  const gaps = new Map(q.planGaps.map((g) => [g.no, g]))
  const flagged = allRooms.filter((r) => gaps.has(r.no))
  const warning = flaggedText(flagged.map((r) => ({ ...r, cause: gaps.get(r.no)?.cause ?? "outline" })))

  // ---- the finishes take-off (eCCC-Bât, CFC column, a formula per line)
  const lines: Line[] = []
  const L = (eccc: string, cfc: string, what: string, where: string, formula: string, unit: string, qty: number) =>
    lines.push({ eccc, cfc, what, where, formula, unit, qty })
  const group = (title: string) => lines.push({ group: title })
  const thick = (r: [number, number] | null | undefined) =>
    !r ? "" : Math.round(r[0] * 100) === Math.round(r[1] * 100) ? `${Math.round(r[0] * 100)} cm` : `${Math.round(r[0] * 100)}–${Math.round(r[1] * 100)} cm`
  const massT = thick(q.partitionThickness?.mass)
  const lightT = thick(q.partitionThickness?.light)
  const masses = S.filter((st) => st.parts.some((p) => p.t >= 0.2))
  if (masses.length) group("C · Construction du bâtiment")
  for (const st of masses) {
    const c = st.parts.filter((p) => p.t >= 0.2)
    const g = sum(c, (p) => p.gross), h = sum(c, (p) => p.holes), doors = sum(c, (p) => p.n)
    L("C02", "211", `Parois porteuses intérieures, maçonnerie${massT ? ` ${massT}` : ""}`, st.label,
      `Σ ${n2(sum(c, (p) => p.len))} m × ${st.ceiling ? st.clear.toFixed(2) : "hauteur sous pente"}${doors ? ` − ${doors} ouv. ${n2(h)}` : ""}`, "m²", g - h)
  }
  const gw = q.groundWalls
  if (gw || facades.length) group("E · Revêtements de façades et de murs contre terre")
  if (gw) {
    L("E01", "225", "Étanchéité des murs contre terre", S.filter((x) => x.n < 0).map((x) => x.label).join(", ") || "Sous-sol",
      `${gw.perimeter.toFixed(2)} × ${gw.h.toFixed(2)}${gw.nHoles ? ` − ${gw.nHoles} ouv. ${n2(gw.holes)}` : ""}${gw.small ? ` · ${gw.small} soupira${gw.small > 1 ? "ux" : "il"} < 1 m² non déduit${gw.small > 1 ? "s" : ""}` : ""}`,
      "m²", gw.gross - gw.holes)
  }
  for (const f of facades) {
    const base = f.baseH !== null ? `${f.len.toFixed(2)} × ${f.baseH.toFixed(2)}` : `${f.len.toFixed(2)} m, faces mesurées`
    L("E02", "226", "Crépissage de façade", `Façade ${f.name}`,
      `${base}${f.top ? ` + ${f.topKind} ${n2(f.top)}` : ""}${f.nHoles ? ` − ${f.nHoles} ouv. ${n2(f.holes)}` : ""}${f.nKeep ? ` · ${f.nKeep} < 1 m² non déd.` : ""}`,
      "m²", f.net)
  }
  const ext = S.filter((x) => x.n >= 0).flatMap((x) => x.ext.map((o) => ({ ...o, st: x })))
  const glazed = ext.filter((o) => o.kind !== "porte" && o.kind !== "soupirail")
  const doorsExt = ext.filter((o) => o.kind === "porte")
  if (glazed.length) L("E03", "221", "Fenêtres et portes-fenêtres", "Toutes façades", `${glazed.length} pces · Σ l × h`, "m²", sum(glazed, (o) => o.w * o.h))
  if (doorsExt.length) {
    const levels = [...new Set(doorsExt.map((o) => (o.st.n === 0 ? "Rez" : o.st.label)))]
    L("E03", "221", "Portes extérieures", levels.join(", "), `${doorsExt.length} pces · Σ l × h`, "m²", sum(doorsExt, (o) => o.w * o.h))
  }
  if (S.length) group("G · Aménagements intérieurs")
  for (const st of S) {
    const g01 = st.parts.filter((p) => p.t < 0.2)
    const gross = sum(g01, (p) => p.gross), h = sum(g01, (p) => p.holes), doors = sum(g01, (p) => p.n)
    const len = n2(sum(g01, (p) => p.len))
    L("G01", "271", `Cloisons légères${lightT ? ` ${lightT}` : ""}`, st.label,
      st.ceiling ? `Σ ${len} m × ${st.clear.toFixed(2)} − ${doors} portes ${n2(h)}` : `Σ ${len} m, hauteur sous pente − ${doors} portes ${n2(h)}`, "m²", gross - h)
  }
  for (const st of S) {
    const byFloor = new Map<string, QRoom[]>()
    for (const r of st.rooms) byFloor.set(r.floor, [...(byFloor.get(r.floor) ?? []), r])
    for (const [floor, rs] of byFloor) {
      const nos = rs.map((r) => r.no).join(", ")
      if (floor === "concrete") L("G02", "—", "Sol béton brut, sans revêtement", st.label, `locaux ${nos}`, "m²", sum(rs, (r) => r.sn))
      else L("G02", "281", `Revêtement de sol : ${FINISH[floor] ?? floor}`, st.label, `Σ SN locaux ${nos}`, "m²", sum(rs, (r) => r.sn))
    }
  }
  for (const st of S) {
    for (const r of st.rooms.filter((x) => x.tiles > 0)) {
      const full = r.tileFull ?? 0
      L("G03", "282", "Faïence murale", `${r.no} ${r.name}`,
        `${r.tilePartial ? "murs carrelés" : "périmètre"} × ${(r.tileHeight ?? 1.2).toFixed(2)}${full ? ` (${(r.tileFullHeight ?? 2.4).toFixed(2)} sur ${full} mur${full > 1 ? "s" : ""})` : ""}${st.ceiling ? "" : ", sous pente"} − ouv. ≥ 1 m²`,
        "m²", r.tiles)
    }
  }
  for (const st of S) {
    const g = sum(st.rooms, (r) => r.wallsGross), h = sum(st.rooms, (r) => r.holes), t = sum(st.rooms, (r) => r.tiles)
    L("G03", "271 / 285", "Enduit plâtre et peinture des parois", st.label, `Σ parois ${n2(g)} − ouv. ${n2(h)}${t ? ` − faïence ${n2(t)}` : ""}`, "m²", g - h - t)
  }
  const smallKept = sum(S, (st) => sum(st.rooms, (r) => r.small)) + (gw?.small ?? 0)
  const smallTiles = S.flatMap((st) => st.rooms.filter((r) => r.tiles > 0 && r.tiles < 2))
  const tileHeights = [...new Set(S.flatMap((st) => st.rooms.filter((r) => r.tiles > 0).map((r) => `${(r.tileHeight ?? 1.2).toFixed(2)}|${(r.tileFullHeight ?? 2.4).toFixed(2)}`)))]

  // ---- balconies: named by the sides they run along
  const storeyLabel = (i: number) => S.find((x) => x.index === i)?.label ?? ""
  const balconies = q.balconies.map((b) => {
    const sides = facades.filter((f) => b.sides.includes(f.index)).map((f) => f.side)
    const rooms = b.rooms.map((no) => allRooms.find((r) => r.no === no)).filter(Boolean)
    const where = [storeyLabel(b.storey), rooms.length === 1 ? rooms[0]?.name : null].filter(Boolean).join(", ")
    return { ...b, name: `Balcon ${b.shape === "L" ? "en L, " : ""}${words(sides)} (${where})`.replace("Balcon  (", "Balcon ("), flat: q.flats.length ? b.flat : "Logement" }
  })

  // ---- living area by flat (sale); the weighting is practice, not a norm
  const w = { balcony: s.weights?.balcony ?? 0.5, terrace: s.weights?.terrace ?? 0.33, garden: s.weights?.garden ?? 0.1 }
  const terraces = q.terraces.map((t, i) => ({ ...t, index: String(i), name: `Terrasse ${i + 1}`, flat: s.terraces?.[String(i)] ?? null }))
  const flats: Flat[] = flatNames.map((flat) => {
    const rs = allRooms.filter((r) => flatOf(r) === flat)
    const living = rs.filter((r) => r.sia === "SUP" || r.use === "hall").map((r) => ({ ...r, counted: r.sn - (r.low ?? 0) }))
    const stairs = rs.filter((r) => r.use === "stair")
    const annex = rs.filter((r) => r.sia === "SUS")
    const balc = balconies.filter((b) => b.flat === flat).map((b) => ({ name: b.name, area: b.area }))
    const terr = terraces.filter((t) => t.flat === flat).map((t) => ({ name: t.name, area: t.area }))
    const garden = s.gardens?.[flat] ?? 0
    const hab = sum(living, (r) => r.counted)
    const total = hab + sum(balc, (b) => b.area * w.balcony) + sum(terr, (t) => t.area * w.terrace) + garden * w.garden
    return { flat, living, stairs, annex, balc, terr, garden, hab, total }
  })

  // ---- habitability (RLATC)
  const rlatc = q.rlatc.map((r) => {
    const opened = r.group.length > 1
    const together = opened && r.groupVol >= 20
    const ok = r.ok27 && r.ok28 && (r.ok25 || together)
    return { ...r, opened, together, ok, others: r.group.filter((x) => x !== r.no) }
  })

  // ---- the estimate (page 8): quantities from the model, prices from the settings
  const p = s.prices ?? {}
  const plot = q.exterior.plot ?? s.plot_area ?? null
  const ex = q.exterior
  const pools = ex.pools
  const poolDims = pools[0]?.dims
  const sameDims = poolDims && pools.every((x) => x.dims && x.dims.every((v, k) => v === poolDims[k]))
  const est: EstimateLine[] = []
  est.push({ key: "0", cfc: "0", what: "Parcelle", qty: plot, unit: "CHF/m²", label: "Prix du terrain au m²",
    qtyText: plot !== null ? `${n2(plot)} m², ${ex.plot !== null ? "tracée dans la maquette" : "saisie dans les réglages"}` : "surface à saisir dans les réglages", price: p.land_m2 ?? null })
  est.push({ key: "1", cfc: "1", what: `Fouille ${vbUnder > 0 ? "du sous-sol" : "des fondations"}`, qty: vbUnder, unit: "CHF/m³", label: "Prix de la fouille au m³",
    qtyText: `${n2(vbUnder)} m³, volume enterré sans surlargeur ni talus`, price: p.excavation_m3 ?? null })
  const est4: EstimateLine[] = []
  if (pools.length) {
    const desc = sameDims && poolDims ? ` ${poolDims[0].toFixed(2)} × ${poolDims[1].toFixed(2)}${poolDims[2] ? ` × ${poolDims[2].toFixed(2)}` : ""} m` : ""
    est4.push({ key: "4a", cfc: "4", what: `Piscine${pools.length > 1 ? "s" : ""}${desc}${pools.length > 1 && pools.length === flatNames.length ? ", une par appartement" : ""}`, qty: pools.length, unit: "CHF/pce", label: "Prix d'une piscine", qtyText: `${pools.length} pce${pools.length > 1 ? "s" : ""}`, price: p.pool_each ?? null })
  }
  if (ex.paved.area > 0) est4.push({ key: "4b", cfc: "4", what: "Dallages et terrasses", qty: ex.paved.area, unit: "CHF/m²", label: "Prix du dallage au m²", qtyText: `${n2(ex.paved.area)} m², ${ex.paved.n} surface${ex.paved.n > 1 ? "s" : ""}`, price: p.paving_m2 ?? null })
  if (ex.hedges.length > 0) est4.push({ key: "4c", cfc: "4", what: "Haies vives", qty: ex.hedges.length, unit: "CHF/m", label: "Prix de la haie au mètre", qtyText: `${n2(ex.hedges.length)} m${ex.hedges.double > 0.5 ? `, dont ${n2(ex.hedges.double)} m en double rang` : ""}${ex.hedges.fromBushes ? ", mesurées sur les rangées d'arbustes" : ""}`, price: p.hedge_m ?? null })
  if (ex.fences.length > 0) est4.push({ key: "4d", cfc: "4", what: "Clôtures", qty: ex.fences.length, unit: "CHF/m", label: "Prix de la clôture au mètre", qtyText: `${n2(ex.fences.length)} m`, price: p.fence_m ?? null })
  if (ex.trees > 0) est4.push({ key: "4e", cfc: "4", what: "Arbres", qty: ex.trees, unit: "CHF/pce", label: "Prix d'un arbre", qtyText: `${ex.trees} pce${ex.trees > 1 ? "s" : ""}`, price: p.tree_each ?? null })
  const rates = buildings.map((b) => p.building_m3?.[b.key] ?? (b.main ? DEFAULT_RATE.main : DEFAULT_RATE.annex))
  const amounts = (() => {
    const perBuilding = buildings.map((b, i) => (rates[i] == null ? null : b.vb * (rates[i] as number)))
    const cfc2 = perBuilding.every((a) => a === null) ? null : sum(perBuilding, (a) => a ?? 0)
    let cost = cfc2 ?? 0, open = cfc2 === null ? 1 : 0
    const line: Record<string, number | null> = {}
    for (const e of [...est, ...est4]) {
      const a = e.price === null || e.qty === null ? null : e.price * e.qty
      line[e.key] = a
      if (a === null) open++
      else cost += a
    }
    const a5 = p.secondary_pct == null || cfc2 === null ? null : (cfc2 * p.secondary_pct) / 100
    line["5"] = a5
    if (a5 === null) open++
    else cost += a5
    const values = flats.map((F) => {
      const pr = p.sale_m2?.[F.flat]
      return pr == null ? null : pr * F.total
    })
    const missing = values.filter((v) => v === null).length
    const value = sum(values, (v) => v ?? 0)
    return { perBuilding, cfc2, cost, open, line, values, missing, value }
  })()

  return {
    pages: S.length + 5,
    meta: {
      project: meta.project,
      version: meta.version,
      date: meta.date.toLocaleDateString("fr-CH", { day: "2-digit", month: "2-digit", year: "numeric" }),
      description: s.description ?? (flatNames.length === 2 ? "villa de deux appartements" : flatNames.length > 2 ? `immeuble de ${flatNames.length} logements` : "villa individuelle"),
      parcel: s.parcel ?? d.parcel ?? "—",
      datum: s.datum ?? d.datum ?? null,
      north,
    },
    S,
    T,
    allRooms,
    buildings,
    main,
    annexes,
    vb,
    vbUnder,
    facades,
    withPlan,
    flagged,
    warning,
    lines,
    smallKept,
    smallTiles,
    tileHeights,
    balconies,
    terraces,
    weights: w,
    flats,
    rlatc,
    est,
    est4,
    rates,
    amounts,
    attic: q.attic,
    exterior: ex,
  }
}

/** The warning box's sentences: the rooms off the plan grouped by their likely cause. */
function flaggedText(flagged: (QRoom & { storey: string; cause: string })[]) {
  const gap = (r: QRoom) => ((r.sn - (r.plan as number)) / (r.plan as number)) * 100
  const range = (rs: QRoom[]) => {
    const g = rs.map((r) => Math.abs(gap(r)))
    const a = Math.round(Math.min(...g)), b = Math.round(Math.max(...g))
    return a === b ? `${a} %` : `${a} à ${b} %`
  }
  const slope = flagged.filter((r) => r.cause === "slope")
  const small = flagged.filter((r) => r.cause === "small")
  const rest = flagged.filter((r) => r.cause === "outline")
  const out: string[] = []
  const nos = (rs: QRoom[]) => words(rs.map((r) => r.no))
  if (slope.length) out.push(`${slope.length > 1 ? `Les locaux ${nos(slope)} des combles mesurent` : `Le local ${nos(slope)} des combles mesure`} ${range(slope)} de plus dans la maquette : les plans comptent probablement sans la bande basse sous la pente (mur de pied, ou une règle de hauteur).`)
  for (const r of rest) out.push(`${r.no} ${r.name}${r.flat && r.flat !== "Commun" ? ` (${r.flat})` : ""} mesure ${Math.abs(gap(r)).toFixed(0)} % de ${gap(r) > 0 ? "plus" : "moins"} : son contour est à revoir sur le plan.`)
  if (small.length) out.push(`${small.length > 1 ? `Les petits locaux ${nos(small)} mesurent` : `Le petit local ${nos(small)} mesure`} ${range(small)} de ${small[0] && gap(small[0]) > 0 ? "plus" : "moins"} : quelques centimètres de cloison suffisent.`)
  if (out.length) out.push("À vérifier avant de reprendre ces chiffres.")
  return out.join(" ")
}

// ---- CSV: one file per table, ";" separated, decimal points, UTF-8 with a BOM (Excel opens it)
export type Table = { name: string; title: string; head: string[]; rows: (string | number | null | undefined)[][] }

export function tables(R: Report): Table[] {
  const r2 = (v: number | null | undefined) => (v == null ? null : Math.round(v * 100) / 100)
  const objs = [R.main, ...R.annexes].filter(Boolean) as Building[]
  const out: Table[] = []
  out.push({
    name: "recapitulatif", title: "Quantités SIA 416",
    head: ["Quantité", ...objs.map((b, i) => `Objet ${i + 1} · ${b.name}`)],
    rows: [
      ["SB surface de terrain bâtie", ...objs.map((b) => r2(b.sb))],
      ["SP surface de plancher", ...objs.map((b) => r2(b.main ? R.T.sp : b.sp))],
      ["SN surface nette", ...objs.map((b) => r2(b.main ? R.T.sn : b.sn))],
      ["SUP utile principale", ...objs.map((b) => r2(b.main ? R.T.SUP : b.classes?.SUP))],
      ["SUS utile secondaire", ...objs.map((b) => r2(b.main ? R.T.SUS : (b.classes?.SUS ?? b.sn)))],
      ["SD dégagement", ...objs.map((b) => r2(b.main ? R.T.SD : b.classes?.SD))],
      ["SI installations", ...objs.map((b) => r2(b.main ? R.T.SI : b.classes?.SI))],
      ["SC surface de construction", ...objs.map((b) => r2(b.main ? R.T.sc : b.sn == null ? null : b.sp - b.sn))],
      ["VB volume bâti", ...objs.map((b) => r2(b.vb))],
      ["VB hors-sol", ...objs.map((b) => r2(b.vb - b.vbUnder))],
      ["VB sous-sol", ...objs.map((b) => r2(b.vbUnder))],
    ],
  })
  out.push({
    name: "niveaux", title: "Par niveau",
    head: ["Niveau", "SP", "SN", "SC", "SUP", "SUS", "SD", "SI", "VB m³"],
    rows: R.S.map((s, i) => [s.label, r2(s.sp), r2(s.sn), r2(s.sc), r2(s.SUP), r2(s.SUS), r2(s.SD), r2(s.SI), r2(R.main?.bandsV[i])]),
  })
  out.push({
    name: "locaux", title: "Locaux",
    head: ["N°", "Local", "Logement", "Niveau", "SIA", "Calcul", "SN m²", "Plan m²", "Écart %"],
    rows: R.allRooms.map((r) => [r.no, r.name, r.flat ?? "", r.storey, r.sia, r.formula, r2(r.sn), r2(r.plan), r.plan ? r2(((r.sn - r.plan) / r.plan) * 100) : null]),
  })
  out.push({
    name: "volumes", title: "Volume bâti",
    head: ["Objet", "Niveau", "Calcul", "m³"],
    rows: objs.flatMap((b) => b.bands.map((band, k) => [b.name, band.label, b.formulas[k], r2(b.bandsV[k])])),
  })
  out.push({
    name: "metre", title: "Métré des finitions",
    head: ["eCCC", "CFC", "Désignation", "Niveau / local", "Calcul", "Unité", "Quantité"],
    rows: R.lines.filter((l) => !l.group).map((l) => [l.eccc ?? "", l.cfc ?? "", l.what ?? "", l.where ?? "", l.formula ?? "", l.unit ?? "", r2(l.qty)]),
  })
  out.push({
    name: "estimation", title: "Estimation",
    head: ["CFC", "Poste", "Quantité", "Prix unitaire", "Unité", "Montant CHF"],
    rows: [
      ...objs.map((b, i) => ["2", `${b.name} (VB ${n2(b.vb)} m³)`, r2(b.vb), R.rates[i], "CHF/m³", r2(R.amounts.perBuilding[i])]),
      ...[...R.est, ...R.est4].map((e) => [e.cfc, e.what, r2(e.qty), e.price, e.unit, r2(R.amounts.line[e.key])]),
      ["5", "Frais secondaires", null, null, "% du CFC 2", r2(R.amounts.line["5"])],
      ["", "Coût du projet", null, null, "", r2(R.amounts.cost)],
    ],
  })
  out.push({
    name: "surface-habitable", title: "Surface habitable",
    head: ["Logement", "N°", "Local", "Niveau", "m²"],
    rows: R.flats.flatMap((F) => [
      ...F.living.map((r) => [F.flat, r.no, r.name, r.storey, r2(r.counted)]),
      [F.flat, "", "Surface habitable nette", "", r2(F.hab)],
      ...F.balc.map((b) => [F.flat, "", `${b.name} × ${Math.round(R.weights.balcony * 100)} %`, "", r2(b.area * R.weights.balcony)]),
      ...F.terr.map((t) => [F.flat, "", `${t.name} × ${Math.round(R.weights.terrace * 100)} %`, "", r2(t.area * R.weights.terrace)]),
      ...(F.garden ? [[F.flat, "", `Jardin × ${Math.round(R.weights.garden * 100)} %`, "", r2(F.garden * R.weights.garden)]] : []),
      [F.flat, "", "Surface pondérée", "", r2(F.total)],
    ]),
  })
  out.push({
    name: "habitabilite", title: "Habitabilité RLATC",
    head: ["N°", "Local", "Logement", "Utilisable m²", "Hauteur", "Baies m²", "Rapport 1/x", "Volume m³", "Résultat"],
    rows: R.rlatc.map((r) => [r.no, r.name, r.flat ?? "", r2(r.use), r.attic ? `${Math.round(r.high * 100)} % ≥ 2.40 m` : r.clear.toFixed(2), r2(r.light), typeof r.ratio === "number" ? r2(r.ratio) : null, r2(r.vol), r.ok ? "conforme" : "à vérifier"]),
  })
  return out
}

export function csv(t: Table): string {
  const cell = (v: string | number | null | undefined) => {
    if (v == null) return ""
    const s = String(v)
    return /[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return "﻿" + [t.head, ...t.rows].map((row) => row.map(cell).join(";")).join("\r\n") + "\r\n"
}

/** A level line's label on the section: "+5.48", "±0.00 (667.60)". */
export const levelText = (v: number, datum: number | null) => (Math.abs(v) < 0.005 && datum !== null ? `±0.00 (${datum.toFixed(2)})` : lvl(v))

export type StoreyView = QStorey
