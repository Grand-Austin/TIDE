// Bottom-left map of the passage on a real basemap (MapLibre + OpenFreeMap, as web/prototype): the track sailed
// so far, the ship and its heading, and in a replay the rest of the recorded track. Live play adds the released
// forecast areas (coloured by forecast Hs), the scenario's operating area and the helm preview: where the current
// order takes the ship, from game.live's run of the ship model. Released information only: no escape region.
// Positions are the simulation's, at the storm's real location.
//
// Two guidance layers cross the whole ocean: the Le Havre–Halifax track (the Atlantic leg of a container voyage)
// and the world's container ports, of which only the nearest few within 200 nm of the ship are drawn.

import { toLocal, sampleAt, latest } from "./replay.js";
import { relativeSea, GRADE_COLORS } from "./panel.js";
import { addWeather, forecastField, HS_GRADIENT } from "./weather-layer.js";
import { beaufort } from "./live.js";
import { nearbyPorts, portFeature, rangeRing, portPopup, PORT_RANGE_NM } from "./port-guide.js";
import { routeFeature, routeBounds } from "./ocean-route.js";
import { haversineNm } from "./geo.js";

const LOSS_GROUP_S = 60; // cargo losses this close in time share one marker
const PEAK_ROLL_S = 30; // a marker reports the largest roll in this time before the first loss
const CLOSE_ZOOM = 10; // about 30 km across the small map: track, preview and loss markers stay apart
const INTRO_DELAY_MS = 900, INTRO_MS = 2600; // the map opens on the whole area, then flies in to the ship
const MIN_SPAN_DEG = 3; // a replay opens wide enough to show the nearest coast; the player can zoom in
const HS_COLORS = ["interpolate", ["linear"], ["get", "hs"], 3, "#4caf7d", 5, "#e6c84f", 8, "#f0954a", 11, "#b8324a"];

// Track points in metres (x east, z south) and the cumulative distance sailed at each sample.
export function trackGeometry(replay) {
  const pts = [], run = [0];
  for (let i = 0; i < replay.t.length; i++) {
    pts.push(toLocal(replay, replay.lon[i], replay.lat[i]));
    if (i > 0) run.push(run[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  }
  return { pts, run };
}

// Index of the last sample at or before t.
export function sampleIndex(T, t) {
  let lo = 0, hi = T.length - 1;
  if (t >= T[hi]) return hi;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (T[m] <= t) lo = m;
    else hi = m;
  }
  return lo;
}

// Released forecast reports as map polygons; sector_bbox is [west, east, south, north].
export function forecastAreas(reports) {
  return {
    type: "FeatureCollection",
    features: reports.map((r) => {
      const [w, e, s, n] = r.sector_bbox;
      const [f0, f1] = r.wind_range_mps.map(beaufort), force = f0 === f1 ? `F${f0}` : `F${f0}–${f1}`;
      return { type: "Feature", properties: { hs: r.hs_range_m[1],
        label: `${r.hs_range_m[0]}–${r.hs_range_m[1]} m${r.trend === "increasing" ? " ↑" : ""}\n${force}` },
        geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } };
    }),
  };
}

// "Steady on 130° in about 6 min" from a preview; settled_s null means still turning at the preview's end.
export function settleNote(p, nowS) {
  if (!p) return "";
  const h = Math.round(p.heading_deg) % 360;
  if (p.settled_s === null) return `Still turning to ${h}° after 8 min`;
  const left = p.settled_s - (nowS - p.from_s);
  return left < 20 ? `On the ordered ${h}°` : `Steady on ${h}° in about ${Math.ceil(left / 60)} min`;
}

