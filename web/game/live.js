// Live play against game/live.py (`uv run python -m game.live`, then ?live=<scenario id>).
// The server runs game.session.Session; this module streams its 1 s samples into the same column shape as a
// replay (replay.js), sends the player's orders, and shows forecasts, judgement checkpoints and the score.
// Nothing here simulates: every number shown comes from the session.

import { sampleAt, turnRateAt } from "./replay.js";
import { relativeSea, GRADES } from "./panel.js";

const COLUMNS = ["lon", "lat", "heading", "turn_rate", "roll", "pitch", "heave", "hs", "tp", "wave_from", "wind_e", "wind_n", "cloud", "precip"];
const KN = 1.94384;
const PREVIEW_EVERY_S = 20; // game seconds between helm preview refreshes while the ship sails
const DIRS = { "0,1": "North", "1,1": "North-east", "1,0": "East", "1,-1": "South-east", "0,-1": "South",
  "-1,-1": "South-west", "-1,0": "West", "-1,1": "North-west", "0,0": "Your area" };
const ENDINGS = {
  timeout: "Time ran out inside the storm",
  missing_environment: "Ran out of sea data close to the coast: the voyage ended there",
  domain_boundary: "Left the area the scenario covers",
  model_limit: "The ship's motion left the range the model covers",
  numerical_failure: "The simulation could not continue",
};
// Why the ship was lost (game/ship.py causes), in plain words. The limits are authored game assumptions.
const SHIP_LOSS = {
  roll_capsize: "She capsized: one roll went further than she could come back from.",
  sustained_rolling: "Heavy rolling, beyond 20°, went on too long and the hull gave way.",
  structural_overload: "Pitching and slamming overloaded the hull for too long.",
  following_sea_instability: "Fast following seas made her surf and swing broadside (broach).",
  extreme_pitch: "She pitched beyond 35° in one wave.",
  extreme_acceleration: "One wave threw the hull with an extreme acceleration.",
};
const ROLL_LOSS = new Set(["roll_capsize", "sustained_rolling"]);

// The result card's account of a lost ship: the cause, where the waves met her then, and what would have helped.
export function shipLossNote(replay, ship) {
  const s = Number.isFinite(ship?.time_s) && replay.t.length ? sampleAt(replay, ship.time_s) : null;
  const sea = s ? relativeSea(s.heading, s.wave_from) : null;
  const tip = ROLL_LOSS.has(ship?.cause) ? "Next time keep the waves off the beam: bring them towards the bow or stern."
    : ship?.cause === "following_sea_instability" ? "Next time slow down or take the following seas at an angle."
    : "Next time ease speed into the waves, or meet them at an angle.";
  return { cause: SHIP_LOSS[ship?.cause] ?? "The hull failed in the storm.",
    then: s ? `At that moment: ${sea.label}, Hs ${s.hs.toFixed(1)} m, roll ${Math.abs(s.roll).toFixed(0)}°, heading ${Math.round(s.heading)}°.` : "",
    tip };
}

// Marine bulletin words for released numbers: Beaufort force from wind speed (upper bounds in m/s) and the WMO
// sea-state term (code 3700) from significant wave height (upper bounds in m).
const BEAUFORT_MPS = [0.5, 1.5, 3.3, 5.5, 7.9, 10.7, 13.8, 17.1, 20.7, 24.4, 28.4, 32.6];
const SEA_STATES = [[0.1, "calm"], [0.5, "smooth"], [1.25, "slight"], [2.5, "moderate"], [4, "rough"], [6, "very rough"],
  [9, "high"], [14, "very high"], [Infinity, "phenomenal"]];
export const beaufort = (mps) => BEAUFORT_MPS.filter((b) => mps > b).length;
export const seaState = (hs) => SEA_STATES.find(([top]) => hs <= top)[1];
const hhmm = (iso) => iso.slice(11, 16);
// How to read the bulletin and the map: method only, the way out stays the trainee's call.
const READING = `<details class="reading"><summary>How to read it</summary><ol>` +
  `<li>Compare your area with its neighbours: a lower Hs is a calmer sea.</li>` +
  `<li>The map arrows show where the waves run. Picture your bow on a heading: waves on the beam roll the ship hardest and throw boxes overboard.</li>` +
  `<li>A rising area gets worse within the hour, and a veering or backing wind turns the sea with it: decide early, the ship takes minutes to turn.</li></ol></details>`;

