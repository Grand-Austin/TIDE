import * as THREE from "three";

// Spray thrown up where the bow meets the sea. Contact points on the bow are checked every frame against the water
// height; the faster the bow drops into the water (or the deeper it sits), the more spray each point sheds.
// Spray is a look only: it does not change the ship or the numbers.

const MAX = 1500, LIFE = 2.4, G = 9.81;
// bow contact points in the ship model frame (x starboard, y up, -z forward) and the side each one throws to
const CONTACTS = [[0, 0, -90], [2.2, 0, -86], [-2.2, 0, -86], [4.5, 0, -80], [-4.5, 0, -80], [7, 0, -72], [-7, 0, -72]];

export class BowSpray {
  constructor(waterAt) {
    this.waterAt = waterAt;
    this.pos = new Float32Array(MAX * 3);
    this.vel = new Float32Array(MAX * 3);
    this.age = new Float32Array(MAX).fill(LIFE);
    this.alpha = new Float32Array(MAX);
    this.next = 0;
    this.last = CONTACTS.map(() => null); // previous clearance above the water per contact point
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(this.pos, 3));
    g.setAttribute("alpha", new THREE.BufferAttribute(this.alpha, 1));
    this.points = new THREE.Points(g, new THREE.ShaderMaterial({
      transparent: true, depthWrite: false,
      uniforms: { uPixels: { value: 900 }, uLight: { value: 1 } },
      vertexShader: `attribute float alpha; uniform float uPixels; varying float vA;
        void main() { vA = alpha; vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = alpha > 0.0 ? uPixels * 1.6 / -mv.z : 0.0; gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `uniform float uLight; varying float vA;
        void main() { float d = length(gl_PointCoord - 0.5); gl_FragColor = vec4(vec3(0.9, 0.93, 0.95) * uLight, vA * 0.6 * smoothstep(0.5, 0.1, d)); }`,
    }));
    this.points.frustumCulled = false;
  }

  // dt in simulated seconds; scrubbing backwards (dt < 0) clears the spray instead of running it backwards
  update(ship, t, dt, wind, shipVelocity) {
    if (dt <= 0 || dt > 2) {
      this.age.fill(LIFE);
      this.last.fill(null);
    } else {
      let water = 0;
      CONTACTS.forEach(([x, y, z], i) => {
        const p = ship.localToWorld(V.set(x, y, z));
        const h = this.waterAt(p.x, p.z, t), clearance = p.y - h;
        water += h / CONTACTS.length;
        const drop = this.last[i] === null ? 0 : (this.last[i] - clearance) / dt; // m/s the bow sinks into the sea
        this.last[i] = clearance;
        const strength = THREE.MathUtils.clamp(drop / 4, 0, 1) * THREE.MathUtils.smoothstep(-clearance, -3, 1) +
          0.06 * THREE.MathUtils.smoothstep(-clearance, -1.5, 0.5); // a steady bow wave while it sits in the water
        const side = Math.sign(x) || (Math.random() < 0.5 ? -1 : 1);
        const out = OUT.set(side, 0, -0.3).transformDirection(ship.matrixWorld);
        for (let k = Math.round(strength * 300 * dt * (1 + Math.random())); k > 0; k--) {
          const j = this.next, speed = 3 + 9 * strength * Math.random();
          this.next = (j + 1) % MAX;
          this.age[j] = Math.random() * 0.2;
          this.pos.set([p.x + (Math.random() - 0.5) * 3, Math.max(p.y, p.y - clearance) + Math.random(), p.z + (Math.random() - 0.5) * 3], j * 3);
          this.vel.set([shipVelocity.x + out.x * speed, 4 + 10 * strength * Math.random(), shipVelocity.z + out.z * speed], j * 3);
        }
      });
      for (let j = 0; j < MAX; j++) {
        if (this.age[j] >= LIFE) continue;
        this.age[j] += dt;
        const k = j * 3, drag = Math.min(dt * 1.2, 1);
        this.vel[k] += (wind.x - this.vel[k]) * drag;
        this.vel[k + 2] += (wind.y - this.vel[k + 2]) * drag;
        this.vel[k + 1] -= G * dt;
        for (let a = 0; a < 3; a++) this.pos[k + a] += this.vel[k + a] * dt;
        if (this.pos[k + 1] < water - 1.5) this.age[j] = LIFE; // spray stays near the bow, so the bow's water level will do
      }
    }
    for (let j = 0; j < MAX; j++) this.alpha[j] = this.age[j] < LIFE ? 1 - this.age[j] / LIFE : 0;
    this.points.geometry.attributes.position.needsUpdate = true;
    this.points.geometry.attributes.alpha.needsUpdate = true;
  }
}

const V = new THREE.Vector3(), OUT = new THREE.Vector3();
