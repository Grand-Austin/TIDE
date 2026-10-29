import * as THREE from "three";

// Soft, ragged white-water points sized in metres; shared by the bow spray and the container splashes.
export function sprayMaterial() {
  return new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { uPixels: { value: 900 }, uLight: { value: 1 } },
    vertexShader: `attribute float alpha, size, seed; uniform float uPixels; varying float vA, vSeed;
      void main() {
        vA = alpha; vSeed = seed;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = alpha > 0.0 ? min(uPixels * size / -mv.z, 256.0) : 0.0;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `uniform float uLight; varying float vA, vSeed;
      void main() {
        vec2 c = gl_PointCoord - 0.5;
        float ragged = 0.7 + 0.3 * sin(c.x * 11.0 + vSeed * 6.3) * sin(c.y * 9.0 + vSeed * 4.1);
        float a = vA * smoothstep(0.5, 0.12, length(c)) * ragged;
        gl_FragColor = vec4(vec3(0.9, 0.94, 0.96) * uLight, a);
      }`,
  });
}

export function sprayPoints(count) {
  const g = new THREE.BufferGeometry();
  for (const [name, n] of [["position", 3], ["alpha", 1], ["size", 1], ["seed", 1]])
    g.setAttribute(name, new THREE.BufferAttribute(new Float32Array(count * n), n));
  g.attributes.seed.array.forEach((_, i, a) => (a[i] = Math.random()));
  const points = new THREE.Points(g, sprayMaterial());
  points.frustumCulled = false;
  return points;
}

// Spray where the bow meets the sea. Contact points on the bow are checked every frame against the water;
// the faster the bow drops into it, the more each sheds: a sheet of white water at the stem, droplets thrown out
// and up, and mist that the wind carries back over the deck. A look only: nothing here changes the numbers.
const MAX = 2200, G = 9.81;
// bow contact points in the ship model frame (x starboard, y up, -z forward)
const CONTACTS = [[0, 0, -90], [2.2, 0, -86], [-2.2, 0, -86], [4.5, 0, -80], [-4.5, 0, -80], [7, 0, -72], [-7, 0, -72]];
// kind: spawn per second at full strength, life (s), size (m) start..end, peak alpha, how fast the wind takes it
const KINDS = {
  sheet: { rate: 160, life: 1.3, size: [2.5, 5], alpha: 0.75, drag: 0.6 },
  drops: { rate: 260, life: 2.2, size: [0.5, 1.1], alpha: 0.85, drag: 0.8 },
  mist: { rate: 70, life: 3.5, size: [4, 10], alpha: 0.22, drag: 2.5 },
};

export class BowSpray {
  constructor(waterAt) {
    this.waterAt = waterAt;
    this.points = sprayPoints(MAX);
    this.vel = new Float32Array(MAX * 3);
    this.age = new Float32Array(MAX).fill(1e9);
    this.kind = new Array(MAX).fill(KINDS.drops);
    this.next = 0;
    this.slam = 0; // recent bow impact strength, 0..1, for the foam on the water
    this.last = CONTACTS.map(() => null); // previous clearance above the water per contact point
  }

  #emit(kind, p, out, strength, shipVelocity) {
    const j = this.next, pos = this.points.geometry.attributes.position.array;
    this.next = (j + 1) % MAX;
    this.age[j] = 0;
    this.kind[j] = kind;
    const spread = kind === KINDS.mist ? 4 : 2, lift = kind === KINDS.mist ? 2 + 3 * Math.random() : Math.random();
    pos.set([p.x + (Math.random() - 0.5) * spread, p.y + lift, p.z + (Math.random() - 0.5) * spread], j * 3);
    const [outV, upV] = kind === KINDS.sheet ? [2 + 4 * strength, 1 + 3 * strength]
      : kind === KINDS.drops ? [3 + 8 * strength * Math.random(), 4 + 12 * strength * Math.random()] : [1, 2 + 3 * Math.random()];
    this.vel.set([shipVelocity.x + out.x * outV, upV, shipVelocity.z + out.z * outV], j * 3);
  }

  // dt in simulated seconds; scrubbing backwards (dt < 0) clears the spray instead of running it backwards
  update(ship, t, dt, wind, shipVelocity) {
    const attr = this.points.geometry.attributes, pos = attr.position.array;
    if (dt <= 0 || dt > 2) {
      this.age.fill(1e9);
      this.last.fill(null);
      this.slam = 0;
    } else {
      let water = 0, slam = 0;
      CONTACTS.forEach(([x, y, z], i) => {
        const p = ship.localToWorld(V.set(x, y, z));
        const h = this.waterAt(p.x, p.z, t), clearance = p.y - h;
        water += h / CONTACTS.length;
        const drop = this.last[i] === null ? 0 : (this.last[i] - clearance) / dt; // m/s the bow sinks into the sea
        this.last[i] = clearance;
        const wet = THREE.MathUtils.smoothstep(-clearance, -3, 1);
        const strength = THREE.MathUtils.clamp(drop / 4, 0, 1) * wet + 0.08 * THREE.MathUtils.smoothstep(-clearance, -1.5, 0.5);
        slam = Math.max(slam, strength);
        p.y = h; // spray starts at the water line
        const out = OUT.set(Math.sign(x) || (Math.random() < 0.5 ? -1 : 1), 0, -0.4).transformDirection(ship.matrixWorld);
        for (const kind of Object.values(KINDS))
          for (let k = Math.round(kind.rate * strength * dt * (0.5 + Math.random())); k > 0; k--) this.#emit(kind, p, out, strength, shipVelocity);
      });
      this.slam = Math.max(slam, this.slam * Math.exp(-dt / 2));
      for (let j = 0; j < MAX; j++) {
        const kind = this.kind[j];
        if (this.age[j] >= kind.life) continue;
        this.age[j] += dt;
        const k = j * 3, drag = Math.min(dt * kind.drag, 1);
        this.vel[k] += (wind.x - this.vel[k]) * drag;
        this.vel[k + 2] += (wind.y - this.vel[k + 2]) * drag;
        this.vel[k + 1] -= (kind === KINDS.mist ? 0.15 : 1) * G * dt;
        for (let a = 0; a < 3; a++) pos[k + a] += this.vel[k + a] * dt;
        if (pos[k + 1] < water - 1.5) this.age[j] = kind.life; // spray stays near the bow, so its water level will do
      }
    }
    for (let j = 0; j < MAX; j++) {
      const kind = this.kind[j], f = this.age[j] / kind.life;
      attr.alpha.array[j] = f < 1 ? kind.alpha * Math.min(f * 8, 1) * (1 - f) : 0;
      attr.size.array[j] = kind.size[0] + (kind.size[1] - kind.size[0]) * Math.min(f, 1);
    }
    attr.position.needsUpdate = attr.alpha.needsUpdate = attr.size.needsUpdate = true;
  }
}

const V = new THREE.Vector3(), OUT = new THREE.Vector3();
