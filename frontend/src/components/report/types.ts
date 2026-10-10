/**
 * The figures the scene page computes for the surfaces and volumes report (kit/quantities.js, answered
 * to `house:quantities`). Metres and square / cubic metres; points are [x, z] (+x east, +z south).
 */
export type Pt = [number, number]

export type QOpening = {
  kind: "porte" | "passage" | "fenêtre" | "porte-fenêtre" | "soupirail"
  w: number
  h: number
  sill: number
  at: Pt
  /** the façade index (exterior openings only) */
  edge?: number
  storey?: number | null
}

export type QRoom = {
  no: string
  name: string
  full: string
  flat: string | null
  use: string
  sia: "SUP" | "SUS" | "SD" | "SI"
  sn: number
  plan: number | null
  floor: string
  formula: string
  polygon: Pt[]
  label: { at: Pt; room: number }
  openings: QOpening[]
  wallsGross: number
  tiles: number
  tileFull?: number
  tileHeight?: number
  tileFullHeight?: number
  tilePartial?: boolean
  holes: number
  small: number
  paint: number
  sloped: boolean
  /** area under 1.30 m (storeys under the roof) */
  low: number
}

export type QPartition = {
  len: number
  t: number
  gross: number
  holes: number
  n: number
  from: Pt
  to: Pt
  openings: { offset: number; width: number; door: boolean }[]
}

export type QStorey = {
  key: string
  index: number
  n: number
  label: string
  prefix: string
  y: number
  clear: number
  ceiling: boolean
  clearRange: [number, number] | null
  sp: number
  sn: number
  sc: number
  SUP: number
  SUS: number
  SD: number
  SI: number
  outline: Pt[] | null
  rooms: QRoom[]
  parts: QPartition[]
  ext: QOpening[]
  open: [string, string][]
  roofWindows: { at: Pt; area: number }[]
  walls: { from: Pt; to: Pt; thickness: number }[]
}

export type QBand = {
  label: string
  y0: number
  y1: number | null
  h: number | null
  area: number
  dims: [number, number] | null
  /** from the band's floor up (the slab under the lowest storey is added by the report) */
  v: number
  under: boolean
  top: boolean
  meanTop: number | null
}

export type QBuilding = {
  key: string
  main: boolean
  footprint: Pt[]
  sb: number
  dims: [number, number] | null
  sp: number
  sn: number | null
  classes: Record<"SUP" | "SUS" | "SD" | "SI", number> | null
  rooms: { name: string; use: string; sia: string; sn: number }[] | null
  /** the slab under the lowest storey from the model (m), null when it has none */
  slab: number | null
  storeys: { y: number; sp: number }[]
  bands: QBand[]
  gap: number | null
  section: { axis: "u" | "v"; span: [number, number]; profile: [number, number | null][]; levels: number[]; top: number | null }
}

export type QFacade = {
  index: number
  a: Pt
  b: Pt
  /** outward normal */
  n: Pt
  len: number
  baseH: number | null
  base: number
  top: number
  topKind: "pignon" | "mur sous pente"
  gross: number
  holes: number
  nHoles: number
  nKeep: number
  net: number
}

export type QRlatc = {
  no: string
  name: string
  flat: string | null
  attic: boolean
  clear: number
  use: number
  high: number
  aWin: number
  aSky: number
  light: number
  need: number
  ratio: number | "Infinity"
  vol: number
  ok25: boolean
  ok27: boolean
  ok28: boolean
  open: string[]
  group: string[]
  groupVol: number
}

export type Quantities = {
  version: 1
  ok: boolean
  messages: string[]
  groundY: number
  buildings: QBuilding[]
  storeys: QStorey[]
  facades: QFacade[]
  groundWalls: { perimeter: number; h: number; gross: number; holes: number; nHoles: number; small: number } | null
  balconies: { polygon: Pt[]; area: number; storey: number; flat: string | null; rooms: string[]; sides: number[]; shape: "L" | null }[]
  terraces: { polygon: Pt[]; area: number }[]
  exterior: {
    plot: number | null
    paved: { area: number; n: number }
    lawn: { area: number; n: number }
    hedges: { length: number; double: number; n: number; fromBushes: boolean }
    fences: { length: number; n: number }
    trees: number
    pools: { dims: [number, number, number | null] | null }[]
  }
  rlatc: QRlatc[]
  attic: { storey: number; minClear: number; maxClear: number } | null
  flats: string[]
  /** the rooms more than 5 % off the area printed on the plan (the kit's planGaps, also the builder's audit) */
  planGaps: { no: string; gap: number; share: number; cause: "slope" | "outline" | "small" }[]
  partitionThickness: { light: [number, number] | null; mass: [number, number] | null } | null
}

/** The scene page's answer to `house:quantities`. */
export type QuantitiesReply = { quantities: Quantities | null; error: string | null }
