from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd


@dataclass
class LLMSettings:
    api_key: str
    base_url: str
    model: str

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


def settings_from_env(data_dir: str | None = None) -> LLMSettings:
    saved = {}
    if data_dir:
        config_path = Path(data_dir) / "deepseek_config.json"
        if config_path.exists():
            try:
                saved = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved = {}
    return LLMSettings(
        api_key=(
            os.getenv("OPENAI_API_KEY")
            or os.getenv("AI_API_KEY")
            or saved.get("api_key", "")
        ),
        base_url=(
            os.getenv("OPENAI_API_BASE")
            or os.getenv("AI_BASE_URL")
            or saved.get("base_url", "https://api.deepseek.com")
        ).rstrip("/"),
        model=(
            os.getenv("MODEL_ID")
            or os.getenv("AI_MODEL")
            or saved.get("model", "openai/deepseek-v4-flash")
        ),
    )


def save_settings(data_dir: str, settings: LLMSettings) -> Path:
    folder = Path(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "deepseek_config.json"
    path.write_text(
        json.dumps(
            {
                "api_key": settings.api_key,
                "base_url": settings.base_url.rstrip("/"),
                "model": settings.model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _local_analysis(
    products: pd.DataFrame, local_voc: list[dict], category_key: str
) -> dict:
    brand_counts = products["brand"].replace("", pd.NA).dropna().value_counts()
    leaders = "、".join(f"{brand}（{count}款）" for brand, count in brand_counts.head(5).items())
    price = products["price"].dropna()
    if not price.empty:
        price_text = f"价格中位数约 ${price.median():.2f}，区间 ${price.min():.2f}-${price.max():.2f}。"
    else:
        price_text = "当前数据没有足够的有效价格。"
    category_name = "便携织物清洗机" if category_key == "spot_cleaner" else "干湿两用吸尘器"
    recommendations = [
        "优先处理高频结构性痛点，并在详情页以可量化证据表达改进。",
        "围绕主流价位段建立基础款、主推款和差异化高配款。",
        "将售后高频问题转化为安装、维护和使用场景内容。",
    ]
    if category_key == "spot_cleaner":
        recommendations.insert(0, "重点验证软管耐久、水箱密封、抽取残水与管道自清洁能力。")
    price_band_analysis = _build_price_band_analysis(products, category_key)
    brand_benchmark = _build_brand_benchmark(products)
    vacmaster = products[
        products["brand"].fillna("").astype(str).str.contains("vacmaster", case=False)
    ]
    competitors = products.drop(vacmaster.index)
    vacmaster_price = vacmaster["price"].dropna().mean() if not vacmaster.empty else None
    competitor_price = (
        competitors["price"].dropna().mean() if not competitors.empty else None
    )
    if vacmaster.empty:
        vacmaster_positioning = {
            "样本覆盖": "当前Top样本中未识别到Vacmaster，无法做品牌级定价结论。",
            "后续动作": "检查品牌字段或补充Vacmaster目标ASIN后重新分析。",
        }
    else:
        price_sentence = "价格数据不足"
        if pd.notna(vacmaster_price) and pd.notna(competitor_price) and competitor_price:
            difference = (vacmaster_price - competitor_price) / competitor_price
            price_sentence = (
                f"Vacmaster样本均价约${vacmaster_price:.2f}，"
                f"较其他品牌样本均价{'高' if difference > 0 else '低'}{abs(difference):.1%}。"
            )
        vacmaster_positioning = {
            "样本覆盖": f"识别到{len(vacmaster)}款Vacmaster产品。",
            "价格定位": price_sentence,
            "能力数据完整度": (
                f"{int(vacmaster['detail_status'].isin(['成功', '部分成功']).sum())}/"
                f"{len(vacmaster)}款已补全详情。"
            ),
            "结论边界": "本地结论基于当前Top榜单页面字段，不代表真实市场份额或销量。",
        }
    return {
        "market_landscape": (
            f"当前样本包含 {len(products)} 款{category_name}。"
            f"品牌出现情况：{leaders or '品牌字段不足'}。{price_text}"
        ),
        "pain_points": local_voc,
        "selling_points": [],
        "brand_comparison": "",
        "price_band_analysis": price_band_analysis,
        "brand_benchmark": brand_benchmark,
        "vacmaster_positioning": vacmaster_positioning,
        "capability_comparison": [],
        "opportunity_gaps": [
            "优先补全Top产品详情参数后，再识别同价格带的功能缺口。",
            "将Vacmaster目标ASIN与同容量、同马力或同清洁方式产品进行一对一比较。",
        ],
        "sku_insights": [],
        "recommendations": recommendations,
        "analysis_mode": "本地规则（未调用外部大模型）",
    }


def _build_price_band_analysis(products: pd.DataFrame, category_key: str) -> list[dict]:
    if products.empty or "price" not in products:
        return []
    data = products.copy()
    data["price"] = pd.to_numeric(data["price"], errors="coerce")
    data = data.dropna(subset=["price"])
    if data.empty:
        return []
    if category_key == "spot_cleaner":
        bins = [-float("inf"), 99.99, 149.99, 199.99, float("inf")]
        labels = ["<$100", "$100–149", "$150–199", "$200+"]
    else:
        bins = [-float("inf"), 59.99, 99.99, 149.99, float("inf")]
        labels = ["<$60", "$60–99", "$100–149", "$150+"]
    data["price_band"] = pd.cut(data["price"], bins=bins, labels=labels)
    data["is_vacmaster"] = data["brand"].fillna("").astype(str).str.contains(
        "vacmaster", case=False
    )
    rows = []
    for label in labels:
        group = data[data["price_band"] == label]
        if group.empty:
            continue
        rows.append(
            {
                "price_band": label,
                "sku_count": int(len(group)),
                "share_of_sample": round(len(group) / len(data) * 100, 1),
                "average_price": round(float(group["price"].mean()), 2),
                "vacmaster_sku_count": int(group["is_vacmaster"].sum()),
                "top_brands": "、".join(
                    group["brand"].replace("", pd.NA).dropna().value_counts().head(3).index
                ),
            }
        )
    return rows


def _build_brand_benchmark(products: pd.DataFrame) -> list[dict]:
    if products.empty or "brand" not in products:
        return []
    data = products.copy()
    data["brand"] = data["brand"].fillna("").astype(str).str.strip()
    data = data[data["brand"] != ""]
    if data.empty:
        return []
    rows = []
    for brand, group in data.groupby("brand", sort=False):
        price = pd.to_numeric(group["price"], errors="coerce")
        rating = pd.to_numeric(group["rating"], errors="coerce")
        rank = pd.to_numeric(group["rank"], errors="coerce")
        reviews = pd.to_numeric(group["review_count"], errors="coerce")
        detail_coverage = (
            group["detail_status"].isin(["成功", "部分成功"]).mean() * 100
            if "detail_status" in group
            else 0
        )
        rows.append(
            {
                "brand": brand,
                "sku_count": int(len(group)),
                "average_price": round(float(price.mean()), 2) if price.notna().any() else None,
                "average_rank": round(float(rank.mean()), 1) if rank.notna().any() else None,
                "average_rating": round(float(rating.mean()), 2)
                if rating.notna().any()
                else None,
                "average_review_count": round(float(reviews.mean()), 0)
                if reviews.notna().any()
                else None,
                "detail_coverage_percent": round(float(detail_coverage), 1),
            }
        )
    return sorted(rows, key=lambda row: (-row["sku_count"], row["brand"]))[:15]


def _parse_json_content(content: str) -> dict:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def _call_json(settings: LLMSettings, messages: list[dict]) -> dict:
    payload = json.dumps(
        {
            "model": settings.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{settings.base_url}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            error_body = ""
        raise RuntimeError(
            f"模型接口返回 HTTP {exc.code}"
            + (f"：{error_body}" if error_body else "")
        ) from exc
    return _parse_json_content(body["choices"][0]["message"]["content"])


def _safe_records(frame: pd.DataFrame, limit: int, text_limit: int = 700) -> list[dict]:
    records = frame.head(limit).astype(object).where(pd.notna(frame.head(limit)), "").to_dict(
        orient="records"
    )
    for record in records:
        for key, value in list(record.items()):
            if isinstance(value, str) and len(value) > text_limit:
                record[key] = value[:text_limit]
    return records


def _chunks(records: list[dict], size: int) -> list[list[dict]]:
    return [records[index : index + size] for index in range(0, len(records), size)]


def test_connection(settings: LLMSettings) -> str:
    if not settings.enabled:
        raise ValueError("请填写 API Key、Base URL 和模型名称。")
    result = _call_json(
        settings,
        [
            {
                "role": "system",
                "content": "只输出JSON，格式为 {\"status\":\"ok\",\"message\":\"connected\"}。",
            },
            {"role": "user", "content": "测试连接。"},
        ],
    )
    return str(result.get("message") or result.get("status") or "connected")


def analyze_market(
    products: pd.DataFrame,
    reviews: pd.DataFrame,
    local_voc: list[dict],
    category_key: str,
    settings: LLMSettings,
) -> dict:
    fallback = _local_analysis(products, local_voc, category_key)
    if not settings.enabled:
        return fallback
    product_records = _safe_records(products, 50)
    prompt = {
        "category": category_key,
        "products": product_records,
        "required_schema": {
            "market_landscape": "string",
            "brand_comparison": "string，spot_cleaner品类必须比较美系与中国出海品牌",
            "price_band_analysis": fallback["price_band_analysis"],
            "brand_benchmark": fallback["brand_benchmark"],
            "vacmaster_positioning": {
                "price_position": "string",
                "capability_position": "string",
                "strengths": ["string"],
                "weaknesses": ["string"],
                "evidence": ["string"],
            },
            "capability_comparison": [
                {
                    "dimension": "容量/马力/吸力/水箱/蒸汽/配件/保修等",
                    "vacmaster": "string",
                    "competitors": "string",
                    "commercial_implication": "string",
                    "evidence_level": "high|medium|low",
                }
            ],
            "opportunity_gaps": ["string"],
            "recommendations": ["string"],
        },
    }
    try:
        result = _call_json(
            settings,
            [
                {
                    "role": "system",
                    "content": (
                        "你是CLEVA市场情报分析师。只根据输入数据作答，不得捏造销量、份额或规格。"
                        "必须重点比较Vacmaster与同价格带竞品的定价、容量、马力/吸力、"
                        "配件、过滤、保修和功能差异；缺失字段必须写明数据不足。"
                        "spot_cleaner必须分析Bissell/Hoover等美系品牌与"
                        "Tineco/UWANT/Dreame等中国出海品牌的技术差异。"
                        "不能把bought_past_month当作真实销量或市场份额。"
                        "建议应具体、可执行，并明确估算数据。只输出严格JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False, default=str),
                },
            ],
        )
        result.setdefault("price_band_analysis", fallback["price_band_analysis"])
        result.setdefault("brand_benchmark", fallback["brand_benchmark"])
        result.setdefault("vacmaster_positioning", fallback["vacmaster_positioning"])
        result.setdefault("capability_comparison", [])
        result.setdefault("opportunity_gaps", fallback["opportunity_gaps"])
        result.setdefault("pain_points", [])
        result.setdefault("selling_points", [])
        result.setdefault("sku_insights", [])
        result["analysis_mode"] = f"大模型：{settings.model}"
        return result
    except Exception as exc:
        fallback["analysis_mode"] += f"；大模型调用失败：{str(exc)[:180]}"
        return fallback


def analyze_voc_report(
    products: pd.DataFrame,
    reviews: pd.DataFrame,
    local_voc: list[dict],
    category_key: str,
    settings: LLMSettings,
) -> dict:
    """Analyze uploaded reviews independently from the product commercial report."""
    fallback = {
        "pain_points": local_voc,
        "selling_points": [],
        "sku_insights": [],
        "voc_summary": (
            "未上传有效评论，无法生成VOC。"
            if reviews.empty
            else f"已读取{len(reviews)}条评论；当前使用本地关键词规则。"
        ),
        "recommendations": [],
        "analysis_mode": "本地规则（未调用外部大模型）",
    }
    if reviews.empty or not settings.enabled:
        return fallback
    product_fields = [
        field for field in ["asin", "brand", "title", "model", "price"] if field in products
    ]
    try:
        result = _call_json(
            settings,
            [
                {
                    "role": "system",
                    "content": (
                        "你是CLEVA消费者洞察分析师。只分析输入评论。区分1-3星痛点和"
                        "4-5星卖点，按ASIN归纳场景与建议。无证据时写数据不足，只输出JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "category": category_key,
                            "products": _safe_records(
                                products[product_fields], 50, text_limit=300
                            ),
                            "reviews": _safe_records(reviews, 500, text_limit=800),
                            "rule_based_voc": local_voc,
                            "required_schema": {
                                "voc_summary": "string",
                                "pain_points": [
                                    {
                                        "pain_point": "string",
                                        "mentions": 0,
                                        "mention_rate": 0.0,
                                        "evidence": "string",
                                    }
                                ],
                                "selling_points": [
                                    {
                                        "selling_point": "string",
                                        "mentions": 0,
                                        "mention_rate": 0.0,
                                        "evidence": "string",
                                    }
                                ],
                                "sku_insights": [
                                    {
                                        "asin": "string",
                                        "pain_points": ["string"],
                                        "selling_points": ["string"],
                                        "use_scenarios": ["string"],
                                        "differentiation_recommendation": "string",
                                        "confidence": "high|medium|low",
                                    }
                                ],
                                "recommendations": ["string"],
                            },
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
        )
        result["analysis_mode"] = f"大模型：{settings.model}"
        return result
    except Exception as exc:
        fallback["analysis_mode"] += f"；大模型调用失败：{str(exc)[:180]}"
        return fallback


def merge_sku_insights(products: pd.DataFrame, analysis: dict) -> pd.DataFrame:
    result = products.copy()
    columns = [
        "llm_pain_points",
        "llm_selling_points",
        "llm_use_scenarios",
        "llm_differentiation",
        "llm_confidence",
    ]
    result = result.drop(columns=[column for column in columns if column in result], errors="ignore")
    rows = []
    for item in analysis.get("sku_insights", []) or []:
        if not isinstance(item, dict):
            continue
        asin = str(item.get("asin", "")).strip()
        if not asin:
            continue
        rows.append(
            {
                "asin": asin,
                "llm_pain_points": "；".join(map(str, item.get("pain_points", []) or [])),
                "llm_selling_points": "；".join(
                    map(str, item.get("selling_points", []) or [])
                ),
                "llm_use_scenarios": "；".join(
                    map(str, item.get("use_scenarios", []) or [])
                ),
                "llm_differentiation": str(
                    item.get("differentiation_recommendation", "")
                ),
                "llm_confidence": str(item.get("confidence", "")),
            }
        )
    if rows:
        insights = pd.DataFrame(rows).drop_duplicates("asin", keep="first")
        result = result.merge(insights, on="asin", how="left")
    for column in columns:
        if column not in result:
            result[column] = ""
        result[column] = result[column].fillna("")
    return result


def enrich_competitor_intelligence(
    intelligence: pd.DataFrame, settings: LLMSettings, max_items: int = 60
) -> tuple[pd.DataFrame, str]:
    result = intelligence.copy().reset_index(drop=True)
    for column in [
        "competitive_impact",
        "recommended_action",
        "llm_summary",
        "llm_confidence",
    ]:
        if column not in result:
            result[column] = ""
    if result.empty or not settings.enabled:
        return result, "本地规则（未调用外部大模型）"

    target_indices = result.index.tolist()[: max(1, min(max_items, len(result)))]
    target = result.loc[target_indices]
    selected_columns = [
        column
        for column in [
            "discovered_at",
            "region",
            "brand",
            "category",
            "product_name",
            "status",
            "source_title",
            "source_summary",
            "source_url",
        ]
        if column in target
    ]
    records = _safe_records(target[selected_columns], len(target), text_limit=500)
    for record, row_id in zip(records, target_indices):
        record["_row_id"] = int(row_id)

    def analyze_batch(batch: list[dict]) -> list[dict]:
        payload = {
            "items": batch,
            "required_schema": {
                "items": [
                    {
                        "_row_id": 0,
                        "category": "string",
                        "product_name": "string",
                        "model": "string",
                        "status": "Active Sales|Pre-order|Teaser|Leak|待判断",
                        "core_specs": "string",
                        "price_strategy": "string",
                        "competitive_impact": "string",
                        "recommended_action": "string",
                        "summary": "string",
                        "confidence": "高|中|低",
                    }
                ]
            },
        }
        response = _call_json(
            settings,
            [
                {
                    "role": "system",
                    "content": (
                        "你是CLEVA全球竞品情报分析师。仅依据标题、摘要和来源字段提取信息，"
                        "不得猜测不存在的型号、价格或规格。明确分析对Vacmaster或Lawnmaster"
                        "的竞争影响与动作；证据不足则留空。输出严格JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                },
            ],
        )
        return response.get("items", []) or []

    updates: list[dict] = []
    failures: list[str] = []
    for batch_number, batch in enumerate(_chunks(records, 6), start=1):
        try:
            updates.extend(analyze_batch(batch))
        except Exception as exc:
            # A large or rate-limited batch should not discard successful earlier batches.
            recovered = False
            if len(batch) > 1:
                for smaller in _chunks(batch, 3):
                    try:
                        time.sleep(0.4)
                        updates.extend(analyze_batch(smaller))
                        recovered = True
                    except Exception as smaller_exc:
                        failures.append(f"批次{batch_number}: {str(smaller_exc)[:120]}")
            if not recovered:
                failures.append(f"批次{batch_number}: {str(exc)[:120]}")
        time.sleep(0.15)

    mapping = {
        "category": "category",
        "product_name": "product_name",
        "model": "model",
        "status": "status",
        "core_specs": "core_specs",
        "price_strategy": "price_strategy",
        "competitive_impact": "competitive_impact",
        "recommended_action": "recommended_action",
        "summary": "llm_summary",
        "confidence": "llm_confidence",
    }
    for item in updates:
        row_id = item.get("_row_id")
        if not isinstance(row_id, int) or not 0 <= row_id < len(result):
            continue
        for source, target_column in mapping.items():
            value = item.get(source)
            if value not in (None, ""):
                result.at[row_id, target_column] = value
    successful_rows = int(
        result.loc[target_indices, "llm_summary"].fillna("").astype(str).str.strip().ne("").sum()
    )
    if failures:
        return (
            result,
            f"大模型部分完成：{successful_rows}/{len(target_indices)}条；"
            f"{len(failures)}个小批次失败。可减少分析条数后重试。",
        )
    return result, f"大模型：{settings.model}（完成{successful_rows}/{len(target_indices)}条）"


def summarize_competitor_intelligence(
    intelligence: pd.DataFrame, settings: LLMSettings
) -> dict:
    """Create an executive competitor report for Vacmaster and Lawnmaster."""
    data = intelligence.copy()
    brand_counts = (
        data["brand"].replace("", pd.NA).dropna().value_counts().head(8).to_dict()
        if "brand" in data
        else {}
    )
    category_counts = (
        data["category"].replace("", pd.NA).dropna().value_counts().head(8).to_dict()
        if "category" in data
        else {}
    )

    def unique_values(column: str, limit: int = 10) -> list[str]:
        if column not in data:
            return []
        values = data[column].fillna("").astype(str).str.strip()
        return [value for value in values.drop_duplicates() if value][:limit]

    fallback = {
        "executive_summary": (
            f"本期共{len(data)}条候选情报，涉及{len(brand_counts)}个主要品牌。"
            "以下为已提取字段汇总，仍需结合原始来源人工复核。"
        ),
        "core_technology_and_selling_points": unique_values("core_specs"),
        "pricing_and_market_strategy": unique_values("price_strategy"),
        "competitive_impact_on_cleva": unique_values("competitive_impact"),
        "response_plan": unique_values("recommended_action"),
        "priority_watchlist": unique_values("llm_summary", 8),
        "brand_distribution": brand_counts,
        "category_distribution": category_counts,
        "analysis_mode": "本地汇总（未调用外部大模型）",
    }
    if data.empty or not settings.enabled:
        return fallback
    fields = [
        field
        for field in [
            "discovered_at",
            "brand",
            "category",
            "product_name",
            "model",
            "status",
            "core_specs",
            "price_strategy",
            "competitive_impact",
            "recommended_action",
            "llm_summary",
            "source_url",
        ]
        if field in data
    ]
    compressed = _safe_records(data[fields], 60, text_limit=350)
    try:
        summary = _call_json(
            settings,
            [
                {
                    "role": "system",
                    "content": (
                        "你是CLEVA战略情报负责人。根据候选情报形成管理层报告，重点覆盖"
                        "核心技术规格与卖点、定价与市场策略、对Vacmaster和Lawnmaster的"
                        "竞争影响及分优先级应对方案。不得补造输入中没有的事实，只输出JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "items": compressed,
                            "required_schema": {
                                "executive_summary": "string",
                                "core_technology_and_selling_points": ["string"],
                                "pricing_and_market_strategy": ["string"],
                                "competitive_impact_on_cleva": ["string"],
                                "response_plan": ["string"],
                                "priority_watchlist": ["string"],
                            },
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
        )
        summary.setdefault("brand_distribution", brand_counts)
        summary.setdefault("category_distribution", category_counts)
        summary["analysis_mode"] = f"大模型：{settings.model}"
        return summary
    except Exception as exc:
        fallback["analysis_mode"] += f"；大模型汇总失败：{str(exc)[:160]}"
        return fallback


def enrich_sales_diagnostics(
    sales: pd.DataFrame, settings: LLMSettings
) -> tuple[pd.DataFrame, str]:
    result = sales.copy().reset_index(drop=True)
    result["diagnosis_source"] = "规则"
    result["risk_level"] = result.get("risk_level", "")
    result["evidence"] = result.get("evidence", "")
    result["action_priority"] = result.get("action_priority", "")
    if result.empty or not settings.enabled or "needs_attention" not in result:
        return result, "本地规则（未调用外部大模型）"

    abnormal = result[result["needs_attention"].fillna(False)].copy()
    if abnormal.empty:
        return result, f"大模型：{settings.model}（本期无异常SKU，无需调用）"
    records = _safe_records(abnormal, len(abnormal))
    row_ids = abnormal.index.tolist()
    for record, row_id in zip(records, row_ids):
        record["_row_id"] = int(row_id)
    try:
        updates: list[dict] = []
        for batch in _chunks(records, 15):
            response = _call_json(
                settings,
                [
                    {
                        "role": "system",
                        "content": (
                            "你是CLEVA Amazon运营分析师。针对变化超过5%的SKU，"
                            "综合WoW/MoM/QoQ/HoH/YoY、ACoS、CTR、CVR、库存、价格和BSR字段诊断。"
                            "缺失数据必须明确写为证据不足，不得捏造。输出严格JSON。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "items": batch,
                                "required_schema": {
                                    "items": [
                                        {
                                            "_row_id": 0,
                                            "diagnosis": "string",
                                            "evidence": "string",
                                            "risk_level": "高|中|低",
                                            "recommendation": "string",
                                            "action_priority": "P0|P1|P2",
                                        }
                                    ]
                                },
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                ],
            )
            updates.extend(response.get("items", []) or [])
        for item in updates:
            row_id = item.get("_row_id")
            if not isinstance(row_id, int) or not 0 <= row_id < len(result):
                continue
            result.at[row_id, "diagnosis"] = str(item.get("diagnosis", ""))
            result.at[row_id, "recommendation"] = str(item.get("recommendation", ""))
            result.at[row_id, "evidence"] = str(item.get("evidence", ""))
            result.at[row_id, "risk_level"] = str(item.get("risk_level", ""))
            result.at[row_id, "action_priority"] = str(item.get("action_priority", ""))
            result.at[row_id, "diagnosis_source"] = settings.model
        return result, f"大模型：{settings.model}"
    except Exception as exc:
        return result, f"DeepSeek调用失败，已保留规则诊断：{type(exc).__name__}"
