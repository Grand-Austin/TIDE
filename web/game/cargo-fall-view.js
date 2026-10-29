import * as THREE from "three";
import { cargoFall } from "./cargo-fall.js";

const SPRAY = 48, SPLASH_S = 3, MAX_SPLASHES = 40;

// Draws lost deck units along their cargoFall paths, with a burst of spray where each one hits the water.
// units: [{ id, size, color, position }]; parts: { [size]: [{ geometry, material, body }] }; lostAt: Map id -> s.
export class CargoFallView {
  constructor({ units, parts, lostAt, env }) {
    this.env = env;
    this.object = new THREE.Group();
    this.items = [];
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
    const spray = new Float32Array(MAX_SPLASHES * SPRAY * 3);
    this.spray = new THREE.Points(
      new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(spray, 3)),
      new THREE.PointsMaterial({ color: "#e8eef2", size: 0.9, transparent: true, opacity: 0.85, depthWrite: false }),
    );
    this.spray.frustumCulled = false;
    this.object.add(this.spray);
  }

  // True while a box is sliding or in the air, so a player can slow down to watch it.
  falling(t) {
    return this.items.some((it) => t >= it.lostAt && (!it.fall || t < it.fall.tImpact));
  }

  update(t) {
    const pos = this.spray.geometry.attributes.position;
    let splashes = 0;
    for (const it of this.items) {
      if (t >= it.lostAt && !it.fall) it.fall = cargoFall(it.unit, it.lostAt, this.env);
      const s = it.fall ? it.fall.at(t) : { phase: "deck" };
      const visible = s.phase !== "deck" && s.phase !== "gone";
      if (visible) MATRIX.compose(V.set(...s.position), Q.setFromEuler(E.set(...s.euler, "YXZ")), ONE);
      for (const mesh of it.meshes) {
        mesh.setMatrixAt(it.k, visible ? MATRIX : HIDDEN);
        mesh.instanceMatrix.needsUpdate = true;
      }
      const age = it.fall ? t - it.fall.tImpact : -1;
      if (age >= 0 && age < SPLASH_S && splashes < MAX_SPLASHES) this.#splash(pos, splashes++, it, age);
    }
    for (let i = splashes * SPRAY; i < MAX_SPLASHES * SPRAY; i++) pos.setXYZ(i, 0, -1e4, 0);
    pos.needsUpdate = true;
  }

  #splash(pos, slot, it, age) {
    const [x, y, z] = it.fall.impact, water = this.env.waterAt(x, z, it.fall.tImpact);
    for (let j = 0; j < SPRAY; j++) {
      const a = j * 2.399 + it.k, out = 2 + (j % 5), up = 4 + ((j * 7) % 9);
      const h = water + up * age - 4.9 * age * age;
      pos.setXYZ(slot * SPRAY + j, x + Math.cos(a) * out * age, h > water - 0.5 ? h : -1e4, z + Math.sin(a) * out * age);
    }
  }
}

const HIDDEN = new THREE.Matrix4().makeScale(0, 0, 0);
const MATRIX = new THREE.Matrix4(), V = new THREE.Vector3(), Q = new THREE.Quaternion(), E = new THREE.Euler(), ONE = new THREE.Vector3(1, 1, 1);
