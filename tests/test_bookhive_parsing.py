"""Pure-function tests for `.po` parsing and filename heuristics."""

from spacebee.atproto import bookhive


class TestParsePo:
    def test_round_trip(self) -> None:
        raw = "1703297605115*24@0#0:30.0%"
        p = bookhive.parse_po(raw)
        assert p is not None
        assert p.timestamp_ms == 1703297605115
        assert p.chapter == 24
        assert p.volume == 0
        assert p.char_offset == 0
        assert p.percentage == 30.0
        assert p.raw == raw

    def test_percent_with_fraction(self) -> None:
        p = bookhive.parse_po("1000*5@0#123:79.7%")
        assert p is not None
        assert p.percentage == 79.7

    def test_rejects_garbage(self) -> None:
        assert bookhive.parse_po("not a po file") is None


class TestSerializePo:
    def test_prefers_stored_raw(self) -> None:
        """When moonReader.position exists, return it byte-for-byte.

        This matters: the internal timestamp is the ebook's import mtime and
        Moon+ Reader recognizes the file by that value. Re-synthesizing would
        trip false "position changed" events.
        """
        record = {
            "bookProgress": {
                "percent": 30,
                "currentChapter": 25,
                "moonReader": {"position": "1703297605115*24@0#0:30.0%"},
            }
        }
        assert bookhive.serialize_po(record) == "1703297605115*24@0#0:30.0%"

    def test_synthesizes_when_moonreader_missing(self) -> None:
        record = {"bookProgress": {"percent": 42, "currentChapter": 5}}
        # Chapter goes 1-indexed → 0-indexed on the way out.
        assert bookhive.serialize_po(record) == "0*4@0#0:42.0%"

    def test_synthesizes_zero_progress(self) -> None:
        assert bookhive.serialize_po({}) == "0*0@0#0:0.0%"


class TestParseFilename:
    def test_title_dash_author(self) -> None:
        assert bookhive.parse_filename(
            "The Lesser Dead - Christopher Buehlman.epub.po"
        ) == ("The Lesser Dead", "Christopher Buehlman")

    def test_series_prefix_stripped(self) -> None:
        assert bookhive.parse_filename(
            "(Dungeon Crawler Carl 1) Matt Dinniman - Dungeon Crawler Carl.epub.po"
        ) == ("Matt Dinniman", "Dungeon Crawler Carl")

    def test_dashes_in_title(self) -> None:
        assert bookhive.parse_filename(
            "Cultish - The Language of Fanaticism - Amanda Montell.epub.po"
        ) == ("Cultish - The Language of Fanaticism", "Amanda Montell")

    def test_comma_last_first_author(self) -> None:
        a, b = bookhive.parse_filename("Book Title - Doe, Jane.epub.po")
        assert a == "Book Title"
        assert b == "Jane Doe"

    def test_article_suffix(self) -> None:
        a, _ = bookhive.parse_filename("Lesser Dead, The - Christopher Buehlman.epub.po")
        assert a == "The Lesser Dead"


class TestMatchRecord:
    RECORDS = [
        {"value": {"title": "The Lesser Dead", "authors": "Christopher Buehlman"}},
        {"value": {"title": "Dungeon Crawler Carl", "authors": "Matt Dinniman"}},
        {"value": {"title": "Cultish", "authors": "Amanda Montell"}},
    ]

    def test_matches_title_first_order(self) -> None:
        m = bookhive.match_record("The Lesser Dead", "Christopher Buehlman", self.RECORDS)
        assert m is not None
        assert m["value"]["title"] == "The Lesser Dead"

    def test_matches_reversed_order(self) -> None:
        m = bookhive.match_record("Matt Dinniman", "Dungeon Crawler Carl", self.RECORDS)
        assert m is not None
        assert m["value"]["title"] == "Dungeon Crawler Carl"

    def test_no_match_bogus_input(self) -> None:
        assert bookhive.match_record("Zz", "Qq", self.RECORDS) is None
