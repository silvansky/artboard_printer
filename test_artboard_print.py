#!/usr/bin/env python3
import unittest
from unittest.mock import Mock, patch

from artboard_print import (
    BLOCK, CTRL_K, CTRL_V, DOWN, LEFT, RIGHT, UP,
    Board, Cell, Op, build_steps, parse_art, paste_keys, row_ops, xterm256,
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
            f"{CTRL_K}FF0000\r{paste_keys('a')}{CTRL_V} {RIGHT}",
            f"{CTRL_K}FF0000\r{paste_keys('b')}{CTRL_V} {RIGHT}",
        ])

    def test_reuse_color_picks_once(self):
        steps = build_steps(parse_art(f"{fg(RED)}ab", WHITE), reuse_color=True)
        self.assertEqual(steps[1], paste_keys("b") + CTRL_V + " " + RIGHT)

    def test_group_merges_same_color_run(self):
        steps = build_steps(parse_art(f"{fg(RED)}ab", WHITE), group=True)
        self.assertEqual(steps[0], f"{CTRL_K}FF0000\r{paste_keys('ab')}{CTRL_V} {RIGHT * 2}")

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

    def test_wide_run_is_stamped_once_and_moves_by_display_width(self):
        op, = row_ops(parse_art("aあ", WHITE)[0], group=True)
        self.assertEqual(op.advance, 3)
        self.assertTrue(op.keys.endswith(paste_keys("aあ") + CTRL_V + " " + RIGHT * 3))


class DrawingFeedback(unittest.TestCase):
    def setUp(self):
        self.board = Board("session", 0.0, settle=0.0)
        self.board.send = Mock()
        self.board.cursor = Mock()
        self.board.move_to = Mock()

    def test_failed_chunk_is_reanchored_and_retried(self):
        self.board.cursor.side_effect = [(10, 21), (11, 20)]
        self.assertEqual(self.board.run([Op("draw", 1)], (10, 20), 12), 1)
        self.board.move_to.assert_called_once_with((10, 20))
        self.assertEqual(self.board.send.call_count, 2)

    def test_stuck_cursor_is_an_error_not_success(self):
        self.board.cursor.return_value = (10, 20)
        with self.assertRaisesRegex(RuntimeError, "drawing failed after 4 attempts"):
            self.board.run([Op("draw", 1)], (10, 20), 12)
        self.assertEqual(self.board.send.call_count, 4)

    def test_cursor_clamps_at_right_canvas_edge(self):
        self.board.cursor.return_value = (383, 20)
        self.assertEqual(self.board.run([Op("draw", 2)], (382, 20), 12), 0)

    def test_failed_movement_stops_drawing(self):
        self.board.cursor.return_value = (10, 20)
        with self.assertRaisesRegex(RuntimeError, "could not move cursor"):
            Board.move_to(self.board, (11, 20), attempts=2)

    def test_movement_can_succeed_on_last_attempt(self):
        self.board.cursor.side_effect = [(10, 20), (11, 20)]
        self.assertEqual(Board.move_to(self.board, (11, 20), attempts=1), (11, 20))

    def test_history_failure_is_not_an_empty_panel(self):
        import subprocess
        with patch("artboard_print.subprocess.run") as run:
            run.side_effect = subprocess.CalledProcessError(1, "zmx")
            with self.assertRaises(subprocess.CalledProcessError):
                self.board.screen()
            self.assertTrue(run.call_args.kwargs["check"])


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
