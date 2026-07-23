from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from typing import Iterable
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
    "model",
    "capacity",
    "horsepower",
    "clean_tank_capacity",
    "dirty_tank_capacity",
    "heating_or_steam",
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
    "model": ["model", "model number", "型号"],
    "capacity": ["capacity", "容量", "gal"],
    "horsepower": ["horsepower", "hp", "峰值马力"],
    "clean_tank_capacity": ["clean_tank_capacity", "clean tank", "清水箱容量"],
    "dirty_tank_capacity": ["dirty_tank_capacity", "dirty tank", "污水箱容量"],
    "heating_or_steam": ["heating_or_steam", "steam", "heat", "加热/蒸汽"],
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
    result = result[AMAZON_COLUMNS]
    result["rank"] = pd.to_numeric(result["rank"].map(_number), errors="coerce").astype("Int64")
    for col in ["price", "rating", "review_count", "bought_past_month"]:
        result[col] = pd.to_numeric(result[col].map(_number), errors="coerce")
    for col in ["asin", "brand", "title", "product_url"]:
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
        "model": "",
        "capacity": "",
        "horsepower": "",
        "clean_tank_capacity": "",
        "dirty_tank_capacity": "",
        "heating_or_steam": "",
    }


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