// The released forecasts as a ship receives them: issue and validity times, then one line per sea area, the ship's
// own area first and the rest in compass order (no ranking, so the bulletin does not pick the way out).
export function bulletin(reports, shipSector) {
  if (!reports.length) return `<h2>Weather bulletin</h2><p class="live-status">None released yet.</p>`;
  const [sx, sy] = shipSector, order = Object.keys(DIRS);
  const where = (r) => `${r.sector[0] - sx},${r.sector[1] - sy}`;
  const lines = [...reports].sort((a, b) => ((order.indexOf(where(a)) + 1) % 9) - ((order.indexOf(where(b)) + 1) % 9)).map((r) => {
    const [w0, w1] = r.wind_range_mps.map(beaufort), [h0, h1] = r.hs_range_m;
    const force = w0 === w1 ? `${w0}` : `${w0} to ${w1}`, sea = seaState(h0) === seaState(h1) ? seaState(h1) : `${seaState(h0)} to ${seaState(h1)}`;
    // wind_from_deg and wind_shift arrive with forecasts built since 30 Oct; older reports give the force alone
    const from = Number.isFinite(r.wind_from_deg) ? `from ${compass(r.wind_from_deg)} ` : "";
    const shift = r.wind_shift && r.wind_shift !== "steady" ? `, ${r.wind_shift}` : "";
    return `<li><b>${DIRS[where(r)] ?? "Further away"}</b>: wind ${from}force ${force}${shift} (${(r.wind_range_mps[0] * KN).toFixed(0)}–` +
      `${(r.wind_range_mps[1] * KN).toFixed(0)} kn). Sea ${sea}, ${h0}–${h1} m${r.trend === "increasing" ? ", rising" : ""}. ` +
      `Waves from ${compass(r.wave_from_deg)}, ${r.wave_period_s} s.</li>`;
  });
  const issued = reports.map((r) => r.issued_at).sort().at(-1), valid = reports.map((r) => r.valid_to).sort()[0];
  return `<h2>Weather bulletin</h2><p class="live-status">Issued ${hhmm(issued)} UTC · valid to ${hhmm(valid)} UTC · ` +
    `next update ${hhmm(valid)} UTC</p><ul class="bulletin">${lines.join("")}</ul>${READING}`;
}

// 16 points, as forecasts give directions to the nearest 22.5° (pipeline/gameplay.py)
const POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
export const compass = (d) => POINTS[Math.round((((d % 360) + 360) % 360) / 22.5) % 16];

// Signed actual rate for display: two decimals, direction, and no invented number for missing data.
export function turnRateText(rate, { short = false } = {}) {
  if (!Number.isFinite(rate)) return "—";
  const zero = Math.abs(rate) < 0.005, dir = zero ? "steady" : rate > 0 ? (short ? "stbd" : "starboard") : "port";
  return `${zero ? "0.00" : rate > 0 ? `+${rate.toFixed(2)}` : rate.toFixed(2)} °/s ${dir}`;
}

// Heading now, signed degrees still to turn (+ starboard), legacy average rate in °/min, signed actual rate
// (deg/s, supplied or finite-difference fallback) and minutes to go. ETA uses the actual rate only when one was
// supplied and its direction approaches the order; without the field the old average-rate ETA is unchanged.
export function turnInfo(replay, orderedHeading) {
  const t = replay.t, h = replay.heading, n = t.length;
  if (!n) return null;
  const now = h[n - 1], togo = ((orderedHeading - now + 540) % 360) - 180;
  const i = Math.max(0, n - 61); // rate over the last minute of game time
  const rate = Math.abs((((now - h[i] + 540) % 360) - 180) / Math.max(t[n - 1] - t[i], 1)) * 60; // degrees per minute
  const actual = turnRateAt(replay, t[n - 1]);
  const actualSupplied = Number.isFinite(replay.turn_rate?.at(-1));
  const approaching = Number.isFinite(actual) && Math.abs(togo) >= 2 && Math.sign(actual) === Math.sign(togo);
  let min = null;
  if (actualSupplied) {
    if (approaching && Math.abs(actual) * 60 > 0.5) min = Math.ceil(Math.abs(togo) / (Math.abs(actual) * 60));
  } else if (rate > 0.5) min = Math.ceil(Math.abs(togo) / rate);
  return { now, togo, rate, actual, actualSupplied, approaching, min };
}

