import { expect, test } from "bun:test";
import { spectrumWaves, displace, heightAt } from "./ocean.js";
import { deckLayout } from "./cargo-layout.js";
import { sunPosition } from "./sky.js";
import { sampleAt, latest } from "./replay.js";

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

test("all 320 deck units get a slot inside the hatch area", () => {
  const units = deckLayout([-11, 7.7, 47.45]);
  expect(units.length).toBe(320);
  expect(new Set(units.map((u) => `${u.tier}:${u.bay}:${u.row}:${u.position[2].toFixed(2)}`)).size).toBe(320);
  for (const u of units) expect(Math.abs(u.position[0])).toBeLessThan(11);
});
