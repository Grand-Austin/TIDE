// Deck stowage for the 320 deck units of game/cargo.py (stack_overload): 64 stacks of 5 tiers. A stack fails as a
// whole, so its five boxes stand on top of each other here and leave the deck together.
// Unit deck-NNN (index i = NNN-1) sits in stack s at tier k where i = 10 * (s // 2) + s % 2 + 2 * k
// (game/cargo.py STACK_UNIT_INDICES): even stacks hold 20 ft boxes, odd stacks 40 ft boxes.
// Stacks fill 8 bays (aft -> fore) x 8 rows (port -> starboard), centred across the 9-row hatch covers.
// Positions are in the ship model frame (x starboard, y up, -z forward), from the CargoOrigin marker: the aft,
// port corner on top of the hatch covers. The Blender loaded preview still shows the earlier three-tier rule.

export const BAY_PITCH = 13.1, BAY_LENGTH = 12.2, ROW_PITCH = 22 / 9, TIER_HEIGHT = 2.591, HALF_20 = 3.029;
export const PALETTE = ["#A33B2F", "#2F5D8A", "#3F7A4A", "#C9A227", "#D8D8D2", "#6B4E3D", "#7A8088"];
export const STACKS = 64, TIERS = 5, ROWS = 8;

export function stackUnitIndex(stack, tier) {
  return 10 * Math.floor(stack / 2) + (stack % 2) + 2 * tier;
}

// Boxes leave a stack from the top down, so none is drawn hanging over a gap. Scenes exported with the older
// per-unit cargo model (response_damage_v1) lose single boxes mid-stack; this draws the n-th loss in a stack as its
// top remaining box. Times, counts and box sizes (one size per stack) stay the simulation's.
// Returns id -> drawn id; call it in loss-time order.
export function topDown(units) {
  const byId = new Map(units.map((u) => [u.id, u])), gone = new Map(); // stack -> boxes lost so far
  return (id) => {
    const u = byId.get(id), n = u ? gone.get(u.stack) ?? 0 : TIERS;
    if (n >= TIERS) return id;
    gone.set(u.stack, n + 1);
    return `deck-${String(stackUnitIndex(u.stack, TIERS - 1 - n) + 1).padStart(3, "0")}`;
  };
}

export function deckLayout(origin) {
  const units = [];
  for (let stack = 0; stack < STACKS; stack++) {
    const bay = Math.floor(stack / ROWS), row = stack % ROWS, size = stack % 2 ? 40 : 20;
    for (let tier = 0; tier < TIERS; tier++) {
      const i = stackUnitIndex(stack, tier);
      units.push({
        id: `deck-${String(i + 1).padStart(3, "0")}`,
        size, tier, bay, row, stack, half: null,
        position: [origin[0] + ROW_PITCH * (row + 1), origin[1] + tier * TIER_HEIGHT, origin[2] - BAY_PITCH * bay - BAY_LENGTH / 2],
        color: PALETTE[(i * 5 + bay) % PALETTE.length],
      });
    }
  }
  return units.sort((a, b) => a.id.localeCompare(b.id));
}
