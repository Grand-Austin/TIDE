import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { spectrumWaves, waveUniforms, detailMap, heightAt, N_WAVES, OCEAN_VERTEX, OCEAN_FRAGMENT, SPRAY_VERTEX, SPRAY_FRAGMENT } from "./ocean.js";
import { BowSpray } from "./bow-spray.js";
import { CargoFallView } from "./cargo-fall-view.js";
import { createPanel } from "./panel.js";
import { createMinimap } from "./minimap.js";
import { loadField } from "./weather-layer.js";
import { sunPosition, sunVector, SKY_VERTEX, SKY_FRAGMENT } from "./sky.js";
import { deckLayout, topDown } from "./cargo-layout.js";
import { sampleAt, toLocal, fromScene } from "./replay.js";
import { startLive, liveOverlay, liveScenarios, helmGauge, turnRateText } from "./live.js";

const q = new URLSearchParams(location.search);
const loader = new GLTFLoader();
// ?scene=<id> plays a storm scene, ?replay=<path> a recorded game session, ?live=<scenario> a live game
// against game/live.py (uv run python -m game.live).
const liveId = q.get("live");
const sceneId = liveId ? null : q.get("scene") ?? (q.has("replay") ? null : "biscay-20260123-06");
const [data, hull, boxes] = await Promise.all([
  liveId ? null : fetch(sceneId ? `./scenes/${sceneId}.json` : q.get("replay")).then((r) => r.json()),
  loader.loadAsync("./assets/s175.glb"),
  loader.loadAsync("./assets/containers.glb"),
]);
const LIVE_BUFFER_S = 1.2; // wall seconds of session records kept ahead of the live scene
const lossQueue = []; // live: [unit id, t] reported by the session, applied in the frame loop
const live = liveId ? await startLive(liveId, { onLoss: (id, t) => lossQueue.push([id, t]) }) : null;
const replay = live ? live.replay : sceneId ? fromScene(data) : data;
const start = sampleAt(replay, 0);
const startMs = Date.parse(replay.start_utc);
const duration = live ? live.brief.budget_s : replay.t.at(-1);

// Dev only: ?hs=8&tp=12&from=270 freezes a sea the replay does not contain, ?hour=12 moves the sun to that UTC hour.
// Never shown to players as data.
const dev = ["hs", "tp", "from", "hour"].some((k) => q.has(k));
const sea = { hs: +(q.get("hs") ?? start.hs), tp: +(q.get("tp") ?? start.tp), fromDeg: +(q.get("from") ?? start.wave_from) };
// Hs scales the sea every frame (uAmp). When the data's Tp or direction drifts from the wave set in use, a new set is
// built and cross-faded in over FADE_S of scene time (followSea below); all sets share the reference Hs sea.hs.
let current = spectrumWaves(sea), next = null, fadeStart = 0, fadeSeed = 1;
let waves = current; // what heightAt and the GPU use: one set, or both weighted during a fade
const seaNow = { tp: sea.tp, fromDeg: sea.fromDeg };
const FADE_S = 20, DIR_STEP = 15, TP_STEP = 1.5;

const HORIZON = new THREE.Color("#8d99a2"), FOG_DENSITY = 0.00042;
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
// ?lite: for slower machines; no shadows, 1x pixels, a coarser sea grid and less crest spray. Looks only.
const lite = q.has("lite");
renderer.setPixelRatio(lite ? 1 : Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;
renderer.shadowMap.enabled = !lite;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(HORIZON, FOG_DENSITY);
const sunDir = new THREE.Vector3();
const wind = new THREE.Vector2();
const TINT = new THREE.Color(), fogNow = new THREE.Color(), NIGHT_TINT = new THREE.Color(0.16, 0.2, 0.3), WHITE = new THREE.Color(1, 1, 1);
let day = 1; // 0 at night, 1 in daylight: civil twilight in between

const skyMaterial = new THREE.ShaderMaterial({
  vertexShader: SKY_VERTEX,
  fragmentShader: SKY_FRAGMENT,
  side: THREE.BackSide,
  depthWrite: false,
  uniforms: {
    uSun: { value: sunDir }, uWind: { value: wind }, uTime: { value: 0 }, uCover: { value: 0.55 }, uDay: { value: 1 },
    uZenith: { value: new THREE.Color("#55636e") }, uHorizon: { value: HORIZON },
    uCloudLight: { value: new THREE.Color("#a7b0b6") }, uCloudDark: { value: new THREE.Color("#4a535a") },
  },
});
const sky = new THREE.Mesh(new THREE.SphereGeometry(9000, 48, 24), skyMaterial);
sky.frustumCulled = false;
scene.add(sky);

// The sky rendered into a cube map: water reflects it and the ship is lit by it. Redone as the sun moves.
const envScene = new THREE.Scene();
envScene.add(new THREE.Mesh(sky.geometry, skyMaterial));
const cubeTarget = new THREE.WebGLCubeRenderTarget(256, { type: THREE.HalfFloatType });
const cubeCamera = new THREE.CubeCamera(1, 20000, cubeTarget);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environmentIntensity = 0.8;

const sun = new THREE.DirectionalLight("#fff4e6", 2.2);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -120, right: 120, top: 120, bottom: -120, near: 10, far: 1200 });
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.4;
scene.add(sun, sun.target);

