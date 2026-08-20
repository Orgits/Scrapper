from typing import Any
from bs4 import BeautifulSoup
import re


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


def parse_company_page(html: str, fields: dict[str, str], site_name: str = "generic") -> dict[str, Any]:
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
            if site_name == "tofler":
                record[key] = _extract_directors_tofler(soup)
            else:
                record[key] = _extract_directors(soup)
        elif selector == "REGISTERED_ADDRESS":
            if site_name == "tofler":
                record[key] = _extract_registered_address_tofler(soup)
            else:
                record[key] = _extract_registered_address(soup)
        elif selector == "AUTHORIZED_CAPITAL":
            if site_name == "tofler":
                record[key] = _extract_authorized_capital_tofler(soup)
            else:
                record[key] = None
        elif selector == "PAID_UP_CAPITAL":
            if site_name == "tofler":
                record[key] = _extract_paid_up_capital_tofler(soup)
            else:
                record[key] = None
        elif selector == "INDUSTRY":
            if site_name == "tofler":
                record[key] = _extract_industry_tofler(soup)
            else:
                record[key] = None
        elif selector == "EMAIL":
            if site_name == "tofler":
                record[key] = _extract_email_tofler(soup)
            else:
                record[key] = None
        elif selector == "COMPANY_NAME":
            if site_name == "tofler":
                record[key] = _extract_company_name_tofler(soup)
            else:
                record[key] = None
        elif selector == "CIN":
            if site_name == "tofler":
                record[key] = _extract_cin_tofler(soup)
            else:
                record[key] = None
        elif selector == "STATUS":
            if site_name == "tofler":
                record[key] = _extract_status_tofler(soup)
            else:
                record[key] = None
        elif selector == "DATE_OF_INCORPORATION":
            if site_name == "tofler":
                record[key] = _extract_date_of_incorporation_tofler(soup)
            else:
                record[key] = None
        elif selector == "COMPANY_CLASS":
            if site_name == "tofler":
                record[key] = _extract_company_class_tofler(soup)
            else:
                record[key] = None
        elif selector == "COMPANY_CATEGORY":
            if site_name == "tofler":
                record[key] = _extract_company_category_tofler(soup)
            else:
                record[key] = None
        elif selector == "COMPANY_SUB_CATEGORY":
            if site_name == "tofler":
                record[key] = _extract_company_sub_category_tofler(soup)
            else:
                record[key] = None
        elif selector == "ROC":
            if site_name == "tofler":
                record[key] = _extract_roc_tofler(soup)
            else:
                record[key] = None
        elif selector == "LISTING_STATUS":
            if site_name == "tofler":
                record[key] = _extract_listing_status_tofler(soup)
            else:
                record[key] = None
        elif selector == "REGISTERED_OFFICE_ADDRESS":
            if site_name == "tofler":
                record[key] = _extract_registered_office_address_tofler(soup)
            else:
                record[key] = None
        elif selector == "NIC_CODE":
            if site_name == "tofler":
                record[key] = _extract_nic_code_tofler(soup)
            else:
                record[key] = None
        elif selector == "NIC_DESCRIPTION":
            if site_name == "tofler":
                record[key] = _extract_nic_description_tofler(soup)
            else:
                record[key] = None
        elif selector == "LAST_AGM_DATE":
            if site_name == "tofler":
                record[key] = _extract_last_agm_date_tofler(soup)
            else:
                record[key] = None
        elif selector == "LAST_BALANCE_SHEET_DATE":
            if site_name == "tofler":
                record[key] = _extract_last_balance_sheet_date_tofler(soup)
            else:
                record[key] = None
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
    """Extract directors from the people module table (Tofler)."""
    directors = []
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

            if designation.lower() in ("director", "kmp") and din != "<HIDDEN>":
                directors.append({
                    "director_name": name,
                    "din": din,
                    "designation": designation,
                    "tenure": tenure
                })
    return directors


def _extract_registered_address(soup: BeautifulSoup) -> str | None:
    """Extract registered address from the overview module description (Tofler)."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"registered address is at\s+(.+?)(?:\.|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


# ==================== Tofler-specific extractors ====================

def _extract_directors_tofler(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Extract directors from the people module table (Tofler)."""
    directors = []
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

            if designation.lower() in ("director", "kmp") and din != "<HIDDEN>":
                directors.append({
                    "director_name": name,
                    "din": din,
                    "designation": designation,
                    "tenure": tenure
                })
    return directors