// "Heading 250° → ordered 130°: turning to port, 120° to go · rate -0.8 °/s port, about 3 min" (game time).
export function turnNote(replay, orderedHeading) {
  const k = turnInfo(replay, orderedHeading);
  if (!k) return "";
  const rate = `rate ${turnRateText(k.actual)}`;
  if (Math.abs(k.togo) < 2) return `Heading ${k.now.toFixed(0)}°, on the ordered course · ${rate}`;
  const etaRate = k.actualSupplied ? Math.abs(k.actual) * 60 : k.rate;
  const eta = k.min !== null ? `, about ${k.min} min at ${etaRate.toFixed(0)}°/min`
    : k.actualSupplied && !k.approaching ? ", turning away from the order"
    : ", barely turning: more speed gives the rudder more grip";
  return `Heading ${k.now.toFixed(0)}° → ordered ${orderedHeading.toFixed(0)}°: turning to ${k.togo < 0 ? "port" : "starboard"}, ` +
    `${Math.abs(k.togo).toFixed(0)}° to go · ${rate}${eta}`;
}

// Helm gauge shown while the arrow keys steer: a north-up compass with the heading (blue ship), the order (orange
// mark) and the turn still to go (orange arc), beside the numbers and the ordered speed against the speed through water.
const DIAL_R = 40;
const onRing = (deg, r = DIAL_R) => [r * Math.sin((deg * Math.PI) / 180), -r * Math.cos((deg * Math.PI) / 180)].map((v) => v.toFixed(1));
const TICKS = Array.from({ length: 36 }, (_, k) => {
  const [x0, y0] = onRing(k * 10, k % 3 ? 43 : 40), [x1, y1] = onRing(k * 10, 46);
  return `<line class="tick${k % 3 ? "" : " major"}" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"/>`;
}).join("");

export function helmGauge() {
  const box = Object.assign(document.createElement("div"), { className: "helm-gauge glass", hidden: true });
  // above the playback dock, so it shares the dock's lane between the map and the panel (index.html)
  (document.querySelector(".dock-wrap") ?? document.body).prepend(box);
  let timer = null;
  const render = (live) => {
    const { heading: order, speed } = live.helm, k = turnInfo(live.replay, order);
    if (!k) return;
    const { now, togo, actual, actualSupplied, approaching, min } = k, steady = Math.abs(togo) < 2, sog = (live.replay.sog.at(-1) ?? 0) * KN;
    const [ax, ay] = onRing(now), [bx, by] = onRing(now + togo);
    const status = steady ? `<span class="ok">On the ordered course</span>`
      : `<b class="ord">${togo < 0 ? "Port" : "Starboard"} ${Math.abs(togo).toFixed(0)}°</b> · ` +
        (min !== null ? `about ${min} min` : actualSupplied && !approaching ? "turning away from the order"
          : "barely turning: add speed for rudder grip");
    box.innerHTML =
      `<svg viewBox="-50 -50 100 100" aria-hidden="true">${TICKS}<text class="n" x="0" y="-28">N</text>` +
      (steady ? "" : `<path class="turn" d="M${ax} ${ay} A${DIAL_R} ${DIAL_R} 0 0 ${togo > 0 ? 1 : 0} ${bx} ${by}"/>`) +
      `<path class="order" d="M0 -39 L-5 -49 L5 -49 Z" transform="rotate(${order.toFixed(1)})"/>` +
      `<g transform="rotate(${now.toFixed(1)})"><line class="hdg" x1="0" y1="-14" x2="0" y2="-${DIAL_R}"/>` +
      `<path class="ship" d="M0 -15 L7 11 L0 6 L-7 11 Z"/></g></svg>` +
      `<div class="helm-read"><div class="helm-hdg mono"><b>${now.toFixed(0)}°</b><span>→</span><b class="ord">${order.toFixed(0)}°</b></div>` +
      `<div class="helm-turn"><span class="helm-rate">Rate ${turnRateText(actual, { short: true })}</span> · ${status}</div>` +
      `<div class="helm-spd" title="Orange: ordered speed · blue mark: speed through water"><span>Speed</span>` +
      `<i><b style="width:${(speed / 18) * 100}%"></b><s style="left:${Math.min(100, (sog / 18) * 100)}%"></s></i>` +
      `<span class="mono ord">${speed.toFixed(1)} kn</span></div>` +
      `<div class="helm-keys"><kbd>←</kbd><kbd>→</kbd> 5° · <kbd>⇧</kbd> 1° · <kbd>↑</kbd><kbd>↓</kbd> 0.5 kn` +
      `${live.state?.paused ? ' · <span class="ord">paused</span>' : ""}</div></div>`;
    box.setAttribute("aria-label", `${turnNote(live.replay, order)}, ordered speed ${speed.toFixed(1)} knots`);
  };
  return {
    show(live) { // after a key: show for a few seconds
      render(live);
      box.hidden = false;
      clearTimeout(timer);
      timer = setTimeout(() => (box.hidden = true), 5000);
    },
    update: (live) => box.hidden || render(live), // while shown, follow the turn
  };
}

