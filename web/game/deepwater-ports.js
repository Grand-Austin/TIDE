// Container ports that take ocean-going ships, worldwide: the guidance list behind the 200 nm port layer.
//
// The canonical list is web/game/ports.json, so the browser, the tests and Python tools read one catalogue.
// This module only imports it and keeps the DEEPWATER_PORTS export used by port-guide.js.
//
// Selection: a port stays on this list when it has a container terminal that ocean-going ships call at. In the
// Bay of Biscay the list also keeps smaller regional container ports, because those are the realistic places to
// put in when the ship must leave the storm track. Depth is not a filter: the World Port Index records it
// unevenly, and a recorded value would claim more precision than the source supports.
//
// Coordinates come from the NGA World Port Index (Pub. 150), CSV downloaded on 2026-10-30, where that source has
// the port; the remaining entries were placed by hand. Country names are the World Port Index names.
// This list guides a training game. It is not a survey and not for navigation.
//
// Rendering: web/game/port-guide.js shows at most six of these within 200 nautical miles of the ship, so the
// whole list can stay global while the map stays readable.

import PORTS from "./ports.json" with { type: "json" };

export const DEEPWATER_PORTS = PORTS;
