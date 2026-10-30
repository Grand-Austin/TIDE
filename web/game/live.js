// Live play against game/live.py (`uv run python -m game.live`, then ?live=<scenario id>).
// The server runs game.session.Session; this module streams its 1 s samples into the same column shape as a
// replay (replay.js), sends the player's orders, and shows forecasts, judgement checkpoints and the score.
// Nothing here simulates: every number shown comes from the session.

const COLUMNS = ["lon", "lat", "heading", "roll", "pitch", "heave", "hs", "tp", "wave_from", "wind_e", "wind_n", "cloud", "precip"];
const KN = 1.94384;
const DIRS = { "0,1": "North", "1,1": "North-east", "1,0": "East", "1,-1": "South-east", "0,-1": "South",
  "-1,-1": "South-west", "-1,0": "West", "-1,1": "North-west", "0,0": "Your area" };
const compass = (d) => ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round((((d % 360) + 360) % 360) / 45) % 8];

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
      replay.events.push({ time_s: e.t, kind: e.kind, detail: e.detail, unit_ids: e.units });
      for (const u of e.units) onLoss(u, e.t);
    }
    const v = st.view, last = replay.orders.at(-1);
    if (Math.abs(v.target_heading_deg - last.heading_deg) > 0.01 || Math.abs(v.target_speed_knots - last.speed_knots) > 0.01)
      replay.orders.push({ t: v.elapsed_s, heading_deg: v.target_heading_deg, speed_knots: v.target_speed_knots });
    live.state = st;
    return st;
  };
  // Arrow keys change the pending order; it is sent after a short pause in typing.
  live.steer = (dHeading, dSpeed) => {
    live.helm.heading = ((live.helm.heading + dHeading) % 360 + 360) % 360;
    live.helm.speed = Math.max(0, Math.min(18, live.helm.speed + dSpeed));
    clearTimeout(timer);
    timer = setTimeout(() => post("/api/order", { heading_deg: live.helm.heading, speed_knots: live.helm.speed }), 350);
  };
  live.pause = (paused) => post("/api/pause", { paused });
  live.rate = (r) => post("/api/rate", { rate: r });
  await live.poll();
  return live;
}

// ---------- overlay: forecasts, checkpoint dialog, result ----------
const CSS = `
  .live-forecasts { position: fixed; left: 12px; top: 12px; width: 300px; max-height: 45vh; overflow: auto; padding: 10px 12px;
    border-radius: 8px; background: var(--panel); font-size: 12px; }
  .live-forecasts h2, .live-card h2 { margin: 0 0 6px; font-size: 11px; letter-spacing: .12em; color: var(--muted); text-transform: uppercase; }
  .live-forecasts ul { margin: 0; padding-left: 16px; } .live-forecasts li { margin: 4px 0; }
  .live-status { margin-bottom: 6px; font-size: 13px; color: var(--text); }
  .live-modal { position: fixed; inset: 0; display: grid; place-items: center; background: rgba(5, 10, 15, .6); z-index: 20; }
  .live-modal[hidden] { display: none; }
  .live-card { width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 48px); overflow: auto; padding: 18px 20px;
    border-radius: 12px; background: #0e1b29; border: 1px solid #2b4660; color: var(--text); font-size: 14px; }
  .live-card h2 { font-size: 13px; color: var(--accent); }
  .live-card fieldset { border: 1px solid #2b4660; border-radius: 8px; margin: 10px 0; padding: 8px 10px; }
  .live-card legend { color: var(--muted); padding: 0 4px; } .live-card label { display: block; margin: 3px 0; cursor: pointer; }
  .live-card input[type=number], .live-card textarea { background: #08131d; color: var(--text); border: 1px solid #2b4660;
    border-radius: 6px; padding: 5px 7px; font: inherit; } .live-card textarea { width: 100%; box-sizing: border-box; min-height: 54px; }
  .live-card .row2 { display: flex; gap: 12px; flex-wrap: wrap; } .live-card .ok { color: #6fd3a0; } .live-card .bad { color: #ff7a6b; }
  .live-card .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
  .live-stars { font-size: 28px; color: var(--order); letter-spacing: 4px; }`;

