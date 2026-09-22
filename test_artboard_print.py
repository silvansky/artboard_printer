#!/usr/bin/env python3
import unittest

from artboard_print import (
    BLOCK, CTRL_K, DOWN, LEFT, RIGHT, UP,
    Board, Cell, build_steps, parse_art, paste_keys, row_ops, xterm256,
)

WHITE = (255, 255, 255)
RED = (255, 0, 0)


def fg(rgb):
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def bg(rgb):
    return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


class ParseArt(unittest.TestCase):
    def test_plain_text_uses_default_color(self):
        self.assertEqual(parse_art("hi", WHITE), [[Cell("h", WHITE, 1), Cell("i", WHITE, 1)]])

    def test_truecolor_foreground(self):
        grid = parse_art(f"{fg(RED)}X", WHITE)
        self.assertEqual(grid, [[Cell("X", RED, 1)]])

    def test_color_carries_across_lines(self):
        grid = parse_art(f"{fg(RED)}A\nB", WHITE)
        self.assertEqual([c.color for row in grid for c in row], [RED, RED])

    def test_reset_restores_default(self):
        grid = parse_art(f"{fg(RED)}A\x1b[0mB", WHITE)
        self.assertEqual(grid[0][1], Cell("B", WHITE, 1))

    def test_background_space_becomes_block(self):
        grid = parse_art(f"{bg(RED)} ", WHITE)
        self.assertEqual(grid[0], [Cell(BLOCK, RED, 1)])

    def test_uncolored_space_is_skipped(self):
        self.assertEqual(parse_art("a b", WHITE)[0][1], None)

    def test_256_color_and_basic_codes(self):
        self.assertEqual(parse_art("\x1b[38;5;196mX", WHITE)[0][0].color, xterm256(196))
        self.assertEqual(parse_art("\x1b[31mX", WHITE)[0][0].color, (205, 49, 49))

    def test_wide_glyph_width(self):
        self.assertEqual(parse_art("あ", WHITE)[0][0].width, 2)

    def test_trailing_blank_rows_dropped(self):
        self.assertEqual(len(parse_art("a\n\n\n", WHITE)), 1)


class BuildSteps(unittest.TestCase):
    def test_color_picked_for_every_glyph(self):
        steps = build_steps(parse_art(f"{fg(RED)}ab", WHITE))
        self.assertEqual(steps[:2], [
            f"{CTRL_K}FF0000\r{paste_keys('a')}",
            f"{CTRL_K}FF0000\r{paste_keys('b')}",
        ])

    def test_reuse_color_picks_once(self):
        steps = build_steps(parse_art(f"{fg(RED)}ab", WHITE), reuse_color=True)
        self.assertEqual(steps[1], paste_keys("b"))

    def test_group_merges_same_color_run(self):
        steps = build_steps(parse_art(f"{fg(RED)}ab", WHITE), group=True)
        self.assertEqual(steps[0], f"{CTRL_K}FF0000\r{paste_keys('ab')}")

    def test_gaps_move_right(self):
        steps = build_steps(parse_art("a  b", WHITE))
        self.assertIn(RIGHT * 2, steps)

    def test_row_returns_to_start_column(self):
        steps = build_steps(parse_art("abc", WHITE))
        self.assertEqual(steps[-1], LEFT * 3)

    def test_rows_step_down_and_cursor_comes_home(self):
        steps = build_steps(parse_art("a\nb", WHITE))
        self.assertEqual(steps.count(DOWN), 1)
        self.assertEqual(steps[-1], UP)

    def test_wide_glyph_advances_two_columns(self):
        self.assertEqual(build_steps(parse_art("あ", WHITE))[-1], LEFT * 2)

    def test_trailing_gap_is_not_walked(self):
        self.assertEqual(build_steps(parse_art("a  ", WHITE))[-1], LEFT)


class RowOps(unittest.TestCase):
    def test_advance_matches_glyph_widths(self):
        ops = row_ops(parse_art("a\u3042", WHITE)[0])
        self.assertEqual([op.advance for op in ops], [1, 2])

    def test_gap_is_one_op_per_run(self):
        ops = row_ops(parse_art("a  b", WHITE)[0])
        self.assertEqual([op.advance for op in ops], [1, 2, 1])
        self.assertEqual(ops[1].keys, RIGHT * 2)

    def test_group_merges_into_one_op(self):
        ops = row_ops(parse_art(f"{fg(RED)}abc", WHITE)[0], group=True)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].advance, 3)


class InfoPanel(unittest.TestCase):
    def board(self, screen):
        board = Board("session", 0.0, settle=0.0)
        board.screen = lambda: screen
        return board

    def test_reads_cursor_and_mode(self):
        board = self.board("Mode       active\nCursor     12,34\n")
        self.assertEqual(board.state(), ((12, 34), "active"))

    def test_missing_panel_reads_as_nothing(self):
        self.assertEqual(self.board("no artboard here").state(), (None, None))

    def test_last_reading_wins_over_scrollback(self):
        board = self.board("Cursor     1,1\nCursor     9,9\nMode  view\n")
        self.assertEqual(board.state()[0], (9, 9))


if __name__ == "__main__":
    unittest.main()
