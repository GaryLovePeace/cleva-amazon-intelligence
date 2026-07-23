from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd


AMAZON_COLUMNS = [
    "rank",
    "asin",
    "brand",
    "title",
    "price",
    "coupon",
    "rating",
    "review_count",
    "bought_past_month",
    "product_url",
    "image_url",
    "list_price",
    "discount_percent",
    "availability",
    "model",
    "capacity",
    "horsepower",
    "airflow",
    "suction",
    "power",
    "weight",
    "dimensions",
    "hose_length",
    "cord_length",
    "filtration",
    "tank_material",
    "clean_tank_capacity",
    "dirty_tank_capacity",
    "heating_or_steam",
    "accessories",
    "warranty",
    "special_features",
    "bullet_points",
    "specifications",
    "detail_status",
    "detail_collected_at",
]


@dataclass
class FetchResult:
    data: pd.DataFrame
    warnings: list[str]


ALIASES = {
    "rank": ["rank", "bsr", "bsr rank", "排名", "榜单排名"],
    "asin": ["asin", "amazon asin", "商品asin"],
    "brand": ["brand", "品牌"],
    "title": ["title", "product title", "product name", "产品名称", "标题"],
    "price": ["price", "售价", "价格", "list price"],
    "coupon": ["coupon", "discount", "优惠券", "折扣"],
    "rating": ["rating", "stars", "评分", "星级"],
    "review_count": ["review_count", "reviews", "ratings count", "评论数", "评价数量"],
    "bought_past_month": ["bought_past_month", "monthly bought", "过去一个月购买量", "月购买量"],
    "product_url": ["product_url", "url", "link", "产品链接", "amazon link"],
    "image_url": ["image_url", "image", "图片", "主图"],
    "list_price": ["list_price", "original price", "原价", "建议零售价"],
    "discount_percent": ["discount_percent", "discount rate", "折扣率"],
    "availability": ["availability", "stock status", "库存状态"],
    "model": ["model", "model number", "型号"],
    "capacity": ["capacity", "容量", "gal"],
    "horsepower": ["horsepower", "hp", "峰值马力"],
    "airflow": ["airflow", "cfm", "风量"],
    "suction": ["suction", "kpa", "吸力", "water lift"],
    "power": ["power", "wattage", "功率"],
    "weight": ["weight", "item weight", "重量"],
    "dimensions": ["dimensions", "product dimensions", "尺寸"],
    "hose_length": ["hose_length", "hose length", "软管长度"],
    "cord_length": ["cord_length", "cord length", "电源线长度"],
    "filtration": ["filtration", "filter", "过滤系统"],
    "tank_material": ["tank_material", "tank material", "桶体材质"],
    "clean_tank_capacity": ["clean_tank_capacity", "clean tank", "清水箱容量"],
    "dirty_tank_capacity": ["dirty_tank_capacity", "dirty tank", "污水箱容量"],
    "heating_or_steam": ["heating_or_steam", "steam", "heat", "加热/蒸汽"],
    "accessories": ["accessories", "included components", "配件"],
    "warranty": ["warranty", "保修"],
    "special_features": ["special_features", "special feature", "特殊功能"],
    "bullet_points": ["bullet_points", "bullets", "五点描述"],
    "specifications": ["specifications", "specs", "规格明细"],
    "detail_status": ["detail_status", "详情采集状态"],
    "detail_collected_at": ["detail_collected_at", "详情采集时间"],
}


def _column_lookup(columns: Iterable[str]) -> dict[str, str]:
    normalized = {str(c).strip().lower(): str(c) for c in columns}
    result: dict[str, str] = {}
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if alias.lower() in normalized:
                result[normalized[alias.lower()]] = target
                break
    return result


def _number(value) -> float | None:
    if pd.isna(value):
        return None
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", str(value))
    return float(match.group(0).replace(",", "")) if match else None