// Cargo losses up to time t as map points, grouped by LOSS_GROUP_S, with the released sea state and ship motion at
// the first loss of each group. demand_g (live play) is the load the cargo model found at the failed stacks; the
// stacks' strengths stay hidden.
export function lossMarkers(replay, t) {
  const groups = [];
  for (const e of replay.events.filter((e) => e.kind === "cargo_lost" && e.time_s <= t).sort((a, b) => a.time_s - b.time_s)) {
    const g = groups.at(-1);
    if (g && e.time_s - g[0].time_s < LOSS_GROUP_S) g.push(e);
    else groups.push([e]);
  }
  return {
    type: "FeatureCollection",
    features: groups.map((g) => {
      const t0 = g[0].time_s, s = sampleAt(replay, t0), teu = g.map((e) => e.teu);
      const rolls = Array.from({ length: PEAK_ROLL_S + 1 }, (_, k) => sampleAt(replay, t0 - k).roll);
      const roll = rolls.reduce((a, b) => (Math.abs(b) > Math.abs(a) ? b : a), 0);
      const demand = g.map((e) => e.demand_g).filter(Number.isFinite);
      const grade = latest(replay.cargo, g.at(-1).time_s).grade;
      return { type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] }, properties: {
        t0, t1: g.at(-1).time_s, boxes: g.reduce((n, e) => n + e.unit_ids.length, 0),
        teu: teu.every(Number.isFinite) ? teu.reduce((a, b) => a + b, 0) : -1, demand: demand.length ? Math.max(...demand) : -1,
        hs: s.hs, tp: s.tp, wave_from: s.wave_from, heading: s.heading, kn: (s.sog * 3600) / 1852, roll,
        wind_kn: (Math.hypot(s.wind_e, s.wind_n) * 3600) / 1852, wind_from: ((Math.atan2(-s.wind_e, -s.wind_n) * 180) / Math.PI + 360) % 360,
        sea: relativeSea(s.heading, s.wave_from).label, grade, color: GRADE_COLORS[grade] } };
    }),
  };
}

// What a loss marker says: what went, and the sea and motion that came with it.
export function lossPopup(p, startMs) {
  const at = (s) => new Date(startMs + s * 1000).toISOString().slice(11, 16);
  const span = p.t1 - p.t0 >= 60 ? `${at(p.t0)}–${at(p.t1)}` : at(p.t0);
  const what = `${p.boxes} box${p.boxes > 1 ? "es" : ""}${p.teu >= 0 ? `, ${p.teu} TEU` : ""} overboard`;
  return `<b>${span} UTC · ${what}</b>` +
    `<p>${p.sea[0].toUpperCase()}${p.sea.slice(1)}: waves from ${Math.round(p.wave_from)}° · Hs ${p.hs.toFixed(1)} m, Tp ${p.tp.toFixed(0)} s · ` +
    `wind ${p.wind_kn.toFixed(0)} kn from ${Math.round(p.wind_from)}°</p>` +
    `<p>Heading ${Math.round(p.heading)}° at ${p.kn.toFixed(1)} kn · peak roll ${Math.abs(p.roll).toFixed(0)}° to ` +
    `${p.roll >= 0 ? "starboard" : "port"} in the half minute before</p>` +
    (p.demand >= 0 ? `<p>Sideways load at the stack tops: <b>${p.demand.toFixed(2)} g</b></p>` : "") +
    `<p class="fine">Game cargo model: a stack goes when its load stays above its strength. Not a prediction of real container losses.</p>`;
}

// The voyage plan across the operating area: a constant course through the start position, clipped to the area
// bbox [w, e, s, n] (degrees, locally flat). Returns [from, to] in [lon, lat], drawn in the direction of travel.
export function plannedRoute([lon, lat], courseDeg, [w, e, s, n]) {
  const c = (courseDeg * Math.PI) / 180, dx = Math.sin(c) / Math.cos((lat * Math.PI) / 180), dy = Math.cos(c);
  let lo = -Infinity, hi = Infinity;
  for (const [p, d, a, b] of [[lon, dx, w, e], [lat, dy, s, n]]) {
    if (Math.abs(d) < 1e-12) continue;
    const [t0, t1] = [(a - p) / d, (b - p) / d].sort((x, y) => x - y);
    [lo, hi] = [Math.max(lo, t0), Math.min(hi, t1)];
  }
  return [[lon + lo * dx, lat + lo * dy], [lon + hi * dx, lat + hi * dy]];
}

const line = (coords) => ({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } });
const outline = ([w, e, s, n]) => line([[w, s], [e, s], [e, n], [w, n], [w, s]]);