export async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

export async function liveScenarios() {
  try {
    const r = await fetch("/api/scenarios");
    return r.ok ? r.json() : [];
  } catch {
    return [];
  }
}

// Start a session and return { brief, replay, poll(), order(), pause(), rate() }.
// onLoss(unitId, t) is called once per container the session reports overboard.
export async function startLive(id, { onLoss }) {
  const brief = await post("/api/start", { scenario: id });
  if (brief.error) throw new Error(brief.error);
  const idx = Object.fromEntries(brief.fields.map((n, i) => [n, i]));
  const replay = {
    start_utc: brief.start, speed_label: "through water", live: true,
    note: `${brief.title}. ${brief.objective}`,
    credits: ["Live TIDE session. Generated using E.U. Copernicus Marine Service Information (storm subsets in data/storms)",
      "Cloud and rain: Weather data by Open-Meteo.com (CC BY 4.0), from ERA5, Copernicus C3S",
      "Forecast cards are game forecasts generated from the historical reconstruction"],
    t: [], sog: [], ...Object.fromEntries(COLUMNS.map((c) => [c, []])),
    cargo: [{ t: 0, lost_teu: 0, grade: 0, total_teu: 1200 }],
    orders: [{ t: 0, heading_deg: brief.initial.heading_deg, speed_knots: brief.initial.speed_mps * KN }],
    events: [],
  };
  const live = { brief, replay, since: -1, state: null, helm: { heading: brief.initial.heading_deg, speed: brief.initial.speed_mps * KN } };
  let timer = null;

  live.poll = async () => {
    const st = await (await fetch(`/api/state?since=${live.since}`)).json();
    for (const row of st.samples) {
      if (replay.t.length && row[0] <= replay.t.at(-1)) continue;
      replay.t.push(row[0]);
      for (const c of COLUMNS) replay[c].push(row[idx[c]] ?? undefined);
      replay.sog.push(row[idx.stw]);
      const last = replay.cargo.at(-1);
      if (row[idx.lost_teu] !== last.lost_teu || row[idx.grade] !== last.grade)
        replay.cargo.push({ t: row[0], lost_teu: row[idx.lost_teu], grade: row[idx.grade], total_teu: 1200 });
      live.since = row[0];
    }
    if (replay.t.length === 1) { // sampleAt needs two records
      replay.t.push(replay.t[0] + 0.001);
      for (const c of [...COLUMNS, "sog"]) replay[c].push(replay[c][0]);
    }
    for (const e of st.log) {
      if (e.kind !== "cargo_lost") continue;
      replay.events.push({ time_s: e.t, kind: e.kind, detail: e.detail, unit_ids: e.units, teu: e.teu, demand_g: e.demand_g });
      for (const u of e.units) onLoss(u, e.t);
    }
    const v = st.view, last = replay.orders.at(-1);
    if (Math.abs(v.target_heading_deg - last.heading_deg) > 0.01 || Math.abs(v.target_speed_knots - last.speed_knots) > 0.01)
      replay.orders.push({ t: v.elapsed_s, heading_deg: v.target_heading_deg, speed_knots: v.target_speed_knots });
    live.state = st;
    if (!st.paused && !st.checkpoint && live.since - (live.preview?.from_s ?? -Infinity) >= PREVIEW_EVERY_S) live.askPreview();
    return st;
  };
  // Arrow keys change the pending order; it is sent after a short pause in typing.
  live.steer = (dHeading, dSpeed) => {
    live.helm.heading = ((live.helm.heading + dHeading) % 360 + 360) % 360;
    live.helm.speed = Math.max(0, Math.min(18, live.helm.speed + dSpeed));
    clearTimeout(timer);
    timer = setTimeout(() => { // once the keys rest: one order and one preview, not a model run per key press
      post("/api/order", { heading_deg: live.helm.heading, speed_knots: live.helm.speed });
      live.askPreview();
    }, 350);
  };
  // Helm preview: game.live runs the ship model ahead on a copy of the session (track only) for a proposed order.
  // One request at a time; the latest wish waits its turn. Refreshed every PREVIEW_EVERY_S of game time.
  live.preview = null;
  let inflight = false, queued = null;
  live.askPreview = async (heading = live.helm.heading, speed = live.helm.speed) => {
    if (inflight) return void (queued = [heading, speed]);
    inflight = true;
    const p = await post("/api/preview", { heading_deg: heading, speed_knots: speed }).catch(() => null);
    inflight = false;
    if (p && !p.busy && !p.error) live.preview = p;
    const next = queued;
    queued = null;
    if (next) live.askPreview(...next);
  };
  live.pause = (paused) => post("/api/pause", { paused });
  live.rate = (r) => post("/api/rate", { rate: r });
  await live.poll();
  return live;
}

