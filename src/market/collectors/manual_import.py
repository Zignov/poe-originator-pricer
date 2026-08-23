"""Manual market-data import: CSV, JSON, and pasted item text with a price.

The application is fully usable with only these -- if the trade API ever becomes
unavailable or inappropriate to use, nothing else has to change.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from src.market.collectors.base import MarketCollector
from src.parsing.item_parser import classify, parse_api_item, parse_item_text
from src.parsing.price_parser import parse_api_price, parse_price_note
from src.pricing.currency import CurrencyConverter
from src.schema import FEATURE_NAMES, MarketListing, NormalizedMap

CSV_TEMPLATE_COLUMNS = (
    *FEATURE_NAMES,
    "price_amount",
    "price_currency",
    "map_tier",
    "item_level",
    "corrupted",
    "seller",
    "listing_id",
    "indexed_at",
)


class ManualImportError(ValueError):
    pass


class ManualCsvCollector(MarketCollector):
    name = "manual_csv"

    def __init__(self, converter: CurrencyConverter, league: str, map_tier: int = 16):
        self.converter = converter
        self.league = league
        self.map_tier = map_tier

    def collect(self, text: str = "", path: str | Path | None = None, **_kw) -> Iterator[MarketListing]:
        if path is not None:
            text = Path(path).read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ManualImportError("CSV has no header row")
        missing = [c for c in (*FEATURE_NAMES, "price_amount", "price_currency") if c not in reader.fieldnames]
        if missing:
            raise ManualImportError(f"CSV is missing required columns: {missing}")

        for row in reader:
            amount = _to_float(row.get("price_amount"))
            currency = (row.get("price_currency") or "chaos").strip().lower()
            item = NormalizedMap(
                base_type=f"Map (Tier {row.get('map_tier') or self.map_tier})",
                map_tier=int(row.get("map_tier") or self.map_tier),
                item_level=int(row["item_level"]) if (row.get("item_level") or "").strip() else None,
                corrupted=str(row.get("corrupted", "")).strip().lower() in ("1", "true", "yes"),
                is_originator=True,
                raw_item_data={"csv_row": row},
                **{name: int(float(row.get(name) or 0)) for name in FEATURE_NAMES},
            )
            yield MarketListing(
                item=classify(item, map_tier=self.map_tier),
                listing_id=(row.get("listing_id") or "").strip() or None,
                indexed_at=_to_datetime(row.get("indexed_at")),
                league=self.league,
                price_amount=amount,
                price_currency=currency,
                price_type="~price",
                price_chaos=self.converter.to_chaos(amount, currency),
                seller=(row.get("seller") or "").strip() or None,
                source=self.name,
                raw_data={"csv_row": row},
            )


class JsonExportCollector(MarketCollector):
    """Import a saved trade-API response, or anything shaped like one.

    This is the target of the user-triggered browser-export workflow: the user
    performs their own trade search, saves the response the site already sent to
    their browser, and drops the file here. Nothing is scraped and no request is
    made on the user's behalf.
    """

    name = "browser_export"

    def __init__(self, converter: CurrencyConverter, league: str, map_tier: int = 16, require_uncorrupted: bool = True):
        self.converter = converter
        self.league = league
        self.map_tier = map_tier
        self.require_uncorrupted = require_uncorrupted

    def collect(self, payload: Any = None, path: str | Path | None = None, **_kw) -> Iterator[MarketListing]:
        if path is not None:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        results = _extract_results(payload)
        if not results:
            raise ManualImportError("no listing results found in the payload")

        for result in results:
            item_json = result.get("item") or {}
            listing_info = result.get("listing") or {}
            item = classify(
                parse_api_item(item_json),
                map_tier=self.map_tier,
                require_uncorrupted=self.require_uncorrupted,
            )
            if not item.is_originator:
                continue
            price = parse_api_price(listing_info)
            yield MarketListing(
                item=item,
                listing_id=result.get("id"),
                indexed_at=_to_datetime(listing_info.get("indexed")),
                league=item_json.get("league") or self.league,
                price_amount=price["price_amount"],
                price_currency=price["price_currency"],
                price_type=price["price_type"],
                price_chaos=self.converter.to_chaos(price["price_amount"], price["price_currency"]),
                seller=(listing_info.get("account") or {}).get("name"),
                source=self.name,
                raw_data=result,
            )


class ClipboardListingCollector(MarketCollector):
    """One copied item plus a price the user types in."""

    name = "clipboard"

    def __init__(self, converter: CurrencyConverter, league: str, map_tier: int = 16):
        self.converter = converter
        self.league = league
        self.map_tier = map_tier

    def collect(
        self,
        text: str = "",
        price_note: str | None = None,
        price_amount: float | None = None,
        price_currency: str | None = None,
        **_kw,
    ) -> Iterator[MarketListing]:
        item = classify(parse_item_text(text), map_tier=self.map_tier)
        parsed = parse_price_note(price_note) if price_note else None
        amount = parsed["price_amount"] if parsed else price_amount
        currency = parsed["price_currency"] if parsed else price_currency
        if amount is None or currency is None:
            raise ManualImportError("a price is required (e.g. '~price 9.8 divine')")
        yield MarketListing(
            item=item,
            captured_at=datetime.now(timezone.utc),
            indexed_at=datetime.now(timezone.utc),
            league=self.league,
            price_amount=amount,
            price_currency=currency,
            price_type=(parsed or {}).get("price_type", "~price"),
            price_chaos=self.converter.to_chaos(amount, currency),
            source=self.name,
            raw_data={"text": text},
        )


def csv_template() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_TEMPLATE_COLUMNS)
    writer.writerow([84, 134, 32, 47, 35, 0, 0, 9.8, "divine", 16, 84, "false", "", "", ""])
    return buffer.getvalue()


def _extract_results(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict) and "item" in p]
    if isinstance(payload, dict):
        if isinstance(payload.get("result"), list):
            return [p for p in payload["result"] if isinstance(p, dict) and "item" in p]
        if "item" in payload:
            return [payload]
    return []


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
