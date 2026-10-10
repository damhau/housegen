/**
 * The surfaces and volumes report's sheets (SIA 416, #47, #48), page for page as the mockup
 * (spikes/sia416/mockup.html): summary, one page per storey, the built volume, the finishes take-off,
 * the estimate (prices entered here, saved on the project) and the living area with the habitability check.
 */
import { useEffect, useState } from "react"
import type { Prices, ReportSettings } from "@/api/model"
import { PlanSVG, SectionSVG, planBox } from "./drawings"
import type { Report } from "./compute"
import { CLASS, PRESETS, chf, n2, pct, sum } from "./compute"
import type { Pt, QStorey } from "./types"

const rectText = (p: Pt[] | null) => {
  if (!p || p.length !== 4) return null
  const xs = new Set(p.map((q) => q[0].toFixed(3))), zs = new Set(p.map((q) => q[1].toFixed(3)))
  if (xs.size !== 2 || zs.size !== 2) return null
  const w = Math.max(...p.map((q) => q[0])) - Math.min(...p.map((q) => q[0]))
  const d = Math.max(...p.map((q) => q[1])) - Math.min(...p.map((q) => q[1]))
  return `${w.toFixed(2)} × ${d.toFixed(2)}`
}

function Header({ R, n, title }: { R: Report; n: number; title: string }) {
  return (
    <>
      <header className="run">
        <span>housegen · Surfaces et volumes SIA 416</span>
        <span>
          {R.meta.project}
          {R.meta.version ? ` · version ${R.meta.version}` : ""}
        </span>
      </header>
      <h2 className="page-title">
        <span className="page-no mono">
          {n}/{R.pages}
        </span>
        {title}
      </h2>
    </>
  )
}

function Footer({ R, n }: { R: Report; n: number }) {
  return (
    <footer className="run">
      <span>Estimé sur la maquette 3D, non contractuel. Les surfaces lues sur les plans sont indiquées à côté.</span>
      <span className="mono">
        {n}/{R.pages}
      </span>
    </footer>
  )
}

const Legend = () => (
  <div className="legend" aria-label="Affectations SIA 416">
    <span>
      <i style={{ background: "var(--sup)" }} />
      SUP utile principale
    </span>
    <span>
      <i style={{ background: "var(--sus)" }} />
      SUS utile secondaire
    </span>
    <span>
      <i style={{ background: "var(--sd)" }} />
      SD dégagement
    </span>
    <span>
      <i style={{ background: "var(--si)" }} />
      SI installations
    </span>
  </div>
)

/** The storeys above ground, as the summary names them: "rez, étage, combles et toiture". */
function aboveLabel(R: Report) {
  const bands = R.main?.bands.filter((x) => !x.under) ?? []
  const middle = bands.filter((x, i) => i > 0 && !x.top)
  return bands
    .map((x, i) => (i === 0 && !x.top ? "rez" : !x.top && middle.length === 1 ? "étage" : x.label.toLowerCase()))
    .join(", ")
}

