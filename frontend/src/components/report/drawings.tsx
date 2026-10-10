/**
 * The report's drawings, from the scene's geometry: a storey's plan coloured by SIA 416 class (the same
 * scale on every storey) and the section across the roof used for the volume.
 */
import type { Building, Report } from "./compute"
import { CLASS, levelText, n2 } from "./compute"
import type { Pt, QStorey } from "./types"

const f = (v: number) => +v.toFixed(3)
const pts = (p: Pt[]) => p.map(([x, z]) => `${f(x)},${f(z)}`).join(" ")

function inside(p: Pt[], x: number, z: number) {
  let c = false
  for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
    const [xi, zi] = p[i]!, [xj, zj] = p[j]!
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) c = !c
  }
  return c
}
function segDist(x: number, z: number, [ax, az]: Pt, [bx, bz]: Pt) {
  const dx = bx - ax, dz = bz - az, l2 = dx * dx + dz * dz
  const t = l2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / l2)) : 0
  return Math.hypot(x - ax - t * dx, z - az - t * dz)
}
/** A point well inside a polygon: the 10 cm grid point farthest from its edges. */
function labelAt(p: Pt[]): Pt {
  const xs = p.map((q) => q[0]), zs = p.map((q) => q[1])
  let best: Pt = p[0] ?? [0, 0], bd = -1
  for (let x = Math.min(...xs); x <= Math.max(...xs); x += 0.1) {
    for (let z = Math.min(...zs); z <= Math.max(...zs); z += 0.1) {
      if (!inside(p, x, z)) continue
      let d = Infinity
      for (let i = 0; i < p.length; i++) d = Math.min(d, segDist(x, z, p[i]!, p[(i + 1) % p.length]!))
      if (d > bd) [bd, best] = [d, [x, z]]
    }
  }
  return best
}
/** The quad of a wall from s0 to s1 along from → to, `t` thick on its inner side (left of from → to). */
function band(from: Pt, to: Pt, t: number, s0: number, s1: number, a0 = 0, a1 = t): Pt[] {
  const L = Math.hypot(to[0] - from[0], to[1] - from[1]) || 1
  const ux = (to[0] - from[0]) / L, uz = (to[1] - from[1]) / L, nx = uz, nz = -ux // inward: the exterior is on the right
  const at = (s: number, a: number): Pt => [from[0] + ux * s + nx * a, from[1] + uz * s + nz * a]
  return [at(s0, a0), at(s1, a0), at(s1, a1), at(s0, a1)]
}

/** The box every storey's plan is drawn in (metres): walls, rooms and balconies of all storeys, with margins. */
export function planBox(R: Report): [number, number, number, number] {
  const all: Pt[] = R.S.flatMap((s) => [...s.rooms.flatMap((r) => r.polygon), ...s.walls.flatMap((w) => [w.from, w.to])])
  for (const b of R.balconies) all.push(...b.polygon)
  const xs = all.map((p) => p[0]), zs = all.map((p) => p[1])
  const x0 = Math.min(...xs) - 0.7, x1 = Math.max(...xs) + 0.8, z0 = Math.min(...zs) - 1.4, z1 = Math.max(...zs) + 1.1
  return [x0, z0, x1 - x0, z1 - z0]
}

