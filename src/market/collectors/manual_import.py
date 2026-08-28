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
    # When the row was *collected*, as opposed to when the listing was posted.
    # Appended last so that a CSV written before this column existed still
    # imports -- everything here past the feature and price columns is optional.
    #
    # It carries more weight than its name suggests. `validation.is_snapshot`
    # asks whether a dataset was gathered in one sitting, and answers with the
    # span of this column; the answer decides between a chronological split and
    # a seller-grouped random one, and whether rolling-window folds are computed
    # at all. Dropping it stamped every imported row with the moment of import,
    # so the shipped sample -- three weeks of collections -- arrived looking like
    # a single snapshot and was validated as one.
    "captured_at",
)


class ManualImportError(ValueError):
    pass


class ManualCsvCollector(MarketCollector):
    name = "manual_csv"

    def __init__(
        self,
        converter: CurrencyConverter,
        league: str,
        map_tier: int = 16,
        source_name: str | None = None,
    ):
        self.converter = converter
        self.league = league
        self.map_tier = map_tier
        # Rows written by the first-run seed are labelled `bundled_sample`
        # rather than `manual_csv`, which is what lets a later release swap its
        # sample for a bigger one without touching a CSV the user imported
        # themselves. See src/market/seed.py.
        self.name = source_name or type(self).name

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
            captured_at = _to_datetime(row.get("captured_at"))
            yield MarketListing(
                item=classify(item, map_tier=self.map_tier),
                listing_id=(row.get("listing_id") or "").strip() or None,
                indexed_at=_to_datetime(row.get("indexed_at")),
                **({"captured_at": captured_at} if captured_at else {}),
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


def listing_rows_to_csv(
    rows: Any, anonymise: bool = False, in_chaos: bool = False
) -> str:
    """Render repository rows as CSV this module's own importer can read back.

    The Market Data page used to serialise its display DataFrame instead. That
    frame is built for reading: its price column is converted into whatever unit
    the user has on display, while its currency column carries the listing's
    original currency. The two describe different things, so quite apart from
    the column names not matching, a rename-only fix would have re-imported a
    222c listing as 48,950c without raising anything. Exporting from the stored
    price_amount / price_currency pair is the only version that round-trips.

    `in_chaos` writes the stored `price_chaos` as the price instead of the
    listing's own amount and currency, which makes a snapshot self-contained:
    a divine-priced row otherwise has to be re-converted whenever it is
    imported, at whatever rate the importer can find that day, and one row in
    eight is priced in divine.

    Worth being precise about what this did and did not buy, since the obvious
    reading is that it fixed something. It did not: re-exporting the sample this
    way and retraining produced identical metrics to the digit, because
    `recompute_price_chaos` restates every stored price whenever the rate
    changes, so the two representations already agree. What it removes is the
    dependency itself -- the import no longer has to guess a rate for a
    snapshot whose prices were settled weeks ago. A user exporting their own
    data leaves this off and keeps the original amounts.

    `anonymise` blanks the seller and the listing id, for the sample dataset
    that ships with the repository. A user exporting their own data keeps both:
    the per-seller cap that protects training from bulk dumps needs to know who
    listed what, and the listing id is what makes re-importing an update rather
    than a duplicate. The shipped sample wants the opposite on both counts --
    no account names, and no live ids that would upsert over somebody's own
    freshly collected rows.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_TEMPLATE_COLUMNS)
    pseudonyms: dict[str, str] = {}
    for row in rows:
        amount, currency = row["price_amount"], row["price_currency"]
        if in_chaos and row["price_chaos"]:
            amount, currency = round(float(row["price_chaos"]), 4), "chaos"
        writer.writerow(
            [
                *(row[name] for name in FEATURE_NAMES),
                amount,
                currency,
                row["map_tier"],
                row["item_level"],
                "true" if row["corrupted"] else "false",
                _pseudonym(row["seller"], pseudonyms) if anonymise else (row["seller"] or ""),
                "" if anonymise else (row["listing_id"] or ""),
                row["indexed_at"] or "",
                row["captured_at"] or "",
            ]
        )
    return buffer.getvalue()


def _pseudonym(seller: Any, seen: dict[str, str]) -> str:
    """A stable, meaningless label for one account within a single export.

    Blanking the seller entirely was the first version of this, and it cost more
    than it protected. `grouped_split` keys on the column to keep one account's
    maps on one side of the train/test line, so a whole export sharing the empty
    string became one enormous group: seeding a fresh install and retraining
    produced train 2743 / valid 3 / test 5 and an R2 of nan.

    Sequential labels in order of first appearance keep exactly the fact the
    split needs -- these two rows came from the same account -- and carry nothing
    else. There is no hash to reverse and no salt to leak: "anon-0007" is the
    seventh distinct account in this file and means nothing in any other.
    """
    name = (seller or "").strip()
    if not name:
        return ""
    if name not in seen:
        seen[name] = f"anon-{len(seen) + 1:04d}"
    return seen[name]


def csv_template() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_TEMPLATE_COLUMNS)
    writer.writerow([84, 134, 32, 47, 35, 0, 0, 9.8, "divine", 16, 84, "false", "", "", "", ""])
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
