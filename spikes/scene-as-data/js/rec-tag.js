// The recorder's tags (spike "scene as data"): every object a recorded call builds carries the
// call itself in userData.spec, so that once the scene is built, each piece can be written down as
// data ({ kit: "wc", args: [...] } or { code: { module, fn }, args }) and rebuilt from it.

const plain = (v) => v === null || ["string", "number", "boolean"].includes(typeof v);

/** The arguments as JSON, or null when one cannot be (a function, a three.js object, a cycle). */
export function jsonArgs(args) {
  let ok = true;
  const out = JSON.parse(
    JSON.stringify(args, (k, v) => {
      if (plain(v) || Array.isArray(v)) return v;
      if (typeof v === "object" && !v.isObject3D && !v.isMaterial && Object.getPrototypeOf(v) === Object.prototype) return v;
      if (v === undefined) return v;
      ok = false;
      return null;
    }) ?? "null",
  );
  return ok ? out : null;
}

function tag(o, spec, a) {
  if (!o || !o.isObject3D) return o;
  o.userData.spec = a === null ? { ...spec, opaque: true } : { ...spec, args: a };
  return o;
}

/**
 * f, its results tagged with `spec` and the arguments of the call (a promise's result too),
 * written down before the call: a maker may fill in the options object it is given.
 */
export function wrap(spec, f) {
  return (...args) => {
    const a = jsonArgs(args);
    const r = f(...args);
    return r && typeof r.then === "function" ? r.then((o) => tag(o, spec, a)) : tag(r, spec, a);
  };
}

/** A post-processing step the scene's code applies to a piece (trimRoof…): kept in its record. */
export function post(name, f) {
  return (o, ...rest) => {
    const r = f(o, ...rest);
    if (o?.isObject3D) (o.userData.post ??= []).push(name);
    return r;
  };
}
