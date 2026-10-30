"""Full-screen terminal instruments and player input; physics stays in Session."""

import curses
import math
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime

KNOTS_PER_MPS = 3600 / 1852


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def utc_stamp(value):
    return (
        datetime.fromisoformat(value).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    )


def instruments(view):
    v, c = view["vessel"], view["cargo"]
    retained = 100 * (1 - c["loss_fraction"])
    filled = round(retained / 5)
    wind = math.hypot(v["wind_east_mps"], v["wind_north_mps"])
    current = math.hypot(v["current_east_mps"], v["current_north_mps"])
    rate = "MAX" if view["rate"] == "max" else f"{view['rate']:g}x"
    achieved = 0 if view["paused"] else view["achieved_rate"]
    return [
        utc_stamp(view["utc"]),
        (
            f"Elapsed {clock(view['elapsed_s'])}  Remaining {clock(view['remaining_s'])}"
            f"  Rate {rate} / actual {achieved:.1f}x"
        ),
        f"Position  LAT {v['lat']:.6f}   LON {v['lon']:.6f}  (degrees)",
        f"Heading   actual {v['heading_deg']:06.2f} deg    target {view['target_heading_deg']:06.2f} deg",
        (
            f"Speed     water {v['speed_through_water_mps'] * KNOTS_PER_MPS:.2f} kn"
            f"  ground {v['speed_over_ground_mps'] * KNOTS_PER_MPS:.2f} kn"
            f"  target {view['target_speed_knots']:.2f} kn"
        ),
        (
            f"Motion    roll {v['roll_deg']:+.2f} deg  pitch {v['pitch_deg']:+.2f} deg"
            f"  heave {v['heave_m']:+.2f} m"
        ),
        (
            f"Observed  Hs {v['hs_m']:.2f} m  Tp {v['tp_s']:.2f} s"
            f"  waves from {v['wave_from_deg']:.0f} deg"
        ),
        f"          wind {wind:.2f} m/s  current {current * KNOTS_PER_MPS:.2f} kn",
        (
            f"Cargo     [{'#' * filled}{'-' * (20 - filled)}] {retained:.2f}% TEU retained"
            f"  grade {c['grade']}"
        ),
        (
            f"          {c['remaining_units']}/{c['total_units']} units aboard"
            f"  lost {c['lost_teu']}/{c['total_teu']} TEU"
        ),
    ]


def forecast_lines(view, width):
    """Format only the reports released through the player-visible state."""
    lon, lat = view["vessel"]["lon"], view["vessel"]["lat"]

    def here(report):
        w, e, s, n = report["sector_bbox"]
        x, y = report["sector"]
        return (
            w <= lon
            and (lon < e or x == 2 and lon == e)
            and s <= lat
            and (lat < n or y == 2 and lat == n)
        )

    def stamp(value):
        return datetime.fromisoformat(value).astimezone(UTC).strftime("%m-%d %H:%MZ")

    lines = []
    for r in sorted(
        view["reports"], key=lambda r: (not here(r), -r["issued_s"], r["id"])
    ):
        hs, wind = r["hs_range_m"], r["wind_range_mps"]
        text = [
            (
                f"Sector {r['sector']} {'[HERE] ' if here(r) else ''}"
                f"issued {stamp(r['issued_at'])}"
            ),
            f"  Valid {stamp(r['valid_from'])} -> {stamp(r['valid_to'])}",
            (
                f"  Hs {hs[0]:g}-{hs[1]:g} m | wind {wind[0]:g}-{wind[1]:g} m/s | "
                f"waves from {r['wave_from_deg']:g} deg / {r['wave_period_s']:g} s"
            ),
            f"  Trend: {r['trend']}. {r['reason']}",
        ]
        for line in text:
            lines.extend(textwrap.wrap(line, max(1, width), subsequent_indent="  "))
        lines.append("")
    return lines or ["No released forecast is currently applicable to this location."]


@dataclass
class Input:
    text: str = ""
    cursor: int = 0
    selected: bool = False

    @classmethod
    def prefilled(cls, value):
        return cls(value, len(value), True)

    def edit(self, key):
        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            if self.selected:
                self.text, self.cursor = "", 0
            elif self.cursor:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
                self.cursor -= 1
        elif key == curses.KEY_DC:
            self.text = (
                ""
                if self.selected
                else self.text[: self.cursor] + self.text[self.cursor + 1 :]
            )
            self.cursor = min(self.cursor, len(self.text))
        elif key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key in (curses.KEY_HOME, "\x01"):
            self.cursor = 0
        elif key in (curses.KEY_END, "\x05"):
            self.cursor = len(self.text)
        elif key == "\x15":
            self.text, self.cursor = "", 0
        elif isinstance(key, str) and key.isascii() and key.isprintable():
            if self.selected:
                self.text, self.cursor = "", 0
            if len(self.text) < 256:
                self.text = self.text[: self.cursor] + key + self.text[self.cursor :]
                self.cursor += len(key)
        self.selected = False


