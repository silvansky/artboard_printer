# artboard_printer

Draws an ANSI-coloured text file onto the [late.sh](https://github.com/mpiorowski/late-sh)
artboard by feeding keystrokes to a zmx session.

```
python3 artboard_print.py <zmx-session> <file> [--at X,Y]
```

## Preconditions

- The zmx session runs `ssh late.sh`.
- The Artboard page is open (`4`) in **active mode** (`i` or `Enter`) — the
  script refuses to draw otherwise.
- The top-left corner goes at the current artboard cursor, or at `--at X,Y`.
  The canvas is 384x192 and the art must fit below and right of that point.

## How it draws

Per glyph: `Ctrl+K` opens the colour picker, six hex digits set the colour,
`Enter` applies it, then a bracketed paste (`ESC[200~…ESC[201~`) arms a
floating brush. `Ctrl+V` stamps the brush, `Space` dismisses it, and right
arrows advance by the pasted text's display width. This supports Unicode
glyphs and same-colour grouped runs. Current late.sh paste handling only
previews the brush; paste alone no longer paints or advances the cursor.

**The board drops keystrokes under load**, so the driver does not dead-reckon.
The Info panel reports an absolute `Cursor x,y`; after every chunk of cells the
driver reads it back (the panel trails the session by about half a second) and
redraws the chunk from its start if the cursor did not land where it should —
**both** coordinates, since a dropped `Ctrl+K` leaves its `Enter` to fall
through as "move down one row", painting the rest of a row a line low while the
column still adds up.
Every row is re-anchored to an absolute start column, so a loss can never
accumulate into the rows below — the failure mode that made blind
`Left x N` row returns shift every later line.
If movement or drawing still fails after the retry limit, the script stops
with an error instead of treating an unexpected cursor position as success.

Cells are read from truecolor, 256-colour and basic SGR codes:

- glyph with a foreground → that colour
- blank cell with a background → `█` in the background colour (the artboard
  paints foregrounds only)
- blank cell with no colour → skipped, cursor steps right

## Options

| flag | effect |
| --- | --- |
| `--at X,Y` | canvas position for the top-left corner (default: cursor) |
| `--delay MS` | pause between keystroke batches (default 15) |
| `--chunk N` | cells drawn per cursor check (default 12) |
| `--rows A,B-C` | only draw these 0-based rows, for repairing a patch |
| `--width N`, `--height N` | crop the art |
| `--default-fg RRGGBB` | colour for glyphs with no foreground |
| `--reuse-color` | skip the picker while the colour is unchanged |
| `--group` | paste same-coloured neighbours in one go |
| `--dry-run` | write the blind keystroke stream to stdout |

A 78x39 photo is about 3000 glyphs and takes roughly 15 minutes: the cursor
round-trip, not the keystrokes, is the cost. `--reuse-color --group` cut that
sharply on art with flat colour areas, and a larger `--chunk` trades fewer
checks for more work per repair.

## Tests

```
python3 -m unittest -q test_artboard_print
```
