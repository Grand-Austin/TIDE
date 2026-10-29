import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { spectrumWaves, waveUniforms, detailWaves, OCEAN_VERTEX, OCEAN_FRAGMENT } from "./ocean.js";
import { sunPosition, sunVector, SKY_VERTEX, SKY_FRAGMENT } from "./sky.js";
import { deckLayout } from "./cargo-layout.js";
import { sampleAt, latest, toLocal } from "./replay.js";

const q = new URLSearchParams(location.search);
const loader = new GLTFLoader();
const [replay, hull, boxes] = await Promise.all([
  fetch(q.get("replay") ?? "./replays/menorca-20260909.json").then((r) => r.json()),
  loader.loadAsync("./assets/s175.glb"),
  loader.loadAsync("./assets/containers.glb"),
]);
const start = sampleAt(replay, 0);
const startMs = Date.parse(replay.start_utc);
const duration = replay.t.at(-1);

// Dev only: ?hs=8&tp=12&from=270 freezes a sea the replay does not contain. Never shown to players as data.
const dev = ["hs", "tp", "from"].some((k) => q.has(k));
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

// shortcut: cloud cover is a fixed artistic choice; our Copernicus products carry no cloud data
const skyMaterial = new THREE.ShaderMaterial({
  vertexShader: SKY_VERTEX,
  fragmentShader: SKY_FRAGMENT,
  side: THREE.BackSide,
  depthWrite: false,
  uniforms: {
    uSun: { value: sunDir }, uWind: { value: wind }, uTime: { value: 0 }, uCover: { value: 0.55 },
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
  const s = sampleAt(replay, t);
  sunDir.set(...sunVector(sunPosition(new Date(startMs + t * 1000), s.lat, s.lon)));
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
    });
    ship.add(mesh);
  });
}

// Pose from the simulation: NED heave is positive down, FSD roll positive starboard down, pitch positive bow up.
const RAD = Math.PI / 180;
function placeShip(s) {
  const [x, z] = toLocal(replay, s.lon, s.lat);
  ship.position.set(x, -s.heave, z);
  ship.rotation.set(s.pitch * RAD, -s.heading * RAD, -s.roll * RAD);
}

// --- playback controls
const ui = Object.fromEntries(["play", "scrub", "clock", "sea", "helm", "motion", "cargo", "fps"].map((id) => [id, document.getElementById(id)]));
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
  ui.helm.textContent = `Heading ${s.heading.toFixed(0)}° → ${order.heading_deg}° · speed ${(s.sog * 3600 / 1852).toFixed(1)} → ${order.speed_knots.toFixed(1)} kn`;
  ui.motion.textContent = `Roll ${s.roll.toFixed(1)}° · pitch ${s.pitch.toFixed(1)}° · heave ${s.heave.toFixed(1)} m`;
  ui.sea.textContent = `${dev ? "DEV OVERRIDE · " : ""}Hs ${(dev ? sea.hs : s.hs).toFixed(2)} m · Tp ${(dev ? sea.tp : s.tp).toFixed(1)} s · ` +
    `waves from ${Math.round(dev ? sea.fromDeg : s.wave_from)}° · wind ${windSpeed.toFixed(1)} m/s`;
  ui.cargo.textContent = `Cargo ${(cargo.total_teu - cargo.lost_teu).toLocaleString()} / ${cargo.total_teu.toLocaleString()} TEU · grade ${cargo.grade}`;
}

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
  skyMaterial.uniforms.uTime.value = simT;
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
