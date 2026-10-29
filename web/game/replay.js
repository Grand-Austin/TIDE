// Read a replay exported by pipeline/export_replay.py at any time between its 1 s samples.

const SMOOTH = ["roll", "pitch", "heave"]; // fast motions: Catmull-Rom, so 1 s samples do not show corners
const ANGLES = ["heading", "wave_from"];
const LINEAR = ["lon", "lat", "sog", "hs", "tp", "wind_e", "wind_n"];

export function sampleAt(r, t) {
  const T = r.t, n = T.length;
  t = Math.min(Math.max(t, T[0]), T[n - 1]);
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (T[m] <= t) lo = m;
    else hi = m;
  }
  const f = T[hi] > T[lo] ? (t - T[lo]) / (T[hi] - T[lo]) : 0;
  const s = { t };
  for (const c of LINEAR) s[c] = r[c][lo] + (r[c][hi] - r[c][lo]) * f;
  for (const c of ANGLES) s[c] = (r[c][lo] + ((((r[c][hi] - r[c][lo]) % 360) + 540) % 360 - 180) * f + 360) % 360;
  for (const c of SMOOTH) {
    const a = r[c], p0 = a[Math.max(lo - 1, 0)], p1 = a[lo], p2 = a[hi], p3 = a[Math.min(hi + 1, n - 1)];
    s[c] = 0.5 * (2 * p1 + (p2 - p0) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f * f + (3 * p1 - p0 - 3 * p2 + p3) * f * f * f);
  }
  return s;
}

// Latest entry of a time-ordered list (orders, cargo) at time t.
export function latest(list, t) {
  let found = list[0];
  for (const x of list) if (x.t <= t) found = x;
  return found;
}

// Metres east (x) and south (z) of the replay start; a flat local frame is fine over a 30 km passage.
export function toLocal(r, lon, lat) {
  const lat0 = r.lat[0] * (Math.PI / 180);
  return [(lon - r.lon[0]) * 111320 * Math.cos(lat0), -(lat - r.lat[0]) * 110574];
}