export function liveOverlay() {
  document.head.append(Object.assign(document.createElement("style"), { textContent: CSS }));
  const box = Object.assign(document.createElement("aside"), { className: "live-forecasts" });
  box.innerHTML = `<div class="live-status" id="live-status"></div><h2>Released forecasts</h2><ul id="live-forecasts"></ul>`;
  const modal = Object.assign(document.createElement("div"), { className: "live-modal", hidden: true });
  modal.innerHTML = `<div class="live-card"><h2 id="live-title"></h2><div id="live-body"></div><div class="actions" id="live-actions"></div></div>`;
  document.body.append(box, modal);
  const $ = (id) => document.getElementById(id);
  const open = (title, html, actions) => {
    $("live-title").textContent = title;
    $("live-body").innerHTML = html;
    $("live-actions").replaceChildren(...actions.map(([label, fn]) => Object.assign(document.createElement("button"), { textContent: label, onclick: fn })));
    modal.hidden = false;
  };
  const close = () => (modal.hidden = true);
  let shownCheckpoint = null, shownScore = false;

  return {
    get open() { return !modal.hidden; },
    update(live) {
      const st = live.state, v = st.view, left = v.remaining_s;
      $("live-status").innerHTML = `<b>Live</b> · ${Math.floor(left / 3600)}h${String(Math.floor((left % 3600) / 60)).padStart(2, "0")} left · ` +
        (st.paused ? "paused" : `×${(+v.achieved_rate || 0).toFixed(1)}`) + " · ← → heading, ↑ ↓ speed, Space pause";
      const [sx, sy] = v.ship_sector;
      $("live-forecasts").innerHTML = v.reports.map((r) =>
        `<li><b>${DIRS[`${r.sector[0] - sx},${r.sector[1] - sy}`] ?? "Further away"}</b>: Hs ${r.hs_range_m[0]}–${r.hs_range_m[1]} m, ` +
        `wind ${(r.wind_range_mps[0] * KN).toFixed(0)}–${(r.wind_range_mps[1] * KN).toFixed(0)} kn, waves from ${compass(r.wave_from_deg)}, ` +
        `${r.wave_period_s} s, ${r.trend}<br><span style="color:var(--muted)">game forecast, valid to ${r.valid_to.slice(11, 16)} UTC</span></li>`)
        .join("") || "<li>None released yet.</li>";
      if (st.checkpoint && shownCheckpoint !== st.checkpoint.id) this.checkpoint(live, st.checkpoint);
      if (st.score && !shownScore) this.score(st.score);
    },
    checkpoint(live, cp) {
      shownCheckpoint = cp.id;
      const b = live.brief, o = live.helm;
      const intro = cp.trigger === "briefing" ? `<p>${b.objective}</p><p>${b.title}: ${b.start.slice(0, 16).replace("T", " ")} UTC, ` +
        `${(b.budget_s / 3600).toFixed(0)} h. S175 container ship with 1,200 TEU. The storm area's edge is not shown: read the sea and the forecasts.</p>` : "";
      const qs = cp.questions.map((q) => `<fieldset><legend>${q.prompt}</legend>` +
        q.options.map((x) => `<label><input type="radio" name="${q.id}" value="${x}"> ${x}</label>`).join("") + "</fieldset>").join("");
      open(cp.trigger === "briefing" ? "Briefing" : "Decision point", `${intro}<p><b>${cp.message}</b></p>${qs}` +
        `<fieldset><legend>Your orders</legend><div class="row2"><label>Heading ° <input type="number" id="cp-h" min="0" max="359" value="${o.heading.toFixed(0)}"></label>` +
        `<label>Speed kn <input type="number" id="cp-s" min="0" max="18" step="0.5" value="${o.speed.toFixed(1)}"></label></div>` +
        `<label>Why? <textarea id="cp-r" placeholder="What did you see, and why this order?"></textarea></label></fieldset>`,
        [["Submit and continue", async () => {
          const answers = Object.fromEntries(cp.questions.map((q) => [q.id, document.querySelector(`input[name="${q.id}"]:checked`)?.value ?? null]));
          const order = { heading_deg: ((+$("cp-h").value % 360) + 360) % 360, speed_knots: Math.max(0, Math.min(18, +$("cp-s").value || 0)) };
          Object.assign(live.helm, { heading: order.heading_deg, speed: order.speed_knots });
          const res = await post("/api/answer", { id: cp.id, answers, reason: $("cp-r").value, order });
          if (res.error) return;
          open("Feedback", cp.questions.map((q) => {
            const r = res[q.id];
            return `<p class="${r.correct ? "ok" : "bad"}">${r.correct ? "✓" : "✗"} ${q.prompt}<br>Your answer: ${r.given ?? "none"} · Expected: ${r.answer}</p>`;
          }).join(""), [["Continue", close]]);
        }]]);
    },
    score(s) {
      shownScore = true;
      const p = s.parts;
      const review = s.checkpoints.map((cp) => `<li>${cp.trigger} at ${(cp.t / 60).toFixed(0)} min: ` +
        Object.values(cp.results ?? {}).map((r) => `${r.correct ? "✓" : "✗"} ${r.given ?? "none"}`).join(", ") +
        (cp.reason ? ` — “${cp.reason}”` : "") + "</li>").join("");
      open("Voyage result", `<div class="live-stars">${"★".repeat(s.stars)}${"☆".repeat(3 - s.stars)}</div>` +
        `<p><b>${s.escaped ? "Escaped the storm" : `Did not escape (${s.outcome})`}</b> after ${(s.elapsed_s / 60).toFixed(0)} min · ` +
        `${s.lost_teu} TEU lost (grade ${s.grade}) · ${s.answers_correct}/${s.answers_total} judgements right</p>` +
        `<p>Escape ${p.escape}/40 · Cargo ${p.cargo}/40 · Judgement ${p.judgement}/20 · <b>Total ${s.total}/100</b></p><ul>${review}</ul>` +
        `<p style="color:var(--muted)">Game scoring for training, not a professional assessment.</p>`, [["Close", close]]);
    },
  };
}
