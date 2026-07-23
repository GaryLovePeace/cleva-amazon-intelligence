from __future__ import annotations

import re
from collections import Counter

import pandas as pd


ALIASES = {
    "asin": ["asin", "商品asin"],
    "rating": ["rating", "stars", "star rating", "星级", "评分"],
    "review_title": ["review_title", "title", "评论标题"],
    "review_text": ["review_text", "review", "body", "评论正文", "评论内容"],
    "review_date": ["review_date", "date", "评论日期"],
}

PAIN_RULES = {
    "wet_dry": {
        "软管折弯/破裂": ["hose", "crack", "split", "kink", "软管", "开裂"],
        "过滤堵塞/吸力衰减": ["filter", "clog", "suction", "weak", "堵塞", "吸力"],
        "脚轮卡顿/机器翻倒": ["caster", "wheel", "tip", "fall over", "脚轮", "翻倒"],
        "噪音过大": ["noise", "noisy", "loud", "高频", "噪音"],
        "漏水/密封问题": ["leak", "seal", "gasket", "漏水", "密封"],
        "电机/耐用性": ["motor", "burn", "broke", "stopped", "电机", "损坏"],
    },
    "spot_cleaner": {
        "喷嘴不喷水/水压不足": ["not spray", "spray", "pump", "pressure", "不喷水", "水泵", "水压"],
        "软管破裂/发霉发臭": ["hose", "crack", "mold", "smell", "odor", "软管", "发霉", "异味"],
        "水箱/密封圈漏水": ["tank", "leak", "seal", "gasket", "水箱", "漏水", "密封"],
        "抽取吸力不足": ["suction", "wet", "dry", "extract", "吸力", "抽取", "不干"],
        "清洁维护困难": ["clean", "dirty", "rinse", "清洗", "维护", "污水"],
        "噪音/发热": ["noise", "loud", "hot", "overheat", "噪音", "发热"],
    },
}


def normalize_reviews(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    lower = {str(c).strip().lower(): str(c) for c in result.columns}
    rename = {}
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if alias.lower() in lower:
                rename[lower[alias.lower()]] = target
                break
    result = result.rename(columns=rename)
    for col in ALIASES:
        if col not in result:
            result[col] = ""
    result = result[list(ALIASES)]
    result["rating"] = pd.to_numeric(
        result["rating"].astype(str).str.extract(r"(\d+(?:\.\d+)?)")[0],
        errors="coerce",
    )
    result["review_text"] = result["review_text"].fillna("").astype(str).str.strip()
    result["review_title"] = result["review_title"].fillna("").astype(str).str.strip()
    result["asin"] = result["asin"].fillna("").astype(str).str.strip()
    return result[result["review_text"] != ""].reset_index(drop=True)


def analyze_reviews(reviews: pd.DataFrame, category_key: str) -> list[dict]:
    if reviews.empty:
        return []
    low = reviews[(reviews["rating"].isna()) | (reviews["rating"] <= 3)]
    if low.empty:
        return []
    rules = PAIN_RULES.get(category_key, PAIN_RULES["wet_dry"])
    counts: Counter[str] = Counter()
    for text in (low["review_title"] + " " + low["review_text"]).str.lower():
        for label, terms in rules.items():
            if any(term.lower() in text for term in terms):
                counts[label] += 1
    denominator = max(len(low), 1)
    return [
        {
            "pain_point": label,
            "mentions": count,
            "mention_rate": round(count / denominator * 100, 1),
        }
        for label, count in counts.most_common()
    ]
