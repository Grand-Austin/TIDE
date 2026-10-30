// Live play against game/live.py (`uv run python -m game.live`, then ?live=<scenario id>).
// The server runs game.session.Session; this module streams its 1 s samples into the same column shape as a
// replay (replay.js), sends the player's orders, and shows forecasts, judgement checkpoints and the score.
// Nothing here simulates: every number shown comes from the session.

const COLUMNS = ["lon", "lat", "heading", "roll", "pitch", "heave", "hs", "tp", "wave_from", "wind_e", "wind_n", "cloud", "precip"];
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
const compass = (d) => ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round((((d % 360) + 360) % 360) / 45) % 8];

// "Heading 250° → ordered 130°: turning to port, 120° to go, about 12 min at the current rate" (game time).
export function turnNote(replay, orderedHeading) {
  const t = replay.t, h = replay.heading, n = t.length;
  if (!n) return "";
  const now = h[n - 1], togo = ((orderedHeading - now + 540) % 360) - 180;
  if (Math.abs(togo) < 2) return `Heading ${now.toFixed(0)}°, on the ordered course`;
  const i = Math.max(0, n - 61); // rate over the last minute of game time
  const rate = Math.abs((((now - h[i] + 540) % 360) - 180) / Math.max(t[n - 1] - t[i], 1)) * 60; // degrees per minute
  const eta = rate > 0.5 ? `, about ${Math.ceil(Math.abs(togo) / rate)} min at ${rate.toFixed(0)}°/min` : ", barely turning: more speed gives the rudder more grip";
  return `Heading ${now.toFixed(0)}° → ordered ${orderedHeading.toFixed(0)}°: turning to ${togo < 0 ? "port" : "starboard"}, ${Math.abs(togo).toFixed(0)}° to go${eta}`;
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
    live.askPreview();
    timer = setTimeout(() => post("/api/order", { heading_deg: live.helm.heading, speed_knots: live.helm.speed }), 350);
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
  @media (prefers-reduced-motion: reduce) { .live-card.attention:not(.touched), .live-card.attention button.primary, .live-pill { animation: none; } }`;

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
  const render = (title, html, actions, attention) => {
    card.replaceChildren(el(`<h2>${title}</h2>${html}`));
    card.classList.toggle("attention", !!attention);
    card.classList.remove("touched");
    if (actions?.length) {
      const row = Object.assign(document.createElement("div"), { className: "actions" });
      row.append(...actions);
      card.append(row);
    }
    card.hidden = false;
    pill.hidden = true;
  };
  // put a decision aside: the session stays paused while the player looks around
  const lookFirst = (label) => {
    card.hidden = true;
    pill.textContent = label;
    pill.hidden = false;
  };

  const forecasts = (live) => {
    const v = live.state.view, [sx, sy] = v.ship_sector;
    return v.reports.map((r) =>
      `<li><b>${DIRS[`${r.sector[0] - sx},${r.sector[1] - sy}`] ?? "Further away"}</b>: Hs ${r.hs_range_m[0]}–${r.hs_range_m[1]} m, ` +
      `wind ${(r.wind_range_mps[0] * KN).toFixed(0)}–${(r.wind_range_mps[1] * KN).toFixed(0)} kn, waves from ${compass(r.wave_from_deg)}, ` +
      `${r.wave_period_s} s, ${r.trend}</li>`).join("") || "<li>None released yet.</li>";
  };

  const questions = (live, cp, intro) => {
    const answers = {};
    render(cp.trigger === "briefing" ? "Before you sail" : "Decision point", `${intro ?? ""}<p><b>${cp.message}</b></p>` +
      cp.questions.map((q) => `<div class="q" data-q="${q.id}"><span>${q.prompt}</span><div class="choices">` +
        q.options.map((o) => `<button type="button" aria-pressed="false" data-v="${o}">${o}</button>`).join("") + "</div></div>").join("") +
      `<h2>Your orders</h2><div class="fields"><label>Heading °<input type="number" id="cp-h" min="0" max="359" value="${live.helm.heading.toFixed(0)}"></label>` +
      `<label>Speed kn<input type="number" id="cp-s" min="0" max="18" step="0.5" value="${live.helm.speed.toFixed(1)}"></label></div>` +
      `<label>Why this order?<textarea id="cp-r" placeholder="What did you see?"></textarea></label>` +
      `<p class="live-hint">● Pick an answer for each question, set your orders, then press ${cp.trigger === "briefing" ? "Set sail" : "Carry on"}.</p>`,
      [button("Look around first", () => lookFirst("⏸ Paused for your decision · Answer")),
       button(cp.trigger === "briefing" ? "Set sail" : "Carry on", async () => {
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
      if (st.score && !shownScore) return this.score(st.score);
      if (st.checkpoint && shownCheckpoint !== st.checkpoint.id) {
        shownCheckpoint = st.checkpoint.id;
        view = "decision";
        pending = { live, cp: st.checkpoint };
        return st.checkpoint.trigger === "briefing" ? briefing(live, st.checkpoint) : questions(live, st.checkpoint);
      }
      if (view !== "status") return;
      const left = v.remaining_s;
      render("Mission", `<p class="live-status">${Math.floor(left / 3600)} h ${String(Math.floor((left % 3600) / 60)).padStart(2, "0")} min left · ` +
        `${st.paused ? "paused" : `running ×${(+v.achieved_rate || 0).toFixed(1)}`}</p><p>${turnNote(live.replay, v.target_heading_deg)}</p>` +
        `<h2>Released forecasts</h2><ul>${forecasts(live)}</ul>` +
        `<p class="live-status">Game forecasts generated from the historical reconstruction.</p>`);
    },
    score(s) {
      shownScore = true;
      view = "score";
      const p = s.parts;
      const review = s.checkpoints.map((cp) => `<li>${cp.trigger} at ${(cp.t / 60).toFixed(0)} min: ` +
        Object.values(cp.results ?? {}).map((r) => `${r.correct ? "✓" : "✗"} ${r.given ?? "no answer"}`).join(", ") +
        (cp.reason ? ` — “${cp.reason}”` : "") + "</li>").join("");
      render("Voyage result", `<div class="live-stars">${"★".repeat(s.stars)}${"☆".repeat(3 - s.stars)}</div>` +
        `<p><b>${s.ship_lost ? "Ship lost: the mission failed and all cargo went down with her"
          : s.escaped ? "Escaped the storm" : ENDINGS[s.outcome] ?? `Did not escape (${s.outcome})`}</b></p>` +
        `<p>${(s.elapsed_s / 60).toFixed(0)} min · ${s.lost_teu} TEU lost (grade ${s.grade}) · ${s.answers_correct}/${s.answers_total} judgements right</p>` +
        `<p>Escape ${p.escape}/40 · Cargo ${p.cargo}/40 · Judgement ${p.judgement}/20 · <b>Total ${s.total}/100</b></p><ul>${review}</ul>` +
        `<p class="live-status">Game scoring for training, not a professional assessment.</p>`);
    },
  };
}
