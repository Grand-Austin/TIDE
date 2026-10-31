// Where a lost deck unit is at any time: it slides off the low side, falls, floats and sinks.
// A pure function of time, so scrubbing works; no three.js, so any page can reuse it.
// The simulation decides which unit is lost and when (cargo_lost); this only draws a plausible path.
//
// env.poseAt(t)       -> { x, y, z, heading, pitch, roll } ship pose in world metres (x east, y up, z south) and degrees
// env.waterAt(x, z, t) -> water surface height (m)
// env.windAt(t)        -> [east, north] wind (m/s), optional: floating boxes drift with it
// unit                -> { position: [x, y, z] } box bottom centre in the ship frame (x starboard, y up, -z forward)

const G = 9.81, RAD = Math.PI / 180;
const EDGE = 14.0; // box centre is clear of the 25.4 m beam
const HALF_HEIGHT = 1.3, BOX_HEIGHT = 2.591;
const TUMBLE = 1.2; // rad/s while airborne
// shortcut: about a third of the boxes stay afloat (real ones can float for days, low in the water);
// the rest plunge, bob up and go down end first within a minute. FLOATER_S keeps the sea from filling up.
const FLOATER_SHARE = 0.3, FLOATER_S = 900, SINK_RATE = 0.15, GONE_DEPTH = 10, LEEWAY = 0.035;

// Stable per-box numbers in [0, 1) from its deck position, so a replay always looks the same.
const hash = (x, z, k) => {
  const v = Math.sin(x * 12.9898 + z * 78.233 + k * 37.719) * 43758.5453;
  return v - Math.floor(v);
};

// Rotate a ship-frame vector into the world like three.js Euler(pitch, -heading, -roll, "YXZ").
export function shipToWorld(pose, [x, y, z]) {
  const p = pose.pitch * RAD, h = -pose.heading * RAD, r = -pose.roll * RAD;
  [x, y] = [x * Math.cos(r) - y * Math.sin(r), x * Math.sin(r) + y * Math.cos(r)];
  [y, z] = [y * Math.cos(p) - z * Math.sin(p), y * Math.sin(p) + z * Math.cos(p)];
  [x, z] = [x * Math.cos(h) + z * Math.sin(h), -x * Math.sin(h) + z * Math.cos(h)];
  return [x, y, z];
}

const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

export function cargoFall(unit, lostAt, env) {
  const p0 = env.poseAt(lostAt);
  const side = p0.roll >= 0 ? 1 : -1; // positive roll is starboard down
  const accel = Math.max(G * Math.sin(Math.abs(p0.roll) * RAD), 1.5);
  const [x0, y0, z0] = unit.position;
  const slideS = Math.sqrt((2 * Math.max(EDGE - side * x0, 0.5)) / accel);
  const tEdge = lostAt + slideS;

  const local = (dt) => [x0 + side * 0.5 * accel * dt * dt, y0, z0];
  const pe = env.poseAt(tEdge);
  const start = add([pe.x, pe.y, pe.z], shipToWorld(pe, local(slideS)));
  const before = env.poseAt(tEdge - 0.5), after = env.poseAt(tEdge + 0.5);
  const push = shipToWorld(pe, [side * accel * slideS, 0, 0]);
  const v0 = [after.x - before.x + push[0], after.y - before.y + push[1], after.z - before.z + push[2]];
  const flight = (dt) => [start[0] + v0[0] * dt, start[1] + v0[1] * dt - 0.5 * G * dt * dt, start[2] + v0[2] * dt];

  let airS = 8;
  for (let dt = 0; dt < 8; dt += 0.05) {
    const p = flight(dt);
    if (p[1] + HALF_HEIGHT <= env.waterAt(p[0], p[2], tEdge + dt)) {
      airS = dt;
      break;
    }
  }
  const tImpact = tEdge + airS, impact = flight(airS);
  const spin = side * TUMBLE * airS, impactSpeed = Math.hypot(v0[0], v0[1] - G * airS, v0[2]);
  const floater = hash(x0, z0, 1) < FLOATER_SHARE;
  const floatS = floater ? FLOATER_S : 20 + 40 * hash(x0, z0, 2);
  const freeboard = floater ? 0.25 + 0.5 * hash(x0, z0, 3) : 0.6;
  const endFirst = hash(x0, z0, 4) < 0.5 ? 1 : -1;
  const [we, wn] = env.windAt ? env.windAt(tImpact) : [0, 0];

  return {
    side, tEdge, tImpact, impact, impactSpeed, floatS,
    at(t) {
      if (t < lostAt) return { phase: "deck" };
      if (t < tEdge) {
        const pose = env.poseAt(t);
        const position = add([pose.x, pose.y, pose.z], shipToWorld(pose, local(t - lostAt)));
        return { phase: "slide", position, euler: [pose.pitch * RAD, -pose.heading * RAD, -pose.roll * RAD] };
      }
      if (t < tImpact) {
        const dt = t - tEdge;
        return { phase: "air", position: flight(dt), euler: [pe.pitch * RAD, -pe.heading * RAD, -pe.roll * RAD - side * TUMBLE * dt] };
      }
      const age = t - tImpact, keep = 8 * (1 - Math.exp(-age / 8)); // momentum that dies out in the water
      const x = impact[0] + v0[0] * keep + we * LEEWAY * age, z = impact[2] + v0[2] * keep - wn * LEEWAY * age;
      const sink = Math.max(0, age - floatS) * SINK_RATE;
      if (sink > GONE_DEPTH) return { phase: "gone" };
      // it plunges under on impact, then bobs back up to its floating level
      const plunge = Math.min(impactSpeed * 0.3, 4) * Math.exp(-age * 0.9) * Math.cos(age * 2.2);
      const h = env.waterAt(x, z, t);
      const sx = (env.waterAt(x + 2, z, t) - env.waterAt(x - 2, z, t)) / 4;
      const sz = (env.waterAt(x, z + 2, t) - env.waterAt(x, z - 2, t)) / 4;
      const nose = endFirst * Math.min(sink / 5, 1) * 0.9; // a sinking box goes down one end first
      return {
        phase: "water", floater, sinking: sink > 0,
        position: [x, h - BOX_HEIGHT + freeboard - sink - plunge, z],
        euler: [Math.atan(sz) * 0.7 + nose, -pe.heading * RAD + age * 0.004 * side, -pe.roll * RAD - spin - Math.atan(sx) * 0.7],
      };
    },
  };
}
