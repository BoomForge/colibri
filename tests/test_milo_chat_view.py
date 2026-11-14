"""Presentation regression checks independent of model and private transcripts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from desktop.chat_view import day_label, natural_time, parse_timestamp, should_group


class CompactChatPresentationTests(unittest.TestCase):
    def setUp(self):
        self.au = timezone(timedelta(hours=11))
        self.now = datetime(2026, 11, 14, 17, 8, tzinfo=self.au)

    def test_timestamps_today_and_yesterday(self):
        self.assertEqual(
            natural_time(datetime(2026, 11, 14, 17, 8, tzinfo=self.au), self.now),
            "Today at 5:08 PM")
        self.assertEqual(
            natural_time(datetime(2026, 11, 13, 22, 38, tzinfo=self.au), self.now),
            "Yesterday at 10:38 PM")
        self.assertEqual(day_label(self.now, self.now), "TODAY")

    def test_utc_archive_timestamp_converts_locally(self):
        timestamp = "2026-11-14T06:08:00+00:00"
        self.assertEqual(natural_time(timestamp, self.now), "Today at 5:08 PM")

    def test_grouping_respects_role_gap_and_midnight(self):
        at = datetime(2026, 11, 14, 17, 0, tzinfo=self.au)
        self.assertTrue(should_group("assistant", at, "assistant",
                                     at + timedelta(minutes=5)))
        self.assertFalse(should_group("assistant", at, "user",
                                      at + timedelta(minutes=1)))
        self.assertFalse(should_group("assistant", at, "assistant",
                                      at + timedelta(minutes=7)))
        self.assertFalse(should_group("assistant", at, "assistant",
                                      at - timedelta(seconds=1)))
        near_midnight = at.replace(hour=23, minute=59)
        self.assertFalse(should_group(
            "user", near_midnight, "user", near_midnight + timedelta(minutes=2)))

    def test_historical_naive_timestamps_supported(self):
        result = parse_timestamp("2026-11-14T17:08:00")
        self.assertIsNotNone(result.tzinfo)
        self.assertEqual(result.hour, 17)

    def test_invalid_old_timestamp_does_not_crash(self):
        self.assertIsInstance(natural_time("old-bad-date"), str)


if __name__ == "__main__":
    unittest.main()
