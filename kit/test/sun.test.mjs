// housekit/sun: the sun over the plot (#49) against NREL's SPA (pvlib 0.13, "nrel_numpy", apparent
// elevation, Lausanne 46.5197 N 6.6323 E), which NOAA's solar calculator matches within ~0.01°.
import { test } from "node:test";
import assert from "node:assert/strict";
import { sunPosition, sunTimes, zurichToUTC, zurichHours, sceneSunDirection, horizonProfile, horizonAt, lv95ToWgs84 } from "../sun.js";

const LAT = 46.5197, LON = 6.6323;
// [Swiss local time, elevation °, azimuth °]
const POSITIONS = [
  ["2026-12-21T08:00", -2.905, 121.687],
  ["2026-12-21T10:30", 14.894, 151.322],
  ["2026-12-21T12:30", 20.083, 179.628],
  ["2026-12-21T15:00", 12.476, 214.524],
  ["2026-12-21T17:45", -9.269, 245.568],
  ["2026-03-20T08:00", 13.418, 104.696],
  ["2026-03-20T10:30", 35.311, 138.51],
  ["2026-03-20T12:30", 43.38, 176.246],
  ["2026-03-20T15:00", 34.425, 223.737],
  ["2026-03-20T17:45", 9.667, 259.796],
  ["2026-06-21T08:00", 20.937, 77.519],
  ["2026-06-21T10:30", 46.456, 105.671],
  ["2026-06-21T12:30", 63.453, 144.783],
  ["2026-06-21T15:00", 61.328, 223.677],
  ["2026-06-21T17:45", 35.535, 267.568],
  ["2026-09-23T08:00", 5.912, 96.268],
  ["2026-09-23T10:30", 29.595, 127.033],
  ["2026-09-23T12:30", 41.746, 161.131],
  ["2026-09-23T15:00", 38.933, 210.888],
  ["2026-09-23T17:45", 16.891, 250.977],
];
// [date, sunrise, solar noon, sunset] (Swiss local time)
const DAYS = [
  ["2026-12-21", "08:14:20", "12:31:31", "16:48:42"],
  ["2026-03-20", "06:36:46", "12:40:54", "18:45:53"],
  ["2026-06-21", "05:40:25", "13:35:17", "21:30:08"],
  ["2026-09-23", "07:21:27", "13:25:51", "19:29:24"],
];
const hm = (s) => { const [h, m, sec] = s.split(":").map(Number); return h + m / 60 + (sec ?? 0) / 3600; };

test("the sun's elevation and azimuth within 0.05° on the solstices and the equinoxes", () => {
  for (const [local, el, az] of POSITIONS) {
    const [d, t] = local.split("T");
    const [y, mo, da] = d.split("-").map(Number);
    const p = sunPosition(zurichToUTC(y, mo, da, hm(t)), LAT, LON);
    assert.ok(Math.abs(p.elevation - el) < 0.05, `${local}: elevation ${p.elevation.toFixed(3)} for ${el}`);
    assert.ok(Math.abs(p.azimuth - az) < 0.05, `${local}: azimuth ${p.azimuth.toFixed(3)} for ${az}`);
  }
});

test("sunrise, solar noon and sunset within a minute, summer time included", () => {
  for (const [date, rise, noon, set] of DAYS) {
    const [y, mo, da] = date.split("-").map(Number);
    const t = sunTimes(y, mo, da, LAT, LON);
    for (const [got, want, name] of [[t.sunrise, rise, "sunrise"], [t.noon, noon, "noon"], [t.sunset, set, "sunset"]]) {
      assert.ok(Math.abs(got - hm(want)) * 60 < 1, `${date} ${name}: ${(got * 60).toFixed(1)} min for ${want}`);
    }
  }
});

test("a 10 m pole at solar noon on 21 December casts about 27 m of shadow", () => {
  const t = sunTimes(2026, 12, 21, LAT, LON);
  const p = sunPosition(zurichToUTC(2026, 12, 21, t.noon), LAT, LON);
  const shadow = 10 / Math.tan((p.elevation * Math.PI) / 180);
  assert.ok(Math.abs(shadow - 27.3) < 0.3, `${shadow.toFixed(2)} m`);
  assert.ok(Math.abs(p.azimuth - 180) < 0.3);
});

test("Swiss time: summer time from the last Sunday of March to the last Sunday of October", () => {
  assert.equal(zurichToUTC(2026, 3, 28, 12).toISOString(), "2026-03-28T11:00:00.000Z");
  assert.equal(zurichToUTC(2026, 3, 29, 12).toISOString(), "2026-03-29T10:00:00.000Z");
  assert.equal(zurichToUTC(2026, 10, 25, 12).toISOString(), "2026-10-25T11:00:00.000Z");
  assert.ok(Math.abs(zurichHours(new Date("2026-06-21T10:30:00Z")) - 12.5) < 1e-9);
});

test("the scene's sun: north turned by the alignment; the horizon from the far landscape", () => {
  // the sun due south, the scene not turned: toward its +z; turned 90° clockwise (its -z points east,
  // so its +x points south): toward its +x
  const [x, y, z] = sceneSunDirection(30, 180, 0);
  assert.ok(Math.abs(x) < 1e-9 && z > 0.8 && y > 0.49);
  const [x2, , z2] = sceneSunDirection(30, 180, 90);
  assert.ok(x2 > 0.8 && Math.abs(z2) < 1e-9);
  // a ridge 1000 m to the south, 100 m above the eye: 5.7° of horizon there, none to the north
  const bins = 8, radii = [500, 1000];
  const h = new Float32Array(radii.length * bins).fill(400);
  h[1 * bins + 2] = 500; // bin 2 of 8: 90° from east toward south = south
  const prof = horizonProfile(h, radii, bins, 400);
  assert.ok(Math.abs(horizonAt(prof, 180) - 5.71) < 0.01);
  assert.ok(horizonAt(prof, 0) <= 0);
  const [lat, lon] = lv95ToWgs84(2537559, 1157053);
  assert.ok(Math.abs(lat - 46.5618) < 0.001 && Math.abs(lon - 6.6243) < 0.001);
});
