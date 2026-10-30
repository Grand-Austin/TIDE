// The one ocean track the game draws: Le Havre to Halifax, the North Europe–Canada leg of a container service.
// A great-circle line, sampled about every 40 nm, and it crosses the Bay of Biscay where the storm scenarios start.
//
// This is a reference track for the map, not the scenario's authored voyage (each scenario keeps its own
// voyage.json with its own from/to ports and course). It shows where an Atlantic crossing runs in this region.
// A Portugal–Halifax crossing was the alternative; its great circle passes about 380 nm south of the Biscay
// operating area, so it would not appear where the game is played.

import { greatCircleTrack, haversineNm, boundsOf } from "./geo.js";

export const LE_HAVRE = { name: "Le Havre", country: "France", lon: 0.117, lat: 49.483 };
export const HALIFAX = { name: "Halifax", country: "Canada", lon: -63.583, lat: 44.65 };

// One vertex per 40 nm keeps the line smooth at ocean zoom without drawing a point per kilometre.
export const OCEAN_TRACK = greatCircleTrack([LE_HAVRE.lon, LE_HAVRE.lat], [HALIFAX.lon, HALIFAX.lat], 40);

export function routeLengthNm(track = OCEAN_TRACK) {
  let nm = 0;
  for (let i = 1; i < track.length; i++) nm += haversineNm(track[i - 1], track[i]);
  return nm;
}

export function routeBounds(track = OCEAN_TRACK) {
  return boundsOf(track);
}

// The track and its two end labels as one FeatureCollection for a single GeoJSON source.
export function routeFeature() {
  const [from, to] = [OCEAN_TRACK[0], OCEAN_TRACK[OCEAN_TRACK.length - 1]];
  return { type: "FeatureCollection", features: [
    { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: OCEAN_TRACK } },
    { type: "Feature", properties: { label: `${LE_HAVRE.name} · France`, anchor: "right", role: "from" },
      geometry: { type: "Point", coordinates: from } },
    { type: "Feature", properties: { label: `${HALIFAX.name} · Canada`, anchor: "left", role: "to" },
      geometry: { type: "Point", coordinates: to } },
  ] };
}
