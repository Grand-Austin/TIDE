// Deck stowage for the 320 deck units of game/cargo.py, the same rule as the Blender loaded preview.
// Unit deck-NNN (index i = NNN-1): even i is a 20 ft box, odd i a 40 ft box; tier = i % 3 (the cargo category).
// Each tier has 9 bays (aft -> fore) x 9 rows (port -> starboard). A 40 ft box takes a slot; 20 ft boxes fill a
// slot's aft half, then its fore half. Positions are in the ship model frame (x starboard, y up, -z forward),
// measured from the CargoOrigin marker: the aft, port corner on top of the hatch covers.

export const BAY_PITCH = 13.1, BAY_LENGTH = 12.2, ROW_PITCH = 22 / 9, TIER_HEIGHT = 2.591, HALF_20 = 3.029;
export const PALETTE = ["#A33B2F", "#2F5D8A", "#3F7A4A", "#C9A227", "#D8D8D2", "#6B4E3D", "#7A8088"];

export function deckLayout(origin) {
  const units = [];
  for (let tier = 0; tier < 3; tier++) {
    let slot = 0, half = null;
    for (let i = tier; i < 320; i += 3) {
      let s, along, size;
      if (i % 2 === 1) [s, along, size] = [slot++, BAY_LENGTH / 2, 40];
      else if (half === null) [s, along, size, half] = [slot, 0.004 + HALF_20, 20, slot++];
      else [s, along, size, half] = [half, BAY_LENGTH - 0.004 - HALF_20, 20, null];
      const bay = Math.floor(s / 9), row = s % 9;
      if (bay > 8) throw new Error(`deck-${i + 1} does not fit in tier ${tier}`);
      units.push({
        id: `deck-${String(i + 1).padStart(3, "0")}`,
        size, tier, bay, row,
        position: [origin[0] + ROW_PITCH * (row + 0.5), origin[1] + tier * TIER_HEIGHT, origin[2] - BAY_PITCH * bay - along],
        color: PALETTE[(i * 5 + bay) % PALETTE.length],
      });
    }
  }
  return units;
}