// ---------- overlay: forecasts, decision card, result ----------
// Styled like the side panel and the playback dock (index.html): dark translucent cards, small caps headings,
// 4 px buttons that fill when chosen. The scene stays visible; a decision can be put aside to look around first.
const CSS = `
  .live-card { min-height: 0; overflow-y: auto; box-sizing: border-box; padding: 14px 16px; border: 1px solid var(--edge);
    border-radius: var(--radius); background: var(--panel); box-shadow: var(--shadow); backdrop-filter: blur(14px);
    display: flex; flex-direction: column; gap: 10px; font-size: 13px; color: var(--text); z-index: 7; }
  .live-card[hidden], .live-pill[hidden] { display: none; }
  .live-card h2 { margin: 0; font-size: 11px; font-weight: 600; letter-spacing: 0.12em; color: var(--muted); text-transform: uppercase; }
  .live-card p { margin: 0; line-height: 1.45; }
  .live-card .q { display: flex; flex-direction: column; gap: 6px; }
  .live-card .choices { display: flex; flex-wrap: wrap; gap: 6px; }
  .live-card button, .live-pill { height: 34px; padding: 0 14px; border: 1px solid #2b4660; border-radius: 10px;
    background: #0e1b29; color: var(--text); font: inherit; cursor: pointer; }
  .live-card .choices button[aria-pressed="true"], .live-card button.primary { background: #2b4660; color: #fff; }
  .live-card .actions { display: flex; justify-content: flex-end; gap: 8px; }
  .live-card .fields { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .live-card label { display: flex; flex-direction: column; gap: 4px; color: var(--muted); font-size: 12px; }
  .live-card input, .live-card textarea { box-sizing: border-box; width: 100%; border: 1px solid #2b4660; border-radius: 4px;
    background: #0e1b29; color: var(--text); font: 13px ui-monospace, monospace; padding: 6px 8px; }
  .live-card textarea { min-height: 52px; font-family: inherit; resize: vertical; }
  .live-card .ok { color: #6fd3a0; } .live-card .bad { color: var(--order); }
  .live-card ul { margin: 0; padding-left: 16px; } .live-card li { margin: 3px 0; }
  .live-card details.reading { font-size: 12px; color: var(--muted); } .live-card details.reading summary { cursor: pointer; color: var(--text); }
  .live-card details.reading ol { margin: 6px 0 0; padding-left: 18px; } .live-card details.reading li { margin: 3px 0; }
  .live-card ul.bulletin { padding-left: 0; list-style: none; font-size: 12px; line-height: 1.4; }
  .live-stars { font-size: 24px; color: var(--order); letter-spacing: 4px; }
  .live-pill { align-self: flex-start; z-index: 7; border-radius: 999px; color: var(--order); border-color: var(--order); background: var(--panel); }
  .live-status { color: var(--muted); font-size: 12px; }
  /* a briefing or decision waiting for the player: the card glows until they touch it, the next step pulses */
  .live-card.attention:not(.touched) { animation: live-glow 1.6s ease-in-out infinite; border-color: var(--order); }
  .live-card .actions { position: sticky; bottom: -14px; margin: 0 -16px -14px; padding: 10px 16px 14px;
    background: linear-gradient(transparent, rgba(11, 22, 34, 0.96) 30%); }
  .live-card button.primary { flex: 1; height: 40px; border: 0; background: var(--order); color: #0b1622; font-weight: 700; }
  .live-card button.primary:hover { filter: brightness(1.08); }
  .live-card.attention button.primary, .live-pill:not([hidden]) { animation: live-pulse 1.6s ease-out infinite; }
  .live-card .q.todo > span::before { content: "● "; color: var(--order); }
  .live-hint { color: var(--order); font-size: 12px; }
  @keyframes live-glow { 50% { box-shadow: 0 0 0 1px var(--order), 0 0 26px 4px rgba(242, 169, 59, 0.45); } }
  @keyframes live-pulse { 0% { box-shadow: 0 0 0 0 rgba(242, 169, 59, 0.7); } 100% { box-shadow: 0 0 0 12px rgba(242, 169, 59, 0); } }
  @media (prefers-reduced-motion: reduce) { .live-card.attention:not(.touched), .live-card.attention button.primary, .live-pill { animation: none; } }
  /* fixed size and single-line rows, so changing the order never resizes the card */
  .helm-gauge { width: 380px; max-width: 100%; height: 116px; box-sizing: border-box; display: flex; align-items: center; gap: 14px;
    padding: 10px 16px 10px 10px; border-radius: 20px; backdrop-filter: blur(14px); color: var(--text); white-space: nowrap; }
  .helm-gauge[hidden] { display: none; }
  .helm-gauge svg { width: 96px; height: 96px; flex: none; }
  .helm-gauge .tick { stroke: var(--muted); stroke-width: 1; opacity: 0.45; } .helm-gauge .tick.major { stroke-width: 1.6; opacity: 0.9; }
  .helm-gauge .n { fill: var(--muted); font-size: 10px; font-weight: 700; text-anchor: middle; }
  .helm-gauge .turn { fill: none; stroke: var(--order); stroke-width: 4; stroke-linecap: round; opacity: 0.85; }
  .helm-gauge .order { fill: var(--order); } .helm-gauge .hdg { stroke: var(--accent); stroke-width: 1.5; }
  .helm-gauge .ship { fill: var(--accent); stroke: #0b1622; stroke-width: 1; }
  .helm-read { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; }
  .helm-hdg { display: flex; align-items: baseline; gap: 8px; font-size: 24px; line-height: 1; }
  .helm-hdg b { min-width: 4ch; font-weight: 600; color: var(--accent); } .helm-hdg span { color: var(--muted); font-size: 16px; }
  .helm-gauge .ord { color: var(--order); } .helm-gauge .ok { color: #6fd3a0; }
  .helm-turn { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; } .helm-turn b { font-weight: 600; }
  .helm-rate { color: var(--text); font-variant-numeric: tabular-nums; }
  .helm-spd { display: grid; grid-template-columns: auto 1fr 7ch; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
  .helm-spd i { position: relative; height: 6px; border-radius: 3px; background: var(--line); }
  .helm-spd b { position: absolute; inset: 0 auto 0 0; border-radius: 3px; background: var(--order); }
  .helm-spd s { position: absolute; top: -3px; width: 2px; height: 12px; margin-left: -1px; background: var(--accent); }
  .helm-keys { font-size: 11px; color: var(--muted); }
  .helm-gauge kbd { display: inline-block; min-width: 14px; padding: 0 4px; border: 1px solid var(--edge); border-radius: 4px; font: inherit; text-align: center; }`;

