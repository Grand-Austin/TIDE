// Side panel of the 3D replay: clock, helm, sea, motion, cargo bars and the loss log.
// Shows released numbers only: no cargo thresholds, no escape region.

import { latest, sampleAt } from "./replay.js";

const DECK_TEU = 480; // game/cargo.py: hold cargo is protected, so only deck TEU can be lost
export const GRADE_COLORS = ["#4caf7d", "#9ccc65", "#e6c84f", "#f0954a", "#e25c4b", "#b8324a"];

export function cargoStatus(c) {
  return {
    totalPct: (100 * (c.total_teu - c.lost_teu)) / c.total_teu,
    deckPct: (100 * (DECK_TEU - c.lost_teu)) / DECK_TEU,
    grade: c.grade,
    lostTeu: c.lost_teu,
  };
}

// Where the waves meet the ship: 0 = on the bow, positive = from starboard.
export function relativeSea(heading, waveFrom) {
  const rel = ((((waveFrom - heading) % 360) + 540) % 360) - 180, a = Math.abs(rel);
  const name = a <= 30 ? "head seas" : a <= 60 ? "bow seas" : a <= 120 ? "beam seas" : a <= 150 ? "quartering seas" : "following seas";
  return { rel, label: a <= 30 || a > 150 ? name : `${name}, ${rel > 0 ? "starboard" : "port"}` };
}

export function lossLog(replay, t) {
  const log = replay.events.filter((e) => e.kind === "cargo_lost" && e.time_s <= t)
    .map((e) => ({ t: e.time_s, text: `${e.unit_ids.length} unit${e.unit_ids.length > 1 ? "s" : ""} overboard`, detail: e.unit_ids.join(", ") }));
  replay.cargo.forEach((c, i) => {
    if (i > 0 && c.grade > replay.cargo[i - 1].grade && c.t <= t) log.push({ t: c.t, text: `Cargo loss grade ${c.grade}`, grade: c.grade });
  });
  return log.sort((a, b) => b.t - a.t);
}

const $ = (id) => document.getElementById(id);
const hhmmss = (ms) => new Date(ms).toISOString().slice(11, 19);

