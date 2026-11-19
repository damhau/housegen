// housekit/sun — where the sun stands over the plot (#49): the NOAA solar position (the algorithm of
// NOAA's solar calculator: within ~0.01° of NREL's SPA over these centuries) with SPA's refraction,
// sunrise and sunset, the
// clock in Switzerland (Europe/Zurich, summer time from the last Sunday of March to the last Sunday of
// October), the place from its Swiss coordinates (LV95), and the sun's direction in the scene once the
// surroundings' alignment says where north is. No three.js here: plain numbers, tested in Node.

const RAD = Math.PI / 180, DEG = 180 / Math.PI;
const sin = (d) => Math.sin(d * RAD), cos = (d) => Math.cos(d * RAD), tan = (d) => Math.tan(d * RAD);

/** LV95 (EPSG:2056) → WGS84 [latitude, longitude], swisstopo's approximate formulas (about a metre). */
export function lv95ToWgs84(e, n) {
  const y = (e - 2600000) / 1e6, x = (n - 1200000) / 1e6;
  const lon = 2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x * x - 0.0436 * y ** 3;
  const lat = 16.9023892 + 3.238272 * x - 0.270978 * y * y - 0.002528 * x * x - 0.0447 * y * y * x - 0.014 * x ** 3;
  return [(lat * 100) / 36, (lon * 100) / 36];
}

/** The last Sunday of a month (1-12) of a year, as a day of the month. */
function lastSunday(year, month) {
  const last = new Date(Date.UTC(year, month, 0)); // day 0 of the next month: the last of this one
  return last.getUTCDate() - last.getUTCDay();
}

/** Is a UTC instant within Swiss summer time (CEST, UTC+2)? */
export function zurichSummerTime(date) {
  const y = date.getUTCFullYear();
  const start = Date.UTC(y, 2, lastSunday(y, 3), 1), end = Date.UTC(y, 9, lastSunday(y, 10), 1);
  return date.getTime() >= start && date.getTime() < end;
}

/** The UTC instant of a Swiss wall-clock time: year, month (1-12), day, hours (decimal). */
export function zurichToUTC(year, month, day, hours) {
  const guess = new Date(Date.UTC(year, month - 1, day) + (hours - 1) * 3600e3);
  const offset = zurichSummerTime(new Date(guess.getTime() - 3600e3)) ? 2 : 1;
  return new Date(Date.UTC(year, month - 1, day) + (hours - offset) * 3600e3);
}

/** The Swiss wall-clock hours (decimal) of a UTC instant. */
export function zurichHours(date) {
  const offset = zurichSummerTime(date) ? 2 : 1;
  const d = new Date(date.getTime() + offset * 3600e3);
  return d.getUTCHours() + d.getUTCMinutes() / 60 + d.getUTCSeconds() / 3600;
}

/** NOAA's intermediate terms for an instant: declination (°), equation of time (minutes). */
function solar(date) {
  const jd = date.getTime() / 86400000 + 2440587.5;
  const t = (jd - 2451545) / 36525;
  const l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360;
  const m = 357.52911 + t * (35999.05029 - 0.0001537 * t);
  const ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);
  const c = sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t)) + sin(2 * m) * (0.019993 - 0.000101 * t) + sin(3 * m) * 0.000289;
  const omega = 125.04 - 1934.136 * t;
  const lambda = l0 + c - 0.00569 - 0.00478 * sin(omega);
  const obliq0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60;
  const obliq = obliq0 + 0.00256 * cos(omega);
  const decl = Math.asin(sin(obliq) * sin(lambda)) * DEG;
  const v = tan(obliq / 2) ** 2;
  const eot = 4 * DEG * (v * sin(2 * l0) - 2 * ecc * sin(m) + 4 * ecc * v * sin(m) * cos(2 * l0) - 0.5 * v * v * sin(4 * l0) - 1.25 * ecc * ecc * sin(2 * m));
  return { decl, eot };
}

/**
 * The atmosphere's refraction at a geometric elevation (°): the sun seen higher by it. SPA's (Bennett's
 * formula at 950 hPa and 10 °C, the Swiss plateau), none once the sun's upper rim is below the horizon.
 */
function refraction(e) {
  if (e < -0.8333) return 0;
  return (950 / 1010) * (283 / (273 + 10)) * 1.02 / (60 * tan(e + 10.3 / (e + 5.11)));
}

