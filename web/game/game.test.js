import { test } from "node:test";
import assert from "node:assert/strict";
import { spectrumWaves, displace, heightAt, detailMap } from "./ocean.js";
import { deckLayout, topDown } from "./cargo-layout.js";
import { sunPosition } from "./sky.js";
import { sampleAt, latest, fromScene, turnRateAt } from "./replay.js";
import { cargoFall, shipToWorld } from "./cargo-fall.js";
import { turnInfo, turnNote, turnRateText, shipLossNote, beaufort, seaState, bulletin } from "./live.js";
import { forecastField } from "./weather-layer.js";
import { trackGeometry, sampleIndex, forecastAreas, settleNote, lossMarkers, lossPopup, plannedRoute } from "./minimap.js";
import { OCEAN_TRACK, LE_HAVRE, HALIFAX, routeLengthNm, routeBounds, routeFeature } from "./ocean-route.js";
import { nearbyPorts, rangeRing, portPopup } from "./port-guide.js";
import { DEEPWATER_PORTS } from "./deepwater-ports.js";
import { haversineNm } from "./geo.js";

// Small Jest/Bun-style surface over node:assert, so the existing expectations still read naturally under node --test.
function expect(actual) {
  return {
    toBe: (expected) => assert.strictEqual(actual, expected),
    toEqual: (expected) => assert.deepStrictEqual(actual, expected),
    toBeCloseTo: (expected, digits = 2) =>
      assert.ok(Math.abs(actual - expected) < 0.5 * 10 ** -digits, `expected ${actual} to be close to ${expected}`),
    toMatchObject: (expected) => {
      for (const [key, value] of Object.entries(expected)) expect(actual[key]).toEqual(value);
    },
    toBeLessThan: (expected) => assert.ok(actual < expected, `expected ${actual} < ${expected}`),
    toBeGreaterThan: (expected) => assert.ok(actual > expected, `expected ${actual} > ${expected}`),
    toBeLessThanOrEqual: (expected) => assert.ok(actual <= expected, `expected ${actual} <= ${expected}`),
    toBeGreaterThanOrEqual: (expected) => assert.ok(actual >= expected, `expected ${actual} >= ${expected}`),
    toBeUndefined: () => assert.strictEqual(actual, undefined),
    toContain: (expected) => assert.ok(actual.includes(expected), `expected ${JSON.stringify(actual)} to contain ${JSON.stringify(expected)}`),
  };
}

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
  const halves = new Map(); // "tier:stack:half" -> unit id
  for (const u of units) {
    expect(Math.abs(u.position[0])).toBeLessThan(11);
    for (const half of u.size === 40 ? [0, 1] : [0]) {
      const key = `${u.tier}:${u.stack}:${half}`;
      expect(halves.has(key)).toBe(false);
      halves.set(key, u.id);
    }
  }
  for (const key of halves.keys()) {
    const [tier, stack, half] = key.split(":").map(Number);
    if (tier > 0) expect(halves.has(`${tier - 1}:${stack}:${half}`)).toBe(true);
  }
  // Five tiers per stack: unit NNN's tier follows i = 10 * (stack // 2) + stack % 2 + 2 * tier.
  expect(units.filter((u) => u.tier !== Math.floor(((Number(u.id.slice(5)) - 1) % 10) / 2)).map((u) => u.id)).toEqual([]);
});

test("storm scene reads like a replay", () => {
  const fields = ["t", "lon", "lat", "heading", "turn_rate", "roll", "pitch", "heave", "stw", "hs", "tp", "wave_from", "wind_e", "wind_n", "lost_teu", "grade", "cloud", "precip"];
  const row = (t, lost) => [t, -10.75, 48.5, 287, 0.2, 1, 0, 0, 5, 12, 14, 287, 19, -14, lost, lost ? 1 : 0, 100, 0.3];
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
  expect(sampleAt(r, 0.75).turn_rate).toBeCloseTo(0.2, 9); // exported actual rate, not inferred from heading
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
  const units = deckLayout([0, 0, 0]), drawn = topDown(units);
  // stack 0 holds deck-001, -003, -005, -007, -009 (tiers 0-4); the first two losses there are its bottom boxes
  expect([drawn("deck-001"), drawn("deck-003")]).toEqual(["deck-009", "deck-007"]);
  expect(drawn("deck-002")).toBe("deck-010"); // stack 1, its own count
  const on = new Set(units.map((u) => u.id));
  for (const u of units) on.delete(drawn(u.id)); // every loss of a whole deck, in any order, lands on a real box once
  expect(on.size).toBe(0);
});