export function liveOverlay() {
  document.head.append(Object.assign(document.createElement("style"), { textContent: CSS }));
  const card = Object.assign(document.createElement("aside"), { className: "live-card" });
  const pill = Object.assign(document.createElement("button"), { className: "live-pill", type: "button", hidden: true });
  (document.getElementById("left") ?? document.body).append(card, pill); // under the title card (index.html)
  card.addEventListener("pointerdown", () => card.classList.add("touched"));
  let shownCheckpoint = null, shownScore = false, view = "status", pending = null;

  const el = (html) => Object.assign(document.createElement("template"), { innerHTML: html.trim() }).content;
  const button = (label, fn, primary) => Object.assign(document.createElement("button"), { type: "button", textContent: label, onclick: fn,
    className: primary ? "primary" : "" });
  // attention: a briefing or decision waits for the player; the card glows until they first touch it
  let readingOpen = false; // the card redraws on every poll: keep "How to read it" as the trainee left it
  const render = (title, html, actions, attention) => {
    card.replaceChildren(el(`<h2>${title}</h2>${html}`));
    for (const d of card.querySelectorAll("details.reading")) {
      d.open = readingOpen;
      d.ontoggle = () => (readingOpen = d.open);
    }
    card.classList.toggle("attention", !!attention);
    card.classList.remove("touched");
    if (actions?.length) {
      const row = Object.assign(document.createElement("div"), { className: "actions" });
      row.append(...actions);
      card.append(row);
    }
    card.hidden = false;
    pill.hidden = true;
    // a decision gets the whole left column: index.html hides the passage map while body.deciding is set
    document.body.classList.toggle("deciding", !!attention);
  };
  // put a decision aside: the session stays paused while the player looks around
  const lookFirst = (label) => {
    document.body.classList.remove("deciding");
    card.hidden = true;
    pill.textContent = label;
    pill.hidden = false;
  };

  const forecasts = (live) => bulletin(live.state.view.reports, live.state.view.ship_sector);

  const questions = (live, cp, intro) => {
    const answers = {};
    render(cp.trigger === "briefing" ? "Before you sail" : "Decision point", `${intro ?? ""}<p><b>${cp.message}</b></p>` + forecasts(live) +
      cp.questions.map((q) => `<div class="q" data-q="${q.id}"><span>${q.prompt}</span><div class="choices">` +
        q.options.map((o) => `<button type="button" aria-pressed="false" data-v="${o}">${o}</button>`).join("") + "</div></div>").join("") +
      `<h2>Your orders</h2><div class="fields"><label>Heading °<input type="number" id="cp-h" min="0" max="359" value="${live.helm.heading.toFixed(0)}"></label>` +
      `<label>Speed kn<input type="number" id="cp-s" min="0" max="18" step="0.5" value="${live.helm.speed.toFixed(1)}"></label></div>` +
      `<label>Why this order?<textarea id="cp-r" placeholder="What did you see?"></textarea></label>` +
      `<p class="live-hint">● Pick an answer for each question, set your orders, then press ${cp.trigger === "briefing" ? "Set sail" : "Carry on"}.</p>`,
      [button("Look around first", () => lookFirst("⏸ Paused for your decision · Answer")),
       button(cp.trigger === "briefing" ? "Set sail" : "Carry on", async () => {
         clearTimeout(typing);
         const order = { heading_deg: ((+card.querySelector("#cp-h").value % 360) + 360) % 360,
                         speed_knots: Math.max(0, Math.min(18, +card.querySelector("#cp-s").value || 0)) };
         Object.assign(live.helm, { heading: order.heading_deg, speed: order.speed_knots });
         live.askPreview(order.heading_deg, order.speed_knots);
         const res = await post("/api/answer", { id: cp.id, answers, reason: card.querySelector("#cp-r").value, order });
         if (res.error) return;
         render("Feedback", cp.questions.map((q) => {
           const r = res[q.id];
           return `<p class="${r.correct ? "ok" : "bad"}">${r.correct ? "✓" : "✗"} ${q.prompt}<br>You: ${r.given ?? "no answer"} · Expected: ${r.answer}</p>`;
         }).join(""), [button("Continue", () => { view = "status"; pending = null; }, true)], true);
       }, true)], true);
    // the passage map shows where the order being typed takes the ship
    const typed = () => live.askPreview(+card.querySelector("#cp-h").value % 360, +card.querySelector("#cp-s").value || 0);
    let typing = null;
    for (const id of ["#cp-h", "#cp-s"]) card.querySelector(id).oninput = () => { clearTimeout(typing); typing = setTimeout(typed, 150); };
    typed();
    for (const box of card.querySelectorAll("[data-q]")) {
      box.classList.add("todo");
      box.querySelectorAll("button").forEach((b) => (b.onclick = () => {
        box.classList.remove("todo");
        box.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        answers[box.dataset.q] = b.dataset.v;
      }));
    }
  };

  const briefing = (live, cp) => {
    const b = live.brief;
    render("Briefing", `<p><b>${b.title}</b></p><p>${b.objective}</p>` +
      `<p>${b.start.slice(0, 16).replace("T", " ")} UTC · ${(b.budget_s / 3600).toFixed(0)} h · S175 container ship, 1,200 TEU. ` +
      `The storm area's edge is not shown: read the sea, the Details panel and the forecasts.</p>` +
      `<p>The <b>Passage</b> map shows the released forecast areas, coloured by forecast wave height, and in dashed orange ` +
      `where your order takes the ship over the next few minutes: the ship turns slowly.</p>` +
      `<p class="live-status">← → heading (Shift: 1°) · ↑ ↓ speed · Space pause · drag to look around</p>`,
      [button("Look around first", () => lookFirst("⏸ Paused · ▶ Ready to sail")), button("I'm ready →", () => questions(live, cp), true)], true);
  };

  pill.onclick = () => pending && questions(pending.live, pending.cp);

  return {
    get open() { return !card.hidden && view === "decision"; },
    update(live) {
      const st = live.state, v = st.view;
      if (st.score && !shownScore) return this.score(st.score, live);
      if (st.checkpoint && shownCheckpoint !== st.checkpoint.id) {
        shownCheckpoint = st.checkpoint.id;
        view = "decision";
        pending = { live, cp: st.checkpoint };
        return st.checkpoint.trigger === "briefing" ? briefing(live, st.checkpoint) : questions(live, st.checkpoint);
      }
      if (view !== "status") return;
      const left = v.remaining_s;
      render("Mission", `<p class="live-status">${Math.floor(left / 3600)} h ${String(Math.floor((left % 3600) / 60)).padStart(2, "0")} min left · ` +
        `${st.paused ? "paused" : `running ×${(+v.achieved_rate || 0).toFixed(1)}`}</p>` +
        `<p>${turnNote(live.replay, v.target_heading_deg)}</p>` +
        forecasts(live) +
        `<p class="live-status">Game forecasts generated from the historical reconstruction.</p>`);
    },
    score(s, live) {
      shownScore = true;
      view = "score";
      const p = s.parts;
      const review = s.checkpoints.map((cp) => `<li>${cp.trigger} at ${(cp.t / 60).toFixed(0)} min: ` +
        Object.values(cp.results ?? {}).map((r) => `${r.correct ? "✓" : "✗"} ${r.given ?? "no answer"}`).join(", ") +
        (cp.reason ? ` — “${cp.reason}”` : "") + "</li>").join("");
      render("Voyage result", `<div class="live-stars">${"★".repeat(s.stars)}${"☆".repeat(3 - s.stars)}</div>` +
        `<p><b>${s.ship_lost ? "Ship lost: the mission failed and all cargo went down with her"
          : s.escaped ? "Escaped the storm" : ENDINGS[s.outcome] ?? `Did not escape (${s.outcome})`}</b></p>` +
        (s.ship_lost ? (({ cause, then, tip }) => `<p>${cause} ${then}</p><p class="live-hint">${tip}</p>`)(shipLossNote(live.replay, s.ship)) : "") +
        `<p>${(s.elapsed_s / 60).toFixed(0)} min · ${s.lost_teu} TEU lost (grade ${s.grade}: ${GRADES[s.grade]}) · ${s.answers_correct}/${s.answers_total} judgements right</p>` +
        `<p>Escape ${p.escape}/40 · Cargo ${p.cargo}/40 · Judgement ${p.judgement}/20 · <b>Total ${s.total}/100</b></p><ul>${review}</ul>` +
        `<p class="live-status">Game scoring for training, not a professional assessment.</p>`);
    },
  };
}
