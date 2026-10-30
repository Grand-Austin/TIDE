import * as THREE from "three";
import { cargoFall } from "./cargo-fall.js";
import { sprayPoints } from "./bow-spray.js";
import { FOAM_SPOTS } from "./ocean.js";

// Each splash: a crown of droplets, a central column and a mist puff, all sized by how hard the box hit.
const CROWN = 56, COLUMN = 12, MIST = 8, PER_SPLASH = CROWN + COLUMN + MIST, SPLASH_S = 3.5, MAX_SPLASHES = 24;

const rnd = (j, k, n) => {
  const v = Math.sin(j * 91.345 + k * 47.853 + n * 13.37) * 43758.5453;
  return v - Math.floor(v);
};

// Draws lost deck units along their cargoFall paths, the splash where each one hits the water, and tells the sea
// where to put foam: an expanding ring at each fresh splash and white water washing round each floating box.
// units: [{ id, size, color, position }]; parts: { [size]: [{ geometry, material, body }] }; lostAt: Map id -> s.
export class CargoFallView {
  constructor({ units, parts, lostAt, env }) {
    this.env = env;
    this.object = new THREE.Group();
    this.items = [];
    this.splashSpots = Array.from({ length: FOAM_SPOTS }, () => new THREE.Vector4());
    this.floatSpots = Array.from({ length: FOAM_SPOTS }, () => new THREE.Vector4());
    const color = new THREE.Color();
    for (const [size, list] of Object.entries(parts)) {
      const lost = units.filter((u) => String(u.size) === size && lostAt.has(u.id));
      if (!lost.length) continue;
      const meshes = list.map((part) => {
        const mesh = new THREE.InstancedMesh(part.geometry, part.material, lost.length);
        mesh.castShadow = true;
        mesh.frustumCulled = false; // instances travel far from the mesh origin
        lost.forEach((u, k) => {
          mesh.setMatrixAt(k, HIDDEN);
          if (part.body) mesh.setColorAt(k, color.set(u.color));
        });
        this.object.add(mesh);
        return mesh;
      });
      lost.forEach((u, k) => this.items.push({ unit: u, lostAt: lostAt.get(u.id), meshes, k, fall: null }));
    }
    this.splash = sprayPoints(MAX_SPLASHES * PER_SPLASH);
    this.object.add(this.splash);
  }

  // Live play learns loss times as they happen: units built with lostAt Infinity get their time here.
  setLost(id, t) {
    for (const it of this.items) if (it.unit.id === id && it.lostAt !== t) [it.lostAt, it.fall] = [t, null];
  }

  // True while a box is sliding or in the air, so a player can slow down to watch it.
  falling(t) {
    return this.items.some((it) => t >= it.lostAt && (!it.fall || t < it.fall.tImpact));
  }

  update(t, near) {
    const attr = this.splash.geometry.attributes;
    let splashes = 0;
    const fresh = [], floating = [];
    for (const it of this.items) {
      if (t >= it.lostAt && !it.fall) it.fall = cargoFall(it.unit, it.lostAt, this.env);
      const s = it.fall ? it.fall.at(t) : { phase: "deck" };
      const visible = s.phase !== "deck" && s.phase !== "gone";
      if (visible) MATRIX.compose(V.set(...s.position), Q.setFromEuler(E.set(...s.euler, "YXZ")), ONE);
      for (const mesh of it.meshes) {
        mesh.setMatrixAt(it.k, visible ? MATRIX : HIDDEN);
        mesh.instanceMatrix.needsUpdate = true;
      }
      if (!it.fall) continue;
      const age = t - it.fall.tImpact;
      if (age >= 0 && age < SPLASH_S && splashes < MAX_SPLASHES) this.#splash(attr, splashes++, it, age);
      if (age >= 0 && age < 10) fresh.push([age, it]);
      if (s.phase === "water") floating.push([Math.hypot(s.position[0] - near.x, s.position[2] - near.z), s, it]);
    }
    for (let i = splashes * PER_SPLASH; i < MAX_SPLASHES * PER_SPLASH; i++) attr.alpha.array[i] = 0;
    attr.position.needsUpdate = attr.alpha.needsUpdate = attr.size.needsUpdate = true;

    fresh.sort((a, b) => a[0] - b[0]);
    floating.sort((a, b) => a[0] - b[0]);
    this.splashSpots.forEach((v, i) => {
      const f = fresh[i];
      if (!f) return v.set(0, 0, 1e6, 0);
      v.set(f[1].fall.impact[0], f[1].fall.impact[2], f[0], Math.min(f[1].fall.impactSpeed / 10, 1.5));
    });
    this.floatSpots.forEach((v, i) => {
      const f = floating[i];
      if (!f) return v.set(0, 0, 0, 0);
      const [, s, it] = f, half = it.unit.size === 40 ? 6.1 : 3.0;
      v.set(s.position[0], s.position[2], s.euler[1], s.sinking ? half * 0.5 : half); // a sinking box washes less
    });
  }

  #splash(attr, slot, it, age) {
    const [x, , z] = it.fall.impact, water = this.env.waterAt(x, z, it.fall.tImpact), k = it.k;
    const power = THREE.MathUtils.clamp(it.fall.impactSpeed / 12, 0.5, 1.4), f = age / SPLASH_S;
    const [we, wn] = this.env.windAt ? this.env.windAt(it.fall.tImpact) : [0, 0];
    for (let j = 0; j < PER_SPLASH; j++) {
      const i = slot * PER_SPLASH + j, a = rnd(j, k, 1) * 6.2832;
      let out, up, size, alpha, drift = 0;
      if (j < CROWN) [out, up, size, alpha] = [(3 + 4 * rnd(j, k, 2)) * power, (5 + 6 * rnd(j, k, 3)) * power, 0.6 + 0.6 * rnd(j, k, 4), 0.85];
      else if (j < CROWN + COLUMN) [out, up, size, alpha] = [0.5 + rnd(j, k, 2), (10 + 6 * rnd(j, k, 3)) * power, 1.2, 0.8];
      else [out, up, size, alpha, drift] = [1.5, 1.5, 3 + 6 * f, 0.28, 0.6];
      const h = water + up * age - (j < CROWN + COLUMN ? 4.9 : 0.3) * age * age;
      attr.position.array.set([x + Math.cos(a) * out * age + we * drift * age, h, z + Math.sin(a) * out * age - wn * drift * age], i * 3);
      attr.alpha.array[i] = h > water - 0.5 ? alpha * (1 - f) * Math.min(age * 10, 1) : 0;
      attr.size.array[i] = size;
    }
  }
}

const HIDDEN = new THREE.Matrix4().makeScale(0, 0, 0);
const MATRIX = new THREE.Matrix4(), V = new THREE.Vector3(), Q = new THREE.Quaternion(), E = new THREE.Euler(), ONE = new THREE.Vector3(1, 1, 1);
