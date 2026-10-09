// housekit/walk: the glide's track and timing (#53). The walk itself needs a browser (its grid is a render).
import { test } from "node:test";
import assert from "node:assert/strict";
import { roundCorners, glideTimes, simplifyRoute, MOTION } from "../walk.js";

const step = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
const dir = (a, b) => Math.atan2(b[1] - a[1], b[0] - a[0]);
const wrap = (x) => Math.atan2(Math.sin(x), Math.cos(x));

test("a right-angle corner is rounded: points every 5 cm, no kink, shorter than the sharp route", () => {
  const track = roundCorners([[0, 0], [4, 0], [4, 4]]);
  assert.equal(track.corners.length, 1);
  assert.ok(Math.abs(track.corners[0].turn - Math.PI / 2) < 1e-9);
  assert.ok(track.corners[0].r > 1, "the full rounding fits");
  assert.ok(track.length < 8 && track.length > 7);
  const { pts } = track;
  for (let i = 1; i < pts.length; i++) assert.ok(Math.abs(step(pts[i - 1], pts[i]) - 0.05) < 0.002);
  for (let i = 2; i < pts.length; i++) {
    const bend = Math.abs(wrap(dir(pts[i - 1], pts[i]) - dir(pts[i - 2], pts[i - 1])));
    assert.ok(bend < 0.1, `kink of ${bend.toFixed(3)} rad at point ${i}`);
  }
  assert.deepEqual(pts[0], [0, 0]);
  assert.ok(step(pts[pts.length - 1], [4, 4]) < 1e-9);
});

test("a rounding that would cut through an obstacle shrinks until it does not", () => {
  const ok = (x, z) => Math.hypot(x - 3.4, z - 0.6) > 0.45; // a pillar inside the corner, clear of the straight route
  const track = roundCorners([[0, 0], [4, 0], [4, 4]], ok);
  assert.ok(track.pts.every(([x, z]) => ok(x, z)));
  assert.ok(track.corners[0].r < 1.2);
  // nothing passes: a sharp corner
  const sharp = roundCorners([[0, 0], [4, 0], [4, 4]], (x, z) => !(x > 3 && z < 1 && !(x === 4 && z === 0)));
  assert.equal(sharp.corners[0].r, 0);
});

test("a glide starts and ends at rest, speeds up and slows down at glideAccel, and stays under the top speed", () => {
  const track = roundCorners([[0, 0], [8, 0]]);
  const { t, v, duration } = glideTimes(track);
  assert.equal(v[0], 0);
  assert.equal(v[v.length - 1], 0);
  assert.ok(Math.max(...v) <= MOTION.glide + 1e-9);
  assert.ok(Math.max(...v) > 0.95 * MOTION.glide, "8 m is long enough to reach the top speed");
  for (let i = 1; i < t.length; i++) {
    const a = Math.abs(v[i] - v[i - 1]) / (t[i] - t[i - 1]);
    assert.ok(a <= MOTION.glideAccel * 1.05, `${a.toFixed(2)} m/s² at point ${i}`);
  }
  // the time is consistent with the speeds: about length / top speed plus the two ramps
  const ramps = MOTION.glide / MOTION.glideAccel;
  assert.ok(Math.abs(duration - (8 / MOTION.glide + ramps)) < 0.15, `${duration}`);
});

test("a short glide lasts minGlide; a glide that starts moving keeps its speed", () => {
  const short = glideTimes(roundCorners([[0, 0], [0.5, 0]]));
  assert.ok(Math.abs(short.duration - MOTION.minGlide) < 1e-9);
  const moving = glideTimes(roundCorners([[0, 0], [6, 0]]), { v0: 1.5 });
  assert.ok(Math.abs(moving.v[0] - 1.5) < 1e-9);
  assert.ok(moving.t[1] < 0.05, "no stop at the start");
});

test("the sharper the corner, the slower through it; a slight bend barely slows it", () => {
  const speedAt = (pts) => {
    const track = roundCorners(pts);
    const { v } = glideTimes(track);
    const c = track.corners[0];
    const ds = track.length / (track.pts.length - 1);
    const mid = Math.round((c.from + c.to) / 2 / ds);
    return { v: v[mid], r: c.r };
  };
  const gentle = speedAt([[0, 0], [5, 0], [10, 1.5]]); // ~17°
  const square = speedAt([[0, 0], [5, 0], [5, 5]]);
  const hairpin = speedAt([[0, 0], [5, 0], [0.5, 1]]); // ~167°
  assert.ok(gentle.v > square.v && square.v > hairpin.v, `${gentle.v} ${square.v} ${hairpin.v}`);
  assert.ok(hairpin.v <= MOTION.corner + 1e-9);
  assert.ok(gentle.v > 1.8, `a slight bend barely slows the glide: ${gentle.v}`);
});

test("a route loses the kinks that add nothing: near-straight and near-duplicate waypoints", () => {
  const route = [[0, 0], [2, 0.02], [2.05, 0.02], [4, 0], [4, 3]];
  assert.deepEqual(simplifyRoute(route), [[0, 0], [4, 0], [4, 3]]);
  assert.deepEqual(simplifyRoute([[0, 0], [1, 0]]), [[0, 0], [1, 0]]);
});