def normalize_product_table(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result = result.rename(columns=_column_lookup(result.columns))
    for column in AMAZON_COLUMNS:
        if column not in result:
            result[column] = ""
    extra_columns = [column for column in result.columns if column not in AMAZON_COLUMNS]
    result = result[AMAZON_COLUMNS + extra_columns]
    result["rank"] = pd.to_numeric(result["rank"].map(_number), errors="coerce").astype("Int64")
    for col in [
        "price",
        "list_price",
        "discount_percent",
        "rating",
        "review_count",
        "bought_past_month",
    ]:
        result[col] = pd.to_numeric(result[col].map(_number), errors="coerce")
    for col in ["asin", "brand", "title", "product_url", "detail_status"]:
        result[col] = result[col].fillna("").astype(str).str.strip()
    result = result[(result["asin"] != "") | (result["title"] != "")]
    result = result.drop_duplicates(subset=["asin"], keep="first")
    return result.sort_values(["rank", "asin"], na_position="last").reset_index(drop=True)


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Cache-Control": "no-cache",
    }


def _download(url: str, timeout: int = 25) -> str:
    request = Request(url, headers=_headers())
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        if exc.code in (403, 429, 503):
            raise RuntimeError(
                f"Amazon 返回 {exc.code}，可能触发了访问限制。请稍后重试或上传表格。"
            ) from exc
        raise RuntimeError(f"页面请求失败（HTTP {exc.code}）。") from exc
    except URLError as exc:
        raise RuntimeError(f"网络连接失败：{exc.reason}") from exc
    text = body.decode("utf-8", errors="replace")
    lowered = text.lower()
    if "captcha" in lowered or "robot check" in lowered:
        raise RuntimeError("Amazon 返回验证码页面。请切换到上传文件方式。")
    return text


def _parse_card(card, fallback_rank: int) -> dict:
    asin = card.get("data-asin", "")
    link_el = card.select_one("a[href*='/dp/'], a[href*='/gp/product/']")
    href = link_el.get("href", "") if link_el else ""
    if not asin:
        match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", href)
        asin = match.group(1) if match else ""
    title_el = card.select_one(
        "div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1, "
        "div._cDEzb_p13n-sc-css-line-clamp-2_EWgCb, "
        "span.a-size-base-plus, span.a-size-medium, img[alt]"
    )
    title = ""
    if title_el:
        title = title_el.get("alt", "") if title_el.name == "img" else title_el.get_text(" ", strip=True)
    rank_el = card.select_one(".zg-bdg-text")
    rank = _number(rank_el.get_text(strip=True) if rank_el else fallback_rank)
    price_el = card.select_one(".a-price .a-offscreen, .p13n-sc-price")
    rating_el = card.select_one(".a-icon-alt")
    reviews_el = card.select_one("span.a-size-small")
    brand = title.split()[0] if title else ""
    return {
        "rank": rank,
        "asin": asin,
        "brand": brand,
        "title": title,
        "price": _number(price_el.get_text(strip=True) if price_el else None),
        "coupon": "",
        "rating": _number(rating_el.get_text(strip=True) if rating_el else None),
        "review_count": _number(reviews_el.get_text(strip=True) if reviews_el else None),
        "bought_past_month": None,
        "product_url": f"https://www.amazon.com/dp/{asin}" if asin else "",
        "image_url": "",
        "list_price": None,
        "discount_percent": None,
        "availability": "",
        "model": "",
        "capacity": "",
        "horsepower": "",
        "airflow": "",
        "suction": "",
        "power": "",
        "weight": "",
        "dimensions": "",
        "hose_length": "",
        "cord_length": "",
        "filtration": "",
        "tank_material": "",
        "clean_tank_capacity": "",
        "dirty_tank_capacity": "",
        "heating_or_steam": "",
        "accessories": "",
        "warranty": "",
        "special_features": "",
        "bullet_points": "",
        "specifications": "",
        "detail_status": "",
        "detail_collected_at": "",
    }


def _clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _product_json_ld(soup) -> dict:
    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        objects = payload if isinstance(payload, list) else [payload]
        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "Product":
                return obj
            if isinstance(obj, dict) and isinstance(obj.get("@graph"), list):
                for item in obj["@graph"]:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        return item
    return {}