export function createPanel({ replay, startMs, duration, dev, sea }) {
  const el = Object.fromEntries(["p-clock", "p-elapsed", "p-progress", "p-heading", "p-order", "p-speed", "p-relsea", "p-needle", "p-target",
    "p-wave", "p-hs", "p-tp", "p-wind", "p-weather", "p-roll", "p-pitch", "p-heave", "p-spark", "p-total", "p-total-bar", "p-deck",
    "p-deck-bar", "p-grade", "p-lost", "p-log", "p-toast", "p-dev"].map((id) => [id, $(id)]));
  el["p-dev"].hidden = !dev;
  const spark = el["p-spark"].getContext("2d");
  let shownGrade = null, lastT = 0, toastUntil = 0, logKey = "";

  return function update(s, now) {
    const order = latest(replay.orders, s.t), c = cargoStatus(latest(replay.cargo, s.t)), rs = relativeSea(s.heading, s.wave_from);
    el["p-clock"].textContent = `${hhmmss(startMs + s.t * 1000)} UTC`;
    el["p-elapsed"].textContent = `${hhmmss(s.t * 1000)} / ${hhmmss(duration * 1000)}`;
    el["p-progress"].style.width = `${(100 * s.t) / duration}%`;
    el["p-heading"].textContent = `${s.heading.toFixed(0)}°`;
    el["p-order"].textContent = `ordered ${Math.round(order.heading_deg)}°`;
    el["p-speed"].textContent = `${((s.sog * 3600) / 1852).toFixed(1)} kn ${replay.speed_label ?? "over ground"} · ordered ${order.speed_knots.toFixed(1)} kn`;
    el["p-relsea"].textContent = rs.label;
    el["p-needle"].setAttribute("transform", `rotate(${s.heading} 50 50)`);
    el["p-target"].setAttribute("transform", `rotate(${order.heading_deg} 50 50)`);
    el["p-wave"].setAttribute("transform", `rotate(${dev ? sea.fromDeg : s.wave_from} 50 50)`);
    el["p-hs"].textContent = `${(dev ? sea.hs : s.hs).toFixed(1)} m`;
    el["p-tp"].textContent = `${(dev ? sea.tp : s.tp).toFixed(1)} s`;
    const windKn = (Math.hypot(s.wind_e, s.wind_n) * 3600) / 1852;
    const windFrom = ((Math.atan2(-s.wind_e, -s.wind_n) * 180) / Math.PI + 360) % 360;
    el["p-wind"].textContent = `${windKn.toFixed(0)} kn from ${Math.round(windFrom)}°`;
    el["p-weather"].textContent = s.cloud === undefined ? "" : `Cloud ${Math.round(s.cloud)}% · rain ${s.precip.toFixed(1)} mm/h (ERA5)`;
    el["p-roll"].textContent = `${s.roll.toFixed(1)}°`;
    el["p-pitch"].textContent = `${s.pitch.toFixed(1)}°`;
    el["p-heave"].textContent = `${s.heave.toFixed(1)} m`;
    drawRoll(spark, replay, s.t);

    el["p-total"].textContent = `${c.totalPct.toFixed(1)}%`;
    el["p-deck"].textContent = `${c.deckPct.toFixed(0)}%`;
    for (const [bar, pct] of [["p-total-bar", (c.totalPct - 60) / 0.4], ["p-deck-bar", c.deckPct]]) {
      el[bar].style.width = `${Math.max(pct, 0)}%`;
      el[bar].style.background = GRADE_COLORS[c.grade];
    }
    el["p-grade"].textContent = `Grade ${c.grade}`;
    el["p-grade"].style.background = GRADE_COLORS[c.grade];
    el["p-lost"].textContent = c.lostTeu ? `${c.lostTeu} TEU lost` : "No loss";

    const log = lossLog(replay, s.t), key = `${log.length}`;
    if (key !== logKey) {
      logKey = key;
      el["p-log"].replaceChildren(...log.slice(0, 30).map((e) => {
        const li = document.createElement("li");
        li.textContent = `${hhmmss(startMs + e.t * 1000)}  ${e.text}`;
        if (e.detail) li.title = e.detail;
        if (e.grade !== undefined) li.style.color = GRADE_COLORS[e.grade];
        return li;
      }));
    }

    // a grade rise while playing forward gets a banner; the caller slows down while it shows
    let rose = false;
    if (shownGrade !== null && c.grade > shownGrade && s.t > lastT && s.t - lastT < 30) {
      el["p-toast"].textContent = `Cargo loss grade ${c.grade} — ${c.totalPct.toFixed(1)}% of cargo left`;
      el["p-toast"].style.borderColor = GRADE_COLORS[c.grade];
      toastUntil = now + 3000;
      rose = true;
    }
    el["p-toast"].hidden = now > toastUntil;
    shownGrade = c.grade;
    lastT = s.t;
    return rose;
  };
}

// Roll over the last two minutes, sampled from the replay itself so scrubbing needs no history.
function drawRoll(ctx, replay, t) {
  const { width: w, height: h } = ctx.canvas, span = 120;
  const peak = Math.max(10, ...Array.from({ length: 61 }, (_, i) => Math.abs(sampleAt(replay, t - span + i * 2).roll)));
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#2b4660";
  ctx.beginPath();
  ctx.moveTo(0, h / 2);
  ctx.lineTo(w, h / 2);
  ctx.stroke();
  ctx.strokeStyle = "#5cc8e8";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i <= span; i++) {
    const at = t - span + i, y = h / 2 + (at < 0 ? 0 : (sampleAt(replay, at).roll / peak) * (h / 2 - 2));
    i ? ctx.lineTo((i / span) * w, y) : ctx.moveTo(0, y);
  }
  ctx.stroke();
  ctx.fillStyle = "#a9bccd";
  ctx.font = "10px ui-monospace, monospace";
  ctx.fillText(`±${peak.toFixed(0)}°  stbd down ↓`, 4, 11);
}
