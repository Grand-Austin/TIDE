import { expect, test } from "bun:test";
import { spectrumWaves, displace, heightAt } from "./ocean.js";
import { deckLayout } from "./cargo-layout.js";
import { sunPosition } from "./sky.js";
import { sampleAt, latest, fromScene } from "./replay.js";
import { cargoFall, shipToWorld } from "./cargo-fall.js";

test("panel numbers come from released cargo and sea state", async () => {
  globalThis.document ??= {}; // panel.js only touches the DOM inside createPanel
  const { cargoStatus, relativeSea, lossLog } = await import("./panel.js");
  expect(cargoStatus({ total_teu: 1200, lost_teu: 480, grade: 5 })).toMatchObject({ totalPct: 60, deckPct: 0 });
  expect(relativeSea(287, 287).label).toBe("head seas");
  expect(relativeSea(17, 287).label).toBe("beam seas, port");
  expect(relativeSea(62, 287).label).toBe("quartering seas, port");
  expect(relativeSea(100, 190).label).toBe("beam seas, starboard");
  const log = lossLog({ events: [{ time_s: 5, kind: "cargo_lost", unit_ids: ["deck-180"] }],
    cargo: [{ t: 0, grade: 0 }, { t: 5, grade: 1 }] }, 10);
  expect(log.map((e) => e.text)).toEqual(["1 unit overboard", "Cargo loss grade 1"]);
});

test("ship frame turns like the rendered ship", () => {
  // heading 90 (east): the bow (-z in the model) points to +x and starboard (+x) to +z (south)
  const east = { heading: 90, pitch: 0, roll: 0 };
  shipToWorld(east, [0, 0, -1]).forEach((v, i) => expect(v).toBeCloseTo([1, 0, 0][i], 9));
  shipToWorld(east, [1, 0, 0]).forEach((v, i) => expect(v).toBeCloseTo([0, 0, 1][i], 9));
  // starboard-down roll lowers the starboard side
  expect(shipToWorld({ heading: 0, pitch: 0, roll: 10 }, [10, 0, 0])[1]).toBeLessThan(0);
});

test("a lost box leaves the low side, splashes, floats, then sinks", () => {
  const env = { poseAt: (t) => ({ x: 0, y: 0, z: -5 * t, heading: 0, pitch: 0, roll: 20 }), waterAt: () => 0 };
  const f = cargoFall({ position: [3.9, 12.9, 10] }, 100, env);
  expect(f.side).toBe(1);
  expect(f.at(99).phase).toBe("deck");
  expect(f.at(100).position[0]).toBeCloseTo(shipToWorld(env.poseAt(100), [3.9, 12.9, 10])[0], 6);
  expect(f.at((100 + f.tEdge) / 2).phase).toBe("slide");
  const air = f.at((f.tEdge + f.tImpact) / 2);
  expect(air.phase).toBe("air");
  expect(air.position[0]).toBeGreaterThan(12);
  expect(f.impact[1] + 1.3).toBeLessThanOrEqual(0.01);
  const floating = f.at(f.tImpact + 30);
  expect(floating.phase).toBe("water");
  expect(floating.position[1]).toBeCloseTo(0.7 - 2.591, 6);
  expect(f.at(f.tImpact + 120 + 200).phase).toBe("gone");
});

test("replay interpolation passes through samples and turns the short way", () => {
  const cols = { lon: [3, 3.1, 3.2, 3.3], lat: [40, 40, 40, 40], sog: [3, 3, 3, 3], hs: [3, 4, 5, 6], tp: [8, 8, 8, 8],
    wind_e: [0, 0, 0, 0], wind_n: [0, 0, 0, 0], heading: [350, 10, 30, 50], wave_from: [0, 0, 0, 0],
    roll: [0, 2, -1, 3], pitch: [0, 0, 0, 0], heave: [0, 0, 0, 0] };
  const r = { t: [1, 2, 3, 4], ...cols };
  expect(sampleAt(r, 2).roll).toBeCloseTo(2, 9);
  expect(sampleAt(r, 2.5).hs).toBeCloseTo(4.5, 9);
  expect(sampleAt(r, 1.5).heading).toBeCloseTo(0, 9);
  expect(sampleAt(r, 0).t).toBe(1);
  expect(latest([{ t: 0, g: 0 }, { t: 5, g: 1 }], 4).g).toBe(0);
});

