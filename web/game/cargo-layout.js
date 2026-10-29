// Deck stowage for the 320 deck units of game/cargo.py, the same rule as the Blender loaded preview.
// Unit deck-NNN (index i = NNN-1): even i is a 20 ft box, odd i a 40 ft box; its cargo category (i % 3) is the tier
// it aims for. Each tier has 9 bays (aft -> fore) x 9 rows (port -> starboard); a slot holds one 40 ft box or two
// 20 ft boxes (aft half, fore half). Category by category, in id order, each unit takes the first free place on
// its tier, or the tier below, that stands on a box.
// The categories need 160, 161 and 159 half-slots, so the last two units (deck-318, deck-320) drop one tier;
// the free half-slots end up on top, so no box hangs over a gap.
// Positions are in the ship model frame (x starboard, y up, -z forward), from the CargoOrigin marker: the aft,
// port corner on top of the hatch covers.

export const BAY_PITCH = 13.1, BAY_LENGTH = 12.2, ROW_PITCH = 22 / 9, TIER_HEIGHT = 2.591, HALF_20 = 3.029;
export const PALETTE = ["#A33B2F", "#2F5D8A", "#3F7A4A", "#C9A227", "#D8D8D2", "#6B4E3D", "#7A8088"];
const SLOTS = 81, TIERS = 3;

export function deckLayout(origin) {
  const taken = Array.from({ length: TIERS }, () => new Uint8Array(SLOTS * 2)); // [tier][slot * 2 + half]
  const free = (tier, h) => !taken[tier][h] && (tier === 0 || taken[tier - 1][h]);
  const units = [];
  for (let category = 0; category < TIERS; category++) {
    for (let i = category; i < 320; i += 3) {
      const size = i % 2 === 1 ? 40 : 20;
      let place = null;
      for (let tier = category; tier >= 0 && !place; tier--)
        for (let h = 0; h < SLOTS * 2 && !place; h++)
          if (size === 20 ? free(tier, h) : h % 2 === 0 && free(tier, h) && free(tier, h + 1)) place = [tier, h];
      if (!place) throw new Error(`deck-${i + 1} does not fit`);
      const [tier, h] = place, slot = h >> 1, bay = Math.floor(slot / 9), row = slot % 9;
      taken[tier][h] = 1;
      if (size === 40) taken[tier][h + 1] = 1;
      const along = size === 40 ? BAY_LENGTH / 2 : h % 2 === 0 ? 0.004 + HALF_20 : BAY_LENGTH - 0.004 - HALF_20;
      units.push({
        id: `deck-${String(i + 1).padStart(3, "0")}`,
        size, tier, bay, row, half: size === 40 ? null : h % 2,
        position: [origin[0] + ROW_PITCH * (row + 0.5), origin[1] + tier * TIER_HEIGHT, origin[2] - BAY_PITCH * bay - along],
        color: PALETTE[(i * 5 + bay) % PALETTE.length],
      });
    }
  }
  return units;
}
