import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { spectrumWaves, waveUniforms, detailWaves, OCEAN_VERTEX, OCEAN_FRAGMENT } from "./ocean.js";
import { sunPosition, sunVector, SKY_VERTEX, SKY_FRAGMENT } from "./sky.js";
import { deckLayout } from "./cargo-layout.js";
import { sampleAt, latest, toLocal, fromScene } from "./replay.js";

const q = new URLSearchParams(location.search);
const loader = new GLTFLoader();
// ?scene=<id> plays a storm scene from web/3d-scene; ?replay=<path> plays a recorded game session.
const sceneId = q.get("scene") ?? (q.has("replay") ? null : "biscay-20261022-21");
const [data, hull, boxes] = await Promise.all([
  fetch(sceneId ? `./scenes/${sceneId}.json` : q.get("replay")).then((r) => r.json()),
  loader.loadAsync("./assets/s175.glb"),
  loader.loadAsync("./assets/containers.glb"),
]);
const replay = sceneId ? fromScene(data) : data;
const start = sampleAt(replay, 0);
const startMs = Date.parse(replay.start_utc);
const duration = replay.t.at(-1);

// Dev only: ?hs=8&tp=12&from=270 freezes a sea the replay does not contain, ?hour=12 moves the sun to that UTC hour.
// Never shown to players as data.
const dev = ["hs", "tp", "from", "hour"].some((k) => q.has(k));
const sea = { hs: +(q.get("hs") ?? start.hs), tp: +(q.get("tp") ?? start.tp), fromDeg: +(q.get("from") ?? start.wave_from) };
// shortcut: wave periods and directions stay at the replay's first sample and only Hs follows the data;
// rebuild the spectrum with a cross-fade once a storm scenario changes Tp or direction a lot
const waves = spectrumWaves(sea);

const HORIZON = new THREE.Color("#8d99a2"), FOG_DENSITY = 0.00042;
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.95;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(HORIZON, FOG_DENSITY);
const sunDir = new THREE.Vector3();
const wind = new THREE.Vector2();
const fogNow = new THREE.Color();
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
  renderer.toneMappingExposure = THREE.MathUtils.lerp(3.2, 0.95, day); // the camera opens up at night
  nightAmbient.intensity = 1.4 * (1 - day);
  for (const f of floodlights) f.intensity = 9 * (1 - day);
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
function oceanGrid(segments = 400, radius = 6000) {
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
const oceanUniforms = {
  uA: { value: wu.a.map((v) => new THREE.Vector4(...v)) },
  uB: { value: wu.b.map((v) => new THREE.Vector4(...v)) },
  uDetail: { value: detailWaves({ windEast: start.wind_e, windNorth: start.wind_n }).map((v) => new THREE.Vector4(...v)) },
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
const ocean = new THREE.Mesh(oceanGrid(), new THREE.ShaderMaterial({ vertexShader: OCEAN_VERTEX, fragmentShader: OCEAN_FRAGMENT, uniforms: oceanUniforms }));
ocean.frustumCulled = false; // vertices move in the shader
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
const nightAmbient = new THREE.HemisphereLight("#7d93b0", "#0b1622", 0);
scene.add(nightAmbient);
for (const [color, x, y, z] of [["#fff6e0", 0, 18.8, -80.55], ["#fff6e0", 0, 28.2, 67.45], ["#ff3b30", -12.9, 22.2, 60.5],
  ["#34c759", 12.9, 22.2, 60.5], ["#fff6e0", 0, 7.0, 87.6]]) {
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.45, 12, 8), new THREE.MeshBasicMaterial({ color, toneMapped: false }));
  lamp.position.set(x, y, z);
  ship.add(lamp);
}
const floodlights = [[0, 23, 59, 0, 8, -20], [0, 18, -80, 0, 8, -30], [-12, 21, 60, -10, 8, 20], [12, 21, 60, 10, 8, 20]].map(([x, y, z, tx, ty, tz]) => {
  const f = new THREE.SpotLight("#ffe9c4", 0, 190, 0.75, 0.7, 0);
  f.position.set(x, y, z);
  f.target.position.set(tx, ty, tz);
  ship.add(f, f.target);
  return f;
});