export function PlanSVG({ R, s, box }: { R: Report; s: QStorey; box: [number, number, number, number] }) {
  const id = `hatch-${s.key}`
  const balconies = R.balconies.filter((b) => b.storey === s.index)
  const [bx, bz, bw, bh] = box
  // the openings in the exterior walls, on the wall they sit in
  const gaps = s.ext.map((o) => {
    let best = s.walls[0], bd = Infinity
    for (const w of s.walls) {
      const d = segDist(o.at[0], o.at[1], w.from, w.to)
      if (d < bd) [bd, best] = [d, w]
    }
    if (!best || bd > 0.2) return null
    const L = Math.hypot(best.to[0] - best.from[0], best.to[1] - best.from[1])
    const along = ((o.at[0] - best.from[0]) * (best.to[0] - best.from[0]) + (o.at[1] - best.from[1]) * (best.to[1] - best.from[1])) / L
    return { o, w: best, s0: Math.max(0, along - o.w / 2), s1: Math.min(L, along + o.w / 2) }
  }).filter(Boolean) as { o: QStorey["ext"][number]; w: QStorey["walls"][number]; s0: number; s1: number }[]
  return (
    <svg className="plan" viewBox={`${f(bx)} ${f(bz)} ${f(bw)} ${f(bh)}`} role="img" aria-label={`Plan schématique, ${s.label} : locaux colorés par affectation SIA 416`}>
      <defs>
        <pattern id={id} patternUnits="userSpaceOnUse" width="0.22" height="0.22" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="0.22" stroke="#8b938f" strokeWidth="0.025" />
        </pattern>
      </defs>
      {balconies.map((b, i) => (
        <polygon key={`b${i}`} className="pl-balc" points={pts(b.polygon)} />
      ))}
      {balconies.map((b, i) => {
        const [x, z] = labelAt(b.polygon)
        return (
          <text key={`bt${i}`} className="pl-note" x={f(x)} y={f(z + 0.1)} textAnchor="middle">
            balcon (hors SP)
          </text>
        )
      })}
      {s.rooms.map((r) => (
        <polygon key={r.no} className={`pl-room pl-${CLASS[r.sia]}`} points={pts(r.polygon)} />
      ))}
      {s.rooms
        .filter((r) => r.use === "stair")
        .map((r) => (
          <polygon key={`st${r.no}`} points={pts(r.polygon)} fill={`url(#${id})`} />
        ))}
      {s.walls.map((w, i) => (
        <polygon key={`w${i}`} className="pl-wall" points={pts(band(w.from, w.to, w.thickness, 0, Math.hypot(w.to[0] - w.from[0], w.to[1] - w.from[1])))} />
      ))}
      {gaps.map((g, i) => {
        const glass = band(g.w.from, g.w.to, g.w.thickness, g.s0, g.s1, g.w.thickness / 2, g.w.thickness / 2)
        return (
          <g key={`g${i}`}>
            <polygon className="pl-gap" points={pts(band(g.w.from, g.w.to, g.w.thickness, g.s0, g.s1))} />
            {g.o.kind !== "porte" && <line className="pl-glass" x1={f(glass[0]![0])} y1={f(glass[0]![1])} x2={f(glass[1]![0])} y2={f(glass[1]![1])} />}
          </g>
        )
      })}
      {s.parts.flatMap((p, i) => {
        const L = Math.hypot(p.to[0] - p.from[0], p.to[1] - p.from[1]) || 1
        const ux = (p.to[0] - p.from[0]) / L, uz = (p.to[1] - p.from[1]) / L
        const pieces: [number, number][] = []
        let t = 0
        for (const o of [...p.openings].sort((a, b) => a.offset - b.offset)) {
          if (o.offset > t) pieces.push([t, o.offset])
          t = o.offset + o.width
        }
        if (t < L) pieces.push([t, L])
        return pieces.map(([a, b], k) => (
          <line key={`p${i}-${k}`} className={p.t >= 0.2 ? "pl-mass" : "pl-part"} strokeWidth={p.t}
            x1={f(p.from[0] + ux * a)} y1={f(p.from[1] + uz * a)} x2={f(p.from[0] + ux * b)} y2={f(p.from[1] + uz * b)} />
        ))
      })}
      {s.rooms.map((r) => {
        const [x, z] = r.label.at, roomy = r.label.room > 0.62
        return (
          <g key={`l${r.no}`}>
            <text className="pl-no" x={f(x)} y={f(z + (roomy ? -0.04 : 0.09))}>{r.no}</text>
            {roomy && <text className="pl-area" x={f(x)} y={f(z + 0.3)}>{n2(r.sn)}</text>}
          </g>
        )
      })}
      {/* north (from the scene's turn on the map, when the surroundings are aligned) and a 5 m bar */}
      <g transform={`translate(${f(bx + bw - 1.3)} ${f(bz + 0.85)})`}>
        <circle className="pl-ring" r="0.55" />
        <g transform={`rotate(${f(-R.meta.north)})`}>
          <path className="pl-north" d="M0 -0.5 L0.17 0.18 L0 0.06 L-0.17 0.18 Z" />
        </g>
        <text className="pl-n" x="0.62" y="-0.5">N</text>
      </g>
      <g transform={`translate(${f(bx + 0.5)} ${f(bz + bh - 0.7)})`}>
        <rect className="pl-bar" x="0" y="0" width="2.5" height="0.12" />
        <rect className="pl-bar2" x="2.5" y="0" width="2.5" height="0.12" />
        <text className="pl-scale" x="0" y="0.48">0</text>
        <text className="pl-scale" x="5" y="0.48" textAnchor="end">5 m</text>
      </g>
    </svg>
  )
}