// ---- 1 · summary
function Summary({ R }: { R: Report }) {
  const { T, main } = R
  const objs = [main, ...R.annexes].filter(Boolean) as NonNullable<Report["main"]>[]
  const cell = (v: number | null | undefined, unit = "m²") => (v == null || Math.abs(v) < 0.005 ? "—" : `${n2(v)} ${unit}`)
  const val = (b: (typeof objs)[number], k: "sb" | "sp" | "sn" | "SUP" | "SUS" | "SD" | "SI" | "sc") => {
    if (b.main) return k === "sb" ? b.sb : k === "sp" ? T.sp : k === "sn" ? T.sn : k === "sc" ? T.sc : T[k]
    if (k === "sb") return b.sb
    if (k === "sp") return b.sp
    if (k === "sn") return b.sn
    if (k === "sc") return b.sn == null ? null : b.sp - b.sn
    return b.classes ? b.classes[k] : k === "SUS" ? b.sn : null
  }
  const lastStorey = 1 + R.S.length
  return (
    <article className="sheet" id="p1">
      <Header R={R} n={1} title="Quantités de base selon SIA 416" />
      <div className="grow">
        <div className="cover">
          <div style={{ display: "grid", gap: 10 }}>
            <p className="lead">
              Récapitulatif des surfaces et volumes selon la norme SIA 416 (édition 2003), sur le modèle de la fiche L9 du Guide romand des
              marchés publics. Les calculs sont détaillés par niveau (pages 2 à {lastStorey}), chacun avec son schéma.
            </p>
            <Legend />
          </div>
          <dl>
            <dt>Projet</dt>
            <dd>
              {R.meta.project}, {R.meta.description}
            </dd>
            <dt>Parcelle</dt>
            <dd>{R.meta.parcel}</dd>
            <dt>Source</dt>
            <dd>maquette 3D{R.meta.version ? `, version ${R.meta.version}` : ""}</dd>
            <dt>Date</dt>
            <dd className="mono">{R.meta.date}</dd>
            <dt>Norme</dt>
            <dd>SIA 416:2003, Surfaces et volumes des bâtiments</dd>
          </dl>
        </div>
        <div className="big" role="group" aria-label="Chiffres principaux, objet 1">
          <div>
            <b>{n2(T.sp)} m²</b>
            <span>surface de plancher SP</span>
          </div>
          <div>
            <b>{n2(T.sn)} m²</b>
            <span>surface nette SN</span>
          </div>
          <div>
            <b>{n2(R.vb)} m³</b>
            <span>volume bâti VB{R.vbUnder > 0 ? `, dont ${n2(R.vbUnder)} m³ en sous-sol` : ""}</span>
          </div>
        </div>
        <div className="tablewrap">
          <table className="quant">
            <thead>
              <tr>
                <th>Quantité SIA 416</th>
                {objs.map((b, i) => (
                  <th key={b.key} className="num">
                    Objet {i + 1} · {b.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["SB", "Surface de terrain bâtie", "sb", false],
                  ["SP", "Surface de plancher", "sp", false],
                  ["SN", "Surface nette", "sn", false],
                  ["SUP", "utile principale", "SUP", true],
                  ["SUS", "utile secondaire", "SUS", true],
                  ["SD", "dégagement", "SD", true],
                  ["SI", "installations", "SI", true],
                ] as const
              ).map(([code, label, k, sub]) => (
                <tr key={code} className={sub ? "sub" : undefined}>
                  <td>
                    <span className="code">{code}</span> {label}
                  </td>
                  {objs.map((b) => (
                    <td key={b.key} className="num">
                      {k === "sb" || k === "sp" ? `${n2(val(b, k) ?? 0)} m²` : cell(val(b, k))}
                    </td>
                  ))}
                </tr>
              ))}
              <tr>
                <td>
                  <span className="code">SC</span> Surface de construction
                </td>
                {objs.map((b) => (
                  <td key={b.key} className="num">
                    {cell(val(b, "sc"))}
                    {b.main && T.sp > 0 && <span className="muted small"> ({((T.sc / T.sp) * 100).toFixed(1)} % de SP)</span>}
                  </td>
                ))}
              </tr>
              <tr className="strong">
                <td>
                  <span className="code">VB</span> Volume bâti
                </td>
                {objs.map((b) => (
                  <td key={b.key} className="num">
                    {n2(b.vb)} m³
                  </td>
                ))}
              </tr>
              <tr className="sub">
                <td>hors-sol ({aboveLabel(R)})</td>
                {objs.map((b) => (
                  <td key={b.key} className="num">
                    {n2(b.vb - b.vbUnder)} m³
                  </td>
                ))}
              </tr>
              <tr className="sub">
                <td>sous-sol</td>
                {objs.map((b) => (
                  <td key={b.key} className="num">
                    {cell(b.vbUnder, "m³")}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Niveau</th>
                {["SP", "SN", "SC", "SUP", "SUS", "SD", "SI", "VB m³"].map((h) => (
                  <th key={h} className="num">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {R.S.map((s, i) => (
                <tr key={s.key}>
                  <td>{s.label}</td>
                  {[s.sp, s.sn, s.sc, s.SUP, s.SUS, s.SD, s.SI, main?.bandsV[i] ?? 0].map((v, k) => (
                    <td key={k} className="num">
                      {n2(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="strong">
                <td>Total</td>
                {[T.sp, T.sn, T.sc, T.SUP, T.SUS, T.SD, T.SI, R.vb].map((v, k) => (
                  <td key={k} className="num">
                    {n2(v)}
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>
        </div>
        {R.flagged.length > 0 && (
          <div className="note warnbox">
            <b>
              {R.flagged.length > 1 ? `${R.flagged.length} locaux s'écartent` : "1 local s'écarte"} de plus de 5 % de la surface inscrite sur les plans
            </b>
            <span>
              Sur {R.withPlan.length} locaux dont le plan donne la surface, l'écart total est de{" "}
              {pct(sum(R.withPlan, (r) => r.sn), sum(R.withPlan, (r) => r.plan ?? 0))}. {R.warning}
            </span>
          </div>
        )}
      </div>
      <Footer R={R} n={1} />
    </article>
  )
}

// ---- 2… · one storey per page
function StoreyPage({ R, s, n, box }: { R: Report; s: QStorey; n: number; box: [number, number, number, number] }) {
  const clear = s.ceiling
    ? `${s.clear.toFixed(2)} m`
    : s.clearRange
      ? `sous pente, ${s.clearRange[0].toFixed(2)} à ${s.clearRange[1].toFixed(2)} m`
      : "sous pente"
  const ground = R.S.find((x) => x.n === 0)?.y ?? 0
  const level = s.y - ground
  const outline = rectText(s.outline)
  return (
    <article className="sheet" id={`p${n}`}>
      <Header R={R} n={n} title={`Surfaces par niveau : ${s.label.toLowerCase()}`} />
      <div className="grow">
        <section className="storey" aria-labelledby={`st-${s.key}`}>
          <div className="storey-head">
            <h3 id={`st-${s.key}`}>{s.label}</h3>
            <p className="mono small">
              niveau {level >= 0 ? "+" : "−"}
              {Math.abs(level).toFixed(2)} · hauteur libre {clear}
            </p>
          </div>
          <figure className="plan-fig plan-big">
            <PlanSVG R={R} s={s} box={box} />
            <figcaption>Schéma SIA 416 · {s.label} · même échelle à chaque niveau</figcaption>
          </figure>
          <div className="tablewrap">
            <table className="rooms">
              <thead>
                <tr>
                  <th>N°</th>
                  <th>Local</th>
                  <th>SIA</th>
                  <th>Calcul</th>
                  <th className="num">SN m²</th>
                  <th className="num">Plan m²</th>
                  <th className="num">Écart</th>
                </tr>
              </thead>
              <tbody>
                {s.rooms.map((r) => {
                  const flag = R.flagged.some((x) => x.no === r.no)
                  return (
                    <tr key={r.no} className={flag ? "flag" : undefined}>
                      <td className="mono no">{r.no}</td>
                      <td>
                        {r.name}
                        <span className="flatname">{r.flat ?? (R.flats.length === 1 ? R.flats[0]?.flat : "")}</span>
                      </td>
                      <td>
                        <span className={`cls cls-${CLASS[r.sia]}`}>{r.sia}</span>
                      </td>
                      <td className="mono formula">{r.formula}</td>
                      <td className="num">{n2(r.sn)}</td>
                      <td className="num muted">{r.plan ? n2(r.plan) : "—"}</td>
                      <td className={`num ${flag ? "warn" : "muted"}`}>{r.plan ? pct(r.sn, r.plan) : ""}</td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={4}>Surface nette SN</td>
                  <td className="num">{n2(s.sn)}</td>
                  <td colSpan={2} />
                </tr>
                <tr>
                  <td colSpan={4}>Surface de construction SC = SP − SN</td>
                  <td className="num">{n2(s.sc)}</td>
                  <td colSpan={2} />
                </tr>
                <tr className="strong">
                  <td colSpan={4}>
                    Surface de plancher SP {outline && <span className="mono small">{outline}</span>}
                  </td>
                  <td className="num">{n2(s.sp)}</td>
                  <td colSpan={2} />
                </tr>
              </tfoot>
            </table>
          </div>
        </section>
      </div>
      <Footer R={R} n={n} />
    </article>
  )
}

// ---- the built volume
function VolumePage({ R, n }: { R: Report; n: number }) {
  const B = R.main
  if (!B) return null
  const balc = R.balconies.map((b) => `${n2(b.area)} m²`)
  const slab = `Radier de ${B.slabUsed.toFixed(2)} m sous ${R.S.some((s) => s.n < 0) ? "le sous-sol" : "le rez-de-chaussée"}`
  return (
    <article className="sheet" id={`p${n}`}>
      <Header R={R} n={n} title="Volume bâti VB" />
      <div className="grow">
        <p className="lead">
          Volume réel délimité par les faces extérieures de l'enveloppe, du dessous du radier à la surface extérieure de la toiture, calculé
          niveau par niveau. Les dimensions horizontales sont les dimensions effectives ; la toiture est mesurée sur la maquette, vue d'en haut.
        </p>
        <div className="vol">
          <figure className="plan-fig">
            <SectionSVG R={R} B={B} />
            <figcaption>Coupe schématique perpendiculaire au faîte</figcaption>
          </figure>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Niveau</th>
                  <th>Calcul</th>
                  <th className="num">m³</th>
                </tr>
              </thead>
              <tbody>
                {B.bands.map((band, k) => (
                  <tr key={k}>
                    <td>{band.label}</td>
                    <td className="mono formula">{B.formulas[k]}</td>
                    <td className="num">{n2(B.bandsV[k] ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={2}>hors-sol</td>
                  <td className="num">{n2(R.vb - R.vbUnder)}</td>
                </tr>
                <tr>
                  <td colSpan={2}>sous-sol</td>
                  <td className="num">{n2(R.vbUnder)}</td>
                </tr>
                <tr className="strong">
                  <td colSpan={2}>VB objet 1 · {B.name}</td>
                  <td className="num">{n2(R.vb)}</td>
                </tr>
                {R.annexes.map((A, i) => (
                  <tr key={A.key}>
                    <td>
                      Objet {i + 2} · {A.name}
                    </td>
                    <td className="mono formula">{A.formulas.join(" + ")}</td>
                    <td className="num">{n2(A.vb)}</td>
                  </tr>
                ))}
              </tfoot>
            </table>
          </div>
        </div>
        <div className="rules">
          <div>
            <b>Compris</b>
            <span>
              Tous les niveaux{R.S.some((s) => s.n < 0) ? ", sous-sol compris" : ""} ; {R.S.some((s) => !s.ceiling) ? "les combles et " : ""}la toiture
              jusqu'à sa surface extérieure.
            </span>
          </div>
          <div>
            <b>Non compris</b>
            <span>
              {balc.length ? `Les balcons ouverts (${balc.length > 1 ? `${balc.slice(0, -1).join(", ")} et ${balc.at(-1)}` : balc[0]}), les` : "Les"} débords de
              toiture (en pointillé sur la coupe), les fondations spéciales.
            </span>
          </div>
          <div>
            <b>Hypothèse</b>
            <span>
              {B.slabAssumed
                ? `${slab} : il n'est pas dans la maquette. À corriger d'après la coupe des plans.`
                : `${slab}, lu dans la maquette.`}
            </span>
          </div>
          {R.annexes.map((A) => (
            <div key={A.key}>
              <b>{A.name}</b>
              <span>
                Bâtiment non contigu{A.gap != null ? ` (environ ${Math.max(1, Math.round(A.gap))} m de la ${B.name.toLowerCase()})` : ""} : compté comme un
                objet à part, comme le demande la fiche L9.
              </span>
            </div>
          ))}
        </div>
      </div>
      <Footer R={R} n={n} />
    </article>
  )
}

// ---- the finishes take-off
function TakeoffPage({ R, n }: { R: Report; n: number }) {
  const tileH = R.tileHeights.map((t) => t.split("|"))
  const tileText = tileH.length
    ? `Faïence : ${[...new Set(tileH.map(([h]) => h))].join(", ")} m, ${[...new Set(tileH.map(([, fh]) => fh))].join(", ")} m aux murs de douche et de baignoire.`
    : ""
  return (
    <article className="sheet" id={`p${n}`}>
      <Header R={R} n={n} title="Métré des finitions" />
      <div className="grow">
        <p className="lead">
          Quantités par élément du code des coûts eCCC-Bât (SN 506 511), avec le CFC correspondant. Chaque ligne donne son calcul, pour qu'elle
          puisse être vérifiée et reprise dans un devis. Les colonnes de prix sont à compléter.
        </p>
        <div className="tablewrap">
          <table className="takeoff">
            <thead>
              <tr>
                <th>eCCC</th>
                <th>CFC</th>
                <th>Désignation</th>
                <th>Niveau / local</th>
                <th>Calcul</th>
                <th>Unité</th>
                <th className="num">Quantité</th>
                <th className="num">PU CHF</th>
                <th className="num">Montant CHF</th>
              </tr>
            </thead>
            <tbody>
              {R.lines.map((l, i) =>
                l.group ? (
                  <tr key={i} className="grp">
                    <td colSpan={9}>{l.group}</td>
                  </tr>
                ) : (
                  <tr key={i}>
                    <td className="mono">{l.eccc}</td>
                    <td className="mono">{l.cfc}</td>
                    <td>{l.what}</td>
                    <td>{l.where}</td>
                    <td className="mono formula">{l.formula}</td>
                    <td>{l.unit}</td>
                    <td className="num">{n2(l.qty ?? 0)}</td>
                    <td className="num muted">—</td>
                    <td className="num muted">—</td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
        <div className="rules">
          <div>
            <b>Ouvertures</b>
            <span>Déduites dès 1 m² (NPK 643/651) ; {R.smallKept} ouvertures plus petites ne sont pas déduites.</span>
          </div>
          <div>
            <b>Carrelage, petites surfaces</b>
            <span>
              {R.smallTiles.length
                ? `${R.smallTiles.length} surface${R.smallTiles.length > 1 ? "s" : ""} de moins de 2 m² (${R.smallTiles.map((r) => r.no).join(", ")}) : majoration de 20 % à appliquer (NPK 645).`
                : "Aucune surface de moins de 2 m² par local : pas de majoration de 20 % (NPK 645)."}
            </span>
          </div>
          <div>
            <b>Hauteurs</b>
            <span>
              Parois : hauteur libre du niveau{R.S.some((s) => !s.ceiling) ? " ; aux combles, hauteur sous pente au point près" : ""}. {tileText}
            </span>
          </div>
          <div>
            <b>Pas encore compté</b>
            <span>Embrasures et tablettes de fenêtres, plinthes, plafonds (G04) : à ajouter dans le rapport final.</span>
          </div>
        </div>
      </div>
      <Footer R={R} n={n} />
    </article>
  )
}

/** A price field: what the owner types, saved as a number (or null when empty). */
function Field({ value, onChange, label, placeholder, step, max }: { value: number | null | undefined; onChange: (v: number | null) => void; label: string; placeholder?: string; step?: string; max?: number }) {
  const [text, setText] = useState(value == null ? "" : String(value))
  useEffect(() => setText(value == null ? "" : String(value)), [value])
  return (
    <input
      className="field"
      type="number"
      inputMode="decimal"
      min={0}
      max={max}
      step={step}
      placeholder={placeholder}
      aria-label={label}
      value={text}
      data-value={text}
      onChange={(e) => {
        setText(e.target.value)
        const v = parseFloat(e.target.value)
        onChange(Number.isFinite(v) && v >= 0 ? v : null)
      }}
    />
  )
}

// ---- the estimate: prices entered here, saved on the project
function EstimatePage({ R, n, settings, onPrices }: { R: Report; n: number; settings: ReportSettings; onPrices: (p: Prices) => void }) {
  const p = settings.prices ?? {}
  const set = (patch: Partial<Prices>) => onPrices({ ...p, ...patch })
  const A = R.amounts
  const put = (v: number | null | undefined) => (v == null ? "à saisir" : chf(v))
  const main = R.main
  const priceOf: Record<string, keyof Prices> = { "0": "land_m2", "1": "excavation_m3", "4a": "pool_each", "4b": "paving_m2", "4c": "hedge_m", "4d": "fence_m", "4e": "tree_each" }
  const lineRow = (e: Report["est"][number]) => (
    <tr key={e.key}>
      <td className="mono">{e.cfc}</td>
      <td>{e.what}</td>
      <td className="mono">{e.qtyText}</td>
      <td className="num">
        <Field value={e.price} label={e.label} placeholder={e.unit} onChange={(v) => set({ [priceOf[e.key] ?? "land_m2"]: v } as Partial<Prices>)} />
      </td>
      <td className={`num amount${A.line[e.key] == null ? " empty" : ""}`}>{put(A.line[e.key])}</td>
    </tr>
  )
  return (
    <article className="sheet" id={`p${n}`}>
      <Header R={R} n={n} title="Estimation des coûts et de la valeur" />
      <div className="grow">
        <p className="lead">
          Les quantités viennent de la maquette ; les prix se saisissent dans les champs et les montants se recalculent. Les coûts suivent les
          groupes principaux du CFC. Les prix proposés par défaut sont indicatifs : l'agence les remplace par ses propres références.
        </p>
        <section className="est" aria-labelledby="est-a">
          <div className="est-head">
            <h3 id="est-a">A · Bâtiment (CFC 2) par le volume SIA 416</h3>
            {main && (
              <div className="presets" role="group" aria-label="Standard de construction du bâtiment principal">
                {PRESETS.map((x) => (
                  <button key={x.rate} type="button" aria-pressed={R.rates[0] === x.rate}
                    onClick={() => set({ building_m3: { ...(p.building_m3 ?? {}), [main.key]: x.rate } })}>
                    {x.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="tablewrap">
            <table className="est-t">
              <thead>
                <tr>
                  <th>Objet</th>
                  <th>Volume bâti</th>
                  <th className="num">CHF/m³</th>
                  <th className="num">Montant CHF</th>
                </tr>
              </thead>
              <tbody>
                {R.buildings.map((b, i) => (
                  <tr key={b.key}>
                    <td>{b.name}</td>
                    <td className="mono">{n2(b.vb)} m³</td>
                    <td className="num">
                      <Field value={R.rates[i]} step="5" label={`Prix au m³, ${b.name}`}
                        onChange={(v) => set({ building_m3: { ...(p.building_m3 ?? {}), [b.key]: v } })} />
                    </td>
                    <td className={`num amount${A.perBuilding[i] == null ? " empty" : ""}`}>{put(A.perBuilding[i])}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="total">
                  <td colSpan={3}>Total CFC 2</td>
                  <td className="num">{A.cfc2 === null ? "—" : chf(A.cfc2)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p className="small muted">
            Repères indicatifs d'un constructeur (neho.ch), CHF par m³ SIA 416 : simple 600–750, moyen 750–950, élevé 950–1'200. Vérifier si la
            référence comprend les honoraires (CFC 29). Indexer les références anciennes avec l'indice OFS des prix de la construction, région
            lémanique : 116.3 en octobre 2025 (octobre 2020 = 100).
          </p>
        </section>
        <section className="est" aria-labelledby="est-b">
          <h3 id="est-b">B · Coût du projet par groupes CFC</h3>
          <div className="tablewrap">
            <table className="est-t">
              <thead>
                <tr>
                  <th>CFC</th>
                  <th>Poste</th>
                  <th>Quantité de la maquette</th>
                  <th className="num">Prix unitaire</th>
                  <th className="num">Montant CHF</th>
                </tr>
              </thead>
              <tbody>
                <tr className="grp">
                  <td colSpan={5}>
                    <b>0 Terrain</b>
                  </td>
                </tr>
                {R.est[0] && lineRow(R.est[0])}
                <tr className="grp">
                  <td colSpan={5}>
                    <b>1 Travaux préparatoires</b>
                  </td>
                </tr>
                {R.est[1] && lineRow(R.est[1])}
                <tr className="grp">
                  <td colSpan={5}>
                    <b>2 Bâtiment</b>
                  </td>
                </tr>
                <tr>
                  <td className="mono">2</td>
                  <td>{R.buildings.map((b, i) => (i ? b.name.toLowerCase() : b.name)).join(" et ")}</td>
                  <td>selon A</td>
                  <td className="num muted">—</td>
                  <td className={`num amount${A.cfc2 === null ? " empty" : ""}`}>{put(A.cfc2)}</td>
                </tr>
                {R.est4.length > 0 && (
                  <tr className="grp">
                    <td colSpan={5}>
                      <b>4 Aménagements extérieurs</b>
                    </td>
                  </tr>
                )}
                {R.est4.map(lineRow)}
                <tr className="grp">
                  <td colSpan={5}>
                    <b>5 Frais secondaires</b>
                  </td>
                </tr>
                <tr>
                  <td className="mono">5</td>
                  <td>Autorisations, taxes de raccordement, assurances, financement</td>
                  <td>en % du CFC 2</td>
                  <td className="num">
                    <Field value={p.secondary_pct} max={100} step="0.5" placeholder="%" label="Frais secondaires en pour cent du CFC 2" onChange={(v) => set({ secondary_pct: v })} />
                  </td>
                  <td className={`num amount${A.line["5"] == null ? " empty" : ""}`}>{put(A.line["5"])}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr className="total">
                  <td colSpan={4}>
                    Coût du projet{" "}
                    <span className="small muted">{A.open ? `(${A.open} poste${A.open > 1 ? "s" : ""} encore à saisir)` : ""}</span>
                  </td>
                  <td className="num">{chf(A.cost)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>
        <section className="est" aria-labelledby="est-c">
          <h3 id="est-c">C · Valeur de vente</h3>
          <div className="tablewrap">
            <table className="est-t">
              <thead>
                <tr>
                  <th>Logement</th>
                  <th>Surface pondérée (page {R.pages})</th>
                  <th className="num">CHF/m²</th>
                  <th className="num">Valeur CHF</th>
                </tr>
              </thead>
              <tbody>
                {R.flats.map((F, i) => (
                  <tr key={F.flat}>
                    <td>{F.flat}</td>
                    <td className="mono">{n2(F.total)} m²</td>
                    <td className="num">
                      <Field value={p.sale_m2?.[F.flat]} step="50" placeholder="CHF/m²" label={`Prix de vente au m² pondéré, ${F.flat}`}
                        onChange={(v) => set({ sale_m2: { ...(p.sale_m2 ?? {}), [F.flat]: v } })} />
                    </td>
                    <td className={`num amount${A.values[i] == null ? " empty" : ""}`}>{put(A.values[i])}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="total">
                  <td colSpan={3}>Valeur de vente</td>
                  <td className="num">{A.missing === R.flats.length ? "—" : chf(A.value)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          <div className="balance" aria-live="polite">
            <div>
              <b>{A.missing === R.flats.length ? "—" : `CHF ${chf(A.value)}`}</b>
              <span>valeur de vente</span>
            </div>
            <div>
              <b>CHF {chf(A.cost)}</b>
              <span>coût du projet</span>
            </div>
            <div>
              <b>{A.missing || A.open ? "—" : `CHF ${chf(A.value - A.cost)}`}</b>
              <span>marge brute</span>
            </div>
          </div>
          <p className="small muted">
            Le prix au m² vient de l'agence ou d'un outil d'estimation (Wüest Partner, IAZI, RealAdvisor, Fahrländer). Ces outils sont payants ; un
            accès de l'agence pourrait remplir ces champs.
          </p>
        </section>
      </div>
      <Footer R={R} n={n} />
    </article>
  )
}

// ---- the living area and the habitability check
function HabitablePage({ R, n }: { R: Report; n: number }) {
  const w = R.weights
  const pc = (v: number) => `${Math.round(v * 100)} %`
  const low = R.flats.flatMap((F) => F.living).filter((r) => r.low > 0.005)
  const attributed = R.terraces.some((t) => t.flat) || R.flats.some((F) => F.garden > 0)
  return (
    <article className="sheet" id={`p${n}`}>
      <Header R={R} n={n} title="Annexe : surface habitable et habitabilité" />
      <div className="grow">
        <p className="lead">
          Pour la vente : la surface habitable nette de chaque appartement (pièces et dégagements, sans les escaliers ni les annexes) et une surface
          pondérée. Il n'existe pas de norme nationale pour ce calcul : les règles ci-dessous sont celles de la pratique courante et se règlent par
          projet.
        </p>
        <div className="flats">
          {R.flats.map((F) => (
            <div className="flat" key={F.flat}>
              <h3>{F.flat}</h3>
              <div className="tablewrap">
                <table className="rooms compact">
                  <thead>
                    <tr>
                      <th>N°</th>
                      <th>Local</th>
                      <th>Niveau</th>
                      <th className="num">m²</th>
                    </tr>
                  </thead>
                  <tbody>
                    {F.living.map((r) => (
                      <tr key={r.no}>
                        <td className="mono">{r.no}</td>
                        <td>{r.name}</td>
                        <td>{r.storey}</td>
                        <td className="num">{n2(r.counted)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="strong">
                      <td colSpan={3}>Surface habitable nette</td>
                      <td className="num">{n2(F.hab)}</td>
                    </tr>
                    {F.balc.map((b) => (
                      <tr key={b.name}>
                        <td colSpan={3}>
                          {b.name} <span className="mono small">{n2(b.area)} × {pc(w.balcony)}</span>
                        </td>
                        <td className="num">{n2(b.area * w.balcony)}</td>
                      </tr>
                    ))}
                    {F.terr.map((t) => (
                      <tr key={t.name}>
                        <td colSpan={3}>
                          {t.name} <span className="mono small">{n2(t.area)} × {pc(w.terrace)}</span>
                        </td>
                        <td className="num">{n2(t.area * w.terrace)}</td>
                      </tr>
                    ))}
                    {F.garden > 0 && (
                      <tr>
                        <td colSpan={3}>
                          Jardin privatif <span className="mono small">{n2(F.garden)} × {pc(w.garden)}</span>
                        </td>
                        <td className="num">{n2(F.garden * w.garden)}</td>
                      </tr>
                    )}
                    <tr className="strong">
                      <td colSpan={3}>Surface pondérée</td>
                      <td className="num">{n2(F.total)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <p className="small muted">
                Non comptés :{" "}
                {[
                  F.stairs.length ? `escaliers ${F.stairs.map((r) => r.no).join(", ")} (${n2(sum(F.stairs, (r) => r.sn))} m²)` : null,
                  F.annex.length
                    ? `annexes${F.annex.every((r) => r.storeyN < 0) ? " au sous-sol" : ""} ${F.annex.map((r) => r.no).join(", ")} (${n2(sum(F.annex, (r) => r.sn))} m²)`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" ; ") || "rien"}
                .
              </p>
            </div>
          ))}
        </div>
        <div className="rules">
          <div>
            <b>Combles</b>
            <span>
              Les surfaces de moins de 1.30 m de hauteur libre ne comptent pas (RLATC art. 27).
              {R.attic
                ? low.length
                  ? ` Dans la maquette, ${n2(sum(low, (r) => r.low))} m² sont plus bas et sont déduits.`
                  : ` Dans la maquette, la hauteur au pied des murs est de ${R.attic.minClear.toFixed(2)} m : rien n'est déduit.`
                : " La maquette n'a pas de niveau sous la toiture."}
            </span>
          </div>
          <div>
            <b>Pondération</b>
            <span>
              Balcons {pc(w.balcony)}. Terrasses ({pc(w.terrace)}) et jardins privatifs ({pc(w.garden)}) :{" "}
              {attributed ? "attribués par logement dans les réglages du rapport." : "à attribuer par logement selon la répartition PPE ; la maquette les mesure (page 8) mais ne sait pas à qui ils appartiennent."}
            </span>
          </div>
        </div>
        <h3>Habitabilité selon le RLATC vaudois</h3>
        <p className="small muted">
          Art. 25 : volume d'au moins 20 m³. Art. 27 : hauteur d'au moins 2.40 m ; aux combles, sur au moins la moitié de la surface utilisable,
          comptée dès 1.30 m. Art. 28 : baies d'au moins 1/8 du plancher et 1 m² (1/15 et 0.80 m² pour les lucarnes et tabatières).
        </p>
        <div className="tablewrap">
          <table className="rooms compact">
            <thead>
              <tr>
                <th>N°</th>
                <th>Local</th>
                <th className="num">Utilisable m²</th>
                <th>Hauteur, art. 27</th>
                <th>Baies, art. 28</th>
                <th className="num">Volume, art. 25</th>
                <th>Résultat</th>
              </tr>
            </thead>
            <tbody>
              {R.rlatc.map((q) => (
                <tr key={q.no}>
                  <td className="mono no">{q.no}</td>
                  <td>
                    {q.name}
                    <span className="flatname">{q.flat ?? ""}</span>
                  </td>
                  <td className="num">{n2(q.use)}</td>
                  <td>{q.attic ? `${Math.round(q.high * 100)} % à 2.40 m ou plus` : `${q.clear.toFixed(2)} m`}</td>
                  <td>
                    {n2(q.light)} m², {typeof q.ratio === "number" ? `1/${q.ratio.toFixed(1)}` : "aucune baie"}
                    {q.aSky > 0 && <span className="small muted"> dont tabatières {n2(q.aSky)}</span>}
                  </td>
                  <td className={`num${q.ok25 ? "" : " warn"}`}>{n2(q.vol)}</td>
                  <td>
                    {!q.ok25 && q.together && q.ok ? (
                      <>
                        <span className="st st-ok">conforme</span>{" "}
                        <span className="small muted">
                          pièce ouverte sur {q.others.length > 1 ? `${q.others.slice(0, -1).join(", ")} et ${q.others.at(-1)}` : q.others[0]} : {n2(q.groupVol)} m³
                          ensemble
                        </span>
                      </>
                    ) : q.ok ? (
                      <span className="st st-ok">conforme</span>
                    ) : (
                      <span className="st st-warn">à vérifier</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Footer R={R} n={n} />
    </article>
  )
}

/** The report's sheets. */
export function ReportPages({ R, settings, onPrices }: { R: Report; settings: ReportSettings; onPrices: (p: Prices) => void }) {
  const box = planBox(R)
  const k = R.S.length
  return (
    <>
      <Summary R={R} />
      {R.S.map((s, i) => (
        <StoreyPage key={s.key} R={R} s={s} n={i + 2} box={box} />
      ))}
      <VolumePage R={R} n={k + 2} />
      <TakeoffPage R={R} n={k + 3} />
      <EstimatePage R={R} n={k + 4} settings={settings} onPrices={onPrices} />
      <HabitablePage R={R} n={k + 5} />
    </>
  )
}
