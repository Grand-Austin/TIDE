// Gerstner sea built from one JONSWAP spectrum, scaled so its significant height equals the data's Hs.
// World axes: x east, y up, z south. The same sum runs here (height queries) and in the vertex shader (drawing).

const G = 9.81;
export const N_WAVES = 24;
export const FOAM_SPOTS = 12; // splash rings and floating boxes the sea shows as white water

function mulberry32(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Directional spreading cos^2s(θ/2) with Mitsuyasu's s(ω): narrowest at the peak, wider for short waves, so the long
// crests read as one direction. s_max 50: storm swell, between Goda's 25 (short decay) and 75 (long decay).
const S_MAX = 50;

// |θ| at quantile u of the cos^2s(θ/2) spread, from a coarse numeric integral (steps of 2.8°)
function spreadAngle(sp, u) {
  const n = 64, f = (k) => Math.cos(((k + 0.5) * Math.PI) / n / 2) ** (2 * sp);
  let total = 0;
  for (let k = 0; k < n; k++) total += f(k);
  for (let k = 0, acc = 0; k < n; k++) if ((acc += f(k)) >= u * total) return ((k + 0.5) * Math.PI) / n;
  return Math.PI;
}

export function spectrumWaves({ hs, tp, fromDeg, steepness = 0.75, seed = 1 }) {
  const wp = (2 * Math.PI) / tp, gamma = 3.3, rand = mulberry32(seed);
  const travel = ((fromDeg + 180) * Math.PI) / 180;
  const w0 = 0.75 * wp, dw = (3.0 * wp - w0) / N_WAVES;
  const waves = [];
  let off = 0;
  for (let i = 0; i < N_WAVES; i++) {
    const w = w0 + (i + 0.5) * dw;
    const sigma = w <= wp ? 0.07 : 0.09;
    const s = w ** -5 * Math.exp(-1.25 * (wp / w) ** 4) * gamma ** Math.exp(-((w - wp) ** 2) / (2 * sigma ** 2 * wp ** 2));
    const sp = S_MAX * (w <= wp ? (w / wp) ** 5 : (w / wp) ** -2.5);
    // a middle quantile, so 24 components give every seed a similar spread; odd components mirror their neighbour,
    // so the sea's mean direction stays the data's
    off = i % 2 ? -off : (rand() < 0.5 ? -1 : 1) * spreadAngle(sp, 0.2 + 0.6 * rand());
    const dir = travel + off;
    waves.push({ w, k: (w * w) / G, a: Math.sqrt(2 * s * dw), dx: Math.sin(dir), dz: -Math.cos(dir), phase: rand() * 2 * Math.PI });
  }
  const scale = hs / (4 * Math.sqrt(waves.reduce((m0, v) => m0 + (v.a * v.a) / 2, 0)));
  for (const v of waves) {
    v.a *= scale;
    v.h = steepness / (v.k * N_WAVES); // horizontal amplitude: the sum of k*h stays below 1, so crests never fold over
  }
  return waves;
}

// amp scales the whole sea to a later Hs without changing its components, so the surface never jumps.
export function displace(waves, x, z, t, amp = 1) {
  let px = x, py = 0, pz = z;
  const hamp = Math.min(amp, 1);
  for (const v of waves) {
    const th = v.k * (v.dx * x + v.dz * z) - v.w * t + v.phase;
    px += v.h * hamp * v.dx * Math.cos(th);
    pz += v.h * hamp * v.dz * Math.cos(th);
    py += v.a * amp * Math.sin(th);
  }
  return [px, py, pz];
}

// Water height at a world point: find the rest point whose displaced position lands there.
export function heightAt(waves, x, z, t, amp = 1) {
  let x0 = x, z0 = z;
  for (let i = 0; i < 4; i++) {
    const [px, , pz] = displace(waves, x0, z0, t, amp);
    x0 += x - px;
    z0 += z - pz;
  }
  return displace(waves, x0, z0, t, amp)[1];
}

export function waveUniforms(waves) {
  return {
    a: waves.map((v) => [v.dx, v.dz, v.k, v.w]),
    b: waves.map((v) => [v.a, v.h, v.phase, (2 * Math.PI) / v.k]),
  };
}

// The same Gerstner sum as displace(), shared by the sea surface and the breaking-crest spray.
const GERSTNER = /* glsl */ `
#define N ${2 * N_WAVES}
uniform vec4 uA[N];   // dir x, dir z, k, omega
uniform vec4 uB[N];   // amplitude, horizontal amplitude, phase, wavelength
uniform float uAmp;
uniform int uCount;   // components in use: one wave set, or two while the sea cross-fades to a new Tp or direction
vec3 gerstner(vec2 p0, float dist, float t, out vec3 n, out float jac) {
  vec3 P = vec3(p0.x, 0.0, p0.y);
  n = vec3(0.0, 1.0, 0.0);
  jac = 1.0;
  for (int i = 0; i < N; i++) {
    if (i >= uCount) break;
    float fade = 1.0 - smoothstep(uB[i].w * 6.0, uB[i].w * 25.0, dist); // drop waves the far grid cannot resolve
    float a = uB[i].x * uAmp * fade, h = uB[i].y * min(uAmp, 1.0) * fade;
    float th = uA[i].z * dot(uA[i].xy, p0) - uA[i].w * t + uB[i].z;
    float c = cos(th), s = sin(th), ka = uA[i].z * a, kh = uA[i].z * h;
    P.x += h * uA[i].x * c;
    P.z += h * uA[i].y * c;
    P.y += a * s;
    n.x -= uA[i].x * ka * c;
    n.z -= uA[i].y * ka * c;
    n.y -= kh * s;
    jac -= kh * s;
  }
  n = normalize(n);
  return P;
}`;

export const OCEAN_VERTEX = /* glsl */ `
${GERSTNER}
uniform float uTime;
uniform vec3 uCenter;
varying vec3 vWorld;
varying vec3 vNormal;
varying float vFoam;
void main() {
  float jac;
  vWorld = gerstner(position.xz + uCenter.xz, length(position.xz), uTime, vNormal, jac);
  vFoam = 1.0 - smoothstep(0.3, 0.6, jac);
  gl_Position = projectionMatrix * viewMatrix * vec4(vWorld, 1.0);
}`;

// A tileable 256x256 map of short-wave slopes (RG) and bubble-foam texture (A), made once at start.
// Integer wave numbers keep every component periodic over the tile; mipmaps then filter it at a distance.
export function detailMap(size = 256, seed = 7) {
  const rand = mulberry32(seed), waves = [], bubbles = [];
  for (let i = 0; i < 56; i++) {
    const m = Math.round((rand() * 2 - 1) * 24), n = Math.round((rand() * 2 - 1) * 24);
    if (m || n) waves.push([m, n, Math.hypot(m, n) ** -1.6, rand() * 2 * Math.PI]);
  }
  for (let i = 0; i < 24; i++) bubbles.push([Math.round((rand() * 2 - 1) * 40), Math.round((rand() * 2 - 1) * 40), rand() * 2 * Math.PI]);
  const sx = new Float32Array(size * size), sz = new Float32Array(size * size), data = new Uint8Array(size * size * 4);
  let peak = 0;
  for (let j = 0; j < size; j++)
    for (let i = 0; i < size; i++) {
      const u = (2 * Math.PI * i) / size, v = (2 * Math.PI * j) / size, k = j * size + i;
      for (const [m, n, a, ph] of waves) {
        const c = a * Math.cos(m * u + n * v + ph);
        sx[k] += m * c;
        sz[k] += n * c;
      }
      peak = Math.max(peak, Math.abs(sx[k]), Math.abs(sz[k]));
      let b = 0;
      for (const [m, n, ph] of bubbles) b += Math.cos(m * u + n * v + ph);
      data[k * 4 + 3] = 255 * Math.min(Math.max(0.5 + b / 8, 0), 1);
    }
  for (let k = 0; k < size * size; k++) {
    data[k * 4] = 127.5 + (127.5 * sx[k]) / peak;
    data[k * 4 + 1] = 127.5 + (127.5 * sz[k]) / peak;
    data[k * 4 + 2] = 128;
  }
  return { data, size };
}

export const OCEAN_FRAGMENT = /* glsl */ `
uniform samplerCube uEnv;
uniform sampler2D uDetailMap;
uniform vec3 uSun, uSunColor, uDeep, uScatter, uFogColor;
uniform vec2 uWind;
uniform vec4 uShip;    // x, z, heading (rad), speed (m/s)
uniform vec4 uSplash[${FOAM_SPOTS}]; // x, z, age (s), strength: rings where boxes hit the water
uniform vec4 uFloat[${FOAM_SPOTS}];  // x, z, yaw (rad), half length (m): white water round floating boxes
uniform float uTime, uHs, uFogDensity, uFoamAmount, uRipple, uDay, uBowFoam;
varying vec3 vWorld;
varying vec3 vNormal;
varying float vFoam;

// detail slope from three scales of the tile, turned to the wind and drifting with it
vec2 detailSlope(vec2 p, float d) {
  mat2 toWind = mat2(uWind.y, uWind.x, -uWind.x, uWind.y);
  vec2 q = toWind * p, drift = vec2(0.0, uTime);
  vec2 s = (texture2D(uDetailMap, q / 7.0 + drift * 0.11).rg - 0.5) * 1.0
         + (texture2D(uDetailMap, q / 23.0 + drift * 0.045 + 0.37).rg - 0.5) * 0.8
         + (texture2D(uDetailMap, q / 71.0 + drift * 0.019 + 0.71).rg - 0.5) * 0.6;
  return (transpose(toWind) * s) * uRipple * 6.0 * mix(1.0, 0.35, smoothstep(200.0, 1500.0, d));
}

// white water against the hull and in the wake, in the ship's own frame
float shipFoam(vec2 p) {
  vec2 rel = p - uShip.xy;
  float fwd = rel.x * sin(uShip.z) - rel.y * cos(uShip.z) - 2.55; // metres ahead of midships
  float stb = rel.x * cos(uShip.z) + rel.y * sin(uShip.z);
  float body = max(abs(fwd) - 20.0, 0.0) / 71.0;
  float half_b = 12.7 * sqrt(max(1.0 - body * body, 0.0));
  float bow = smoothstep(20.0, 85.0, fwd);
  float hull = abs(fwd) < 91.0 ? (1.0 - smoothstep(0.5, 4.5 + 4.0 * uBowFoam * bow, abs(abs(stb) - half_b))) * (0.35 + (0.65 + uBowFoam) * bow) : 0.0;
  float aft = -fwd - 88.0, width = 8.0 + aft * 0.36; // wake widens behind the stern
  float wake = aft > 0.0 ? (1.0 - smoothstep(width * 0.25, width, abs(stb))) * exp(-aft / 380.0) : 0.0;
  // Kelvin arms from the bow: crests diverge at about 19.5 deg (tan = 0.354) and fade with distance; short
  // feathered crests sit just inside each arm, as seen from the air behind a moving ship
  float behind = 88.0 - fwd, edge = half_b + max(behind, 0.0) * 0.354, w = 1.2 + behind * 0.012;
  float arms = behind > 0.0 && behind < 500.0
    ? (exp(-pow((abs(stb) - edge) / w, 2.0)) * 0.45
       + smoothstep(0.7, 1.0, sin((behind * 0.94 - abs(stb) * 0.34) * 0.16)) * exp(-pow((abs(stb) - edge + 1.5 * w) / (1.6 * w), 2.0)) * 0.18)
      * exp(-behind / 170.0)
    : 0.0;
  return (hull + 0.8 * wake + arms) * smoothstep(0.5, 4.0, uShip.w);
}

float cargoFoam(vec2 p) {
  float f = 0.0;
  for (int i = 0; i < ${FOAM_SPOTS}; i++) {
    vec4 s = uSplash[i];
    float dist = length(p - s.xy), r = 2.0 + s.z * 3.5, fade = exp(-s.z / 4.0) * s.w;
    f += ((1.0 - smoothstep(0.0, 2.5, abs(dist - r))) + (1.0 - smoothstep(0.0, r, dist)) * exp(-s.z / 1.5)) * fade;
    vec4 b = uFloat[i];
    vec2 rel = p - b.xy;
    vec2 local = vec2(dot(rel, vec2(cos(b.z), -sin(b.z))), dot(rel, vec2(sin(b.z), cos(b.z)))); // across, along the box
    float e = length(vec2(local.x / 2.2, local.y / max(b.w, 0.1)));
    f += b.w > 0.0 ? (1.0 - smoothstep(1.0, 1.8, e)) * 0.8 : 0.0;
  }
  return f;
}

void main() {
  vec3 toCam = cameraPosition - vWorld;
  float d = length(toCam);
  vec3 v = toCam / d;

  vec2 slope = detailSlope(vWorld.xz, d);
  vec3 n = normalize(vNormal + vec3(-slope.x, 0.0, -slope.y));
  if (dot(n, v) < 0.0) n = normalize(n + v * (0.01 - dot(n, v)));

  float fres = 0.02 + 0.98 * pow(1.0 - max(dot(n, v), 0.0), 5.0);
  vec3 r = reflect(-v, n);
  r.y = max(r.y, 0.01);
  vec3 sky = textureCube(uEnv, r).rgb;

  float crest = clamp(vWorld.y / max(uHs, 0.5) + 0.25, 0.0, 1.0);
  float back = pow(max(dot(-v, uSun), 0.0), 3.0);
  vec3 night = mix(vec3(0.24, 0.3, 0.42), vec3(1.0), uDay);
  vec3 body = (uDeep * (0.25 + 0.4 * max(dot(n, uSun), 0.0)) + uScatter * crest * crest * (0.35 + 1.4 * back)) * night;
  vec3 col = mix(body, sky, fres);
  vec3 h = normalize(uSun + v);
  col += uSunColor * pow(max(dot(n, h), 0.0), mix(700.0, 80.0, smoothstep(100.0, 2500.0, d))) * (0.2 + fres) * 1.5 * uDay;

  // whitecaps where crests compress, broken into wind-stretched bubbles; plus hull and wake foam
  mat2 toWind = mat2(uWind.y, uWind.x, -uWind.x, uWind.y);
  vec2 q = toWind * vWorld.xz;
  float bubbles = texture2D(uDetailMap, q / vec2(9.0, 26.0) + vec2(0.0, uTime * 0.03)).a * 0.6
                + texture2D(uDetailMap, q / vec2(2.7, 6.0) + vec2(0.3, uTime * 0.08)).a * 0.4;
  float cover = vFoam * uFoamAmount * 1.6 + cargoFoam(vWorld.xz);
  float foam = clamp(cover - (1.0 - smoothstep(0.25, 0.7, bubbles)) * 0.9, 0.0, 1.0);
  // ship white water is broken up on a much larger, along-track scale, so it reads as streaky foam, not dots
  vec2 rel = vWorld.xz - uShip.xy, along = vec2(sin(uShip.z), -cos(uShip.z));
  vec2 shipUV = vec2(dot(rel, vec2(along.y, -along.x)) / 18.0, dot(rel, along) / 60.0);
  float streaks = texture2D(uDetailMap, shipUV + vec2(0.0, uTime * 0.01)).a * 0.6 + texture2D(uDetailMap, shipUV * 2.3 + 0.5).a * 0.4;
  foam = max(foam, clamp(shipFoam(vWorld.xz) * (0.25 + 0.95 * smoothstep(0.3, 0.8, streaks)), 0.0, 0.9));
  col = mix(col, vec3(0.8, 0.84, 0.86) * (0.6 + 0.4 * max(dot(vNormal, uSun), 0.0)) * mix(vec3(0.3, 0.36, 0.48), vec3(1.0), uDay), foam);

  col = mix(col, uFogColor, 1.0 - exp(-uFogDensity * uFogDensity * d * d));
  gl_FragColor = vec4(col, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

// Spray torn off breaking crests near the ship. Each point picks a new spot every lifetime and only shows
// where the sea surface there was folding (low Jacobian) when it was born, so spray follows the whitecaps.
export const SPRAY_VERTEX = /* glsl */ `
${GERSTNER}
attribute vec3 seed;
uniform float uTime, uStorm, uRadius, uWindSpeed, uPixels;
uniform vec2 uWind;
uniform vec3 uCenter;
varying float vAlpha;
void main() {
  float life = 1.8, cycle = floor(uTime / life + seed.z), age = (uTime / life + seed.z - cycle) * life;
  vec2 r = fract(seed.xy + cycle * vec2(0.618034, 0.414214));
  float rad = sqrt(r.y) * uRadius, ang = r.x * 6.2831853;
  vec3 n;
  float jac;
  vec3 P = gerstner(uCenter.xz + vec2(cos(ang), sin(ang)) * rad, rad, uTime - age, n, jac);
  float breaking = (1.0 - smoothstep(0.28, 0.45, jac)) * uStorm;
  vec3 pos = P + vec3(uWind.x, 0.0, uWind.y) * uWindSpeed * 0.45 * age + vec3(0.0, 3.0 * age - 4.9 * age * age, 0.0);
  vAlpha = breaking * (1.0 - age / life);
  vec4 mv = viewMatrix * vec4(pos, 1.0);
  gl_PointSize = breaking > 0.02 ? min(uPixels * (0.7 + 1.2 * age) / -mv.z, 96.0) : 0.0;
  gl_Position = projectionMatrix * mv;
}`;

export const SPRAY_FRAGMENT = /* glsl */ `
uniform float uLight;
varying float vAlpha;
void main() {
  float d = length(gl_PointCoord - 0.5);
  gl_FragColor = vec4(vec3(0.88, 0.91, 0.93) * uLight, vAlpha * 0.55 * (1.0 - smoothstep(0.1, 0.5, d)));
}`;
