// Spherical geometry for the map's guidance layers: distances, great-circle tracks and rings of constant range.
// Points are [lon, lat] in degrees, distances in nautical miles, on a sphere of 6371.0088 km.

const DEG = Math.PI / 180;
const RAD_TO_NM = (180 * 60) / Math.PI;
const EARTH_KM = 6371.0088;
export const NM_PER_KM = 1 / 1.852;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function haversineNm(a, b) {
  const [lon1, lat1] = [a[0] * DEG, a[1] * DEG], [lon2, lat2] = [b[0] * DEG, b[1] * DEG];
  const h = Math.sin((lat2 - lat1) / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2;
  return 2 * (EARTH_KM * Math.asin(Math.sqrt(clamp(h, 0, 1)))) * NM_PER_KM;
}

// Initial great-circle bearing from a to b, in degrees clockwise from north.
export function bearingDeg(a, b) {
  const [lon1, lat1] = [a[0] * DEG, a[1] * DEG], [lon2, lat2] = [b[0] * DEG, b[1] * DEG];
  const y = Math.sin(lon2 - lon1) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(lon2 - lon1);
  return (Math.atan2(y, x) / DEG + 360) % 360;
}

// The shorter of the two arcs between two points, in radians.
function centralAngle(a, b) {
  const [lon1, lat1] = [a[0] * DEG, a[1] * DEG], [lon2, lat2] = [b[0] * DEG, b[1] * DEG];
  const h = Math.sin((lat2 - lat1) / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2;
  return 2 * Math.asin(Math.sqrt(clamp(h, 0, 1)));
}

// Points along the great circle from a to b, with a vertex about every stepNm and at both ends.
export function greatCircleTrack(a, b, stepNm = 40) {
  const d = centralAngle(a, b);
  if (d < 1e-9) return [[a[0], a[1]]];
  const steps = clamp(Math.ceil((d * RAD_TO_NM) / stepNm), 2, 4000);
  const [lon1, lat1] = [a[0] * DEG, a[1] * DEG], [lon2, lat2] = [b[0] * DEG, b[1] * DEG];
  const sin = Math.sin(d), out = [];
  for (let i = 0; i <= steps; i++) {
    const f = i / steps, A = Math.sin((1 - f) * d) / sin, B = Math.sin(f * d) / sin;
    const x = A * Math.cos(lat1) * Math.cos(lon1) + B * Math.cos(lat2) * Math.cos(lon2);
    const y = A * Math.cos(lat1) * Math.sin(lon1) + B * Math.cos(lat2) * Math.sin(lon2);
    const z = A * Math.sin(lat1) + B * Math.sin(lat2);
    const lon = (Math.atan2(y, x) / DEG + 540) % 360 - 180;
    out.push([lon, Math.atan2(z, Math.hypot(x, y)) / DEG]);
  }
  return out;
}

// A closed ring of constant distance from a centre: where the 200 nm port rule reaches.
export function circleTrack(center, radiusNm, steps = 96) {
  const [lon, lat] = [center[0] * DEG, center[1] * DEG];
  const d = radiusNm / RAD_TO_NM, out = []; // angular radius
  for (let i = 0; i <= steps; i++) {
    const brg = (i / steps) * 2 * Math.PI;
    const lat2 = Math.asin(clamp(Math.sin(lat) * Math.cos(d) + Math.cos(lat) * Math.sin(d) * Math.cos(brg), -1, 1));
    const lon2 = lon + Math.atan2(Math.sin(brg) * Math.sin(d) * Math.cos(lat), Math.cos(d) - Math.sin(lat) * Math.sin(lat2));
    out.push([(lon2 / DEG + 540) % 360 - 180, lat2 / DEG]);
  }
  return out;
}

// [west, south, east, north] of a list of points.
export function boundsOf(points) {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [lon, lat] of points) [w, e, s, n] = [Math.min(w, lon), Math.max(e, lon), Math.min(s, lat), Math.max(n, lat)];
  return [w, s, e, n];
}
