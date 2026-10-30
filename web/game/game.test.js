import { expect, test } from "bun:test";
import { spectrumWaves, displace, heightAt, detailMap } from "./ocean.js";
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
  const settled = f.at(f.tImpact + Math.min(f.floatS, 15));
  expect(settled.phase).toBe("water");
  expect(settled.position[1]).toBeGreaterThan(-2.6);
  expect(settled.position[1]).toBeLessThan(0);
  expect(f.at(f.tImpact + f.floatS + 100).phase).toBe("gone");
});

test("about a third of the lost boxes stay afloat, the rest sink within a minute", () => {
  const env = { poseAt: () => ({ x: 0, y: 0, z: 0, heading: 0, pitch: 0, roll: 15 }), waterAt: () => 0, windAt: () => [10, 0] };
  const falls = deckLayout([-11, 7.7, 47.45]).map((u) => cargoFall(u, 0, env));
  const share = falls.filter((f) => f.floatS > 60).length / falls.length;
  expect(share).toBeGreaterThan(0.2);
  expect(share).toBeLessThan(0.4);
  const f = falls.find((x) => x.floatS > 60), a = f.at(f.tImpact + 60), b = f.at(f.tImpact + 120);
  expect(b.position[0] - a.position[0]).toBeCloseTo(10 * 0.035 * 60, 1); // drifts downwind (east) at the leeway rate
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
    copernicus_products: { waves: "W" }, weather: { credit: "ERA5" },
    cargo: { total_teu: 1200, units: [{ id: "deck-180", teu: 1 }, { id: "deck-282", teu: 2 }] },
    fields, samples: [row(0.5, 0), row(1, 0), row(1.5, 3)],
    orders: [{ t: 0, heading_deg: 286.7, speed_mps: 5 }],
    events: [{ t: 1.2, kind: "cargo_lost", detail: "2 units overboard", units: ["deck-180", "deck-282"] }],
  });
  expect(r.sog).toEqual([5, 5, 5]);
  expect(r.cargo.map((c) => c.lost_teu)).toEqual([0, 3]);
  expect(r.events[0].unit_ids).toEqual(["deck-180", "deck-282"]);
  expect(r.events[0].teu).toBe(3);
  expect(r.orders[0].speed_knots).toBeCloseTo(9.72, 2);
  expect(sampleAt(r, 0.75).cloud).toBe(100);
});

test("detail map tiles without a seam", () => {
  const { data, size } = detailMap(64);
  const at = (i, j, c) => data[(j * size + i) * 4 + c];
  let edge = 0, inner = 0;
  for (let j = 0; j < size; j++) {
    edge = Math.max(edge, Math.abs(at(size - 1, j, 0) - at(0, j, 0))); // last column next to the first
    for (let i = 0; i + 1 < size; i++) inner = Math.max(inner, Math.abs(at(i + 1, j, 0) - at(i, j, 0)));
  }
  expect(edge).toBeLessThanOrEqual(inner);
});

test("stacks lose boxes from the top down", () => {
  const { topDown } = require("./cargo-layout.js");
  const units = deckLayout([0, 0, 0]), drawn = topDown(units);
  // stack 0 holds deck-001, -003, -005, -007, -009 (tiers 0-4); the first two losses there are its bottom boxes
  expect([drawn("deck-001"), drawn("deck-003")]).toEqual(["deck-009", "deck-007"]);
  expect(drawn("deck-002")).toBe("deck-010"); // stack 1, its own count
  const on = new Set(units.map((u) => u.id));
  for (const u of units) on.delete(drawn(u.id)); // every loss of a whole deck, in any order, lands on a real box once
  expect(on.size).toBe(0);
});

test("passage map measures the track sailed", () => {
  const { trackGeometry, sampleIndex } = require("./minimap.js");
  const r = { t: [0, 1, 2], lon: [-11.5, -11.5, -11.5], lat: [51.5, 51.51, 51.52] };
  const { run } = trackGeometry(r);
  expect(run[2]).toBeCloseTo(2 * 1105.74, 0);
  expect([sampleIndex(r.t, 0.5), sampleIndex(r.t, 2), sampleIndex(r.t, 9)]).toEqual([0, 2, 2]);
});

test("passage map draws released forecast areas and the helm preview note", () => {
  const { forecastAreas, settleNote } = require("./minimap.js");
  const f = forecastAreas([{ sector_bbox: [-12, -11, 51, 52], hs_range_m: [8, 8.5] }]).features[0];
  expect(f.properties).toEqual({ hs: 8.5, label: "8–8.5 m" });
  expect(f.geometry.coordinates[0][2]).toEqual([-11, 52]);
  expect(settleNote({ heading_deg: 130, settled_s: 400, from_s: 100 }, 100)).toBe("Steady on 130° in about 7 min"); // settled_s counts from from_s
  expect(settleNote({ heading_deg: 130, settled_s: 400, from_s: 100 }, 485)).toBe("On the ordered 130°");
  expect(settleNote({ heading_deg: 130, settled_s: null, from_s: 0 }, 0)).toBe("Still turning to 130° after 8 min");
});

test("cargo losses group into map markers that say what the sea was doing", () => {
  const { lossMarkers, lossPopup } = require("./minimap.js");
  const n = 200, r = { t: [], lon: [], lat: [], heading: [], roll: [], pitch: [], heave: [], hs: [], tp: [], wave_from: [], wind_e: [], wind_n: [], sog: [],
    cargo: [{ t: 0, grade: 0 }, { t: 100, grade: 2 }],
    events: [{ time_s: 100, kind: "cargo_lost", unit_ids: ["a", "b", "c", "d", "e"], teu: 5, demand_g: 0.61 },
             { time_s: 130, kind: "cargo_lost", unit_ids: ["f", "g", "h", "i", "j"], teu: 10, demand_g: 0.72 },
             { time_s: 190, kind: "cargo_lost", unit_ids: ["k", "l", "m", "n", "o"], teu: 5, demand_g: 0.55 }] };
  for (let i = 0; i < n; i++) {
    r.t.push(i); r.lon.push(-11 + i * 1e-4); r.lat.push(51); r.heading.push(0); r.roll.push(i === 90 ? -17 : 3); r.pitch.push(0); r.heave.push(0);
    r.hs.push(8.2); r.tp.push(16); r.wave_from.push(270); r.wind_e.push(18); r.wind_n.push(0); r.sog.push(5);
  }
  expect(lossMarkers(r, 150).features.length).toBe(1); // later losses appear only once they happen
  const [first, second] = lossMarkers(r, 199).features.map((f) => f.properties);
  expect([first.boxes, first.teu, first.demand, second.boxes]).toEqual([10, 15, 0.72, 5]);
  expect(first.roll).toBe(-17); // largest roll in the half minute before the first loss
  expect(first.sea).toBe("beam seas, port");
  const html = lossPopup(first, Date.parse("2025-12-18T10:40:00Z"));
  expect(html).toContain("10:41 UTC · 10 boxes, 15 TEU overboard");
  expect(html).toContain("peak roll 17° to port");
  expect(html).toContain("0.72 g");
  expect(html).toContain("wind 35 kn from 270°");
});