test("sun stands due south at 90 - lat + 23.44 degrees at solstice noon", () => {
  const p = sunPosition(new Date("2026-06-21T11:45:00Z"), 40.05, 3.65); // local apparent noon near 11:45Z
  expect(Math.abs(p.elevation - (90 - 40.05 + 23.44))).toBeLessThan(0.3);
  expect(Math.abs(p.azimuth - 180)).toBeLessThan(3);
});

test("sea keeps the data's significant height and never folds", () => {
  for (const hs of [3.56, 8, 12]) {
    const waves = spectrumWaves({ hs, tp: 8.39, fromDeg: 13.7 });
    expect(4 * Math.sqrt(waves.reduce((m0, v) => m0 + (v.a * v.a) / 2, 0))).toBeCloseTo(hs, 6);
    expect(waves.reduce((s, v) => s + v.k * v.h, 0)).toBeLessThan(1);
  }
});

test("height query lands on the displaced surface", () => {
  const waves = spectrumWaves({ hs: 8, tp: 11, fromDeg: 270 });
  for (const amp of [0.6, 1, 1.4]) {
    const [x, y, z] = displace(waves, 37, -120, 5.5, amp);
    expect(heightAt(waves, x, z, 5.5, amp)).toBeCloseTo(y, 2);
  }
});

test("all 320 deck units stand on a box or the hatch, without overlaps", () => {
  const units = deckLayout([-11, 7.7, 47.45]);
  expect(units.length).toBe(320);
  const halves = new Map(); // "tier:slot:half" -> unit id
  for (const u of units) {
    expect(Math.abs(u.position[0])).toBeLessThan(11);
    for (const half of u.size === 40 ? [0, 1] : [u.half]) {
      const key = `${u.tier}:${u.bay * 9 + u.row}:${half}`;
      expect(halves.has(key)).toBe(false);
      halves.set(key, u.id);
    }
  }
  for (const key of halves.keys()) {
    const [tier, slot, half] = key.split(":").map(Number);
    if (tier > 0) expect(halves.has(`${tier - 1}:${slot}:${half}`)).toBe(true);
  }
  expect(units.filter((u) => u.tier !== (Number(u.id.slice(5)) - 1) % 3).map((u) => u.id)).toEqual(["deck-320", "deck-318"]);
});

test("storm scene reads like a replay", () => {
  const fields = ["t", "lon", "lat", "heading", "roll", "pitch", "heave", "stw", "hs", "tp", "wave_from", "wind_e", "wind_n", "lost_teu", "grade", "cloud", "precip"];
  const row = (t, lost) => [t, -10.75, 48.5, 287, 1, 0, 0, 5, 12, 14, 287, 19, -14, lost, lost ? 1 : 0, 100, 0.3];
  const r = fromScene({
    start: "2026-10-22T20:40:00Z", plan_note: "demo", credit: "Generated using E.U. Copernicus Marine Service Information",
    copernicus_products: { waves: "W" }, weather: { credit: "ERA5" }, cargo: { total_teu: 1200 },
    fields, samples: [row(0.5, 0), row(1, 0), row(1.5, 3)],
    orders: [{ t: 0, heading_deg: 286.7, speed_mps: 5 }],
    events: [{ t: 1.2, kind: "cargo_lost", detail: "2 units overboard", units: ["deck-180", "deck-282"] }],
  });
  expect(r.sog).toEqual([5, 5, 5]);
  expect(r.cargo.map((c) => c.lost_teu)).toEqual([0, 3]);
  expect(r.events[0].unit_ids).toEqual(["deck-180", "deck-282"]);
  expect(r.orders[0].speed_knots).toBeCloseTo(9.72, 2);
  expect(sampleAt(r, 0.75).cloud).toBe(100);
});