/**
 * The sun at a UTC instant over a place: { elevation (°, as seen: refraction included), azimuth (°,
 * from north, clockwise), geometric (° without refraction) }.
 */
export function sunPosition(date, lat, lon) {
  const { decl, eot } = solar(date);
  const minutes = date.getUTCHours() * 60 + date.getUTCMinutes() + date.getUTCSeconds() / 60 + date.getUTCMilliseconds() / 60000;
  const tst = (((minutes + eot + 4 * lon) % 1440) + 1440) % 1440;
  const ha = tst / 4 < 0 ? tst / 4 + 180 : tst / 4 - 180;
  const cz = Math.min(1, Math.max(-1, sin(lat) * sin(decl) + cos(lat) * cos(decl) * cos(ha)));
  const zenith = Math.acos(cz) * DEG;
  const geometric = 90 - zenith;
  const ca = Math.min(1, Math.max(-1, (sin(lat) * cos(zenith) - sin(decl)) / (cos(lat) * sin(zenith) || 1e-9)));
  const a = Math.acos(ca) * DEG;
  const azimuth = ha > 0 ? (a + 180) % 360 : (540 - a) % 360;
  return { elevation: geometric + refraction(geometric), azimuth, geometric };
}

/**
 * Sunrise, solar noon and sunset of a Swiss calendar day (year, month 1-12, day) over a place, as
 * Swiss wall-clock hours (decimal); sunrise and sunset are null on a day the sun does not rise or set.
 * The sun's upper rim on a flat horizon (zenith 90.833°: refraction and the disc's radius).
 */
export function sunTimes(year, month, day, lat, lon) {
  const at = (utcMinutes) => new Date(Date.UTC(year, month - 1, day) + utcMinutes * 60000);
  // solar noon, then sunrise and sunset, each refined once with the terms at its own time
  let noon = 720 - 4 * lon - solar(at(720 - 4 * lon)).eot;
  noon = 720 - 4 * lon - solar(at(noon)).eot;
  const event = (sign) => {
    let t = noon;
    for (let k = 0; k < 2; k++) {
      const { decl, eot } = solar(at(t));
      const c = cos(90.833) / (cos(lat) * cos(decl)) - tan(lat) * tan(decl);
      if (c < -1 || c > 1) return null;
      const ha = Math.acos(c) * DEG;
      t = 720 - 4 * (lon - sign * ha) - eot;
    }
    return t;
  };
  const rise = event(-1), set = event(1);
  const hours = (m) => (m === null ? null : zurichHours(at(m)));
  return { sunrise: hours(rise), noon: hours(noon), sunset: hours(set) };
}

/**
 * The direction toward the sun in the scene (x east, y up, z south of the scene's own frame), from
 * its elevation and azimuth (°, from true north) and the alignment's rotation (° the scene is turned
 * clockwise: its -z points `rotation`° east of true north).
 */
export function sceneSunDirection(elevation, azimuth, rotation = 0) {
  const a = azimuth - rotation, ce = cos(elevation);
  return [sin(a) * ce, sin(elevation), -cos(a) * ce];
}

/**
 * The horizon around the place (° above level, per azimuth bin) from the far landscape's polar grid
 * (geo/far.py: altitudes at `radii` along `azimuths` bins, bin i at angle i/n·360° from east toward
 * south, the Earth's curvature taken off), seen from `eye` metres of altitude.
 */
export function horizonProfile(heights, radii, bins, eye) {
  const out = new Float32Array(bins).fill(-90);
  for (let j = 0; j < radii.length; j++) {
    const r = radii[j];
    if (r < 30) continue;
    for (let i = 0; i < bins; i++) {
      const e = Math.atan2(heights[j * bins + i] - eye, r) * DEG;
      if (e > out[i]) out[i] = e;
    }
  }
  return out;
}

/** The horizon's elevation (°) toward a true azimuth (° from north, clockwise), from horizonProfile. */
export function horizonAt(profile, azimuth) {
  const bins = profile.length;
  // the grid's angles run from east toward south: east = 0, south = 90°: a compass azimuth minus 90°
  const f = ((((azimuth - 90) % 360) + 360) % 360) / 360 * bins;
  const i = Math.floor(f) % bins, k = f - Math.floor(f);
  return profile[i] * (1 - k) + profile[(i + 1) % bins] * k;
}

export default { lv95ToWgs84, zurichSummerTime, zurichToUTC, zurichHours, sunPosition, sunTimes, sceneSunDirection, horizonProfile, horizonAt };
