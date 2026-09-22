# artboard_printer

`artboard_print.py <zmx-session> <file>` paints an ANSI-coloured text file onto
the shared late.sh artboard by typing into a zmx session running `ssh late.sh`.
Pure stdlib; tests are `python3 -m unittest -q test_artboard_print`.

## The target

Upstream is [mpiorowski/late-sh](https://github.com/mpiorowski/late-sh); the
page lives in `late-ssh/src/app/artboard/` and its `CONTEXT.md` is the best
reference. Canvas is **384x192**, one **foreground** colour per cell — there is
no cell background, so a blank cell that only carries a background is painted
as `█` in that colour.

Keys used (`input.rs`, `data.rs`):

- `Ctrl+K` (0x0B) opens the colour picker; six hex digits focus and fill the
  hex field; `\r` applies and closes; `Esc` discards.
- Glyphs go in as a **bracketed paste** (`ESC[200~…ESC[201~`). This is not a
  nicety: `handle_byte` only accepts `is_ascii_graphic()`, so `█ ▀ 𜴗` typed as
  raw UTF-8 are dropped on the floor. Paste paints in the current paint colour
  and advances the cursor by the glyph's display width.
- Arrows move; `Home`/`End` jump to the canvas edge, not the line start.
- The Info panel reports `Mode`, `Color` and an absolute `Cursor x,y`. That
  readout is the only feedback channel there is — use it.

## Hard-won gotchas

- **The board silently drops keystrokes under load.** Never dead-reckon the
  cursor. Blind `Left x N` row returns turn one lost cell into a permanent
  offset for every row below it.
- **Verify both coordinates.** A dropped `Ctrl+K` leaves its `Enter` to reach
  the editor as "move down one row", so the rest of the row paints one line low
  while the column still adds up. Column-only checks pass and the row is lost.
- **The Info panel lags ~0.55s.** A read straight after sending returns the
  position from before it. Wait for the value to move off its previous reading
  and then hold still for two reads; without that, ~40% of chunks look wrong
  and get pointlessly redrawn.
- `zmx history <s> --vt` is the screen and costs ~6ms, so polling it is cheap;
  the sleeps around it are the whole runtime. Budget ~1s per verified chunk.
- **Space does not erase** despite what the in-app help says — it leaves both
  the cell and the cursor untouched. The working erase key is still unknown.
- `zmx send` is byte-for-byte over stdin: `subprocess.run([...], input=keys)`.

## Shape of the code

`parse_art` turns SGR text into a grid of `Cell(ch, color, width)`; `row_ops`
turns a row into `Op(keys, advance)` batches; `Board` drives one session and
owns all the cursor feedback. `build_steps` is the old blind stream, kept only
for `--dry-run`. `--rows A,B-C` redraws a subset, which is how a damaged patch
gets repaired without reprinting everything.

## Working on the live board

It is a shared public canvas that other people draw on. Check placement fits
(`CANVAS`) before starting, draw where the user pointed, and verify afterwards
by decoding the screen dump rather than trusting the exit code.