let sunAt = -Infinity;
function updateSun(t) {
  if (Math.abs(t - sunAt) < 120) return;
  sunAt = t;
  const s = sampleAt(replay, t), when = new Date(startMs + t * 1000);
  if (q.has("hour")) when.setUTCHours(+q.get("hour"));
  const pos = sunPosition(when, s.lat, s.lon);
  sunDir.set(...sunVector(pos));
  day = THREE.MathUtils.smoothstep(pos.elevation, -6, 6);
  skyMaterial.uniforms.uDay.value = oceanUniforms.uDay.value = day;
  sun.intensity = 2.2 * day;
  renderer.toneMappingExposure = THREE.MathUtils.lerp(1.7, 0.95, day); // the camera opens up a little at night
  nightAmbient.intensity = 2.2 * (1 - day);
  for (const f of floodlights) f.intensity = 1.6 * (1 - day);
  cubeCamera.update(renderer, envScene);
  scene.environment?.dispose();
  scene.environment = pmrem.fromCubemap(cubeTarget.texture).texture;
}

const camera = new THREE.PerspectiveCamera(50, 1, 1, 20000);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.495;
controls.minDistance = 30;
controls.maxDistance = 1500;

// Grid that is dense next to the ship (about 2.4 m) and coarse at the horizon (about 85 m).
function oceanGrid(segments = 480, radius = 6000) {
  const map = (u) => radius * u * (0.08 + 0.92 * u * u);
  const pos = [], idx = [];
  for (let j = 0; j <= segments; j++)
    for (let i = 0; i <= segments; i++) pos.push(map((i / segments) * 2 - 1), 0, map((j / segments) * 2 - 1));
  for (let j = 0; j < segments; j++)
    for (let i = 0; i < segments; i++) {
      const a = j * (segments + 1) + i, b = a + 1, c = a + segments + 1, d = c + 1;
      idx.push(a, c, b, b, c, d);
    }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  return g;
}

const wu = waveUniforms(waves);
const detail = detailMap();
const detailTexture = new THREE.DataTexture(detail.data, detail.size, detail.size, THREE.RGBAFormat);
Object.assign(detailTexture, { wrapS: THREE.RepeatWrapping, wrapT: THREE.RepeatWrapping, generateMipmaps: true,
  minFilter: THREE.LinearMipmapLinearFilter, magFilter: THREE.LinearFilter, anisotropy: renderer.capabilities.getMaxAnisotropy(), needsUpdate: true });
const oceanUniforms = {
  uA: { value: Array.from({ length: 2 * N_WAVES }, (_, i) => new THREE.Vector4(...(wu.a[i] ?? [0, 0, 0, 0]))) },
  uB: { value: Array.from({ length: 2 * N_WAVES }, (_, i) => new THREE.Vector4(...(wu.b[i] ?? [0, 0, 0, 1]))) },
  uCount: { value: N_WAVES },
  uDetailMap: { value: detailTexture },
  uShip: { value: new THREE.Vector4() },
  uSplash: { value: [] },
  uFloat: { value: [] },
  uBowFoam: { value: 0 },
  uEnv: { value: cubeTarget.texture },
  uTime: { value: 0 },
  uAmp: { value: 1 },
  uCenter: { value: new THREE.Vector3() },
  uSun: { value: sunDir },
  uSunColor: { value: new THREE.Color("#fff1dd") },
  uDeep: { value: new THREE.Color("#06141b") },
  uScatter: { value: new THREE.Color("#1d6b6a") },
  uFogColor: { value: HORIZON },
  uWind: { value: new THREE.Vector2() },
  uHs: { value: sea.hs },
  uFogDensity: { value: FOG_DENSITY },
  uFoamAmount: { value: 0 },
  uRipple: { value: 0.03 },
  uDay: { value: 1 },
};
const ocean = new THREE.Mesh(oceanGrid(lite ? 240 : 480), new THREE.ShaderMaterial({ vertexShader: OCEAN_VERTEX, fragmentShader: OCEAN_FRAGMENT, uniforms: oceanUniforms }));
ocean.frustumCulled = false; // vertices move in the shader

