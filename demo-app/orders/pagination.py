"""Page-number pagination helpers."""

import math


def paginate(items: list[dict], page: int | str, size: int) -> dict:
    """Return one page of ``items``. Pages are 1-based; ``page="last"`` means the final page."""
    pages = max(1, math.ceil(len(items) / size))
    if page == "last":
        page = pages
    page = int(page)
    start = page * size
    chunk = items[start:start + size]
    return {
        "page": page,
        "pages": pages,
        "first_id": items[start]["id"],
        "items": chunk,
    }
