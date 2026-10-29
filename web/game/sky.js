// Sun position from date and place, and an overcast sky dome that also feeds the reflection cube map.

const RAD = Math.PI / 180;

// Low-precision solar ephemeris (Astronomical Almanac), good to about 0.1 degree.
export function sunPosition(date, latDeg, lonDeg) {
  const d = date.getTime() / 86400000 - 10957.5; // days since J2000.0
  const g = (357.529 + 0.98560028 * d) * RAD;
  const L = (280.459 + 0.98564736 * d + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * RAD;
  const e = (23.439 - 0.00000036 * d) * RAD;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const ha = ((18.697374558 + 24.06570982441908 * d) * 15 + lonDeg) * RAD - ra;
  const lat = latDeg * RAD;
  const el = Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(ha));
  const az = Math.atan2(-Math.sin(ha), Math.tan(dec) * Math.cos(lat) - Math.sin(lat) * Math.cos(ha));
  return { elevation: el / RAD, azimuth: (az / RAD + 360) % 360 };
}

// Unit vector towards the sun in world axes (x east, y up, z south).
export function sunVector({ elevation, azimuth }) {
  const el = elevation * RAD, az = azimuth * RAD;
  return [Math.cos(el) * Math.sin(az), Math.sin(el), -Math.cos(el) * Math.cos(az)];
}

export const NOISE = /* glsl */ `
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
}
float fbm(vec2 p) {
  float s = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++) { s += a * noise(p); p = p * 2.03 + vec2(17.0, 9.0); a *= 0.5; }
  return s;
}`;

export const SKY_VERTEX = /* glsl */ `
varying vec3 vDir;
void main() {
  vDir = position;
  vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  gl_Position = p.xyww; // always on the far plane
}`;

export const SKY_FRAGMENT = /* glsl */ `
uniform vec3 uSun, uZenith, uHorizon, uCloudLight, uCloudDark;
uniform vec2 uWind;
uniform float uTime, uCover, uDay;
varying vec3 vDir;
${NOISE}
void main() {
  vec3 d = normalize(vDir);
  float sunAmt = max(dot(d, uSun), 0.0);
  vec3 col = mix(uHorizon, uZenith, pow(max(d.y, 0.0), 0.5));
  col += vec3(1.0, 0.93, 0.82) * (0.25 * pow(sunAmt, 6.0) + 2.5 * pow(sunAmt, 400.0)) * uDay * (1.0 - 0.8 * uCover);
  if (d.y > 0.0) {
    vec2 uv = d.xz / (d.y + 0.1) * 0.9 + uWind * uTime * 0.0015;
    float c = 0.65 * fbm(uv) + 0.35 * fbm(uv * 3.1 + 4.0);
    float cover = smoothstep(1.0 - uCover, 1.25 - uCover, c) * smoothstep(0.0, 0.12, d.y);
    vec3 cloud = mix(uCloudLight, uCloudDark, smoothstep(0.45, 0.9, c)) + vec3(1.0, 0.92, 0.8) * 0.35 * pow(sunAmt, 5.0) * (1.0 - c) * uDay;
    col = mix(col, cloud, cover);
  } else {
    col = uHorizon * mix(0.55, 1.0, exp(d.y * 10.0));
  }
  col *= mix(0.04, 1.0, uDay); // overcast night: no moon or stars
  gl_FragColor = vec4(col, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;