// Spray off breaking crests within 350 m of the ship, worked out on the GPU from the same wave sum.
const SPRAY = lite ? 2000 : 6000, spraySeeds = new Float32Array(SPRAY * 3).map(() => Math.random());
const sprayGeometry = new THREE.BufferGeometry();
sprayGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(SPRAY * 3), 3));
sprayGeometry.setAttribute("seed", new THREE.BufferAttribute(spraySeeds, 3));
const sprayUniforms = {
  uA: oceanUniforms.uA, uB: oceanUniforms.uB, uAmp: oceanUniforms.uAmp, uCount: oceanUniforms.uCount, uTime: oceanUniforms.uTime, uCenter: oceanUniforms.uCenter,
  uWind: { value: new THREE.Vector2() }, uStorm: { value: 0 }, uRadius: { value: 350 }, uWindSpeed: { value: 0 }, uPixels: { value: 900 }, uLight: { value: 1 },
};
const crestSpray = new THREE.Points(sprayGeometry, new THREE.ShaderMaterial({ vertexShader: SPRAY_VERTEX, fragmentShader: SPRAY_FRAGMENT,
  uniforms: sprayUniforms, transparent: true, depthWrite: false }));
crestSpray.frustumCulled = false;
scene.add(crestSpray);
scene.add(ocean);

const ship = new THREE.Group();
ship.rotation.order = "YXZ"; // heading, then pitch, then roll
hull.scene.traverse((o) => {
  if (o.isMesh) o.castShadow = o.receiveShadow = true;
});
ship.add(hull.scene);
scene.add(ship);

// Night on board. Navigation lights sit at their usual places (masthead, sidelights on the bridge wings, stern);
// deck floodlights and a faint ambient are a viewing aid, not something the data says.
const nightAmbient = new THREE.HemisphereLight("#8fa6cc", "#1a2433", 0);
scene.add(nightAmbient);
for (const [color, x, y, z] of [["#fff6e0", 0, 18.8, -80.55], ["#fff6e0", 0, 28.2, 67.45], ["#ff3b30", -12.9, 22.2, 60.5],
  ["#34c759", 12.9, 22.2, 60.5], ["#fff6e0", 0, 7.0, 87.6]]) {
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.45, 12, 8), new THREE.MeshBasicMaterial({ color, toneMapped: false }));
  lamp.position.set(x, y, z);
  ship.add(lamp);
}
const floodlights = [[0, 23, 59, 0, 8, -20], [0, 18, -80, 0, 8, -30], [-12, 21, 60, -10, 8, 20], [12, 21, 60, 10, 8, 20]].map(([x, y, z, tx, ty, tz]) => {
  const f = new THREE.SpotLight("#ffcf8a", 0, 140, 0.6, 0.8, 0);
  f.position.set(x, y, z);
  f.target.position.set(tx, ty, tz);
  ship.add(f, f.target);
  return f;
});

const slots = new Map(); // unit id -> [instanced mesh, index] for each part of its box
const parts = { 40: [], 20: [] }; // box geometry and materials, shared with the falling boxes
const units = deckLayout(hull.scene.getObjectByName("CargoOrigin").position.toArray());
for (const size of [40, 20]) {
  const placed = units.filter((u) => u.size === size);
  boxes.scene.getObjectByName(`Container${size}`).traverse((part) => {
    if (!part.isMesh) return;
    const body = part.material.name === "ContainerBody";
    const material = body ? part.material.clone() : part.material;
    if (body) material.color.set("#ffffff"); // the instance colour carries the box colour
    parts[size].push({ geometry: part.geometry, material, body });
    const mesh = new THREE.InstancedMesh(part.geometry, material, placed.length);
    mesh.castShadow = mesh.receiveShadow = true;
    const m = new THREE.Matrix4(), c = new THREE.Color();
    placed.forEach((u, k) => {
      mesh.setMatrixAt(k, m.makeTranslation(...u.position));
      if (body) mesh.setColorAt(k, c.set(u.color));
      if (!slots.has(u.id)) slots.set(u.id, []);
      slots.get(u.id).push([mesh, k]);
    });
    ship.add(mesh);
  });
}

