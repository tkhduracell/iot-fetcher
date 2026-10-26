#!/usr/bin/env python3
"""
Unit tests for elpris.get_elpris_price_url date rollover.

Run with: python3 -m unittest test_elpris_url -v
(from fetcher-core/python)
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import elpris  # noqa: E402

BASE = "https://www.elprisetjustnu.se/api/v1/prices"


def url_at(fake_now: datetime, day_offset: int = 0) -> str:
    with patch.object(elpris, "datetime") as dt:
        dt.now.return_value = fake_now
        return elpris.get_elpris_price_url("SE4", day_offset)


class TestGetElprisPriceUrl(unittest.TestCase):
    def test_before_noon_uses_same_day(self):
        self.assertEqual(url_at(datetime(2026, 10, 31, 11, 59)), f"{BASE}/2026/10-31_SE4.json")

    def test_after_noon_month_end_rolls_month(self):
        self.assertEqual(url_at(datetime(2026, 10, 31, 12, 0)), f"{BASE}/2026/11-01_SE4.json")

    def test_after_noon_year_end_rolls_year(self):
        self.assertEqual(url_at(datetime(2026, 12, 31, 15, 0)), f"{BASE}/2027/01-01_SE4.json")


if __name__ == "__main__":
    unittest.main()
