"""Pure transforms from bookhive records → dashboard view models.

Kept separate from the router so it's trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

STATUS_LABELS = {
    "buzz.bookhive.defs#reading": ("Reading", "reading"),
    "buzz.bookhive.defs#finished": ("Finished", "finished"),
    "buzz.bookhive.defs#wantToRead": ("Want to read", "want"),
    "buzz.bookhive.defs#abandoned": ("Abandoned", "abandoned"),
}


@dataclass
class BookView:
    title: str
    authors: str
    status_slug: str        # "reading" / "finished" / "want" / "abandoned" / "unknown"
    status_label: str
    percent: int | None
    cover_cid: str | None
    finished_at: str | None
    started_at: str | None
    created_at: str | None
    updated_at: str | None
    stars: int | None       # 1-10 per lexicon; half-star rendering handled at template time
    review: str | None
    moon_filename: str | None
    bookhive_url: str | None  # https://bookhive.buzz/books/<hiveId> when we have one

    @property
    def sort_date(self) -> str:
        """Best-available datetime for ordering (ISO strings sort lexicographically)."""
        return self.finished_at or self.started_at or self.updated_at or self.created_at or ""

    @property
    def finished_year(self) -> int | None:
        """Year to file this book under in 'Finished in YYYY'."""
        src = self.finished_at or self.started_at or self.created_at
        if not src:
            return None
        try:
            return datetime.fromisoformat(src.replace("Z", "+00:00")).year
        except ValueError:
            return None


def _cover_cid(value: dict) -> str | None:
    cover = value.get("cover")
    if not isinstance(cover, dict):
        return None
    ref = cover.get("ref")
    if isinstance(ref, dict):
        link = ref.get("$link")
        if isinstance(link, str):
            return link
    return None


def _hive_id(value: dict) -> str | None:
    """Extract hiveId — check both the top-level field and the identifiers ref."""
    hid = value.get("hiveId")
    if isinstance(hid, str) and hid:
        return hid
    idents = value.get("identifiers")
    if isinstance(idents, dict):
        hid = idents.get("hiveId")
        if isinstance(hid, str) and hid:
            return hid
    return None


def _to_view(record: dict) -> BookView:
    v = record.get("value") or {}
    bp = v.get("bookProgress") or {}
    moon = bp.get("moonReader") or {}
    status_raw = v.get("status") or ""
    label, slug = STATUS_LABELS.get(status_raw, (status_raw or "Unknown", "unknown"))
    percent = bp.get("percent")
    stars = v.get("stars")
    review = v.get("review")
    hive_id = _hive_id(v)
    return BookView(
        title=v.get("title", "Untitled"),
        authors=v.get("authors", ""),
        status_slug=slug,
        status_label=label,
        percent=int(percent) if isinstance(percent, (int, float)) else None,
        cover_cid=_cover_cid(v),
        finished_at=v.get("finishedAt"),
        started_at=v.get("startedAt"),
        created_at=v.get("createdAt"),
        updated_at=bp.get("updatedAt"),
        stars=int(stars) if isinstance(stars, (int, float)) else None,
        review=review.strip() if isinstance(review, str) and review.strip() else None,
        moon_filename=moon.get("file"),
        bookhive_url=f"https://bookhive.buzz/books/{hive_id}" if hive_id else None,
    )


def build_books_view(records: list[dict]) -> list[BookView]:
    """Turn raw listRecords output into BookView objects. No sorting/filtering."""
    return [_to_view(r) for r in records]


def cover_cids(records: list[dict]) -> set[str]:
    """CIDs for covers we're willing to proxy — used to gate /blob/{cid}."""
    out: set[str] = set()
    for r in records:
        cid = _cover_cid(r.get("value") or {})
        if cid:
            out.add(cid)
    return out


@dataclass
class DashboardSections:
    currently_reading: list[BookView]
    want_to_read: list[BookView]
    finished_this_year: list[BookView]
    finished_previous: list[BookView]
    year: int


def partition(books: list[BookView], current_year: int) -> DashboardSections:
    """Split books into the four dashboard sections.

    `currently_reading` is capped at 3 (matches brad.quest's reading page);
    overflow falls through to `finished_previous` only if the book is actually
    finished — active readers just disappear off the dashboard.
    """
    reading = sorted(
        (b for b in books if b.status_slug == "reading"),
        key=lambda b: b.sort_date, reverse=True,
    )[:3]
    want = sorted(
        (b for b in books if b.status_slug == "want"),
        key=lambda b: b.sort_date, reverse=True,
    )
    finished_all = sorted(
        (b for b in books if b.status_slug == "finished"),
        key=lambda b: b.sort_date, reverse=True,
    )
    this_year = [b for b in finished_all if b.finished_year == current_year]
    previous = [b for b in finished_all if b.finished_year != current_year]
    return DashboardSections(
        currently_reading=reading,
        want_to_read=want,
        finished_this_year=this_year,
        finished_previous=previous,
        year=current_year,
    )
