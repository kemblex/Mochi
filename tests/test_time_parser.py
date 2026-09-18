import datetime as dt
import unittest

from time_parser import parse_mtime, weekdays

UTC = dt.timezone.utc


class TimeParserTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 17, 15, 30, tzinfo=UTC)  # Thursday

    def assert_time(self, text, expected, now=None):
        result = parse_mtime(text, now=self.now if now is None else now)
        self.assertIsInstance(result, int)
        self.assertEqual(dt.datetime.fromtimestamp(result, UTC), dt.datetime.fromisoformat(expected))

    def test_reset_offsets(self):
        for text, expected in [
            ('+0', '2026-09-18T00:00:00+00:00'),
            ('+2', '2026-09-18T02:00:00+00:00'),
            ('-1', '2026-09-17T23:00:00+00:00'),
            ('+2.5', '2026-09-18T02:30:00+00:00'),
            ('today +2', '2026-09-18T02:00:00+00:00'),
            ('tomorrow +2', '2026-09-19T02:00:00+00:00'),
            ('friday +1', '2026-09-19T01:00:00+00:00'),
            ('next friday +2', '2026-09-26T02:00:00+00:00'),
            (' THURSDAY  +2 ', '2026-09-25T02:00:00+00:00'),
            ('next thursday +2', '2026-10-02T02:00:00+00:00'),
            ('tomorrow', '2026-09-19T00:00:00+00:00'),
        ]:
            with self.subTest(text=text):
                self.assert_time(text, expected)

    def test_weekday_aliases(self):
        names = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for alias, index in weekdays.items():
            with self.subTest(alias=alias):
                self.assertEqual(parse_mtime(alias+' +2', now=self.now), parse_mtime(names[index]+' +2', now=self.now))

    def test_dates_and_year_boundaries(self):
        self.assert_time('09/17 +1', '2026-09-18T01:00:00+00:00')
        self.assert_time('09/18 +1', '2026-09-19T01:00:00+00:00')
        self.assert_time('01/01 -1', '2027-01-01T23:00:00+00:00')
        self.assert_time('12/31 +2', '2027-01-01T02:00:00+00:00')
        self.assert_time('+2', '2027-01-01T02:00:00+00:00', dt.datetime(2026, 12, 31, 23, tzinfo=UTC))

    def test_invalid_and_nonfinite_input(self):
        for text in ['', '   ', 'nonsense', 'next', '13/01', '04/31', 'nan', 'inf', '-inf', 'friday nan', 'tomorrow inf', '1e300']:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_mtime(text, now=self.now)

    def test_injected_clock_is_normalized_to_utc(self):
        local_now = dt.datetime(2026, 9, 17, 23, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
        self.assert_time('+0', '2026-09-19T00:00:00+00:00', local_now)
        with self.assertRaises(ValueError):
            parse_mtime('+2', now=dt.datetime(2026, 9, 17))

    def test_rejects_extra_or_invalid_words(self):
        for text in ['friday nonsense', 'today nope', 'next friday +2 extra', '+2 +3', '09/18 +1 extra', '09/18/2026']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_mtime(text, now=self.now)

    def test_leap_day_in_inferred_year(self):
        self.assert_time('02/29 +2', '2028-03-01T02:00:00+00:00', dt.datetime(2028, 2, 20, tzinfo=UTC))
        self.assert_time('02/29', '2028-03-01T00:00:00+00:00', dt.datetime(2027, 12, 31, tzinfo=UTC))
        with self.assertRaises(ValueError):
            parse_mtime('02/29', now=self.now)


if __name__ == '__main__':
    unittest.main()
