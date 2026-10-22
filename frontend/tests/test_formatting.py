import unittest
from datetime import datetime, timezone

from dash import html

from ui.formatting import short_timestamp, state_badge, timestamp_cell


class ShortTimestampTests(unittest.TestCase):
    def test_current_year_drops_year_seconds_and_zone(self) -> None:
        year = datetime.now(timezone.utc).year
        self.assertEqual(short_timestamp(f"{year}-09-03T14:41:40.713622Z"), "03 Sep 14:41")

    def test_other_year_keeps_the_year(self) -> None:
        self.assertEqual(short_timestamp("2019-03-04T11:00:00+00:00"), "04 Mar 2019 11:00")

    def test_missing_values_are_a_dash(self) -> None:
        self.assertEqual(short_timestamp(None), "-")
        self.assertEqual(short_timestamp(""), "-")

    def test_unparseable_values_are_returned_unchanged(self) -> None:
        self.assertEqual(short_timestamp("yesterday"), "yesterday")


class TimestampCellTests(unittest.TestCase):
    def test_keeps_the_stored_value_as_hover_text(self) -> None:
        cell = timestamp_cell("2019-03-04T11:00:00Z")
        self.assertIsInstance(cell, html.Span)
        self.assertEqual(cell.children, "04 Mar 2019 11:00")
        self.assertEqual(cell.title, "2019-03-04T11:00:00Z")

    def test_plain_text_when_there_is_nothing_to_shorten(self) -> None:
        self.assertEqual(timestamp_cell(None), "-")
        self.assertEqual(timestamp_cell("yesterday"), "yesterday")


class StateBadgeTests(unittest.TestCase):
    def test_colour_is_a_variable_and_state_an_attribute(self) -> None:
        badge = state_badge("Failed")
        self.assertEqual(badge.style, {"--state-color": "#B42318"})
        self.assertEqual(getattr(badge, "data-state"), "failed")


if __name__ == "__main__":
    unittest.main()