def _extract_registered_address_tofler(soup: BeautifulSoup) -> str | None:
    """Extract registered address from the overview module description (Tofler)."""
    # Try multiple selectors for overview content
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        overview = soup.select_one("#overview-module .company_description_wrapper")
    if not overview:
        overview = soup.select_one("#overview-module")
    
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    # Try multiple patterns for registered address
    patterns = [
        r"registered address is at\s+(.+?)(?:\.|$)",
        r"registered office (?:address|is)\s+(?:at|:)?\s*([^,.]+(?:,[^,.]+)*)",
        r"address\s+(?:is|:)\s*([^,.]+(?:,[^,.]+)*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _extract_authorized_capital_tofler(soup: BeautifulSoup) -> str | None:
    """Extract authorized capital from Tofler company detail page."""
    financial_module = soup.select_one("#financial-module")
    if not financial_module:
        return None

    for row in financial_module.select("table tr"):
        cells = row.select("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            if "authorised" in label or "authorized" in label:
                return cells[1].get_text(strip=True)
    return None


def _extract_paid_up_capital_tofler(soup: BeautifulSoup) -> str | None:
    """Extract paid-up capital from Tofler company detail page."""
    financial_module = soup.select_one("#financial-module")
    if not financial_module:
        return None

    for row in financial_module.select("table tr"):
        cells = row.select("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            if "paid" in label and "capital" in label:
                return cells[1].get_text(strip=True)
    return None


def _extract_industry_tofler(soup: BeautifulSoup) -> str | None:
    """Extract industry/NIC description from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"(?:nic|industry).*?:\s*([^.]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_email_tofler(soup: BeautifulSoup) -> str | None:
    """Extract email from Tofler company detail page."""
    contact_module = soup.select_one("#contact-module")
    if not contact_module:
        return None

    for row in contact_module.select("table tr"):
        cells = row.select("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            if "email" in label:
                email = cells[1].get_text(strip=True)
                return email if email and email.lower() != "not available" else None
    return None


def _extract_company_name_tofler(soup: BeautifulSoup) -> str | None:
    """Extract company name from Tofler company detail page."""
    title = soup.select_one("#overview-module h1, .company-name h1")
    if title:
        return title.get_text(strip=True)
    return None


def _extract_cin_tofler(soup: BeautifulSoup) -> str | None:
    """Extract CIN from Tofler company detail page."""
    # Try multiple locations for CIN
    selectors = [
        "#overview-module .company_description_wrapper .moreContent",
        "#overview-module .company_description_wrapper",
        "#overview-module",
        ".cin-number",
        "[data-cin]",
    ]
    
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            match = re.search(r"CIN\s*[:]?\s*([A-Z0-9]{21})", text)
            if match:
                return match.group(1).strip()
            # Also check for data attribute
            if el.get("data-cin"):
                return el.get("data-cin").strip()
    
    # Check meta tags
    meta_cin = soup.select_one('meta[name="cin"], meta[property="og:cin"]')
    if meta_cin and meta_cin.get("content"):
        return meta_cin.get("content").strip()
    
    return None


def _extract_status_tofler(soup: BeautifulSoup) -> str | None:
    """Extract status from Tofler company detail page."""
    status_el = soup.select_one("#overview-module .badge, .status-badge, [class*='status']")
    if status_el:
        text = status_el.get_text(strip=True)
        if text:
            return text
    return None


def _extract_date_of_incorporation_tofler(soup: BeautifulSoup) -> str | None:
    """Extract date of incorporation from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"(?:incorporated|incorporation).*?(?:on|date).*?(\d{1,2}[-/]\w{3}[-/]\d{4}|\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_company_class_tofler(soup: BeautifulSoup) -> str | None:
    """Extract company class from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"(?:class|category).*?(?:of|:)\s*(private|public|government|non-government)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_company_category_tofler(soup: BeautifulSoup) -> str | None:
    """Extract company category from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"category.*?(?:of|:)\s*([^,.]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_company_sub_category_tofler(soup: BeautifulSoup) -> str | None:
    """Extract company sub-category from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"sub.*?category.*?(?:of|:)\s*([^,.]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_roc_tofler(soup: BeautifulSoup) -> str | None:
    """Extract ROC from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"ROC\s*[:]?\s*([^,.]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_listing_status_tofler(soup: BeautifulSoup) -> str | None:
    """Extract listing status from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"(?:listed|listing).*?(?:on|:)\s*(unlisted|listed|bse|nse|both)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_registered_office_address_tofler(soup: BeautifulSoup) -> str | None:
    """Extract registered office address from Tofler company detail page."""
    contact_module = soup.select_one("#contact-module")
    if not contact_module:
        return None

    for row in contact_module.select("table tr"):
        cells = row.select("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            if "registered" in label and "address" in label:
                return cells[1].get_text(strip=True)
            if "office" in label and "address" in label:
                return cells[1].get_text(strip=True)
    return None


def _extract_nic_code_tofler(soup: BeautifulSoup) -> str | None:
    """Extract NIC code from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"NIC\s*(?:code)?\s*[:]?\s*(\d{5})", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_nic_description_tofler(soup: BeautifulSoup) -> str | None:
    """Extract NIC description from Tofler company detail page."""
    overview = soup.select_one("#overview-module .company_description_wrapper .moreContent")
    if not overview:
        return None

    text = overview.get_text(" ", strip=True)
    match = re.search(r"NIC\s*(?:description|activity)\s*[:]?\s*([^,.]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_last_agm_date_tofler(soup: BeautifulSoup) -> str | None:
    """Extract last AGM date from Tofler company detail page."""
    financial_module = soup.select_one("#financial-module")
    if not financial_module:
        return None

    text = financial_module.get_text(" ", strip=True)
    match = re.search(r"last\s+agm\s*[:]?\s*(\d{1,2}[-/]\w{3}[-/]\d{4}|\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_last_balance_sheet_date_tofler(soup: BeautifulSoup) -> str | None:
    """Extract last balance sheet date from Tofler company detail page."""
    financial_module = soup.select_one("#financial-module")
    if not financial_module:
        return None

    text = financial_module.get_text(" ", strip=True)
    match = re.search(r"(?:balance\s+sheet|financial\s+year).*?(?:ended|date|:)\s*(\d{1,2}[-/]\w{3}[-/]\d{4}|\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None