def _extract_specifications(soup) -> dict[str, str]:
    specs: dict[str, str] = {}
    selectors = [
        "#productOverview_feature_div tr",
        "#productDetails_techSpec_section_1 tr",
        "#productDetails_techSpec_section_2 tr",
        "#productDetails_detailBullets_sections1 tr",
        "#productDetails_detailBullets_sections2 tr",
        "table.a-normal.a-spacing-micro tr",
    ]
    for row in soup.select(", ".join(selectors)):
        cells = row.select("th, td")
        if len(cells) < 2:
            continue
        key = _clean_text(cells[0].get_text(" ", strip=True)).rstrip(":")
        value = _clean_text(cells[-1].get_text(" ", strip=True))
        if key and value and key.lower() != value.lower():
            specs.setdefault(key, value)
    for li in soup.select("#detailBullets_feature_div li"):
        text = _clean_text(li.get_text(" ", strip=True))
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        key = key.strip("‎ ").rstrip(":")
        value = value.strip()
        if key and value:
            specs.setdefault(key, value)
    return specs


def _spec_value(specs: dict[str, str], *needles: str) -> str:
    for key, value in specs.items():
        normalized = key.lower()
        if any(needle.lower() in normalized for needle in needles):
            return value
    return ""


def _regex_value(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_text(match.group(0))
    return ""


def parse_product_detail_html(html: str, asin: str = "", product_url: str = "") -> dict:
    """Parse one Amazon product page without inventing absent specifications."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    page_text = _clean_text(soup.get_text(" ", strip=True))
    lowered = page_text.lower()
    if "robot check" in lowered or "enter the characters you see below" in lowered:
        raise RuntimeError("Amazon 返回验证码页面")

    product = _product_json_ld(soup)
    specs = _extract_specifications(soup)
    bullets = [
        _clean_text(item.get_text(" ", strip=True))
        for item in soup.select("#feature-bullets li span.a-list-item")
    ]
    bullets = [item for item in bullets if item]
    combined = " | ".join([page_text, *bullets, *specs.values()])

    brand = product.get("brand", "")
    if isinstance(brand, dict):
        brand = brand.get("name", "")
    brand = _clean_text(brand)
    if not brand:
        brand = _clean_text(
            _spec_value(specs, "brand")
            or (
                soup.select_one("#bylineInfo").get_text(" ", strip=True)
                if soup.select_one("#bylineInfo")
                else ""
            )
        )
        brand = re.sub(r"^(Visit the|Brand:)\s+", "", brand, flags=re.IGNORECASE)
        brand = re.sub(r"\s+Store$", "", brand, flags=re.IGNORECASE)

    offers = product.get("offers", {}) or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    aggregate = product.get("aggregateRating", {}) or {}
    title_el = soup.select_one("#productTitle")
    title = _clean_text(product.get("name") or (title_el.get_text(" ", strip=True) if title_el else ""))
    price_el = soup.select_one(
        "#corePrice_feature_div .a-price .a-offscreen, "
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen, "
        ".priceToPay .a-offscreen"
    )
    list_price_el = soup.select_one(
        ".basisPrice .a-offscreen, .a-price.a-text-price .a-offscreen"
    )
    rating_el = soup.select_one("#acrPopover")
    review_count_el = soup.select_one("#acrCustomerReviewText")
    image_el = soup.select_one("#landingImage, #imgBlkFront")
    availability_el = soup.select_one("#availability span")
    coupon_el = soup.select_one("#couponTextpctch, #couponText")

    price = _number(
        offers.get("price")
        or (price_el.get_text(" ", strip=True) if price_el else None)
    )
    list_price = _number(list_price_el.get_text(" ", strip=True) if list_price_el else None)
    discount_percent = (
        round((list_price - price) / list_price * 100, 1)
        if price is not None and list_price and list_price > price
        else None
    )
    image_url = ""
    if image_el:
        image_url = image_el.get("data-old-hires") or image_el.get("src") or ""
    if not image_url:
        image = product.get("image", "")
        image_url = image[0] if isinstance(image, list) and image else str(image or "")

    model = _spec_value(specs, "model name", "item model number", "model number")
    capacity = _spec_value(specs, "capacity")
    if not capacity:
        capacity = _regex_value(
            combined,
            [r"\b\d+(?:\.\d+)?\s*(?:gallon|gallons|gal|liter|liters|litre|litres|L|quart|qt)\b"],
        )
    horsepower = _spec_value(specs, "horsepower")
    if not horsepower:
        horsepower = _regex_value(
            combined, [r"\b\d+(?:\.\d+)?\s*(?:peak\s*)?(?:HP|horsepower)\b"]
        )

    clean_tank = _spec_value(specs, "clean tank")
    dirty_tank = _spec_value(specs, "dirty tank", "recovery tank")
    heating = _spec_value(specs, "heat", "steam")
    if not heating:
        if re.search(r"\b(?:steam|heated cleaning|heatwave)\b", combined, re.IGNORECASE):
            heating = "Yes（页面提及加热/蒸汽）"
        else:
            heating = "Not stated"

    bought_match = re.search(
        r"([\d,.]+[Kk]?\+?)\s+bought in past month", combined, re.IGNORECASE
    )
    bought_value = None
    if bought_match:
        bought_raw = bought_match.group(1).replace(",", "").replace("+", "")
        multiplier = 1000 if bought_raw.lower().endswith("k") else 1
        bought_value = _number(bought_raw.rstrip("Kk"))
        bought_value = bought_value * multiplier if bought_value is not None else None

    detail = {
        "asin": asin,
        "product_url": product_url,
        "title": title,
        "brand": brand,
        "image_url": image_url,
        "price": price,
        "list_price": list_price,
        "discount_percent": discount_percent,
        "coupon": _clean_text(coupon_el.get_text(" ", strip=True) if coupon_el else ""),
        "rating": _number(
            aggregate.get("ratingValue")
            or (rating_el.get("title") if rating_el else None)
        ),
        "review_count": _number(
            aggregate.get("reviewCount")
            or (review_count_el.get_text(" ", strip=True) if review_count_el else None)
        ),
        "bought_past_month": bought_value,
        "availability": _clean_text(
            offers.get("availability")
            or (availability_el.get_text(" ", strip=True) if availability_el else "")
        ).replace("https://schema.org/", ""),
        "model": model,
        "capacity": capacity,
        "horsepower": horsepower,
        "airflow": _spec_value(specs, "air flow", "airflow")
        or _regex_value(combined, [r"\b\d+(?:\.\d+)?\s*CFM\b"]),
        "suction": _spec_value(specs, "suction", "water lift")
        or _regex_value(
            combined,
            [
                r"\b\d+(?:\.\d+)?\s*kPa\b",
                r"\b\d+(?:\.\d+)?\s*(?:inches|in\.?)\s*(?:of\s*)?(?:water|water lift)\b",
            ],
        ),
        "power": _spec_value(specs, "wattage", "power")
        or _regex_value(combined, [r"\b\d+(?:\.\d+)?\s*(?:W|watts)\b"]),
        "weight": _spec_value(specs, "item weight", "weight"),
        "dimensions": _spec_value(specs, "product dimensions", "item dimensions"),
        "hose_length": _spec_value(specs, "hose length")
        or _regex_value(combined, [r"\b\d+(?:\.\d+)?\s*(?:ft|feet|foot)\s+hose\b"]),
        "cord_length": _spec_value(specs, "cord length")
        or _regex_value(combined, [r"\b\d+(?:\.\d+)?\s*(?:ft|feet|foot)\s+(?:power\s+)?cord\b"]),
        "filtration": _spec_value(specs, "filter type", "filtration"),
        "tank_material": _spec_value(specs, "tank material", "material"),
        "clean_tank_capacity": clean_tank,
        "dirty_tank_capacity": dirty_tank,
        "heating_or_steam": heating,
        "accessories": _spec_value(specs, "included components", "included")
        or "；".join(
            item for item in bullets if re.search(r"\b(?:include|tool|nozzle|brush|attachment)\b", item, re.I)
        )[:1200],
        "warranty": _spec_value(specs, "warranty")
        or _regex_value(combined, [r"\b\d+(?:-\w+)?\s*year\s+(?:limited\s+)?warranty\b"]),
        "special_features": _spec_value(specs, "special feature"),
        "bullet_points": "；".join(bullets)[:4000],
        "specifications": json.dumps(specs, ensure_ascii=False),
        "detail_status": "成功" if specs or bullets else "部分成功",
        "detail_collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return detail


def _json_ld_products(soup) -> list[dict]:
    rows = []
    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        objects = payload if isinstance(payload, list) else [payload]
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("@type") != "ItemList":
                continue
            for index, item in enumerate(obj.get("itemListElement", []), start=1):
                product = item.get("item", item) if isinstance(item, dict) else {}
                url = product.get("url", "")
                asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
                offers = product.get("offers", {}) or {}
                rows.append(
                    {
                        "rank": item.get("position", index),
                        "asin": asin_match.group(1) if asin_match else "",
                        "brand": (product.get("brand") or {}).get("name", "")
                        if isinstance(product.get("brand"), dict)
                        else product.get("brand", ""),
                        "title": product.get("name", ""),
                        "price": offers.get("price"),
                        "product_url": url,
                    }
                )
    return rows


def _parse_bestseller_html(html: str) -> pd.DataFrame:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(
        "div[data-asin]._cDEzb_grid-cell_1uMOS, "
        "div.zg-grid-general-faceout, "
        "div[data-asin][id^='p13n-asin-index-'], "
        "#zg-ordered-list > li"
    )
    rows = [_parse_card(card, index) for index, card in enumerate(cards, start=1)]
    if not rows:
        rows = _json_ld_products(soup)
    return normalize_product_table(pd.DataFrame(rows))


def _finish_result(frame: pd.DataFrame, limit: int, method: str) -> FetchResult:
    frame = frame.drop_duplicates(subset=["asin"], keep="first")
    frame = frame.sort_values(["rank", "asin"], na_position="last").head(limit).reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("页面已打开，但没有识别到产品卡片；Amazon 页面结构可能已变化。")
    warnings = []
    if len(frame) < limit:
        warnings.append(
            f"本次只识别到 {len(frame)} 条记录，少于目标 {limit} 条。"
            f"采集方式为{method}；可能遇到验证码、区域差异或页面未继续加载，"
            "请重试或使用上传文件补齐。"
        )
    return FetchResult(frame, warnings)


def fetch_bestseller_products_static(url: str, limit: int = 50) -> FetchResult:
    html = _download(url)
    return _finish_result(_parse_bestseller_html(html), limit, "普通网页请求")


def fetch_bestseller_products_browser(
    url: str,
    limit: int = 50,
    *,
    max_scrolls: int = 30,
    headless: bool = True,
) -> FetchResult:
    """Use a real Chromium page and retain rows found across lazy-load scrolls."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "尚未安装浏览器采集组件。请重新运行 run_mac.command 完成 Playwright 安装。"
        ) from exc

    frames: list[pd.DataFrame] = []
    with sync_playwright() as playwright:
        try:
            system_chromium = (
                shutil.which("chromium")
                or shutil.which("chromium-browser")
                or shutil.which("google-chrome")
            )
            browser = playwright.chromium.launch(
                headless=headless,
                executable_path=system_chromium,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
        except Exception as exc:
            raise RuntimeError(
                "Chromium 尚未安装。请在项目目录运行："
                ".venv/bin/python -m playwright install chromium"
            ) from exc
        context = browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 1100},
            user_agent=_headers()["User-Agent"],
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
            stable_rounds = 0
            previous_count = 0
            for _ in range(max_scrolls):
                html = page.content()
                lowered = html.lower()
                if "robot check" in lowered or "enter the characters you see below" in lowered:
                    raise RuntimeError("Amazon 返回验证码页面，请稍后重试或使用上传文件。")
                parsed = _parse_bestseller_html(html)
                if not parsed.empty:
                    frames.append(parsed)
                combined = (
                    pd.concat(frames, ignore_index=True).drop_duplicates("asin")
                    if frames
                    else pd.DataFrame()
                )
                current_count = len(combined)
                if current_count >= limit:
                    break
                stable_rounds = stable_rounds + 1 if current_count == previous_count else 0
                previous_count = current_count

                page.mouse.wheel(0, 2200)
                page.evaluate("window.scrollBy(0, Math.max(1400, window.innerHeight * 1.4))")
                page.wait_for_timeout(1100)
                if stable_rounds >= 3:
                    page.keyboard.press("End")
                    page.wait_for_timeout(1800)
                if stable_rounds >= 7:
                    break
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("Amazon 页面加载超时，请检查网络后重试。") from exc
        finally:
            context.close()
            browser.close()

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return _finish_result(
        normalize_product_table(combined), limit, f"Chromium 自动滚动（{len(frames)}轮页面快照）"
    )


