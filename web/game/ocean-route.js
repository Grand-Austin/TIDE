// The planned voyage the game draws: the scenario's own authored route from game.live's briefing.
//
// A voyage artifact carries the route as WGS84 [lon, lat] pairs, the two named ports and the authored
// distance. The map draws this line only; the ship's actual trajectory stays a separate layer. The line
// is game context, not a navigation route and not a record of where a real ship sailed.

import { haversineNm, bearingDeg } from "./geo.js";

const NM_PER_RAD = (180 * 60) / Math.PI;
const RAD = Math.PI / 180;

const trackOf = (voyage) => (Array.isArray(voyage) ? voyage : Array.isArray(voyage?.track) ? voyage.track : []);

// Length along a voyage's own track, or along a track passed directly. No track means no voyage:
// there is no fixed fallback lane.
export function routeLengthNm(voyage) {
  const track = trackOf(voyage);
  let nm = 0;
  for (let i = 1; i < track.length; i++) nm += haversineNm(track[i - 1], track[i]);
  return nm;
}

// A label from one endpoint: "Name · Country" when the country is present, otherwise the name alone.
// It stays a plain string for the map's text field, so it never becomes HTML.
function endpointLabel(port) {
  const name = typeof port?.name === "string" ? port.name : "";
  const country = typeof port?.country === "string" ? port.country : "";
  return country ? `${name} · ${country}` : name;
}

// Point on the map for an endpoint: the port's own coordinates when authored, otherwise the track end.
function endpointPoint(port, fallback) {
  if (Number.isFinite(port?.lon) && Number.isFinite(port?.lat)) return [port.lon, port.lat];
  return fallback;
}

// Put each end label on the route side of its point, using the shorter longitude arc across the dateline.
function endAnchors(from, to) {
  const dLon = ((to[0] - from[0] + 540) % 360) - 180;
  if (Math.abs(dLon) > 1e-9) return dLon > 0 ? ["left", "right"] : ["right", "left"];
  return to[1] >= from[1] ? ["bottom", "top"] : ["top", "bottom"];
}

// The planned track and its two end labels as one FeatureCollection for a single GeoJSON source.
// Without an authored voyage the collection is empty: legacy replays show no invented route.
export function routeFeature(voyage) {
  const track = trackOf(voyage);
  if (track.length < 2) return { type: "FeatureCollection", features: [] };
  const [from, to] = [endpointPoint(voyage.origin, track[0]), endpointPoint(voyage.destination, track.at(-1))];
  const [fromAnchor, toAnchor] = endAnchors(from, to);
  return { type: "FeatureCollection", features: [
    { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: track } },
    { type: "Feature", properties: { label: endpointLabel(voyage.origin), anchor: fromAnchor, role: "from" },
      geometry: { type: "Point", coordinates: from } },
    { type: "Feature", properties: { label: endpointLabel(voyage.destination), anchor: toAnchor, role: "to" },
      geometry: { type: "Point", coordinates: to } },
  ] };
}