test("passage map measures the track sailed", () => {
  const r = { t: [0, 1, 2], lon: [-11.5, -11.5, -11.5], lat: [51.5, 51.51, 51.52] };
  const { run } = trackGeometry(r);
  expect(run[2]).toBeCloseTo(2 * 1105.74, 0);
  expect([sampleIndex(r.t, 0.5), sampleIndex(r.t, 2), sampleIndex(r.t, 9)]).toEqual([0, 2, 2]);
});

test("passage map draws released forecast areas and the helm preview note", () => {
  const f = forecastAreas([{ sector_bbox: [-12, -11, 51, 52], hs_range_m: [8, 8.5], wind_range_mps: [17.5, 22.5], trend: "increasing" }]).features[0];
  expect(f.properties).toEqual({ hs: 8.5, label: "8–8.5 m ↑\nF8–9" });
  expect(f.geometry.coordinates[0][2]).toEqual([-11, 52]);
  expect(settleNote({ heading_deg: 130, settled_s: 400, from_s: 100 }, 100)).toBe("Steady on 130° in about 7 min"); // settled_s counts from from_s
  expect(settleNote({ heading_deg: 130, settled_s: 400, from_s: 100 }, 485)).toBe("On the ordered 130°");
  expect(settleNote({ heading_deg: 130, settled_s: null, from_s: 0 }, 0)).toBe("Still turning to 130° after 8 min");
});

test("the planned route crosses the operating area along the initial course, in the direction of travel", () => {
  const bbox = [-11.85, -10.15, 50.15, 51.85];
  const [from, to] = plannedRoute([-10.5, 51.0], 270, bbox); // due west: edge to edge at the start latitude
  expect(from[0]).toBeCloseTo(-10.15, 6);
  expect(to[0]).toBeCloseTo(-11.85, 6);
  expect(to[1]).toBeCloseTo(51.0, 6);
  const [a, b] = plannedRoute([-10.5, 51.0], 200, bbox); // south-south-west: leaves through the south edge
  expect(b[1]).toBeCloseTo(50.15, 6);
  expect(a[1]).toBeGreaterThan(51.0);
});

