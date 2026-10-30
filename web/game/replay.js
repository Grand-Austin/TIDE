// Read a replay exported by pipeline/export_replay.py at any time between its 1 s samples.

const SMOOTH = ["roll", "pitch", "heave"]; // fast motions: Catmull-Rom, so 1 s samples do not show corners
const ANGLES = ["heading", "wave_from"];
const LINEAR = ["lon", "lat", "sog", "hs", "tp", "wind_e", "wind_n", "cloud", "precip"]; // cloud, precip: scenes only

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
  for (const c of LINEAR) if (r[c]) s[c] = r[c][lo] + (r[c][hi] - r[c][lo]) * f;
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

// A storm scene from pipeline/export_scene.py (web/3d-scene branch), read into the same shape as a replay.
export function fromScene(scene) {
  const col = (name) => {
    const i = scene.fields.indexOf(name);
    return i < 0 ? undefined : scene.samples.map((row) => row[i]);
  };
  const t = col("t"), lost = col("lost_teu"), grade = col("grade"), teu = new Map(scene.cargo.units.map((u) => [u.id, u.teu]));
  const cargo = [];
  t.forEach((at, i) => {
    if (i === 0 || lost[i] !== lost[i - 1] || grade[i] !== grade[i - 1])
      cargo.push({ t: at, lost_teu: lost[i], grade: grade[i], total_teu: scene.cargo.total_teu });
  });
  return {
    start_utc: scene.start,
    note: scene.plan_note,
    speed_label: "through water",
    credits: [`${scene.credit}: ${Object.values(scene.copernicus_products).join(", ")}`, scene.weather?.credit].filter(Boolean),
    t, cargo,
    ...Object.fromEntries(["lon", "lat", "heading", "roll", "pitch", "heave", "hs", "tp", "wave_from", "wind_e", "wind_n", "cloud", "precip"].map((c) => [c, col(c)])),
    sog: col("stw"),
    orders: scene.orders.map((o) => ({ t: o.t, heading_deg: o.heading_deg, speed_knots: (o.speed_mps * 3600) / 1852 })),
    events: scene.events.filter((e) => e.kind === "cargo_lost").map((e) => ({ time_s: e.t, kind: e.kind, detail: e.detail, unit_ids: e.units,
      teu: e.units.reduce((sum, id) => sum + (teu.get(id) ?? 0), 0) })),
  };
}
