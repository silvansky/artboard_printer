#!/usr/bin/env python3
"""Print an ANSI-coloured text file onto a late.sh artboard through a zmx session.

The target zmx session must be running `ssh late.sh` with the Artboard open in
active (editing) mode. Drawing starts at the current artboard cursor, which
becomes the top-left corner of the art.

Each glyph is sent on its own: Ctrl+K opens the colour picker, six hex digits
set the colour, Enter applies it, then the glyph goes in as a bracketed paste
(the artboard drops non-ASCII keystrokes but accepts pasted text).

The board silently drops keystrokes under load, so the driver steers by the
artboard's own `Cursor x,y` readout in the Info panel: every chunk of cells is
verified and redrawn from its start if the cursor did not land where it should.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass

ESC = "\x1b"
CTRL_K = "\x0b"
ENTER = "\r"
RIGHT = f"{ESC}[C"
LEFT = f"{ESC}[D"
DOWN = f"{ESC}[B"
UP = f"{ESC}[A"
PASTE_START = f"{ESC}[200~"
PASTE_END = f"{ESC}[201~"

BLOCK = "█"
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]|\x1b.")
CANVAS = (384, 192)
# Readings that must agree before the lagging panel counts as settled.
STILL_READS = 2
INFO_CURSOR = re.compile(r"Cursor\s+(\d+),(\d+)")
INFO_MODE = re.compile(r"Mode\s+(\w+)")

BASIC_COLORS = [
    (0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16),
    (36, 114, 200), (188, 63, 188), (17, 168, 205), (229, 229, 229),
    (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
    (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255),
]

Color = tuple
Pos = tuple


@dataclass(frozen=True)
class Cell:
    ch: str
    color: Color
    width: int


@dataclass(frozen=True)
class Op:
    """One keystroke batch and the columns the cursor should gain from it."""
    keys: str
    advance: int


def xterm256(index: int) -> Color:
    if index < 16:
        return BASIC_COLORS[index]
    if index < 232:
        index -= 16
        levels = (0, 95, 135, 175, 215, 255)
        return (levels[index // 36], levels[(index // 6) % 6], levels[index % 6])
    value = 8 + 10 * (index - 232)
    return (value, value, value)


def display_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def apply_sgr(params: str, fg: Color | None, bg: Color | None):
    codes = [int(p) if p else 0 for p in params.split(";")] or [0]
    i = 0
    while i < len(codes):
        code = codes[i]
        if code == 0:
            fg = bg = None
        elif code == 39:
            fg = None
        elif code == 49:
            bg = None
        elif 30 <= code <= 37:
            fg = BASIC_COLORS[code - 30]
        elif 90 <= code <= 97:
            fg = BASIC_COLORS[code - 90 + 8]
        elif 40 <= code <= 47:
            bg = BASIC_COLORS[code - 40]
        elif 100 <= code <= 107:
            bg = BASIC_COLORS[code - 100 + 8]
        elif code in (38, 48) and i + 1 < len(codes):
            mode = codes[i + 1]
            if mode == 2 and i + 4 < len(codes):
                color = tuple(codes[i + 2:i + 5])
                i += 4
            elif mode == 5 and i + 2 < len(codes):
                color = xterm256(codes[i + 2])
                i += 2
            else:
                i += 1
                color = None
            if color is not None:
                if code == 38:
                    fg = color
                else:
                    bg = color
        i += 1
    return fg, bg


def render(text: str, fg: Color | None, bg: Color | None, default_fg: Color):
    text = OTHER_ESCAPES.sub("", text)
    for ch in text:
        if ch == "\t":
            yield from [None] * 4
        elif ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf"):
            # A blank cell only carries its background, which the artboard
            # cannot paint; a full block in that colour is the closest thing.
            yield Cell(BLOCK, bg, 1) if bg else None
        else:
            yield Cell(ch, fg or bg or default_fg, display_width(ch))


def parse_line(line: str, fg: Color | None, bg: Color | None, default_fg: Color):
    """Return (cells, fg, bg); a cell is None where nothing should be drawn."""
    cells: list[Cell | None] = []
    pos = 0
    for match in SGR.finditer(line):
        cells.extend(render(line[pos:match.start()], fg, bg, default_fg))
        fg, bg = apply_sgr(match.group(1), fg, bg)
        pos = match.end()
    cells.extend(render(line[pos:], fg, bg, default_fg))
    return cells, fg, bg


def parse_art(text: str, default_fg: Color) -> list[list[Cell | None]]:
    fg = bg = None
    grid = []
    for line in text.split("\n"):
        cells, fg, bg = parse_line(line.rstrip("\r"), fg, bg, default_fg)
        grid.append(cells)
    while grid and not any(grid[-1]):
        grid.pop()
    return grid


def color_keys(color: Color) -> str:
    return f"{CTRL_K}{'%02X%02X%02X' % color}{ENTER}"


def paste_keys(text: str) -> str:
    return f"{PASTE_START}{text}{PASTE_END}"


def row_ops(row, reuse_color: bool = False, group: bool = False,
            painted: Color | None = None) -> list[Op]:
    """Keystroke batches for one row, trailing blanks trimmed."""
    while row and row[-1] is None:
        row = row[:-1]
    ops: list[Op] = []
    i = 0
    while i < len(row):
        if row[i] is None:
            gap = 1
            while i + gap < len(row) and row[i + gap] is None:
                gap += 1
            ops.append(Op(RIGHT * gap, gap))
            i += gap
            continue
        cell = row[i]
        text, advance = cell.ch, cell.width
        i += 1
        if group:
            while i < len(row) and row[i] is not None and row[i].color == cell.color:
                text += row[i].ch
                advance += row[i].width
                i += 1
        keys = ""
        if not reuse_color or cell.color != painted:
            keys += color_keys(cell.color)
            painted = cell.color
        ops.append(Op(keys + paste_keys(text), advance))
    return ops


def build_steps(grid, reuse_color: bool = False, group: bool = False) -> list[str]:
    """Blind keystroke stream, for --dry-run; the live driver steers by cursor."""
    steps: list[str] = []
    for index, row in enumerate(grid):
        ops = row_ops(row, reuse_color=reuse_color, group=group)
        steps.extend(op.keys for op in ops)
        column = sum(op.advance for op in ops)
        if column:
            steps.append(LEFT * column)
        if index < len(grid) - 1:
            steps.append(DOWN)
    if len(grid) > 1:
        steps.append(UP * (len(grid) - 1))
    return steps


class Board:
    """The artboard as seen and driven through one zmx session."""

    def __init__(self, session: str, delay: float, settle: float = 0.5):
        self.session = session
        self.delay = delay
        self.settle = settle

    def send(self, keys: str) -> None:
        if keys:
            subprocess.run(["zmx", "send", self.session], input=keys.encode(), check=True)

    def screen(self) -> str:
        out = subprocess.run(["zmx", "history", self.session, "--vt"],
                             capture_output=True).stdout.decode("utf-8", "replace")
        return SGR.sub("", out)

    def state(self):
        screen = self.screen()
        cursor = INFO_CURSOR.findall(screen)
        mode = INFO_MODE.findall(screen)
        return (
            (int(cursor[-1][0]), int(cursor[-1][1])) if cursor else None,
            mode[-1] if mode else None,
        )

    def cursor(self, previous: Pos | None = None, timeout: float = 5.0) -> Pos:
        """The cursor, read off the lagging Info panel.

        The panel trails the session by about half a second, so a plain read
        after sending keys returns the position from before them. When
        `previous` is given the read waits for the value to move off it first,
        then for it to hold still.
        """
        time.sleep(self.settle)
        deadline = time.time() + timeout
        moved = previous is None
        last, held = None, 0
        while time.time() < deadline:
            current, _ = self.state()
            if current is not None:
                if previous is not None and current != previous:
                    moved = True
                held = held + 1 if current == last else 0
                if moved and held >= STILL_READS:
                    return current
                last = current
            time.sleep(0.2)
        if last is None:
            raise RuntimeError("no Cursor readout — is the artboard in active mode?")
        return last

    def move_to(self, target: Pos, attempts: int = 6) -> Pos:
        position = self.cursor()
        for _ in range(attempts):
            dx, dy = target[0] - position[0], target[1] - position[1]
            if not dx and not dy:
                return position
            keys = (RIGHT if dx > 0 else LEFT) * abs(dx) + (DOWN if dy > 0 else UP) * abs(dy)
            for piece in chunks(keys, 60):
                self.send(piece)
                time.sleep(self.delay)
            position = self.cursor(previous=position)
        return position

    def run(self, ops: list[Op], start: Pos, chunk: int, attempts: int = 4) -> int:
        """Draw one row from `start`; returns how many chunks had to be redrawn."""
        redrawn, column = 0, start[0]
        for group in [ops[i:i + chunk] for i in range(0, len(ops), chunk)]:
            expected = column + sum(op.advance for op in group)
            for attempt in range(attempts):
                before = (column, start[1])
                for op in group:
                    self.send(op.keys)
                    time.sleep(self.delay)
                landed = self.cursor(previous=before)
                # The row matters as much as the column: a dropped Ctrl+K
                # leaves its Enter to fall through as "move down one row",
                # which paints the rest of the row a line low while the
                # column still adds up.
                if landed == (expected, start[1]):
                    break
                redrawn += 1
                if attempt == attempts - 1:
                    # A glyph the board measures differently is not a lost
                    # keystroke; take the board's word for where we are.
                    self.move_to((landed[0], start[1]))
                    expected = landed[0]
                else:
                    self.move_to(before)
            column = expected
        return redrawn


def chunks(text: str, size: int):
    return [text[i:i + size] for i in range(0, len(text), size)]


def parse_color(value: str) -> Color:
    value = value.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise argparse.ArgumentTypeError(f"expected a RRGGBB hex colour, got {value!r}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def parse_rows(value: str) -> list[int]:
    rows: list[int] = []
    for part in value.split(","):
        if "-" in part.strip("-"):
            first, last = part.split("-")
            rows.extend(range(int(first), int(last) + 1))
        else:
            rows.append(int(part))
    return rows


def parse_pos(value: str) -> Pos:
    if not re.fullmatch(r"\d+,\d+", value):
        raise argparse.ArgumentTypeError(f"expected X,Y, got {value!r}")
    x, y = value.split(",")
    return (int(x), int(y))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Draw an ANSI-coloured file onto a late.sh artboard via zmx.",
        epilog="The session must have the artboard open in active mode (i / Enter).",
    )
    parser.add_argument("session", help="zmx session running `ssh late.sh`")
    parser.add_argument("file", help="text file, optionally with ANSI colour codes")
    parser.add_argument("--at", type=parse_pos,
                        help="canvas X,Y for the top-left corner (default: cursor)")
    parser.add_argument("--delay", type=float, default=15.0,
                        help="milliseconds between keystroke batches (default: 15)")
    parser.add_argument("--chunk", type=int, default=12,
                        help="cells drawn per cursor check (default: 12)")
    parser.add_argument("--width", type=int, help="crop to this many columns")
    parser.add_argument("--height", type=int, help="crop to this many rows")
    parser.add_argument("--default-fg", type=parse_color, default=(255, 255, 255),
                        help="colour for glyphs with no foreground (default: FFFFFF)")
    parser.add_argument("--reuse-color", action="store_true",
                        help="skip the picker while the colour is unchanged")
    parser.add_argument("--group", action="store_true",
                        help="paste same-coloured neighbours in one go")
    parser.add_argument("--rows", type=parse_rows,
                        help="only draw these 0-based rows, e.g. 3,13,23-25")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the keystrokes instead of sending them")
    args = parser.parse_args(argv)

    with open(args.file, encoding="utf-8", errors="replace") as handle:
        grid = parse_art(handle.read(), args.default_fg)
    if args.height:
        grid = grid[:args.height]
    if args.width:
        grid = [row[:args.width] for row in grid]
    if not grid:
        print("nothing to draw", file=sys.stderr)
        return 1

    if args.dry_run:
        sys.stdout.write("".join(build_steps(grid, args.reuse_color, args.group)))
        return 0

    board = Board(args.session, args.delay / 1000.0)
    cursor, mode = board.state()
    if cursor is None:
        print(f"no artboard Info panel in session {args.session!r} "
              "— open the Artboard in active mode first", file=sys.stderr)
        return 1
    if mode != "active":
        print(f"artboard is in {mode} mode; press i to edit", file=sys.stderr)
        return 1

    origin = args.at or board.cursor()
    span = (max(len(row) for row in grid), len(grid))
    if origin[0] + span[0] > CANVAS[0] or origin[1] + span[1] > CANVAS[1]:
        print(f"{span[0]}x{span[1]} art at {origin[0]},{origin[1]} runs off the "
              f"{CANVAS[0]}x{CANVAS[1]} canvas; top-left must be at most "
              f"{CANVAS[0] - span[0]},{CANVAS[1] - span[1]}", file=sys.stderr)
        return 1
    if board.move_to(origin) != origin:
        print(f"could not park the cursor at {origin}", file=sys.stderr)
        return 1

    wanted = set(args.rows) if args.rows else set(range(len(grid)))
    redrawn = 0
    for index, row in enumerate(grid):
        if index not in wanted:
            continue
        start = (origin[0], origin[1] + index)
        board.move_to(start)
        redrawn += board.run(row_ops(row, args.reuse_color, args.group), start, args.chunk)
        print(f"\rrow {index + 1}/{len(grid)}  redraws {redrawn}",
              end="", file=sys.stderr, flush=True)
    board.move_to(origin)
    glyphs = sum(1 for index, row in enumerate(grid) if index in wanted
                 for cell in row if cell)
    print(f"\r{len(wanted)} rows, {glyphs} glyphs, {redrawn} chunk redraws", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