/** The section across the roof of building B: the storey bands, the roof's measured profile, the levels. */
export function SectionSVG({ R, B }: { R: Report; B: Building }) {
  const sec = B.section
  const [a, b] = sec.span
  const y = (v: number) => f(-v)
  const ground = R.S.length ? R.S.find((s) => s.n === 0)?.y ?? 0 : 0
  const bands = B.bands
  const bottom = bands[0] ? bands[0].y0 - B.slabUsed : ground
  const topBand = bands.at(-1)
  const ridge = sec.top ?? (topBand ? topBand.y0 + 3 : ground + 3)
  const depth = Math.max(3.6, ground - bottom + 0.55)
  // the roof's profile over the building, and past it (the overhangs, dashed)
  const prof = sec.profile.filter(([, h]) => h !== null) as [number, number][]
  const over = (side: "a" | "b") =>
    prof.filter(([s, h]) => (side === "a" ? s <= a : s >= b) && topBand && h > topBand.y0 + 0.3)
  const inner = prof.filter(([s]) => s > a && s < b)
  const hAt = (s: number) => {
    let best = prof[0], bd = Infinity
    for (const p of prof) if (Math.abs(p[0] - s) < bd) [bd, best] = [Math.abs(p[0] - s), p]
    return best ? best[1] : ridge
  }
  const levels: [number, string][] = [[ridge, `${levelText(ridge, null)} faîte`]]
  for (const st of [...bands].reverse()) levels.push([st.y0, levelText(st.y0 - ground, Math.abs(st.y0 - ground) < 0.005 ? R.meta.datum : null)])
  if (bands.length) levels.push([bottom, `${levelText(bottom - ground, null)} radier`])
  // keep the level labels 0.5 apart
  const placed: number[] = []
  const lv = levels.map(([v, t]) => {
    let at = v
    for (const p of placed) if (Math.abs(p - at) < 0.5) at = p - 0.5
    placed.push(at)
    return { v, at, t }
  })
  return (
    <svg className="section" viewBox={`${f(a - 7.9)} ${f(-(ridge + 0.8))} ${f(b - a + 11.6)} ${f(ridge - ground + 0.8 + depth + 0.2)}`} role="img" aria-label="Coupe schématique : volume bâti hors-sol et sous-sol">
      <rect className="sx-ground" x={f(a - 1.1)} y={y(ground)} width={f(b - a + 4.6)} height={f(depth)} />
      {bands.map((band, k) => {
        const lo = k === 0 ? band.y0 - B.slabUsed : band.y0
        if (!band.top) {
          return <rect key={k} className={band.under ? "sx-under" : "sx-above"} x={f(a)} y={y(band.y1 ?? band.y0)} width={f(b - a)} height={f((band.y1 ?? band.y0) - lo)} />
        }
        const poly: Pt[] = [[a, lo], [b, lo], [b, Math.max(lo, hAt(b))], ...inner.slice().reverse().map(([s, h]) => [s, Math.max(lo, h)] as Pt), [a, Math.max(lo, hAt(a))]]
        return <polygon key={k} className={`sx-above${band.under ? " sx-under" : ""}`} points={poly.map(([s, h]) => `${f(s)},${y(h)}`).join(" ")} />
      })}
      {(["a", "b"] as const).map((side) => {
        const o = over(side)
        return o.length > 1 ? <polyline key={side} className="sx-over" points={o.map(([s, h]) => `${f(s)},${y(h)}`).join(" ")} /> : null
      })}
      <line className="sx-terrain" x1={f(a - 1.1)} y1={y(ground)} x2={f(b + 3.5)} y2={y(ground)} />
      {bands.filter((band) => band.y0 > ground + 0.01).map((band, k) => (
        <line key={k} className="sx-slab" x1={f(a)} y1={y(band.y0)} x2={f(b)} y2={y(band.y0)} />
      ))}
      {lv.map(({ v, at, t }, k) => (
        <g key={k}>
          <line className="sx-tick" x1={f(a - 1.4)} y1={y(v)} x2={f(a - 0.1)} y2={y(v)} />
          <text className="sx-lv" x={f(a - 1.6)} y={f(-at + 0.17)}>{t}</text>
        </g>
      ))}
      {bands.map((band, k) => {
        const lo = k === 0 ? band.y0 - B.slabUsed : band.y0
        const hi = band.top ? band.y0 + (band.meanTop ?? 2) : (band.y1 ?? band.y0)
        return <text key={k} className="sx-v" x={f((a + b) / 2)} y={f(-(lo + hi) / 2 + 0.2)}>{n2(B.bandsV[k] ?? 0)} m³</text>
      })}
      {bands.some((x) => x.under) && (
        <text className="sx-cap" x={f(b + 0.4)} y={f(-(ground - (ground - bottom) / 2) + 0.17)}>sous-sol</text>
      )}
      <text className="sx-cap" x={f(b + 1.0)} y={f(-(ground + (ridge - ground) * 0.4) + 0.17)}>hors-sol</text>
    </svg>
  )
}
