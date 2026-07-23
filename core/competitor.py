from __future__ import annotations

import email.utils
import html
import re
import urllib.parse
from datetime import datetime, timezone
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
    "discovered_at",
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
    for column in INTEL_COLUMNS:
        if column not in result:
            result[column] = ""
    result = result[INTEL_COLUMNS]
    result["source_url"] = result["source_url"].fillna("").astype(str)
    result["brand"] = result["brand"].fillna("").astype(str)
    result = result.drop_duplicates(subset=["source_url"], keep="first")
    return result.reset_index(drop=True)


def _clean_google_link(link: str) -> str:
    return link.strip()


def _clean_summary(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def collect_google_news(brands: list[str], max_per_brand: int = 3) -> pd.DataFrame:
    rows = []
    for brand in brands:
        query = urllib.parse.quote(f'"{brand}" (new OR launch OR release) (vacuum OR cleaner OR mower)')
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=25) as response:
            root = ElementTree.fromstring(response.read())
        for item in root.findall("./channel/item")[:max_per_brand]:
            title = item.findtext("title", default="").strip()
            link = _clean_google_link(item.findtext("link", default=""))
            summary = _clean_summary(item.findtext("description", default=""))
            pub_date = item.findtext("pubDate", default="")
            try:
                parsed = email.utils.parsedate_to_datetime(pub_date)
                discovered = parsed.astimezone(timezone.utc).date().isoformat()
            except (TypeError, ValueError):
                discovered = datetime.now(timezone.utc).date().isoformat()
            rows.append(
                {
                    "discovered_at": discovered,
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
    return normalize_intelligence_table(pd.DataFrame(rows))