// Units leave the deck at their cargo_lost time; scrubbing back puts them back.
const drawnId = topDown(units);
const lostAt = live ? new Map(units.map((u) => [u.id, Infinity])) // live: every unit may go; times arrive later
  : new Map([...replay.events].sort((a, b) => a.time_s - b.time_s).flatMap((e) => e.unit_ids.map((id) => [drawnId(id), e.time_s])));
const onDeck = new Map(), HIDDEN = new THREE.Matrix4().makeScale(0, 0, 0), placeAt = new THREE.Matrix4();
function updateCargo(t) {
  for (const u of units) {
    const on = !(lostAt.get(u.id) <= t);
    if (onDeck.get(u.id) === on) continue;
    onDeck.set(u.id, on);
    for (const [mesh, k] of slots.get(u.id)) {
      mesh.setMatrixAt(k, on ? placeAt.makeTranslation(...u.position) : HIDDEN);
      mesh.instanceMatrix.needsUpdate = true;
    }
  }
}

// ...and continue along their fall paths: slide off the low side, fall, splash, float, sink.
const poseAt = (t) => {
  const s = sampleAt(replay, t), [x, z] = toLocal(replay, s.lon, s.lat);
  return { x, y: -s.heave, z, heading: s.heading, pitch: s.pitch, roll: s.roll };
};
let ampT = NaN, amp = 1; // Hs scale at the time last asked; boxes query the same t many times a frame
const waterAt = (x, z, t) => {
  if (t !== ampT) [ampT, amp] = [t, dev ? 1 : sampleAt(replay, t).hs / sea.hs];
  return heightAt(waves, x, z, t, amp);
};
const windAt = (t) => {
  const s = sampleAt(replay, t);
  return [s.wind_e, s.wind_n];
};
const fallView = new CargoFallView({ units, parts, lostAt, env: { poseAt, waterAt, windAt } });
oceanUniforms.uSplash.value = fallView.splashSpots;
oceanUniforms.uFloat.value = fallView.floatSpots;
scene.add(fallView.object);
const bowSpray = new BowSpray(waterAt);
scene.add(bowSpray.points);
const shipVelocity = new THREE.Vector3();
let lastSimT = 0;

// Rain streaks around the camera, as dense as the scene's ERA5 precipitation. None without precipitation data.
const RAIN_MAX = 8000;
const rainGeometry = new THREE.BufferGeometry();
const seeds = new Float32Array(RAIN_MAX * 6), ends = new Float32Array(RAIN_MAX * 2);
for (let i = 0; i < RAIN_MAX; i++) {
  const x = Math.random(), y = Math.random(), z = Math.random();
  seeds.set([x, y, z, x, y, z], i * 6);
  ends[i * 2 + 1] = 1;
}
rainGeometry.setAttribute("position", new THREE.BufferAttribute(seeds, 3));
rainGeometry.setAttribute("aEnd", new THREE.BufferAttribute(ends, 1));
const rainUniforms = { uCam: { value: new THREE.Vector3() }, uVel: { value: new THREE.Vector3(0, -9, 0) }, uTime: { value: 0 }, uLight: { value: 1 } };
const rain = new THREE.LineSegments(rainGeometry, new THREE.ShaderMaterial({
  uniforms: rainUniforms, transparent: true, depthWrite: false,
  vertexShader: `attribute float aEnd; uniform vec3 uCam, uVel; uniform float uTime;
    void main() {
      vec3 p = mod(position * 90.0 + uVel * uTime, 90.0) - 45.0;
      gl_Position = projectionMatrix * viewMatrix * vec4(uCam + p - normalize(uVel) * 0.9 * aEnd, 1.0);
    }`,
  fragmentShader: `uniform float uLight; void main() { gl_FragColor = vec4(vec3(0.75, 0.8, 0.85) * uLight, 0.35); }`,
}));
rain.frustumCulled = false;
scene.add(rain);

