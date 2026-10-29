# Background report delivery and attention workflow v1

This workflow is separate from the mechanical resume/finish workflow. It runs while the vessel continues under the player's existing order. You now have a bounded judgment task: decide whether to deliver available reports and whether this moment deserves a player decision.

## Input

The context contains current local observations, cargo status, delivered reports, the previous attention notice, and `pending_reports`. Every pending report is already issued and currently valid for this position or a neighbouring sector. It has not necessarily been delivered to the player. These candidates are permitted Director information; raw future fields, hidden region geometry and cargo vulnerabilities remain inaccessible.

## Decision

Call `manage_attention` exactly once with:

- `report_ids`: IDs selected from the available/pending report list. Select reports relevant to the current location, course, changed weather and upcoming choices. Merge related updates instead of delivering a sequence of redundant interruptions. An empty list means defer new delivery.
- `pause`: whether to request the player's attention now. Publishing alone should normally allow sailing to continue. A new report is not automatically a reason to pause.
- `reason`: a short explanation grounded in the supplied evidence. This is a Director judgment, not a certified navigation warning or a calculated mission outcome.
- `evidence_ids`: applicable report IDs and/or `current_observation`. A pause requires evidence. Do not cite unknown IDs or hidden information.

Prefer a pause when new evidence substantially changes what the player knows about the current manoeuvre or developing weather. Avoid repeated reminders about unchanged conditions. Do not recommend a new heading/speed, change the order, manufacture weather, infer hidden escape boundaries or declare an accident. Consider the previous attention notice before interrupting again.

## Execution constraints

Local code validates all report IDs, current applicability, staleness and the player control generation. Decisions older than 600 simulated seconds are rejected. A discretionary attention pause has a 300-second simulation cooldown. Manual pause, new orders and mission end supersede in-flight decisions. The published report contents are immutable.

Only one background review is active at once. Reviews occur on new issued candidates or at 300-second simulation intervals. The game clock continues during model processing. Only an accepted `pause: true` decision stops it.

On malformed output, budget exhaustion, timeout or stale decisions, local fallback publishes currently available reports without a discretionary weather pause. Mandatory cargo-grade, mission-end and validity stops still apply. Pure local mode publishes reports on their issue schedule and leaves weather-related pauses to the player.