def fetch_bestseller_products(
    url: str, limit: int = 50, *, use_browser: bool = True
) -> FetchResult:
    if use_browser:
        return fetch_bestseller_products_browser(url, limit)
    return fetch_bestseller_products_static(url, limit)


def enrich_product_details_browser(
    products: pd.DataFrame,
    max_products: int = 50,
    *,
    headless: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> FetchResult:
    """Visit product pages and merge verifiable specifications into the ranking dataset."""
    source = normalize_product_table(products).reset_index(drop=True)
    if source.empty:
        return FetchResult(source, ["没有可补全详情的产品。"])
    targets = source.head(max(1, min(int(max_products), len(source)))).copy()
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "尚未安装浏览器采集组件。请重新运行 run_mac.command 完成 Playwright 安装。"
        ) from exc

    details: list[dict] = []
    warnings: list[str] = []
    consecutive_blocks = 0
    with sync_playwright() as playwright:
        system_chromium = (
            shutil.which("chromium")
            or shutil.which("chromium-browser")
            or shutil.which("google-chrome")
        )
        try:
            browser = playwright.chromium.launch(
                headless=headless,
                executable_path=system_chromium,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
        except Exception as exc:
            raise RuntimeError(
                "Chromium 尚未安装。请在项目目录运行："
                ".venv/bin/python -m playwright install chromium"
            ) from exc
        context = browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 1100},
            user_agent=_headers()["User-Agent"],
        )
        page = context.new_page()
        for position, row in targets.iterrows():
            asin = str(row.get("asin", "")).strip()
            url = str(row.get("product_url", "")).strip()
            if not url and asin:
                url = f"https://www.amazon.com/dp/{asin}"
            label = asin or str(row.get("title", ""))[:40]
            if on_progress:
                on_progress(position + 1, len(targets), label)
            if not url:
                warnings.append(f"第 {position + 1} 条缺少 ASIN 和产品链接，已跳过。")
                continue
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(900)
                html = page.content()
                details.append(parse_product_detail_html(html, asin=asin, product_url=url))
                consecutive_blocks = 0
            except PlaywrightTimeoutError:
                warnings.append(f"{label}：详情页加载超时。")
                details.append(
                    {
                        "asin": asin,
                        "product_url": url,
                        "detail_status": "加载超时",
                        "detail_collected_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                    }
                )
            except RuntimeError as exc:
                message = str(exc)
                warnings.append(f"{label}：{message}。")
                consecutive_blocks += 1 if "验证码" in message else 0
                details.append(
                    {
                        "asin": asin,
                        "product_url": url,
                        "detail_status": "验证码/访问限制" if "验证码" in message else "失败",
                        "detail_collected_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                    }
                )
                if consecutive_blocks >= 2:
                    warnings.append("连续遇到 Amazon 验证码，已提前停止以避免重复请求。")
                    break
            except Exception as exc:
                warnings.append(f"{label}：详情提取失败（{type(exc).__name__}）。")
                details.append(
                    {
                        "asin": asin,
                        "product_url": url,
                        "detail_status": f"失败：{type(exc).__name__}",
                        "detail_collected_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                    }
                )
        context.close()
        browser.close()

    if not details:
        return FetchResult(source, warnings or ["没有采集到产品详情。"])
    detail_frame = pd.DataFrame(details)
    result = source.copy()
    for detail in detail_frame.to_dict(orient="records"):
        asin = str(detail.get("asin", "")).strip()
        matches = result.index[result["asin"] == asin].tolist() if asin else []
        if not matches:
            url = str(detail.get("product_url", "")).strip()
            matches = result.index[result["product_url"] == url].tolist() if url else []
        if not matches:
            continue
        row_index = matches[0]
        for column, value in detail.items():
            if column in {"asin", "product_url"} or value is None:
                continue
            if isinstance(value, float) and pd.isna(value):
                continue
            if column not in result:
                result[column] = ""
            if str(value).strip() != "":
                result.at[row_index, column] = value
    success_count = int(result["detail_status"].isin(["成功", "部分成功"]).sum())
    if success_count < len(targets):
        warnings.append(
            f"本轮成功或部分成功补全 {success_count}/{len(targets)} 个详情页；"
            "失败项可稍后重试或在导出的产品表中人工补充。"
        )
    return FetchResult(normalize_product_table(result), warnings)
