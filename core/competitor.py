from __future__ import annotations

import email.utils
import html
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import pandas as pd


DEFAULT_BRANDS = [
    "DEWALT",
    "Milwaukee",
    "Bissell",
    "Hoover",
    "Shark",
    "Greenworks",
    "Ryobi",
    "CRAFTSMAN",
    "Kärcher",
    "Dreame",
    "Tineco",
    "Roborock",
    "UWANT",
    "Deerma",
    "Narwal",
]

INTEL_COLUMNS = [
    "published_at",
    "collected_at",
    "date_status",
    "source_published_at_raw",
    "region",
    "brand",
    "category",
    "product_name",
    "model",
    "status",
    "source_type",
    "source_title",
    "source_summary",
    "source_url",
    "core_specs",
    "price_strategy",
    "competitive_impact",
    "recommended_action",
    "llm_summary",
    "llm_confidence",
    "confidence",
]


DATE_STATUS_OPTIONS = [
    "已解析",
    "日期缺失",
    "日期无效",
    "未来日期异常",
    "历史字段（待复核）",
]


def _region(brand: str) -> str:
    if brand.lower() in {"dreame", "tineco", "roborock", "uwant", "deerma", "narwal"}:
        return "China Emerging"
    if brand.lower() in {"kärcher"}:
        return "Europe"
    return "Americas"


def _status(title: str) -> str:
    text = title.lower()
    if any(word in text for word in ["pre-order", "preorder", "coming soon"]):
        return "Pre-order"
    if any(word in text for word in ["teaser", "preview", "unveil", "announce"]):
        return "Teaser"
    if any(word in text for word in ["leak", "rumor"]):
        return "Leak"
    if any(word in text for word in ["launch", "new", "release", "available"]):
        return "Active Sales"
    return "待判断"


def _category(title: str) -> str:
    text = title.lower()
    rules = [
        ("Spot Cleaner", ["spot cleaner", "carpet cleaner", "upholstery cleaner"]),
        ("Wet/Dry Vacuum", ["wet dry vacuum", "wet/dry vacuum", "shop vac"]),
        ("Robot Mower", ["robot mower", "robotic mower"]),
        ("Floor Care", ["vacuum", "floor washer", "floor cleaner"]),
        ("Lawn & Garden", ["mower", "blower", "trimmer", "chainsaw"]),
    ]
    for category, words in rules:
        if any(word in text for word in words):
            return category
    return "待分类"


def normalize_intelligence_table(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    imported_legacy_date = "published_at" not in result and "discovered_at" in result
    if imported_legacy_date:
        result["published_at"] = result["discovered_at"]
    if "source_published_at_raw" not in result:
        result["source_published_at_raw"] = result.get("published_at", "")
    if "collected_at" not in result:
        result["collected_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "date_status" not in result:
        result["date_status"] = "历史字段（待复核）" if imported_legacy_date else ""

    for column in INTEL_COLUMNS:
        if column not in result:
            result[column] = ""

    raw_published = result["published_at"].fillna("").astype(str).str.strip()
    parsed_published = pd.to_datetime(raw_published, errors="coerce", utc=True)
    future_cutoff = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
    future_mask = parsed_published.notna() & (parsed_published > future_cutoff)
    invalid_mask = raw_published.ne("") & parsed_published.isna()
    missing_mask = raw_published.eq("")

    normalized_published = parsed_published.dt.strftime("%Y-%m-%d").fillna("")
    normalized_published.loc[future_mask] = ""
    result["published_at"] = normalized_published

    empty_status = result["date_status"].fillna("").astype(str).str.strip().eq("")
    result.loc[empty_status & missing_mask, "date_status"] = "日期缺失"
    result.loc[empty_status & invalid_mask, "date_status"] = "日期无效"
    result.loc[future_mask, "date_status"] = "未来日期异常"
    result.loc[
        empty_status & parsed_published.notna() & ~future_mask,
        "date_status",
    ] = "已解析"

    result = result[INTEL_COLUMNS]
    result["source_url"] = result["source_url"].fillna("").astype(str)
    result["brand"] = result["brand"].fillna("").astype(str)
    result = result.drop_duplicates(subset=["source_url"], keep="first")
    return sort_intelligence_by_recency(result)


def sort_intelligence_by_recency(df: pd.DataFrame) -> pd.DataFrame:
    """Sort all brands together by article publication time, newest first."""
    result = df.copy()
    published = pd.to_datetime(result.get("published_at"), errors="coerce", utc=True)
    collected = pd.to_datetime(result.get("collected_at"), errors="coerce", utc=True)
    result["_published_sort"] = published
    result["_collected_sort"] = collected
    result = result.sort_values(
        ["_published_sort", "_collected_sort", "brand"],
        ascending=[False, False, True],
        na_position="last",
        kind="stable",
    )
    return result.drop(columns=["_published_sort", "_collected_sort"]).reset_index(drop=True)


def _clean_google_link(link: str) -> str:
    return link.strip()


def _clean_summary(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _parse_publication_date(
    pub_date: str,
    collected_at: datetime,
) -> tuple[str, str]:
    raw_value = (pub_date or "").strip()
    if not raw_value:
        return "", "日期缺失"
    try:
        parsed = email.utils.parsedate_to_datetime(raw_value)
        if parsed is None:
            raise ValueError("empty parsed date")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return "", "日期无效"
    if parsed > collected_at + timedelta(days=1):
        return "", "未来日期异常"
    return parsed.date().isoformat(), "已解析"


def collect_google_news(
    brands: list[str],
    max_per_brand: int = 3,
    lookback_days: int | None = 30,
) -> pd.DataFrame:
    collected_at = datetime.now(timezone.utc)
    collected_at_text = collected_at.isoformat(timespec="seconds")
    cutoff = (
        (collected_at - timedelta(days=max(1, int(lookback_days)))).date()
        if lookback_days is not None
        else None
    )
    rows = []
    for brand in brands:
        date_query = f" when:{max(1, int(lookback_days))}d" if lookback_days is not None else ""
        query = urllib.parse.quote(
            f'"{brand}" (new OR launch OR release) (vacuum OR cleaner OR mower){date_query}'
        )
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=25) as response:
            root = ElementTree.fromstring(response.read())
        brand_count = 0
        for item in root.findall("./channel/item"):
            title = item.findtext("title", default="").strip()
            link = _clean_google_link(item.findtext("link", default=""))
            summary = _clean_summary(item.findtext("description", default=""))
            pub_date = item.findtext("pubDate", default="")
            published_at, date_status = _parse_publication_date(pub_date, collected_at)
            if cutoff and published_at:
                if datetime.strptime(published_at, "%Y-%m-%d").date() < cutoff:
                    continue
            rows.append(
                {
                    "published_at": published_at,
                    "collected_at": collected_at_text,
                    "date_status": date_status,
                    "source_published_at_raw": pub_date,
                    "region": _region(brand),
                    "brand": brand,
                    "category": _category(title),
                    "product_name": re.sub(r"\s+-\s+[^-]+$", "", title),
                    "model": "",
                    "status": _status(title),
                    "source_type": "News / Public Web",
                    "source_title": title,
                    "source_summary": summary,
                    "source_url": link,
                    "core_specs": "",
                    "price_strategy": "",
                    "competitive_impact": "",
                    "recommended_action": "",
                    "llm_summary": "",
                    "llm_confidence": "",
                    "confidence": "中",
                }
            )
            brand_count += 1
            if brand_count >= max_per_brand:
                break
    return normalize_intelligence_table(pd.DataFrame(rows))
