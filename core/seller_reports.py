from __future__ import annotations

import re

import numpy as np
import pandas as pd


ALIASES = {
    "date": ["date", "日期", "start date", "开始日期"],
    "asin": ["asin", "(child) asin", "child asin", "商品asin"],
    "sku": ["sku", "seller sku", "merchant sku", "卖家sku"],
    "brand": ["brand", "品牌"],
    "store_category": ["store_category", "category", "店铺分类", "品类"],
    "title": ["title", "product name", "item name", "商品名称", "标题"],
    "units": ["units", "units ordered", "units sold", "销量", "已订购商品数量"],
    "revenue": [
        "revenue",
        "ordered product sales",
        "sales",
        "销售额",
        "已订购商品销售额",
    ],
    "sessions": ["sessions", "会话数"],
    "page_views": ["page_views", "page views", "页面浏览量"],
    "buy_box_percentage": ["buy_box_percentage", "buy box percentage", "buy box百分比"],
    "inventory": ["inventory", "quantity", "库存", "可售数量"],
    "impressions": ["impressions", "曝光量"],
    "clicks": ["clicks", "点击量"],
    "spend": ["spend", "cost", "广告花费"],
    "ad_sales": ["ad_sales", "7 day total sales", "14 day total sales", "广告销售额"],
    "orders": ["orders", "7 day total orders", "14 day total orders", "广告订单"],
    "price": ["price", "list price", "价格"],
    "bsr": ["bsr", "sales rank", "best sellers rank", "bsr排名"],
    "bsr_change": ["bsr_change", "rank shift", "bsr change", "bsr变化"],
}