class Screen:
    def __init__(self, window, controls):
        self.window, self.controls = window, controls
        self.mode = None
        self.field = 0
        self.fields = []
        self.command_input = Input()
        self.scroll = 0
        self.message = "Escape the adverse-weather region within the time budget."
        self.was_paused = False
        self.report_ids = None

    def open_helm(self, view):
        self.mode, self.field = "helm", 0
        self.original_targets = (view["target_heading_deg"], view["target_speed_knots"])
        self.initial_text = [f"{v:.6g}" for v in self.original_targets]
        self.fields = [Input.prefilled(t) for t in self.initial_text]

    def apply_helm(self):
        targets = [
            original if field.text == initial else float(field.text)
            for field, initial, original in zip(
                self.fields, self.initial_text, self.original_targets
            )
        ]
        self.controls.resume_pending = False
        self.controls.session.set_order(*targets)
        self.message = self.controls.resume()
        self.mode = None
        self.was_paused = False

    def handle(self, key, view):
        if key == "\x03":
            self.controls.command("quit")
            return
        if key == curses.KEY_RESIZE:
            self.window.clear()
            return
        height, width = self.window.getmaxyx()
        small = height < 24 or width < 80
        if small and key == "p" and self.mode != "command":
            self.controls.pause()
            return
        if small and self.mode == "helm" and key in ("\n", "\r", curses.KEY_ENTER):
            return
        if key == "/" and self.mode != "command":
            self.mode, self.command_input = "command", Input()
            return
        if self.mode == "command":
            if key == "\x1b":
                self.mode = None
            elif key in ("\n", "\r", curses.KEY_ENTER):
                command = self.command_input.text
                self.mode = None
                if small and command.strip().removeprefix("/") == "resume":
                    raise ValueError(
                        "Resize the terminal to at least 80x24 before sailing"
                    )
                result = self.controls.command(command)
                if command.strip().removeprefix("/") == "resume":
                    self.was_paused = False
                self.message = (
                    "Live instruments and released forecasts are displayed above."
                    if command.strip().removeprefix("/") in ("status", "reports")
                    else result
                )
                updated = self.controls.session.visible()
                if (
                    updated["paused"]
                    and not updated["outcome"]
                    and not self.controls.closed
                ):
                    self.open_helm(updated)
            else:
                self.command_input.edit(key)
            return
        if key in ("j", "k"):
            self.controls.change_rate(-1 if key == "j" else 1)
            return
        if key in (curses.KEY_UP, curses.KEY_PPAGE):
            self.scroll = max(0, self.scroll - (5 if key == curses.KEY_PPAGE else 1))
            return
        if key in (curses.KEY_DOWN, curses.KEY_NPAGE):
            self.scroll += 5 if key == curses.KEY_NPAGE else 1
            return
        if self.mode == "helm":
            if key == "\x1b":
                self.mode = None
            elif key in ("\t", curses.KEY_BTAB):
                self.field = 1 - self.field
                self.fields[self.field].selected = True
            elif key in ("\n", "\r", curses.KEY_ENTER, "p"):
                self.apply_helm()
            else:
                self.fields[self.field].edit(key)
            return
        if key == "p" and not view["outcome"]:
            self.was_paused = True
            if view["paused"]:
                self.controls.pause()
                self.open_helm(view)
            else:
                # Signal cancellation before obtaining a display snapshot.
                self.controls.pause()
                self.open_helm(self.controls.session.visible())
                self.message = "Paused. Set targets, then Enter or p to continue."

    def put(self, row, col, text, style=0):
        height, width = self.window.getmaxyx()
        if 0 <= row < height and 0 <= col < width - 1:
            try:
                self.window.addnstr(row, col, text, width - col - 1, style)
            except curses.error:
                pass  # A terminal resize can invalidate coordinates between calls.

    def draw(self, view):
        h, w = self.window.getmaxyx()
        self.window.erase()
        state = view["outcome"] or ("PAUSED" if view["paused"] else "SAILING")
        engine = "DIRECTOR" if self.controls.engine.director else "LOCAL"
        self.put(
            0,
            0,
            f" TIDE | {engine} | {state.upper()}",
            curses.A_REVERSE | curses.A_BOLD,
        )
        if h < 24 or w < 80:
            self.put(
                2, 0, "Please resize the terminal to at least 80 columns x 24 rows."
            )
            self.put(3, 0, "Paused. /quit or Ctrl+C saves and exits.")
            self.put(5, 0, f"UTC {view['utc']} | {state}")
            if self.mode == "command":
                self.put(h - 2, 0, "/" + self.command_input.text)
            self.window.refresh()
            return
        for row, line in enumerate(instruments(view), start=1):
            self.put(row, 1, line)
        reason = view["stopping_reason"]
        if view["outcome"]:
            mission = ("Outcome: FAILED (ship lost)" if view["outcome"] == "ship_lost"
                       else "Outcome: " + view["outcome"])
        elif reason not in ("initial", "duration_reached", "manual_pause"):
            mission = "Checkpoint: " + reason
        else:
            mission = (
                "Mission: leave the whole adverse-weather region before time runs out."
            )
        self.put(11, 1, mission)
        lines = forecast_lines(view, w - 3)
        available = h - 20
        self.scroll = min(self.scroll, max(0, len(lines) - available))
        self.put(
            12,
            0,
            f" FORECASTS | game_simulated_forecast | {len(view['reports'])} sectors | Up/Down to scroll",
            curses.A_REVERSE,
        )
        self.put(
            13,
            1,
            "Game reconstruction, not an official historical forecast.",
            curses.A_DIM,
        )
        for row, line in enumerate(
            lines[self.scroll : self.scroll + available], start=14
        ):
            self.put(row, 1, line)
        self.put(
            h - 6,
            1,
            f"Forecast lines {self.scroll + 1}-{min(len(lines), self.scroll + available)}/{len(lines)}",
            curses.A_DIM,
        )
        self.put(h - 5, 1, self.message)
        self.put(h - 4, 0, "-" * (w - 1))
        cursor = None
        if self.mode == "helm":
            offsets = [max(0, field.cursor - 12) for field in self.fields]
            self.put(h - 3, 1, "TARGET HEADING")
            self.put(
                h - 3,
                17,
                f"{self.fields[0].text[offsets[0] : offsets[0] + 13]:<14}",
                curses.A_REVERSE if self.field == 0 else 0,
            )
            self.put(h - 3, 32, "deg   SPEED")
            self.put(
                h - 3,
                45,
                f"{self.fields[1].text[offsets[1] : offsets[1] + 13]:<14}",
                curses.A_REVERSE if self.field == 1 else 0,
            )
            self.put(h - 3, 60, "kn")
            self.put(
                h - 2,
                1,
                "Tab: switch field | type: replace | Enter/p: apply & sail | Esc: stay paused",
            )
            cursor = (
                h - 3,
                (17 if self.field == 0 else 45)
                + self.fields[self.field].cursor
                - offsets[self.field],
            )
        elif self.mode == "command":
            room = w - 4
            offset = max(0, self.command_input.cursor - room + 1)
            self.put(h - 3, 1, "/" + self.command_input.text[offset : offset + room])
            self.put(
                h - 2,
                1,
                "Enter: execute | Esc: cancel | quit, save, heading DEG, speed KN, rate N, help",
            )
            cursor = (h - 3, 2 + self.command_input.cursor - offset)
        else:
            self.put(
                h - 3,
                1,
                "MISSION ENDED - /quit to save and exit"
                if view["outcome"]
                else "Press p to pause and edit target heading / speed.",
            )
            self.put(
                h - 2, 1, f"Session: {self.controls.session.directory}", curses.A_DIM
            )
        self.put(
            h - 1,
            0,
            " j slower | k faster | p pause/edit | / command | /quit save & exit",
            curses.A_REVERSE,
        )
        curses.curs_set(1 if cursor else 0)
        if cursor:
            self.window.move(*cursor)
        self.window.refresh()

    def run(self):
        self.window.keypad(True)
        self.window.timeout(100)
        curses.set_escdelay(25)
        while not self.controls.closed:
            resume_pending = self.controls.resume_pending
            messages = self.controls.poll()
            if resume_pending and not self.controls.resume_pending:
                self.was_paused = False
            if messages:
                self.message = " | ".join(messages[-2:])
            view = self.controls.session.visible()
            height, width = self.window.getmaxyx()
            if (height < 24 or width < 80) and not view["paused"]:
                self.controls.pause()
                view = self.controls.session.visible()
            ids = {r["id"] for r in view["reports"]}
            if self.report_ids is not None and ids - self.report_ids:
                self.scroll, self.message = (
                    0,
                    "NEW FORECAST - see the report panel below.",
                )
            self.report_ids = ids
            if view["paused"] and not self.was_paused and not view["outcome"]:
                if self.mode != "command" and not self.controls.resume_pending:
                    self.open_helm(view)
                self.message = (
                    "Paused: "
                    + view["stopping_reason"]
                    + ". Edit targets or keep the current order."
                )
            self.was_paused = view["paused"]
            if not view["paused"] and self.mode == "helm":
                self.mode = None
            self.draw(view)
            try:
                key = self.window.get_wch()
            except curses.error:
                continue
            try:
                self.handle(key, view)
            except ValueError as error:
                self.message = str(error)


def run_screen(controls):
    curses.wrapper(lambda window: Screen(window, controls).run())