const slots = new Map(); // unit id -> [instanced mesh, index] for each part of its box
const units = deckLayout(hull.scene.getObjectByName("CargoOrigin").position.toArray());
for (const size of [40, 20]) {
  const placed = units.filter((u) => u.size === size);
  boxes.scene.getObjectByName(`Container${size}`).traverse((part) => {
    if (!part.isMesh) return;
    const body = part.material.name === "ContainerBody";
    const material = body ? part.material.clone() : part.material;
    if (body) material.color.set("#ffffff"); // the instance colour carries the box colour
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
const lostAt = new Map(replay.events.flatMap((e) => e.unit_ids.map((id) => [id, e.time_s])));
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

// Pose from the simulation: NED heave is positive down, FSD roll positive starboard down, pitch positive bow up.
const RAD = Math.PI / 180;
function placeShip(s) {
  const [x, z] = toLocal(replay, s.lon, s.lat);
  ship.position.set(x, -s.heave, z);
  ship.rotation.set(s.pitch * RAD, -s.heading * RAD, -s.roll * RAD);
}

// --- playback controls
const ui = Object.fromEntries(["play", "scrub", "clock", "sea", "helm", "motion", "cargo", "weather", "fps", "scene", "credit"].map((id) => [id, document.getElementById(id)]));
let simT = 0, rate = 20, playing = true;
ui.scrub.max = duration;
ui.play.onclick = () => {
  if (simT >= duration) simT = 0;
  playing = !playing;
};
ui.scrub.oninput = () => (simT = +ui.scrub.value);
for (const b of document.querySelectorAll("[data-rate]"))
  b.onclick = () => {
    rate = +b.dataset.rate;
    document.querySelectorAll("[data-rate]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  };

const fmt = (s) => new Date(s * 1000).toISOString().slice(11, 19);
function showState(s) {
  const order = latest(replay.orders, s.t), cargo = latest(replay.cargo, s.t);
  const windSpeed = Math.hypot(s.wind_e, s.wind_n);
  ui.play.textContent = playing ? "Pause" : "Play";
  ui.clock.textContent = `${new Date(startMs + s.t * 1000).toISOString().slice(11, 19)} UTC · elapsed ${fmt(s.t)} / ${fmt(duration)}`;
  ui.helm.textContent = `Heading ${s.heading.toFixed(0)}° → ${order.heading_deg}° · speed ${replay.speed_label ?? "over ground"} ` +
    `${(s.sog * 3600 / 1852).toFixed(1)} → ${order.speed_knots.toFixed(1)} kn`;
  ui.motion.textContent = `Roll ${s.roll.toFixed(1)}° · pitch ${s.pitch.toFixed(1)}° · heave ${s.heave.toFixed(1)} m`;
  ui.sea.textContent = `${dev ? "DEV OVERRIDE · " : ""}Hs ${(dev ? sea.hs : s.hs).toFixed(2)} m · Tp ${(dev ? sea.tp : s.tp).toFixed(1)} s · ` +
    `waves from ${Math.round(dev ? sea.fromDeg : s.wave_from)}° · wind ${windSpeed.toFixed(1)} m/s`;
  ui.cargo.textContent = `Cargo ${(cargo.total_teu - cargo.lost_teu).toLocaleString()} / ${cargo.total_teu.toLocaleString()} TEU · grade ${cargo.grade}`;
  ui.weather.textContent = s.cloud === undefined ? "" : `Cloud ${Math.round(s.cloud)}% · rain ${s.precip.toFixed(1)} mm/h (ERA5)`;
}

ui.scene.value = sceneId ?? "replay";
ui.scene.onchange = () => (location.search = ui.scene.value === "replay" ? "?replay=./replays/menorca-20260909.json" : `?scene=${ui.scene.value}`);
ui.credit.textContent = [replay.note, ...(replay.credits ?? ["Recorded TIDE session. Generated using E.U. Copernicus Marine Service Information: " +
  "MEDSEA_MULTIYEAR_WAV_006_012, MEDSEA_MULTIYEAR_PHY_006_004, WIND_GLO_PHY_L4_NRT_012_004"]), "Training prototype, not a navigation tool."]
  .filter(Boolean).join(" · ");

function resize() {
  renderer.setSize(innerWidth, innerHeight);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();

placeShip(start);
updateSun(0);
camera.position.set(ship.position.x - Math.sin(start.heading * RAD) * 170 - 90, 55, ship.position.z + Math.cos(start.heading * RAD) * 170);
controls.target.set(ship.position.x, 8, ship.position.z);

let frames = 0, lastFps = performance.now(), lastFrame = lastFps, uiAt = 0;
const prev = new THREE.Vector3();
renderer.setAnimationLoop((now) => {
  const dt = Math.min((now - lastFrame) / 1000, 0.1);
  lastFrame = now;
  if (playing) simT = Math.min(simT + dt * rate, duration);
  if (simT >= duration) playing = false;
  const s = sampleAt(replay, simT);

  prev.copy(ship.position);
  placeShip(s);
  camera.position.x += ship.position.x - prev.x; // the orbit camera travels with the ship
  camera.position.z += ship.position.z - prev.z;
  controls.target.set(ship.position.x, 8, ship.position.z);

  wind.set(s.wind_e, -s.wind_n);
  const storm = THREE.MathUtils.smoothstep(wind.length(), 7, 20); // whitecaps start near Beaufort 4
  oceanUniforms.uTime.value = simT;
  oceanUniforms.uAmp.value = dev ? 1 : s.hs / sea.hs;
  oceanUniforms.uCenter.value.set(ship.position.x, 0, ship.position.z);
  oceanUniforms.uWind.value.copy(wind).normalize();
  oceanUniforms.uFoamAmount.value = storm;
  oceanUniforms.uRipple.value = 0.03 + 0.04 * storm;
  // shortcut: without scene cloud data (Menorca replay) the cover is a fixed artistic 0.55
  skyMaterial.uniforms.uCover.value = s.cloud === undefined ? 0.55 : 0.2 + (0.75 * s.cloud) / 100;
  skyMaterial.uniforms.uTime.value = simT;
  fogNow.copy(HORIZON).multiplyScalar(THREE.MathUtils.lerp(0.16, 1, day));
  scene.fog.color.copy(fogNow);
  oceanUniforms.uFogColor.value = fogNow;
  rain.geometry.setDrawRange(0, 2 * Math.round(RAIN_MAX * THREE.MathUtils.clamp((s.precip ?? 0) / 2, 0, 1)));
  rainUniforms.uCam.value.copy(camera.position);
  rainUniforms.uVel.value.set(wind.x, -9, wind.y); // drops fall at about 9 m/s and drift with the wind
  rainUniforms.uTime.value = now / 1000;
  rainUniforms.uLight.value = THREE.MathUtils.lerp(0.4, 1, day);
  updateCargo(simT);
  updateSun(simT);
  sky.position.copy(camera.position);
  sun.target.position.copy(ship.position);
  sun.position.copy(ship.position).addScaledVector(sunDir, 500);

  controls.update();
  renderer.render(scene, camera);

  if (now - uiAt > 100) {
    uiAt = now;
    if (document.activeElement !== ui.scrub) ui.scrub.value = simT;
    showState(s);
  }
  frames++;
  if (now - lastFps > 1000) {
    ui.fps.textContent = `${Math.round((frames * 1000) / (now - lastFps))} fps`;
    frames = 0;
    lastFps = now;
  }
});