def _canonical_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def _normalize(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = df.copy()
    lookup = {_canonical_name(c): c for c in result.columns}
    rename = {}
    for target in columns:
        for alias in ALIASES.get(target, [target]):
            if _canonical_name(alias) in lookup:
                rename[lookup[_canonical_name(alias)]] = target
                break
    result = result.rename(columns=rename)
    for col in columns:
        if col not in result:
            result[col] = np.nan if col not in {"asin", "sku", "brand", "title", "store_category"} else ""
    result = result[columns]
    for text_col in ["asin", "sku", "brand", "title", "store_category"]:
        if text_col in result:
            result[text_col] = result[text_col].fillna("").astype(str).str.strip()
    return result


def _numeric(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.extract(r"(-?\d+(?:\.\d+)?)")[0]
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)


def _aggregate_period(
    data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, suffix: str
) -> pd.DataFrame:
    subset = data[(data["date"] >= start) & (data["date"] <= end)]
    grouped = (
        subset.groupby("key", dropna=False)[["units", "revenue", "sessions", "page_views"]]
        .sum()
        .reset_index()
    )
    return grouped.rename(
        columns={
            "units": f"units_{suffix}",
            "revenue": f"revenue_{suffix}",
            "sessions": f"sessions_{suffix}",
            "page_views": f"page_views_{suffix}",
        }
    )


def _pct(current: pd.Series, previous: pd.Series) -> pd.Series:
    return np.where(previous.abs() > 1e-9, (current - previous) / previous, np.nan)


def _scope_filter(frame: pd.DataFrame) -> pd.DataFrame:
    text = (frame["brand"] + " " + frame["store_category"] + " " + frame["title"]).str.lower()
    vacmaster = text.str.contains(r"\bvacmaster\b", regex=True)
    kenmore = text.str.contains(r"\bkenmore\b", regex=True) & text.str.contains(
        r"vacuum|floor|carpet|cleaner|mop", regex=True
    )
    if (vacmaster | kenmore).any():
        return frame[vacmaster | kenmore].copy()
    return frame


def _diagnosis(row: pd.Series) -> str:
    change = row.get("wow_revenue")
    if pd.isna(change) or abs(change) <= 0.05:
        return "本期未触发 ±5% 异常阈值。"
    direction = "上涨" if change > 0 else "下跌"
    clues = [f"销售额周环比{direction}{abs(change):.1%}"]
    if row.get("inventory", 1) <= 0:
        clues.append("库存为零或缺货")
    if row.get("ctr", 0) and row["ctr"] < 0.003:
        clues.append("广告CTR偏低")
    if row.get("cvr", 0) and row["cvr"] < 0.05:
        clues.append("广告转化率偏低")
    if row.get("acos", 0) and row["acos"] > 0.4:
        clues.append("ACoS偏高")
    return "；".join(clues) + "。"


def _recommendation(row: pd.Series) -> str:
    actions = []
    if row.get("inventory", 1) <= 0:
        actions.append("优先补货并核查不可售库存")
    if row.get("ctr", 0) and row["ctr"] < 0.003:
        actions.append("优化主图、标题和广告关键词相关性")
    if row.get("cvr", 0) and row["cvr"] < 0.05:
        actions.append("检查价格、Coupon、评论与详情页说服力")
    if row.get("acos", 0) and row["acos"] > 0.4:
        actions.append("降低低转化词出价并补充否定关键词")
    if not actions and row.get("wow_revenue", 0) < -0.05:
        actions.append("核查流量、价格、促销、BSR及竞品活动变化")
    if not actions and row.get("wow_revenue", 0) > 0.05:
        actions.append("确认增长来源并保障库存，复用有效投放和促销")
    return "；".join(actions) if actions else "保持监控。"


def build_sales_analysis(
    business: pd.DataFrame,
    listings: pd.DataFrame,
    ads: pd.DataFrame,
    as_of: pd.Timestamp,
    saved_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    columns = [
        "date", "asin", "sku", "brand", "store_category", "title",
        "units", "revenue", "sessions", "page_views", "buy_box_percentage", "inventory",
        "bsr", "bsr_change",
    ]
    data = _normalize(business, columns)
    for col in [
        "units", "revenue", "sessions", "page_views", "buy_box_percentage",
        "inventory", "bsr", "bsr_change",
    ]:
        data[col] = _numeric(data[col])
    parsed_dates = pd.to_datetime(data["date"], errors="coerce")
    has_dates = parsed_dates.notna().any()
    data["date"] = parsed_dates.fillna(as_of)
    data["key"] = data["asin"].where(data["asin"] != "", data["sku"])
    data = data[data["key"] != ""]

    identity = (
        data.sort_values("date")
        .groupby("key")[
            ["asin", "sku", "brand", "store_category", "title", "inventory", "bsr", "bsr_change"]
        ]
        .last()
        .reset_index()
    )
    identity = _scope_filter(identity)
    keys = set(identity["key"])
    data = data[data["key"].isin(keys)]

    if has_dates:
        current = _aggregate_period(data, as_of - pd.Timedelta(days=6), as_of, "current")
        periods = {
            "wow": (
                as_of - pd.Timedelta(days=6), as_of,
                as_of - pd.Timedelta(days=13), as_of - pd.Timedelta(days=7),
            ),
            "mom": (
                as_of - pd.Timedelta(days=29), as_of,
                as_of - pd.Timedelta(days=59), as_of - pd.Timedelta(days=30),
            ),
            "qoq": (
                as_of - pd.Timedelta(days=89), as_of,
                as_of - pd.Timedelta(days=179), as_of - pd.Timedelta(days=90),
            ),
            "hoh": (
                as_of - pd.Timedelta(days=181), as_of,
                as_of - pd.Timedelta(days=363), as_of - pd.Timedelta(days=182),
            ),
            "yoy": (
                as_of - pd.Timedelta(days=364), as_of,
                as_of - pd.Timedelta(days=729), as_of - pd.Timedelta(days=365),
            ),
        }
    else:
        current = (
            data.groupby("key")[["units", "revenue", "sessions", "page_views"]]
            .sum()
            .reset_index()
            .rename(columns={c: f"{c}_current" for c in ["units", "revenue", "sessions", "page_views"]})
        )
        periods = {}

    result = identity.merge(current, on="key", how="left")
    for label, (current_start, current_end, previous_start, previous_end) in periods.items():
        period_current = _aggregate_period(
            data, current_start, current_end, f"{label}_current"
        )
        period_previous = _aggregate_period(
            data, previous_start, previous_end, f"{label}_previous"
        )
        result = result.merge(period_current, on="key", how="left")
        result = result.merge(period_previous, on="key", how="left")
        result[f"{label}_units"] = _pct(
            result[f"units_{label}_current"].fillna(0),
            result[f"units_{label}_previous"].fillna(0),
        )
        result[f"{label}_revenue"] = _pct(
            result[f"revenue_{label}_current"].fillna(0),
            result[f"revenue_{label}_previous"].fillna(0),
        )
    for label in ["wow", "mom", "qoq", "hoh", "yoy"]:
        if f"{label}_units" not in result:
            result[f"{label}_units"] = np.nan
            result[f"{label}_revenue"] = np.nan

    if not listings.empty:
        listing = _normalize(
            listings, ["asin", "sku", "brand", "store_category", "title", "inventory", "price"]
        )
        listing["key"] = listing["asin"].where(listing["asin"] != "", listing["sku"])
        listing["inventory"] = _numeric(listing["inventory"])
        listing["price"] = _numeric(listing["price"])
        listing = listing.drop_duplicates("key")
        result = result.merge(
            listing[["key", "inventory", "price"]],
            on="key",
            how="left",
            suffixes=("", "_listing"),
        )
        result["inventory"] = result["inventory_listing"].combine_first(result["inventory"])
        result = result.drop(columns=["inventory_listing"])
    else:
        result["price"] = np.nan

    result["impressions"] = 0.0
    result["clicks"] = 0.0
    result["spend"] = 0.0
    result["ad_sales"] = 0.0
    result["ad_orders"] = 0.0
    if not ads.empty:
        ad = _normalize(
            ads, ["date", "asin", "sku", "impressions", "clicks", "spend", "ad_sales", "orders"]
        )
        ad["key"] = ad["asin"].where(ad["asin"] != "", ad["sku"])
        for col in ["impressions", "clicks", "spend", "ad_sales", "orders"]:
            ad[col] = _numeric(ad[col])
        ad_grouped = (
            ad.groupby("key")[["impressions", "clicks", "spend", "ad_sales", "orders"]]
            .sum()
            .reset_index()
            .rename(columns={"orders": "ad_orders"})
        )
        result = result.drop(columns=["impressions", "clicks", "spend", "ad_sales", "ad_orders"])
        result = result.merge(ad_grouped, on="key", how="left")
    for col in ["units_current", "revenue_current", "sessions_current", "page_views_current",
                "impressions", "clicks", "spend", "ad_sales", "ad_orders"]:
        result[col] = result[col].fillna(0)
    result["ctr"] = np.where(result["impressions"] > 0, result["clicks"] / result["impressions"], np.nan)
    result["cvr"] = np.where(result["clicks"] > 0, result["ad_orders"] / result["clicks"], np.nan)
    result["acos"] = np.where(result["ad_sales"] > 0, result["spend"] / result["ad_sales"], np.nan)
    result["needs_attention"] = result["wow_revenue"].abs().fillna(0) > 0.05
    result["diagnosis"] = result.apply(_diagnosis, axis=1)
    result["recommendation"] = result.apply(_recommendation, axis=1)
    result["product_url"] = result["asin"].map(
        lambda asin: f"https://www.amazon.com/dp/{asin}" if asin else ""
    )
    ordered = [
        "brand", "store_category", "asin", "sku", "title", "price", "inventory",
        "units_current", "revenue_current", "wow_units", "wow_revenue",
        "mom_units", "mom_revenue", "qoq_units", "qoq_revenue",
        "hoh_units", "hoh_revenue", "yoy_units", "yoy_revenue",
        "sessions_current", "page_views_current", "impressions", "clicks",
        "spend", "ad_sales", "ctr", "cvr", "acos", "bsr", "bsr_change",
        "needs_attention", "diagnosis", "recommendation", "product_url",
    ]
    return result[ordered].sort_values(
        ["needs_attention", "revenue_current"], ascending=[False, False]
    ).reset_index(drop=True)