function pushWaves(list) {
  const u = waveUniforms(list);
  u.a.forEach((v, i) => oceanUniforms.uA.value[i].set(...v));
  u.b.forEach((v, i) => oceanUniforms.uB.value[i].set(...v));
  oceanUniforms.uCount.value = list.length;
}
// Follow the data's peak period and wave direction: start a cross-fade when they drift past DIR_STEP or TP_STEP.
function followSea(s, t) {
  if (dev) return;
  if (!next) {
    const turn = Math.abs((((s.wave_from - seaNow.fromDeg) % 360) + 540) % 360 - 180);
    if (turn < DIR_STEP && Math.abs(s.tp - seaNow.tp) < TP_STEP) return;
    next = spectrumWaves({ hs: sea.hs, tp: s.tp, fromDeg: s.wave_from, seed: ++fadeSeed });
    [fadeStart, seaNow.tp, seaNow.fromDeg] = [t, s.tp, s.wave_from];
  }
  const f = THREE.MathUtils.clamp((t - fadeStart) / FADE_S, 0, 1);
  if (f >= 1) [current, next, waves] = [next, null, next];
  else waves = [...current.map((v) => ({ ...v, a: v.a * (1 - f), h: v.h * (1 - f) })), ...next.map((v) => ({ ...v, a: v.a * f, h: v.h * f }))];
  pushWaves(waves);
}

// Pose from the simulation: NED heave is positive down, FSD roll positive starboard down, pitch positive bow up.
const RAD = Math.PI / 180;
function placeShip(s) {
  const [x, z] = toLocal(replay, s.lon, s.lat);
  ship.position.set(x, -s.heave, z);
  ship.rotation.set(s.pitch * RAD, -s.heading * RAD, -s.roll * RAD);
}