test("cargo losses group into map markers that say what the sea was doing", () => {
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

// a beam-sea ship, rolling 3° except 17° to port at t=90 (as in the map marker test)
function beamSeaReplay() {
  const r = { t: [], lon: [], lat: [], heading: [], roll: [], pitch: [], heave: [], hs: [], tp: [], wave_from: [], wind_e: [], wind_n: [], sog: [] };
  for (let i = 0; i < 200; i++) {
    r.t.push(i); r.lon.push(-11); r.lat.push(51); r.heading.push(0); r.roll.push(i === 90 ? -17 : 3); r.pitch.push(0); r.heave.push(0);
    r.hs.push(8.2); r.tp.push(16); r.wave_from.push(270); r.wind_e.push(18); r.wind_n.push(0); r.sog.push(5);
  }
  return r;
}

test("a loss note says where the waves met the ship, the roll, and what helps", async () => {
  globalThis.document ??= {};
  const { lossNote, GRADES } = await import("./panel.js");
  const r = beamSeaReplay(), note = lossNote(r, { time_s: 100, unit_ids: ["a", "b", "c"] });
  expect(note.text).toBe("3 boxes overboard: beam seas, port, rolling up to 17°");
  expect(note.tip).toContain("bow or stern");
  r.heading.fill(270); // head seas: no beam advice
  expect(lossNote(r, { time_s: 100, unit_ids: ["a"] }).tip).toContain("heading or speed");
  expect(GRADES.length).toBe(6);
  expect(GRADES[5]).toContain("120 TEU");
});

test("a lost ship's result says why, what the sea was doing, and what would have helped", () => {
  const note = shipLossNote(beamSeaReplay(), { lost: true, cause: "sustained_rolling", time_s: 90 });
  expect(note.cause).toContain("Heavy rolling");
  expect(note.then).toBe("At that moment: beam seas, port, Hs 8.2 m, roll 17°, heading 0°.");
  expect(note.tip).toContain("off the beam");
  expect(shipLossNote({ t: [] }, { cause: "unknown" }).then).toBe(""); // no invented numbers without samples
});

test("the forecast reads as a marine bulletin: own area first, Beaufort force and WMO sea state", () => {
  expect([beaufort(17.5), beaufort(20.7), beaufort(25), beaufort(40)]).toEqual([8, 8, 10, 12]);
  expect([seaState(3), seaState(5.5), seaState(8.5), seaState(10)]).toEqual(["rough", "very rough", "high", "very high"]);
  const report = (sector, hs, trend) => ({ sector, hs_range_m: hs, wind_range_mps: [17.5, 22.5], wave_from_deg: 270, wave_period_s: 12,
    trend, issued_at: "2026-01-08T18:40:00+00:00", valid_to: "2026-01-08T19:40:00+00:00" });
  const html = bulletin([report([2, 1], [4.5, 5], "steady"), report([1, 1], [8, 8.5], "increasing")], [1, 1]);
  expect(html).toContain("Issued 18:40 UTC · valid to 19:40 UTC");
  expect(html.indexOf("Your area")).toBeLessThan(html.indexOf("East"));
  expect(html).toContain("<b>Your area</b>: wind force 8 to 9 (34–44 kn). Sea high, 8–8.5 m, rising. Waves from W, 12 s.");
  expect(html).toContain("Sea very rough, 4.5–5 m. Waves");
  expect(bulletin([], [1, 1])).toContain("None released yet");
});

test("helm gauge turns the short way round and times the turn", () => {
  const replay = { t: Array.from({ length: 61 }, (_, i) => i), heading: Array.from({ length: 61 }, (_, i) => 350 + i / 6) }; // 10°/min
  const k = turnInfo(replay, 30); // 360 -> 30 across north
  expect(k.togo).toBeCloseTo(30);
  expect(k.rate).toBeCloseTo(10);
  expect(k.min).toBe(3);
  expect(turnInfo({ t: [0, 60], heading: [100, 100] }, 90)).toMatchObject({ togo: -10, min: null });
});

test("turn rate uses the supplied field and interpolates it", () => {
  const r = { t: [0, 1, 2], heading: [359, 1, 3], turn_rate: [0.2, 0.4, 0.6] };
  expect(sampleAt(r, 0.5).turn_rate).toBeCloseTo(0.3, 9);
  expect(sampleAt(r, 1).turn_rate).toBeCloseTo(0.4, 9);
  expect(turnRateAt(r, 2)).toBeCloseTo(0.6, 9);
});

test("old replays fall back to actual heading over real intervals, wrapping at north", () => {
  const r = { t: [0, 2, 5], heading: [359, 1, 4] };
  expect(sampleAt(r, 1).turn_rate).toBeCloseTo(1, 9); // 2° over 2 s
  expect(sampleAt(r, 4).turn_rate).toBeCloseTo(1, 9); // 3° over the 3 s after that
  expect(turnRateAt(r, 2)).toBeCloseTo(1, 9); // the timestamp interval, not the sample count
  const port = { t: [0, 1.5], heading: [1, 359] };
  expect(turnRateAt(port, 0.75)).toBeCloseTo(-4 / 3, 9); // 359 -> 0 -> 1 is the short way
  const withOrder = { t: [0, 1], heading: [10, 8], orders: [{ t: 0, heading_deg: 180 }] };
  expect(turnRateAt(withOrder, 0.5)).toBeCloseTo(-2, 9); // the order never supplies the rate
});

test("supplied actual rate drives ETA while the legacy average stays available", () => {
  const replay = { t: [0, 60], heading: [100, 100], turn_rate: [0.5, 0.5] };
  const k = turnInfo(replay, 130);
  expect(k).toMatchObject({ now: 100, togo: 30, rate: 0, actual: 0.5, actualSupplied: true, approaching: true, min: 1 });
  expect(turnRateText(k.actual)).toBe("+0.50 °/s starboard");
});

test("old scenes keep the legacy average-rate ETA while exposing actual rate", () => {
  const replay = { t: [0, 60], heading: [359, 0] }; // 1° across north in one minute
  const k = turnInfo(replay, 30);
  expect(k.actual).toBeCloseTo(1 / 60, 9);
  expect(k.actualSupplied).toBe(false);
  expect(k.rate).toBeCloseTo(1, 9);
  expect(k.min).toBe(30);
});

test("actual zero is zero, while missing data and one sample show a dash", () => {
  const still = { t: [0, 1, 2], heading: [10, 10, 10], turn_rate: [0, 0, 0] };
  expect(sampleAt(still, 1).turn_rate).toBe(0);
  expect(turnInfo(still, 30)).toMatchObject({ actual: 0, actualSupplied: true, approaching: false, min: null });
  expect(turnRateText(0)).toBe("0.00 °/s steady");
  const one = { t: [0], heading: [10] };
  expect(sampleAt(one, 0).turn_rate).toBeUndefined();
  expect(turnRateText(undefined)).toBe("—");
});

test("signed port rate, and no ETA when the turn is away from the order", () => {
  const port = { t: [0, 1, 2], heading: [102, 101, 100], turn_rate: [-0.25, -0.25, -0.25] };
  const toward = turnInfo(port, 70);
  expect(toward).toMatchObject({ togo: -30, actual: -0.25, actualSupplied: true, approaching: true, min: 2 });
  expect(turnRateText(toward.actual)).toBe("-0.25 °/s port");
  const away = turnInfo(port, 130); // ordered starboard while actually turning to port
  expect(away).toMatchObject({ togo: 30, actual: -0.25, actualSupplied: true, approaching: false, min: null });
  expect(turnNote(port, 130)).toContain("turning away from the order");
  expect(turnNote(port, 130)).toContain("rate -0.25 °/s port");
});

test("forecast field colours each sector by its own Hs, edge to edge", () => {
  const sector = (w, hs) => ({ sector_bbox: [w, w + 0.5667, 50, 50.5667], hs_range_m: [hs, hs], wave_from_deg: 270 });
  const f = forecastField([sector(-11, 8), sector(-10.4333, 6)]);
  expect(f.lon[0]).toBeCloseTo(-11);
  expect(f.lon.at(-1)).toBeCloseTo(-9.8666); // the outer edges are grid lines, so colour reaches them
  const row = f.hs[0].slice(0, f.lon.length), mid = f.lon.findIndex((x) => Math.abs(x + 10.4333) < 1e-6);
  expect(mid).toBeGreaterThan(0); // and so is the shared edge, where the two sectors meet halfway
  expect([row[0], row[mid - 1], row[mid], row[mid + 1], row.at(-1)]).toEqual([80, 80, 70, 60, 60]);
});

test("sea crests keep the data's direction with a storm-swell spread", () => {
  for (const seed of [1, 2, 3, 4, 5]) {
    const waves = spectrumWaves({ hs: 8, tp: 12, fromDeg: 270, seed }); // travelling east
    const e = (f) => waves.reduce((m, v) => m + v.a * v.a * f(Math.atan2(-v.dz, v.dx)), 0);
    const mean = Math.atan2(e(Math.sin), e(Math.cos)), rms = Math.sqrt(e((d) => (d - mean) ** 2) / e(() => 1));
    expect(Math.abs(mean * (180 / Math.PI))).toBeLessThan(3);
    expect(rms * (180 / Math.PI)).toBeLessThan(15);
  }
});

test("the ocean track runs from Le Havre to Halifax on a great circle", () => {
  const [first, last] = [OCEAN_TRACK[0], OCEAN_TRACK.at(-1)];
  expect(first[0]).toBeCloseTo(LE_HAVRE.lon, 2);
  expect(first[1]).toBeCloseTo(LE_HAVRE.lat, 2);
  expect(last[0]).toBeCloseTo(HALIFAX.lon, 2);
  expect(last[1]).toBeCloseTo(HALIFAX.lat, 2);
  const nm = routeLengthNm();
  expect(nm).toBeGreaterThan(2450); // about 2543 nm: the short way round, not the rhumb line
  expect(nm).toBeLessThan(2650);
  const mid = OCEAN_TRACK[Math.floor(OCEAN_TRACK.length / 2)];
  expect(mid[1]).toBeGreaterThan(51); // the great circle bulges north of 51°N in mid-Atlantic
  const [w, s, e, n] = routeBounds();
  expect(w).toBeCloseTo(HALIFAX.lon, 2);
  expect(e).toBeCloseTo(LE_HAVRE.lon, 2);
  expect(s).toBeLessThan(45);
  expect(n).toBeGreaterThan(52);
  // the track crosses the Biscay storm area, so the player can see it at the game's own zoom
  const inArea = OCEAN_TRACK.filter(([lon, lat]) => lon >= -7.35 && lon <= -3.73 && lat >= 47.733 && lat <= 51.35);
  expect(inArea.length).toBeGreaterThan(3);
});

test("the ocean track maps as one line and two named ends", () => {
  const f = routeFeature();
  expect(f.features.length).toBe(3);
  const [line, from, to] = f.features;
  expect(line.geometry.type).toBe("LineString");
  expect(line.geometry.coordinates.length).toBe(OCEAN_TRACK.length);
  expect(from.properties.label).toBe("Le Havre · France");
  expect(from.geometry.coordinates).toEqual(OCEAN_TRACK[0]);
  expect(to.properties.label).toBe("Halifax · Canada");
  expect(to.geometry.coordinates).toEqual(OCEAN_TRACK.at(-1));
});

test("the port guide shows the nearest few ports within 200 nm only", () => {
  const ship = [-5.4601, 49.7926]; // the 2026-01-08 scenario start, west of Cornwall
  const near = nearbyPorts(ship);
  expect(near.length).toBe(6);
  expect(near[0].name).toBe("Falmouth");
  expect(near[0].distance_nm).toBeCloseTo(27, 0);
  expect(near[0].bearing_deg).toBeCloseTo(36, 0); // north-east, over the channel
  for (let i = 1; i < near.length; i++) expect(near[i].distance_nm).toBeGreaterThanOrEqual(near[i - 1].distance_nm);
  for (const p of near) expect(p.distance_nm).toBeLessThanOrEqual(200);
  expect(near.some((p) => p.name === "Leixões")).toBe(false); // 500 nm away: off the list
  const none = nearbyPorts(ship, [{ name: "Leixões", country: "Portugal", lon: -8.7, lat: 41.183 }]);
  expect(none.length).toBe(0);
  const capped = nearbyPorts(ship, DEEPWATER_PORTS, { limit: 2 });
  expect(capped.map((p) => p.name)).toEqual(["Falmouth", "Plymouth"]);
});

test("the 200 nm ring is a closed circle at the stated range", () => {
  const ring = rangeRing([0, 0], 200).geometry.coordinates;
  expect(ring.length).toBe(97);
  expect(ring.at(-1)).toEqual(ring[0]);
  for (const v of ring) expect(haversineNm([0, 0], v)).toBeCloseTo(200, 0); // within half a mile
  expect(ring.every(([, lat]) => Math.abs(lat) < 4)).toBe(true); // at the equator 200 nm is about 3.3°
});

test("a port card says which port, how far, on which bearing, and that the range is guidance", () => {
  const html = portPopup({ name: "Brest", country: "France", distance_nm: 93.4, bearing_deg: 155.6 });
  expect(html).toContain("Brest");
  expect(html).toContain("France");
  expect(html).toContain("93 nm");
  expect(html).toContain("bearing 156°");
  expect(html).toContain("not a route");
  const threeDigits = portPopup({ name: "Milford Haven", country: "United Kingdom", distance_nm: 116, bearing_deg: 8.2 });
  expect(threeDigits).toContain("bearing 008°"); // a bearing keeps three digits, as on a compass
});

test("the global port list is worldwide and free of duplicates", () => {
  expect(DEEPWATER_PORTS.length).toBeGreaterThan(200);
  expect(new Set(DEEPWATER_PORTS.map((p) => p.name)).size).toBe(DEEPWATER_PORTS.length);
  for (const p of DEEPWATER_PORTS) {
    expect(p.lon).toBeGreaterThanOrEqual(-180);
    expect(p.lon).toBeLessThanOrEqual(180);
    expect(p.lat).toBeGreaterThanOrEqual(-90);
    expect(p.lat).toBeLessThanOrEqual(90);
    expect(typeof p.country).toBe("string");
    expect(p.country.length).toBeGreaterThan(0);
  }
  for (const name of ["Leixões", "Halifax", "Busan", "Santos", "Durban", "Fremantle"])
    expect(DEEPWATER_PORTS.some((p) => p.name === name)).toBe(true);
});
