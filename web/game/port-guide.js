// The port guidance layer: which of the world's container ports matter right now. The global list stays in
// deepwater-ports.js; this module keeps the map readable by showing only the nearest few within 200 nm.
//
// The rule is a range rule, not a routing or safety rule. It answers "which ports could the ship reach from
// here", and says nothing about weather, traffic, draft or permission to enter.

import { DEEPWATER_PORTS } from "./deepwater-ports.js";
import { haversineNm, bearingDeg, circleTrack } from "./geo.js";

export const PORT_RANGE_NM = 200; // about 370 km
export const MAX_PORTS = 6; // the map labels only the closest few, so the chart stays readable

// The ports within maxNm of the ship, nearest first, at most limit of them.
export function nearbyPorts(ship, ports = DEEPWATER_PORTS, { maxNm = PORT_RANGE_NM, limit = MAX_PORTS } = {}) {
  return ports
    .map((p) => ({ ...p, distance_nm: haversineNm(ship, [p.lon, p.lat]), bearing_deg: bearingDeg(ship, [p.lon, p.lat]) }))
    .filter((p) => p.distance_nm <= maxNm)
    .sort((a, b) => a.distance_nm - b.distance_nm)
    .slice(0, limit);
}

// The nearby ports as map points. Distance travels as a property, so a click can report it.
export function portFeature(nearby) {
  return { type: "FeatureCollection", features: nearby.map((p) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
    properties: { name: p.name, country: p.country, distance_nm: p.distance_nm, bearing_deg: p.bearing_deg },
  })) };
}

// The circle that explains why these ports and not others.
export function rangeRing(ship, radiusNm = PORT_RANGE_NM) {
  return { type: "Feature", properties: { radius_nm: radiusNm },
    geometry: { type: "LineString", coordinates: circleTrack(ship, radiusNm) } };
}

// A bearing as three digits, the way a compass card and a helm order show it.
const compass = (deg) => String(Math.round(deg) % 360).padStart(3, "0");

// What a port says when clicked: which port, how far, on which bearing, and that the list is guidance only.
export function portPopup(p) {
  return `<b>${p.name}</b><p>${p.country} · ${Math.round(p.distance_nm)} nm · bearing ${compass(p.bearing_deg)}°</p>` +
    `<p class="fine">Container port on the game's guidance list. Distance and bearing are straight lines, not a route.</p>`;
}