// --- playback controls
const ui = Object.fromEntries(["play", "scrub", "fps", "scene", "credit", "slowmo", "panel", "panel-toggle"].map((id) => [id, document.getElementById(id)]));
let simT = 0, rate = 5, playing = true, liveSlow = false;
ui.scrub.max = duration;
ui.play.onclick = () => {
  if (live) return live.pause(!live.state.paused);
  if (simT >= duration) simT = 0;
  playing = !playing;
};
ui.scrub.oninput = () => (simT = +ui.scrub.value);
for (const b of document.querySelectorAll("[data-rate]"))
  b.onclick = () => {
    rate = +b.dataset.rate;
    if (live) live.rate(rate === 60 ? 0 : rate); // live: the fastest button runs the simulation flat out
    document.querySelectorAll("[data-rate]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  };

const updatePanel = createPanel({ replay, startMs, duration, dev, sea });
const field = live ? null : await loadField(data.storm?.id ?? replay.storm_id); // replays only: see weather-layer.js
const updateMap = createMinimap(document.getElementById("map"), { replay, duration, live, field });
const mapProgress = document.getElementById("map-progress"), dockTime = document.getElementById("dock-time");
const turnNow = document.getElementById("p-turn");
const mmss = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
ui.fps.hidden = !q.has("fps"); // ?fps shows the frame rate
let slowUntil = 0; // after a cargo grade rise
ui["panel-toggle"].onclick = () => {
  const open = ui.panel.dataset.collapsed === "true";
  ui.panel.dataset.collapsed = String(!open);
  ui["panel-toggle"].setAttribute("aria-expanded", String(open));
};

// every exported storm scene, the recorded Menorca game, and live scenarios when game/live.py serves the page
const index = await fetch("./scenes/index.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);
// grouped so the playable storms come first: they only appear when game/live.py serves the page
const group = (label, options) => {
  const g = Object.assign(document.createElement("optgroup"), { label });
  g.append(...options);
  return g;
};
const playable = (await liveScenarios()).map((x) => new Option(`▶ Play: ${x.title}`, `live:${x.id}`));
const replays = (index?.storms ?? []).filter((x) => x.scene).map((x) =>
  new Option(`Storm ${x.peak.slice(0, 10)} · ${x.area} · Hs ${x.hs_m} m · ${x.max_wind_kn} kn`, x.id));
if (index) ui.scene.replaceChildren(
  ...(playable.length ? [group("Play a storm (you steer)", playable)] : []),
  group("Watch a storm replay (fixed demo orders)", replays),
  group("Recorded game", [new Option("Menorca 9 Sep 2026", "replay")]));
// a replay while playable storms exist: offer to steer this storm (or the first playable one) from the title card
const mode = document.getElementById("mode"), modeNote = document.getElementById("mode-note"), cta = document.getElementById("cta");
if (live) [mode.textContent, mode.className, modeNote.textContent] = ["Live", "chip live", "you have the helm"];
else if (!sceneId) modeNote.textContent = "recorded game";
if (!live && playable.length) {
  const same = playable.find((o) => o.value === `live:${sceneId}`) ?? playable[0];
  cta.href = `?live=${same.value.slice(5)}`;
  cta.textContent = `▶ Steer ${same.value === `live:${sceneId}` ? "this storm" : same.text.replace("▶ Play: ", "")} yourself`;
  cta.hidden = false;
}
// opened through game/live.py without a choice: go straight to the first playable storm
if (!location.search && playable.length) location.search = `?live=${playable[0].value.slice(5)}`;
ui.scene.value = live ? `live:${liveId}` : sceneId ?? "replay";
const keep = ["lite", "fps"].filter((k) => q.has(k)).map((k) => `&${k}`).join(""); // view flags survive a scene change
ui.scene.onchange = () => (location.search = (ui.scene.value === "replay" ? "?replay=./replays/menorca-20260909.json"
  : ui.scene.value.startsWith("live:") ? `?live=${ui.scene.value.slice(5)}` : `?scene=${ui.scene.value}`) + keep);
const overlay = live ? liveOverlay() : null;
if (live) {
  ui.scrub.disabled = true;
  const helm = helmGauge();
  // On-screen helm for mouse and touch, in the dock where the replay slider sits (live time cannot be scrubbed):
  // the same orders as the arrow keys; a click steps 5 degrees or 1 kn.
  const ctl = Object.assign(document.createElement("div"), { className: "seg helm-ctl" });
  ctl.setAttribute("role", "group");
  ctl.setAttribute("aria-label", "Helm orders");
  const speedOut = Object.assign(document.createElement("span"), { className: "spd mono" });
  const order = (label, title, dHeading, dSpeed) => Object.assign(document.createElement("button"), {
    type: "button", textContent: label, title, onclick: () => (live.steer(dHeading, dSpeed), helm.show(live), showSpeed()) });
  const showSpeed = () => (speedOut.textContent = `${live.helm.speed.toFixed(1)} kn`);
  ctl.append(order("◀ 5°", "Turn to port 5° (←)", -5, 0), order("5° ▶", "Turn to starboard 5° (→)", 5, 0),
    order("−", "Slower, 1 kn (↓ 0.5 kn)", 0, -1), speedOut, order("+", "Faster, 1 kn (↑ 0.5 kn)", 0, 1));
  showSpeed();
  ui.scrub.hidden = true;
  ui.play.after(ctl);
  setInterval(() => live.poll().then(() => (overlay.update(live), helm.update(live), showSpeed())).catch(() => {}), 400);
  // Captured before the scene picker or a focused button sees the key, so arrows always steer the ship
  // (a focused <select> would otherwise switch scenes). Typing in a text field is left alone.
  addEventListener("keydown", (e) => {
    if (overlay.open || e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    const step = e.shiftKey ? 1 : 5;
    const turn = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, 0.5], ArrowDown: [0, -0.5] }[e.key];
    if (!turn && e.code !== "Space") return;
    e.preventDefault();
    e.stopPropagation();
    document.activeElement?.blur();
    if (e.code === "Space") return live.pause(!live.state.paused);
    live.steer(...turn);
    helm.show(live);
  }, true);
}
// the mission or replay note goes in the title card; the full credits open from the short sources line
document.getElementById("objective").textContent = replay.note ?? "";
ui.credit.replaceChildren(...[...(replay.credits ?? ["Recorded TIDE session. Generated using E.U. Copernicus Marine Service Information: " +
  "MEDSEA_MULTIYEAR_WAV_006_012, MEDSEA_MULTIYEAR_PHY_006_004, WIND_GLO_PHY_L4_NRT_012_004"]), "Training prototype, not a navigation tool."]
  .map((text) => Object.assign(document.createElement("p"), { textContent: text })));

function resize() {
  renderer.setSize(innerWidth, innerHeight);
  // pixels per metre at 1 m distance, for spray points
  sprayUniforms.uPixels.value = bowSpray.points.material.uniforms.uPixels.value = fallView.splash.material.uniforms.uPixels.value =
    (innerHeight * renderer.getPixelRatio()) / (2 * Math.tan((camera.fov * Math.PI) / 360));
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

placeShip(start);
updateSun(0);
// low over the quarter, so the sky, rain and horizon are in view from the start (drag up for the deck)
// Camera views around the ship: bearing from the bow (deg, clockwise), distance and height in metres. A view keeps
// its place relative to the bow as the ship turns, until the player drags the camera; keys 1-5 pick one too.
const VIEWS = {
  quarter: { bearing: 140, distance: 230, height: 28 }, // low over the starboard quarter: sky, rain and horizon
  beam: { bearing: 90, distance: 240, height: 14 }, // side on: the roll
  bow: { bearing: 25, distance: 230, height: 22 }, // ahead, looking back at the bow in the seas
  stern: { bearing: 180, distance: 210, height: 45 }, // astern: the course and the waves' angle to it
  top: { bearing: 180, distance: 40, height: 420 }, // from above: the deck stowage and the losses
};
let viewLock = "quarter";
const viewAt = (name, heading) => {
  const v = VIEWS[name], dir = (heading + v.bearing) * RAD;
  return new THREE.Vector3(ship.position.x + Math.sin(dir) * v.distance, v.height, ship.position.z - Math.cos(dir) * v.distance);
};
const viewButtons = document.querySelectorAll("#views button");
const setView = (name) => {
  viewLock = name;
  for (const b of viewButtons) b.setAttribute("aria-pressed", String(b.dataset.view === name));
};
for (const b of viewButtons) b.onclick = () => setView(b.dataset.view);
controls.addEventListener("start", () => setView(null)); // the player's own drag or zoom ends the view's hold
addEventListener("keydown", (e) => {
  const name = Object.keys(VIEWS)[+e.key - 1];
  if (!name || e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  setView(name);
});
camera.position.copy(viewAt("quarter", start.heading));
controls.target.set(ship.position.x, 14, ship.position.z);
setView("quarter");

// ?film&t=1384&view=beam&rate=1: a clean shot for screen recording (pitch video): no interface, starting at t
// (scene seconds) at the given rate, slow motion off, in one of the VIEWS. A replay only; live play ignores it.
if (q.has("film") && !live) {
  document.body.classList.add("film");
  simT = Math.max(0, Math.min(duration, +(q.get("t") ?? 0)));
  rate = +(q.get("rate") ?? 1);
  ui.slowmo.checked = false;
  if (VIEWS[q.get("view")]) setView(q.get("view"));
  camera.position.copy(viewAt(viewLock ?? "quarter", sampleAt(replay, simT).heading));
}

let frames = 0, lastFps = performance.now(), lastFrame = lastFps, uiAt = 0;
const prev = new THREE.Vector3();
renderer.setAnimationLoop((now) => {
  const dt = Math.min((now - lastFrame) / 1000, 0.1);
  lastFrame = now;
  const slow = ui.slowmo.checked && (fallView.falling(simT) || now < slowUntil); // watch each fall at 2x at most
  if (live && slow !== liveSlow) { // live: the session itself runs at 1x while boxes fall, then back to the chosen rate
    liveSlow = slow;
    if (rate > 1) live.rate(slow ? 1 : rate === 60 ? 0 : rate).catch(() => {});
  }
  if (live) {
    // live: play at the session's own pace, about LIVE_BUFFER_S of wall time behind its newest record, so the
    // 400 ms polls and a slower server moment (a helm preview) never run the scene dry; the gap steers the pace
    const rate = live.state?.paused ? 0 : Math.max(+live.state?.view.achieved_rate || 0, 0.5);
    const lag = replay.t.at(-1) - simT, want = 1 + rate * LIVE_BUFFER_S;
    const pace = lag < 0.3 ? 0 : rate * THREE.MathUtils.clamp(lag / want, 0.6, 1.6);
    simT = Math.min(replay.t.at(-1), simT + dt * Math.max(pace, lag > want * 2 ? lag : 0)); // far behind: catch up
    for (const [id, t] of lossQueue.splice(0)) {
      const d = drawnId(id);
      lostAt.set(d, t);
      fallView.setLost(d, t);
    }
  } else {
    if (playing) simT = Math.min(simT + dt * (slow ? Math.min(rate, 2) : rate), duration);
    if (simT >= duration) playing = false;
  }
  const s = sampleAt(replay, simT);

  prev.copy(ship.position);
  placeShip(s);
  camera.position.x += ship.position.x - prev.x; // the orbit camera travels with the ship
  camera.position.z += ship.position.z - prev.z;
  controls.target.set(ship.position.x, 14, ship.position.z);
  if (viewLock) camera.position.lerp(viewAt(viewLock, s.heading), Math.min(1, dt * 2.5)); // glide to the view, then hold it

  wind.set(s.wind_e, -s.wind_n);
  const storm = THREE.MathUtils.smoothstep(wind.length(), 7, 20); // whitecaps start near Beaufort 4
  oceanUniforms.uTime.value = simT;
  oceanUniforms.uAmp.value = dev ? 1 : s.hs / sea.hs;
  followSea(s, simT);
  oceanUniforms.uCenter.value.set(ship.position.x, 0, ship.position.z);
  oceanUniforms.uWind.value.copy(wind).normalize();
  oceanUniforms.uFoamAmount.value = storm;
  oceanUniforms.uRipple.value = 0.03 + 0.04 * storm;
  // shortcut: without scene cloud data (Menorca replay) the cover is a fixed artistic 0.55
  skyMaterial.uniforms.uCover.value = s.cloud === undefined ? 0.55 : 0.2 + (0.75 * s.cloud) / 100;
  skyMaterial.uniforms.uTime.value = simT;
  fogNow.copy(HORIZON).multiply(TINT.lerpColors(NIGHT_TINT, WHITE, day));
  scene.fog.color.copy(fogNow);
  oceanUniforms.uFogColor.value = fogNow;
  rain.geometry.setDrawRange(0, 2 * Math.round(RAIN_MAX * THREE.MathUtils.clamp((s.precip ?? 0) / 2, 0, 1)));
  rainUniforms.uCam.value.copy(camera.position);
  rainUniforms.uVel.value.set(wind.x, -9, wind.y); // drops fall at about 9 m/s and drift with the wind
  rainUniforms.uTime.value = now / 1000;
  rainUniforms.uLight.value = THREE.MathUtils.lerp(0.4, 1, day);
  updateCargo(simT);
  fallView.update(simT, ship.position);
  ship.updateMatrixWorld();
  const [a, b] = [poseAt(simT - 0.5), poseAt(simT + 0.5)];
  bowSpray.update(ship, simT, simT - lastSimT, wind, shipVelocity.set(b.x - a.x, 0, b.z - a.z));
  lastSimT = simT;
  oceanUniforms.uBowFoam.value = bowSpray.slam;
  oceanUniforms.uShip.value.set(ship.position.x, ship.position.z, s.heading * RAD, s.sog);
  sprayUniforms.uWind.value.copy(oceanUniforms.uWind.value);
  sprayUniforms.uWindSpeed.value = wind.length();
  sprayUniforms.uStorm.value = storm;
  sprayUniforms.uLight.value = bowSpray.points.material.uniforms.uLight.value = fallView.splash.material.uniforms.uLight.value =
    THREE.MathUtils.lerp(0.35, 1, day);
  updateSun(simT);
  sky.position.copy(camera.position);
  sun.target.position.copy(ship.position);
  sun.position.copy(ship.position).addScaledVector(sunDir, 500);

  controls.update();
  renderer.render(scene, camera);

  if (now - uiAt > 100) {
    uiAt = now;
    if (document.activeElement !== ui.scrub) ui.scrub.value = simT;
    const paused = live ? live.state?.paused : !playing;
    ui.play.textContent = paused ? "▶" : "❚❚";
    ui.play.setAttribute("aria-label", paused ? (live ? "Resume" : "Play") : "Pause");
    dockTime.textContent = `${mmss(simT)} / ${mmss(duration)}`;
    if (updatePanel(s, now)) slowUntil = now + 2500;
    turnNow.textContent = `Rate ${turnRateText(s.turn_rate)}`;
    mapProgress.textContent = updateMap(simT, s);
  }
  frames++;
  if (now - lastFps > 1000) {
    ui.fps.textContent = `${Math.round((frames * 1000) / (now - lastFps))} fps`;
    frames = 0;
    lastFps = now;
  }
});