export function createMinimap(container, { replay, duration, live, field }) {
  const maplibre = globalThis.maplibregl, figure = container.closest("figure");
  const legend = document.getElementById("map-legend"), settle = document.getElementById("map-settle");
  let map = null, marker = null, placed = false, ready = false, follow = false, ship = [replay.lon[0], replay.lat[0]], drawnAt = -1, geo = null, count = -1;
  let reportsKey = "", shownPreview = null, lossKey = -1, setWeather = null, windOn = false;
  let portsOn = true, portShip = null; // the port layer redraws only after the ship has moved
  const startMs = Date.parse(replay.start_utc);
  if (maplibre) {
    let bounds;
    if (live) {
      const [w, e, s, n] = live.brief.area_bbox; // the scenario's operating area: leaving it ends the voyage
      bounds = [[w, s], [e, n]];
    } else {
      const lons = replay.lon.filter(Number.isFinite), lats = replay.lat.filter(Number.isFinite);
      const pad = (lo, hi) => [Math.min(lo, (lo + hi) / 2 - MIN_SPAN_DEG / 2), Math.max(hi, (lo + hi) / 2 + MIN_SPAN_DEG / 2)];
      const [[w, e], [s, n]] = [pad(Math.min(...lons), Math.max(...lons)), pad(Math.min(...lats), Math.max(...lats))];
      bounds = [[w, s], [e, n]];
    }
    map = new maplibre.Map({ container, style: "https://tiles.openfreemap.org/styles/dark", bounds, fitBoundsOptions: { padding: 12 },
      attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false });
    map.on("load", () => {
      for (const id of ["forecast", "area", "route", "ahead", "sailed", "preview"]) map.addSource(id, { type: "geojson", data: line([]) });
      map.addLayer({ id: "forecast", type: "fill", source: "forecast", paint: { "fill-color": HS_COLORS, "fill-opacity": 0 } }); // an anchor: the colour is the weather layer's
      if (field) (setWeather = addWeather(map, field, "forecast")).wind(windOn); // replays: the Copernicus field under everything else
      map.addLayer({ id: "forecast-edge", type: "line", source: "forecast", paint: { "line-color": HS_COLORS, "line-opacity": 0.6 } });
      map.addLayer({ id: "forecast-label", type: "symbol", source: "forecast", layout: { "text-field": ["get", "label"],
        "text-font": ["Noto Sans Regular"], "text-size": 11 }, paint: { "text-color": "#d6e2ee", "text-halo-color": "#0b1622", "text-halo-width": 1.2 } });
      map.addLayer({ id: "area", type: "line", source: "area", paint: { "line-color": "#a9bccd", "line-dasharray": [4, 3] } });
      // the ocean track: a reference line across the whole Atlantic, under the local track and the preview
      map.addSource("ocean", { type: "geojson", data: routeFeature() });
      map.addLayer({ id: "ocean", type: "line", source: "ocean", paint: { "line-color": "#8fa6ff", "line-width": 1.4,
        "line-dasharray": [5, 4], "line-opacity": 0.7 } });
      map.addLayer({ id: "ocean-label", type: "symbol", source: "ocean", filter: ["==", ["geometry-type"], "Point"],
        layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Regular"], "text-size": 11,
          "text-anchor": ["get", "anchor"] },
        paint: { "text-color": "#a9baff", "text-halo-color": "#0b1622", "text-halo-width": 1.2 } });
      // the voyage plan the storm interrupts: a long-dashed line with the destination at its far end
      map.addLayer({ id: "route", type: "line", source: "route", paint: { "line-color": "#e6eef6", "line-width": 1.5, "line-dasharray": [6, 3], "line-opacity": 0.75 } });
      map.addLayer({ id: "route-label", type: "symbol", source: "route", filter: ["==", ["geometry-type"], "Point"],
        layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Regular"], "text-size": 11, "text-anchor": ["get", "anchor"], "text-offset": [0, 0] },
        paint: { "text-color": "#e6eef6", "text-halo-color": "#0b1622", "text-halo-width": 1.2 } });
      map.addLayer({ id: "ahead", type: "line", source: "ahead", paint: { "line-color": "#9db6cc", "line-width": 1.5, "line-dasharray": [2, 2], "line-opacity": 0.8 } });
      map.addLayer({ id: "sailed", type: "line", source: "sailed", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#5cc8e8", "line-width": 3 } });
      map.addLayer({ id: "preview", type: "line", source: "preview", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#f2a93b", "line-width": 2.5, "line-dasharray": [1.5, 1.5] } });
      if (live) map.getSource("area").setData(outline(live.brief.area_bbox));
      const voyage = live?.brief.voyage;
      if (voyage) {
        const start = [replay.lon[0], replay.lat[0]], [from, to] = plannedRoute(start, voyage.course_deg, live.brief.area_bbox);
        // the label sits about 7 km ahead of the start, so it shows on the map zoomed in to the ship
        const k = Math.min(0.9, 0.06 / Math.hypot((to[0] - start[0]) * Math.cos((start[1] * Math.PI) / 180), to[1] - start[1]));
        const near = [start[0] + k * (to[0] - start[0]), start[1] + k * (to[1] - start[1])];
        map.getSource("route").setData({ type: "FeatureCollection", features: [line([from, to]),
          { type: "Feature", geometry: { type: "Point", coordinates: near },
            properties: { label: `Planned route to ${voyage.to}`, anchor: to[1] > from[1] ? "top" : "bottom" } }] });
      }
      // guidance ports: the nearest few container ports within PORT_RANGE_NM of the ship, and that range as a ring
      map.addSource("port-range", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "port-range", type: "line", source: "port-range", paint: { "line-color": "#6ee7b7",
        "line-width": 1, "line-dasharray": [2, 4], "line-opacity": 0.55 } });
      map.addSource("ports", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "ports", type: "circle", source: "ports", paint: { "circle-color": "#6ee7b7", "circle-radius": 4,
        "circle-stroke-color": "#0b1622", "circle-stroke-width": 1.2 } });
      map.addLayer({ id: "port-label", type: "symbol", source: "ports", layout: { "text-field": ["get", "name"],
        "text-font": ["Noto Sans Regular"], "text-size": 10, "text-anchor": "top", "text-offset": [0, 0.5] },
        paint: { "text-color": "#bfeee0", "text-halo-color": "#0b1622", "text-halo-width": 1.2 } });
      const portCard = new maplibre.Popup({ className: "loss-popup", maxWidth: "240px" });
      map.on("click", "ports", (e) => portCard.setLngLat(e.features[0].geometry.coordinates)
        .setHTML(portPopup(e.features[0].properties)).addTo(map));
      map.on("mouseenter", "ports", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "ports", () => (map.getCanvas().style.cursor = ""));
      map.addSource("losses", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "losses", type: "circle", source: "losses", paint: { "circle-color": ["get", "color"],
        "circle-radius": ["interpolate", ["linear"], ["get", "boxes"], 5, 7, 50, 13], "circle-stroke-color": "#0b1622", "circle-stroke-width": 1.5 } });
      map.addLayer({ id: "loss-count", type: "symbol", source: "losses", layout: { "text-field": ["to-string", ["get", "boxes"]],
        "text-font": ["Noto Sans Regular"], "text-size": 10, "text-allow-overlap": true }, paint: { "text-color": "#0b1622" } });
      const popup = new maplibre.Popup({ className: "loss-popup", maxWidth: "280px" });
      map.on("click", "losses", (e) => popup.setLngLat(e.features[0].geometry.coordinates).setHTML(lossPopup(e.features[0].properties, startMs)).addTo(map));
      map.on("mouseenter", "losses", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "losses", () => (map.getCanvas().style.cursor = ""));
      ready = true;
      if (!portsOn) setPorts(false); // the layer may have been switched off while the style was loading
      // a replay flies in to the ship; live play stays on the whole area, where every released forecast area shows
      if (!live) setTimeout(() => locate(INTRO_MS), INTRO_DELAY_MS);
    });
    container.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show"); // small map: start collapsed
    const el = document.createElement("div");
    el.className = "ship-mark";
    el.innerHTML = '<svg viewBox="0 0 18 18" width="18" height="18"><path d="M9 1 L14 16 L9 12.5 L4 16 Z" fill="#f2a93b" stroke="#0b1622" stroke-width="1"/></svg>';
    marker = new maplibre.Marker({ element: el, rotationAlignment: "map" });
    const expand = document.getElementById("map-expand");
    expand.onclick = () => {
      const big = figure.classList.toggle("big");
      expand.setAttribute("aria-pressed", String(big));
      expand.setAttribute("aria-label", big ? "Shrink the map" : "Enlarge the map");
      expand.title = big ? "Shrink the map (Esc)" : "Enlarge the map";
      expand.textContent = big ? "✕" : "⤢";
      map.resize();
      if (follow) map.jumpTo({ center: ship });
    };
    // a click on the small map enlarges it; once big, clicks pan and open loss markers as usual
    document.getElementById("map").addEventListener("click", () => figure.classList.contains("big") || expand.onclick());
    // and Esc or a click anywhere outside the map shrinks it again
    const shrink = () => figure.classList.contains("big") && expand.onclick();
    document.addEventListener("keydown", (e) => e.key === "Escape" && shrink());
    document.addEventListener("pointerdown", (e) => figure.contains(e.target) || shrink());
    // Locate: fly to the ship and keep it centred until the player pans the map themselves.
    const locateButton = document.getElementById("map-locate");
    const setFollow = (on) => {
      follow = on;
      locateButton.setAttribute("aria-pressed", String(on));
    };
    const locate = (duration = 1200) => {
      setFollow(true);
      map.flyTo({ center: ship, zoom: live ? map.getZoom() : Math.max(map.getZoom(), CLOSE_ZOOM), duration, essential: true });
    };
    locateButton.onclick = () => (follow ? setFollow(false) : locate());
    // Ocean: fit the whole Le Havre–Halifax track, and enlarge the map so the line is not a thumbnail
    document.getElementById("map-ocean").onclick = () => {
      setFollow(false);
      if (!figure.classList.contains("big")) expand.onclick();
      const [w, s, e, n] = routeBounds();
      map.fitBounds([[w, s], [e, n]], { padding: 24, duration: 900 });
    };
    // Ports: the guidance layer switches off as a whole, ring and labels included
    const portsButton = document.getElementById("map-ports");
    const setPorts = (on) => {
      portsOn = on;
      portsButton.setAttribute("aria-pressed", String(on));
      for (const id of ["ports", "port-label", "port-range"])
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
      portShip = null; // redraw the ring and the ports on the next frame
    };
    portsButton.onclick = () => setPorts(!portsOn);
    setPorts(portsOn);
    portsButton.title = `Container ports within ${PORT_RANGE_NM} nm`;
    // Streamlines off by default: replays draw the Copernicus wind, live play the released forecast wave direction.
    const windButton = document.getElementById("map-wind");
    windButton.hidden = !field && !live;
    if (live) [windButton.textContent, windButton.title] = ["Waves", "Show the forecast wave direction"];
    windButton.onclick = () => {
      windOn = !windOn;
      windButton.setAttribute("aria-pressed", String(windOn));
      setWeather?.wind(windOn);
    };
    map.on("dragstart", () => setFollow(false));
    for (const el of legend.querySelectorAll(".live-only")) el.hidden = !live;
    document.getElementById("map-route").hidden = !live?.brief.voyage;
    for (const el of legend.querySelectorAll(".field-only")) el.hidden = !field;
    for (const sw of legend.querySelectorAll(".sw.field, .sw.hs")) sw.style.setProperty("background", HS_GRADIENT);
  } else {
    figure?.setAttribute("hidden", ""); // MapLibre did not load (offline): keep the scene usable
  }

  return (t, s) => {
    if (replay.t.length !== count) [geo, count] = [trackGeometry(replay), replay.t.length]; // live: the track grows
    const { pts, run } = geo, i = sampleIndex(replay.t, t), [sx, sz] = toLocal(replay, s.lon, s.lat);
    if (marker) {
      ship = [s.lon, s.lat];
      marker.setLngLat(ship).setRotation(s.heading);
      if (follow && !map.isMoving()) map.jumpTo({ center: ship });
      if (!placed) {
        marker.addTo(map);
        placed = true;
      }
    }
    if (ready && portsOn && (portShip === null || haversineNm(portShip, ship) > 2)) {
      portShip = ship.slice();
      const near = nearbyPorts(ship);
      map.getSource("ports").setData(portFeature(near));
      map.getSource("port-range").setData({ type: "FeatureCollection", features: [rangeRing(ship)] });
    }
    setWeather?.(startMs + t * 1000);
    if (ready && Math.abs(t - drawnAt) >= 2) { // a line redraw per 2 s of scene time is plenty at this scale
      drawnAt = t;
      const at = (j) => [replay.lon[j], replay.lat[j]];
      map.getSource("sailed").setData(line([...Array.from({ length: i + 1 }, (_, j) => at(j)), [s.lon, s.lat]]));
      map.getSource("ahead").setData(line(live ? [] : [[s.lon, s.lat], ...Array.from({ length: replay.t.length - i - 1 }, (_, j) => at(i + 1 + j))]));
    }
    const lossCount = ready ? replay.events.filter((e) => e.kind === "cargo_lost" && e.time_s <= t).length : -1;
    if (lossCount !== lossKey && ready) {
      lossKey = lossCount;
      map.getSource("losses").setData(lossMarkers(replay, t));
    }
    if (ready && live) {
      const reports = live.state?.view.reports ?? [], key = reports.map((r) => r.id).join();
      if (key !== reportsKey) {
        reportsKey = key;
        map.getSource("forecast").setData(forecastAreas(reports));
        setWeather?.remove();
        const f = forecastField(reports); // released sectors only, see weather-layer.js
        setWeather = f && addWeather(map, f, "forecast");
        setWeather?.wind(windOn);
      }
      if (live.preview !== shownPreview || drawnAt === t) { // new preview, or the ship moved on along it
        shownPreview = live.preview;
        const ahead = shownPreview ? shownPreview.track.filter((p) => p[2] > t).map(([lon, lat]) => [lon, lat]) : [];
        map.getSource("preview").setData(line(ahead.length ? [[s.lon, s.lat], ...ahead] : []));
      }
      settle.textContent = settleNote(shownPreview, t);
    }
    const sailed = (run[i] + Math.hypot(sx - pts[i][0], sz - pts[i][1])) / 1000, pct = Math.min(100, (100 * t) / duration);
    return `${sailed.toFixed(1)} km · ${pct.toFixed(0)}%`;
  };
}
