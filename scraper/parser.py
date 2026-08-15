from typing import Any
from bs4 import BeautifulSoup


def parse_listing_page(html: str, list_item_selector: str, fields: dict[str, str]) -> list[dict[str, Any]]:
    """
    Generic extractor driven entirely by config/targets.yaml — no code
    changes needed to add a new site, just a new YAML entry.

    `fields` maps output_key -> css_selector. Append '::attr(name)' to a
    selector to pull an attribute (e.g. "a.link::attr(href)") instead of
    text content.
    """
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []

    for card in soup.select(list_item_selector):
        record: dict[str, Any] = {}
        for key, selector in fields.items():
            if "::attr(" in selector:
                sel, attr = selector.split("::attr(")
                attr = attr.rstrip(")")
                el = card.select_one(sel.strip())
                record[key] = el.get(attr) if el else None
            else:
                el = card.select_one(selector.strip())
                record[key] = el.get_text(strip=True) if el else None
        items.append(record)

    return items


def parse_company_page(html: str, fields: dict[str, str]) -> dict[str, Any]:
    """
    Parse individual company detail page.
    `fields` maps output_key -> css_selector with support for:
    - '::attr(name)' for attributes
    - '::text' for text content (default)
    - Special keys for complex extraction (directors table)
    """
    soup = BeautifulSoup(html, "lxml")
    record: dict[str, Any] = {}

    for key, selector in fields.items():
        if selector == "DIRECTORS_TABLE":
            record[key] = _extract_directors(soup)
        elif selector == "REGISTERED_ADDRESS":
            record[key] = _extract_registered_address(soup)
        elif "::attr(" in selector:
            sel, attr = selector.split("::attr(")
            attr = attr.rstrip(")")
            el = soup.select_one(sel.strip())
            record[key] = el.get(attr) if el else None
        else:
            el = soup.select_one(selector.strip())
            record[key] = el.get_text(strip=True) if el else None

    return record


def _extract_directors(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Extract directors from the people module table."""
    directors = []
    # Find the directors table in #people-module
    people_module = soup.select_one("#people-module")
    if not people_module:
        return directors

    table = people_module.select_one("table")
    if not table:
        return directors

    rows = table.select("tbody tr")
    for row in rows:
        cells = row.select("td")
        if len(cells) >= 4:
            designation = cells[0].get_text(strip=True)
            name = cells[1].get_text(strip=True)
            din = cells[2].get_text(strip=True)
            tenure = cells[3].get_text(strip=True)

            # Skip header-like rows and hidden DINs
            if designation.lower() in ("director", "kmp") and din != "<HIDDEN>":
                directors.append({
                    "director_name": name,
                    "din": din,
                    "designation": designation,
                    "tenure": tenure
                })
    return directors


def _extract_registered_address(soup: BeautifulSoup) -> str | None:
    """Extract registered address from the overview module description."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    import re
    match = re.search(r"registered address is at\s+(.+?)(?:\.|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None
