// The one ocean track the game draws: Le Havre to Halifax, the North Europe–Canada leg of a container service.
// It follows a real sea lane: out of the Seine bay, west along the English Channel south of the Lizard and the
// Scilly Isles, across the Atlantic and in south of the Grand Banks. It crosses the Biscay area where the storm
// scenarios start.
//
// The lane is the Eurostat SeaRoute maritime network (MARNET), computed once with the searoute package (Python,
// 1.6.0, Apache-2.0) on 2026-10-30: about 2592 nm, 47 network nodes between the two ports, which replace the
// network's own end nodes (within 2.5 km of each port). The game does not depend on searoute. This is a reference
// track for the map, not a scenario's own route and not for navigation.

import { haversineNm } from "./geo.js";

export const LE_HAVRE = { name: "Le Havre", country: "France", lon: 0.117, lat: 49.483 };
export const HALIFAX = { name: "Halifax", country: "Canada", lon: -63.583, lat: 44.65 };

const LANE = [
  [-0.109, 49.4978], [-0.3909, 49.6048], [-0.7545, 49.7429], [-1.3, 49.95], [-1.4411, 49.9578], [-1.6998, 49.9722],
  [-1.8527, 49.9807], [-1.9221, 49.9846], [-2.0498, 49.9917], [-2.0541, 49.9919], [-2.1859, 49.9849],
  [-2.9359, 49.9448], [-4.0883, 49.8833], [-4.8684, 49.8416], [-5.4916, 49.8083], [-5.631, 49.8009],
  [-5.7404, 49.795], [-5.9797, 49.7822], [-6.5833, 49.75], [-6.694, 49.7601], [-10.7091, 50.1269],
  [-11.7803, 50.2248], [-12.8532, 50.3228], [-15.6023, 50.4224], [-16.7231, 50.463], [-19.2306, 50.5539],
  [-19.5299, 50.5484], [-20.0005, 50.5398], [-20.509, 50.5304], [-25.6242, 50.4366], [-29.894, 50.1236],
  [-30.0001, 50.1159], [-30.1039, 50.1083], [-30.1707, 50.1034], [-31.5809, 50], [-31.9411, 49.9736],
  [-38.0964, 49.1767], [-38.9526, 49.0161], [-40.0007, 48.8194], [-41.6586, 48.5084], [-44.0207, 48.0652],
  [-47.121, 47.2954], [-49.6649, 46.6637], [-50.0021, 46.5586], [-55, 45], [-62.1839, 44.5016],
  [-63.4879, 44.5352],
];
export const OCEAN_TRACK = [[LE_HAVRE.lon, LE_HAVRE.lat], ...LANE, [HALIFAX.lon, HALIFAX.lat]];

export function routeLengthNm(track = OCEAN_TRACK) {
  let nm = 0;
  for (let i = 1; i < track.length; i++) nm += haversineNm(track[i - 1], track[i]);
  return nm;
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
