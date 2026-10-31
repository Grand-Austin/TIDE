"""Frozen geometry, scheduled evidence and local mandatory stops."""

import bisect


def inside(polygon, lon, lat):
    if not polygon:
        return False
    sign = None
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        cross = (b[0] - a[0]) * (lat - a[1]) - (b[1] - a[1]) * (lon - a[0])
        if abs(cross) < 1e-12:
            continue
        current = cross > 0
        if sign is not None and sign != current:
            return False
        sign = current
    return True


class MissionRules:
    def __init__(self, gameplay, budget_s, bbox):
        if gameplay.get("version") != 1:
            raise ValueError("Unsupported gameplay version")
        self.gameplay, self.budget_s, self.bbox = gameplay, budget_s, bbox
        # Forecast sectors may reach past the operating area; the domain boundary still uses bbox.
        self.report_bbox = tuple(gameplay.get("report_bbox", bbox))
        # "field" releases every forecast area of the field; "nearby" keeps the ship's 3x3 neighbourhood.
        self.report_scope = gameplay.get("report_scope", "nearby")
        self.regions = gameplay["regions"]
        self.times = [r["time_s"] for r in self.regions]
        if (
            not self.times
            or self.times[0] != 0
            or any(b <= a for a, b in zip(self.times, self.times[1:]))
        ):
            raise ValueError("Region schedule must start at zero and increase")

    def contains(self, lon, lat, elapsed_s):
        index = max(0, bisect.bisect_right(self.times, elapsed_s) - 1)
        return inside(self.regions[index]["polygon"], lon, lat)

    def sector(self, lon, lat):
        w, e, s, n = self.report_bbox
        return min(2, max(0, int((lon - w) / (e - w) * 3))), min(
            2, max(0, int((lat - s) / (n - s) * 3))
        )

    def visible_reports(self, elapsed_s, lon, lat):
        x, y = self.sector(lon, lat)
        w, e, s, n = self.report_bbox
        return [
            dict(
                r,
                sector_bbox=[
                    w + r["sector"][0] * (e - w) / 3,
                    w + (r["sector"][0] + 1) * (e - w) / 3,
                    s + r["sector"][1] * (n - s) / 3,
                    s + (r["sector"][1] + 1) * (n - s) / 3,
                ],
            )
            for r in self.gameplay["reports"]
            if r["issued_s"] <= elapsed_s < r["valid_to_s"]
            and (self.report_scope == "field"
                 or (abs(r["sector"][0] - x) <= 1 and abs(r["sector"][1] - y) <= 1))
        ]

    def evaluate(self, sample, consumed):
        t = sample.time_s
        w, e, s, n = self.bbox
        if not w <= sample.lon <= e or not s <= sample.lat <= n:
            return "domain_boundary", []
        if not self.contains(sample.lon, sample.lat, t):
            return "escaped", [
                {
                    "time_s": t,
                    "kind": "escaped",
                    "detail": "Outside the complete adverse-weather region",
                }
            ]
        if t >= self.budget_s:
            return "timeout", [
                {"time_s": t, "kind": "timeout", "detail": "Time budget exhausted"}
            ]
        events = []
        for r in self.visible_reports(t, sample.lon, sample.lat):
            if r["id"] not in consumed:
                consumed.add(r["id"])
                events.append(
                    {"time_s": t, "kind": "report_released", "detail": r["id"]}
                )
        important = [
            e
            for e in events
            if any(
                r["id"] == e["detail"] and r["important"]
                for r in self.gameplay["reports"]
            )
        ]
        return ("weather_report" if important else None), events
