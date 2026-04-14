"""Unit tests for view transforms and partitioning."""

from __future__ import annotations

from waggle.adapters.web.view import build_books_view, cover_cids, partition


def _rec(title: str, **overrides) -> dict:
    bp = overrides.pop("bookProgress", None)
    value = {
        "$type": "buzz.bookhive.book",
        "title": title,
        "authors": "Someone",
        "status": "buzz.bookhive.defs#reading",
    }
    if bp is not None:
        value["bookProgress"] = bp
    value.update(overrides)
    return {"uri": f"at://x/y/{title}", "cid": "cid", "value": value}


def test_status_label_mapping():
    records = [
        _rec("A"),
        _rec("B", status="buzz.bookhive.defs#finished"),
        _rec("C", status="buzz.bookhive.defs#wantToRead"),
    ]
    by_title = {b.title: b for b in build_books_view(records)}
    assert by_title["A"].status_slug == "reading"
    assert by_title["A"].status_label == "Reading"
    assert by_title["B"].status_slug == "finished"
    assert by_title["C"].status_slug == "want"


def test_percent_and_progress_extraction():
    b = build_books_view([_rec("X", bookProgress={
        "percent": 42,
        "updatedAt": "2026-04-13T00:00:00.000Z",
        "moonReader": {"file": "X.po", "position": "0*0@0#0:42%"},
    })])[0]
    assert b.percent == 42
    assert b.moon_filename == "X.po"
    assert b.updated_at == "2026-04-13T00:00:00.000Z"


def test_cover_cid_extraction():
    r = _rec("X", cover={
        "$type": "blob",
        "ref": {"$link": "bafyreiabc"},
        "mimeType": "image/jpeg",
        "size": 1234,
    })
    assert build_books_view([r])[0].cover_cid == "bafyreiabc"
    assert cover_cids([r]) == {"bafyreiabc"}


def test_cover_cids_skips_missing_covers():
    assert cover_cids([_rec("X")]) == set()


def test_bookhive_url_from_top_level_hiveId():
    b = build_books_view([_rec("X", hiveId="bk_abc123")])[0]
    assert b.bookhive_url == "https://bookhive.buzz/books/bk_abc123"


def test_bookhive_url_from_identifiers_fallback():
    b = build_books_view([_rec("X", identifiers={"hiveId": "bk_xyz"})])[0]
    assert b.bookhive_url == "https://bookhive.buzz/books/bk_xyz"


def test_bookhive_url_none_when_no_hiveId():
    assert build_books_view([_rec("X")])[0].bookhive_url is None


def test_finished_year_prefers_finishedAt():
    b = build_books_view([_rec(
        "X",
        status="buzz.bookhive.defs#finished",
        finishedAt="2024-11-01T00:00:00.000Z",
        startedAt="2023-01-01T00:00:00.000Z",
    )])[0]
    assert b.finished_year == 2024


def test_finished_year_falls_back_to_startedAt():
    b = build_books_view([_rec(
        "X",
        status="buzz.bookhive.defs#finished",
        startedAt="2022-06-01T00:00:00.000Z",
    )])[0]
    assert b.finished_year == 2022


def test_finished_year_none_when_no_date():
    b = build_books_view([_rec("X", status="buzz.bookhive.defs#finished")])[0]
    assert b.finished_year is None


# ---------------------------------------------------------------------------
# Partition into dashboard sections
# ---------------------------------------------------------------------------

def test_partition_splits_by_status_and_year():
    finished = "buzz.bookhive.defs#finished"
    books = build_books_view([
        _rec("R1", bookProgress={"percent": 20, "updatedAt": "2026-04-10T00:00:00.000Z"}),
        _rec("F-this-year", status=finished, finishedAt="2026-02-01T00:00:00.000Z"),
        _rec("F-last-year", status=finished, finishedAt="2025-11-01T00:00:00.000Z"),
        _rec("W1", status="buzz.bookhive.defs#wantToRead"),
    ])
    s = partition(books, current_year=2026)
    assert [b.title for b in s.currently_reading] == ["R1"]
    assert [b.title for b in s.want_to_read] == ["W1"]
    assert [b.title for b in s.finished_this_year] == ["F-this-year"]
    assert [b.title for b in s.finished_previous] == ["F-last-year"]
    assert s.year == 2026


def test_partition_caps_currently_reading_at_three():
    books = build_books_view([
        _rec(f"R{i}", bookProgress={
            "percent": i * 10,
            "updatedAt": f"2026-04-{10 + i:02d}T00:00:00.000Z",
        })
        for i in range(5)
    ])
    s = partition(books, current_year=2026)
    # Newest (updatedAt desc) wins the 3 slots.
    assert [b.title for b in s.currently_reading] == ["R4", "R3", "R2"]


def test_partition_finished_sorted_newest_first():
    books = build_books_view([
        _rec("old", status="buzz.bookhive.defs#finished", finishedAt="2026-01-01T00:00:00.000Z"),
        _rec("new", status="buzz.bookhive.defs#finished", finishedAt="2026-03-01T00:00:00.000Z"),
    ])
    s = partition(books, current_year=2026)
    assert [b.title for b in s.finished_this_year] == ["new", "old"]
