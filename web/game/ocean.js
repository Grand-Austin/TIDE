// Gerstner sea built from one JONSWAP spectrum, scaled so its significant height equals the data's Hs.
// World axes: x east, y up, z south. The same sum runs here (height queries) and in the vertex shader (drawing).
import { NOISE } from "./sky.js";

const G = 9.81;
export const N_WAVES = 16;

function mulberry32(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function spectrumWaves({ hs, tp, fromDeg, spreadDeg = 35, steepness = 0.75, seed = 1 }) {
  const wp = (2 * Math.PI) / tp, gamma = 3.3, rand = mulberry32(seed);
  const travel = ((fromDeg + 180) * Math.PI) / 180;
  const w0 = 0.75 * wp, dw = (3.0 * wp - w0) / N_WAVES;
  const waves = [];
  for (let i = 0; i < N_WAVES; i++) {
    const w = w0 + (i + 0.5) * dw;
    const sigma = w <= wp ? 0.07 : 0.09;
    const s = w ** -5 * Math.exp(-1.25 * (wp / w) ** 4) * gamma ** Math.exp(-((w - wp) ** 2) / (2 * sigma ** 2 * wp ** 2));
    // shortcut: uniform ±spread instead of a cos-2s spreading function; fine for looks, revisit if directions matter
    const dir = travel + (rand() * 2 - 1) * (spreadDeg * Math.PI) / 180;
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

export const OCEAN_VERTEX = /* glsl */ `
#define N ${N_WAVES}
uniform vec4 uA[N];   // dir x, dir z, k, omega
uniform vec4 uB[N];   // amplitude, horizontal amplitude, phase, wavelength
uniform float uTime, uAmp;
uniform vec3 uCenter;
varying vec3 vWorld;
varying vec3 vNormal;
varying float vFoam;
void main() {
  vec2 p0 = position.xz + uCenter.xz;
  float dist = length(position.xz);
  vec3 P = vec3(p0.x, 0.0, p0.y);
  vec3 n = vec3(0.0, 1.0, 0.0);
  float jac = 1.0;
  for (int i = 0; i < N; i++) {
    float fade = 1.0 - smoothstep(uB[i].w * 6.0, uB[i].w * 25.0, dist); // drop waves the far grid cannot resolve
    float a = uB[i].x * uAmp * fade, h = uB[i].y * min(uAmp, 1.0) * fade;
    float th = uA[i].z * dot(uA[i].xy, p0) - uA[i].w * uTime + uB[i].z;
    float c = cos(th), s = sin(th), ka = uA[i].z * a, kh = uA[i].z * h;
    P.x += h * uA[i].x * c;
    P.z += h * uA[i].y * c;
    P.y += a * s;
    n.x -= uA[i].x * ka * c;
    n.z -= uA[i].y * ka * c;
    n.y -= kh * s;
    jac -= kh * s;
  }
  vWorld = P;
  vNormal = normalize(n);
  vFoam = smoothstep(0.55, 0.3, jac);
  gl_Position = projectionMatrix * viewMatrix * vec4(P, 1.0);
}`;

// Short wind ripples (0.6-12 m) that only bend the shading normal; the grid is too coarse to displace them.
export const N_DETAIL = 12;
export function detailWaves({ windEast, windNorth, seed = 7 }) {
  const rand = mulberry32(seed), downwind = Math.atan2(windEast, windNorth);
  return Array.from({ length: N_DETAIL }, (_, i) => {
    const k = (2 * Math.PI) / (0.6 * 20 ** (i / (N_DETAIL - 1))), dir = downwind + (rand() * 2 - 1);
    return [Math.sin(dir), -Math.cos(dir), k, Math.sqrt(G * k)];
  });
}

export const OCEAN_FRAGMENT = /* glsl */ `
#define ND ${N_DETAIL}
uniform samplerCube uEnv;
uniform vec4 uDetail[ND];  // dir x, dir z, k, omega
uniform vec3 uSun, uSunColor, uDeep, uScatter, uFogColor;
uniform vec2 uWind;
uniform float uTime, uHs, uFogDensity, uFoamAmount, uRipple;
varying vec3 vWorld;
varying vec3 vNormal;
varying float vFoam;
${NOISE}
void main() {
  vec3 toCam = cameraPosition - vWorld;
  float d = length(toCam);
  vec3 v = toCam / d;

  // ripples fade out once a wavelength covers only a few pixels, and the sun glint widens instead
  vec2 slope = vec2(0.0);
  float sharp = 0.0;
  for (int i = 0; i < ND; i++) {
    float lambda = 6.2831853 / uDetail[i].z;
    float fade = 1.0 - smoothstep(lambda * 80.0, lambda * 300.0, d);
    float th = uDetail[i].z * dot(uDetail[i].xy, vWorld.xz) - uDetail[i].w * uTime + float(i) * 1.7;
    slope += uDetail[i].xy * (uRipple * fade * cos(th));
    sharp += fade;
  }
  vec3 n = normalize(vNormal + vec3(-slope.x, 0.0, -slope.y));
  if (dot(n, v) < 0.0) n = normalize(n + v * (0.01 - dot(n, v)));

  float cosV = max(dot(n, v), 0.0);
  float fres = 0.02 + 0.98 * pow(1.0 - cosV, 5.0);
  vec3 r = reflect(-v, n);
  r.y = max(r.y, 0.01);
  vec3 sky = textureCube(uEnv, r).rgb;

  float crest = clamp(vWorld.y / max(uHs, 0.5) + 0.25, 0.0, 1.0);
  float back = pow(max(dot(-v, uSun), 0.0), 3.0);
  vec3 body = uDeep * (0.25 + 0.4 * max(dot(n, uSun), 0.0)) + uScatter * crest * crest * (0.35 + 1.4 * back);
  vec3 col = mix(body, sky, fres);
  vec3 h = normalize(uSun + v);
  col += uSunColor * pow(max(dot(n, h), 0.0), mix(60.0, 700.0, sharp / float(ND))) * (0.2 + fres) * 1.5;

  vec2 drift = uWind * uTime * 0.35;
  float streaks = fbm(vWorld.xz * 0.11 - drift * 0.11) * 0.6 + fbm(vWorld.xz * 0.6 - drift * 0.6) * 0.4;
  float foam = clamp(vFoam * uFoamAmount * 1.8 - (1.0 - smoothstep(0.3, 0.75, streaks)), 0.0, 1.0);
  col = mix(col, vec3(0.78, 0.82, 0.84) * (0.55 + 0.45 * max(dot(vNormal, uSun), 0.0)), foam);

  col = mix(col, uFogColor, 1.0 - exp(-uFogDensity * uFogDensity * d * d));
  gl_FragColor = vec4(col, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;
