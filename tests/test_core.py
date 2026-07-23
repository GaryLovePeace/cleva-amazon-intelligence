import importlib.util
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd

from core.amazon import normalize_product_table, parse_product_detail_html
from core.competitor import collect_google_news, normalize_intelligence_table
from core.llm import (
    LLMSettings,
    analyze_market,
    analyze_voc_report,
    enrich_competitor_intelligence,
    enrich_sales_diagnostics,
    merge_sku_insights,
    save_settings,
    settings_from_env,
    summarize_competitor_intelligence,
)
from core.seller_reports import build_sales_analysis
from core.storage import SnapshotStore
from core.voc import analyze_reviews, normalize_reviews


class CoreTests(unittest.TestCase):
    def test_product_normalization(self):
        source = pd.DataFrame(
            [{"BSR": "#2", "ASIN": "B012345678", "品牌": "Vacmaster", "价格": "$99.99"}]
        )
        result = normalize_product_table(source)
        self.assertEqual(result.loc[0, "rank"], 2)
        self.assertEqual(result.loc[0, "price"], 99.99)
        self.assertEqual(result.loc[0, "brand"], "Vacmaster")

    @unittest.skipUnless(importlib.util.find_spec("bs4"), "beautifulsoup4 not installed")
    def test_product_detail_parser(self):
        html = """
        <html><body>
          <span id="productTitle">Vacmaster 5 Gallon Wet Dry Vacuum</span>
          <a id="bylineInfo">Visit the Vacmaster Store</a>
          <div id="corePrice_feature_div"><span class="a-price">
            <span class="a-offscreen">$79.99</span>
          </span></div>
          <div id="feature-bullets"><ul>
            <li><span class="a-list-item">5 gallon tank and 5.5 peak HP motor</span></li>
            <li><span class="a-list-item">Includes floor nozzle and crevice tool</span></li>
          </ul></div>
          <table id="productDetails_techSpec_section_1">
            <tr><th>Item model number</th><td>VOC507PF</td></tr>
            <tr><th>Item Weight</th><td>15 pounds</td></tr>
            <tr><th>Hose Length</th><td>7 Feet</td></tr>
            <tr><th>Warranty</th><td>2 years</td></tr>
          </table>
        </body></html>
        """
        detail = parse_product_detail_html(
            html,
            asin="B012345678",
            product_url="https://www.amazon.com/dp/B012345678",
        )
        self.assertEqual(detail["brand"], "Vacmaster")
        self.assertEqual(detail["model"], "VOC507PF")
        self.assertEqual(detail["price"], 79.99)
        self.assertIn("5 gallon", detail["capacity"].lower())
        self.assertIn("5.5 peak hp", detail["horsepower"].lower())
        self.assertEqual(detail["detail_status"], "成功")

    def test_local_commercial_analysis(self):
        products = normalize_product_table(
            pd.DataFrame(
                [
                    {
                        "rank": 1,
                        "asin": "B000000001",
                        "brand": "Vacmaster",
                        "price": 79.99,
                        "capacity": "5 gallon",
                        "horsepower": "5.5 peak HP",
                        "detail_status": "成功",
                    },
                    {
                        "rank": 2,
                        "asin": "B000000002",
                        "brand": "Craftsman",
                        "price": 99.99,
                        "capacity": "6 gallon",
                        "horsepower": "5 peak HP",
                        "detail_status": "成功",
                    },
                ]
            )
        )
        analysis = analyze_market(
            products,
            pd.DataFrame(),
            [],
            "wet_dry",
            LLMSettings("", "https://api.deepseek.com", "model"),
        )
        self.assertTrue(analysis["price_band_analysis"])
        self.assertTrue(analysis["brand_benchmark"])
        self.assertIn("价格定位", analysis["vacmaster_positioning"])

    @patch("core.llm._call_json")
    def test_voc_is_independent_from_commercial_analysis(self, mocked_call):
        mocked_call.return_value = {
            "voc_summary": "评论显示软管耐用性问题",
            "pain_points": [{"pain_point": "软管破裂", "mentions": 2, "mention_rate": 50}],
            "selling_points": [{"selling_point": "吸力强", "mentions": 2, "mention_rate": 50}],
            "sku_insights": [],
            "recommendations": ["加强软管"],
        }
        products = pd.DataFrame([{"asin": "B012345678", "brand": "Vacmaster"}])
        reviews = pd.DataFrame(
            [{"asin": "B012345678", "rating": 1, "review_text": "hose cracked"}]
        )
        result = analyze_voc_report(
            products,
            reviews,
            [],
            "wet_dry",
            LLMSettings("key", "https://api.deepseek.com", "model"),
        )
        self.assertEqual(result["voc_summary"], "评论显示软管耐用性问题")
        self.assertNotIn("price_band_analysis", result)

    def test_voc(self):
        reviews = normalize_reviews(
            pd.DataFrame(
                [
                    {"ASIN": "B012345678", "星级": 1, "评论内容": "The hose cracked and suction is weak"},
                    {"ASIN": "B012345678", "星级": 5, "评论内容": "Great machine"},
                ]
            )
        )
        result = analyze_reviews(reviews, "wet_dry")
        labels = {row["pain_point"] for row in result}
        self.assertIn("软管折弯/破裂", labels)
        self.assertIn("过滤堵塞/吸力衰减", labels)

    def test_snapshot_store(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SnapshotStore(folder)
            store.save_snapshot("wet_dry", "test", pd.DataFrame([{"asin": "B1", "rank": 1}]))
            self.assertEqual(store.summary()["total_snapshots"], 1)
            self.assertEqual(store.previous_snapshot("wet_dry").loc[0, "asin"], "B1")
            current = pd.DataFrame([{"asin": "B1", "rank": 2}])
            store.save_snapshot("wet_dry", "current", current)
            previous = store.previous_snapshot("wet_dry", current=current)
            self.assertEqual(previous.loc[0, "rank"], 1)

    def test_sales_analysis(self):
        dates = pd.date_range("2026-07-10", periods=14, freq="D")
        rows = []
        for idx, day in enumerate(dates):
            rows.append(
                {
                    "date": day,
                    "asin": "B012345678",
                    "sku": "VM-1",
                    "brand": "Vacmaster",
                    "title": "Vacmaster Wet Dry Vacuum",
                    "units": 2 if idx >= 7 else 1,
                    "revenue": 200 if idx >= 7 else 100,
                }
            )
        result = build_sales_analysis(
            pd.DataFrame(rows),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.Timestamp("2026-07-23"),
        )
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result.loc[0, "wow_revenue"], 1.0)
        self.assertTrue(result.loc[0, "needs_attention"])

    def test_saved_deepseek_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = LLMSettings("sk-test", "https://api.deepseek.com/v1", "deepseek-chat")
            save_settings(folder, saved)
            with patch.dict(
                "os.environ",
                {"AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""},
                clear=False,
            ):
                loaded = settings_from_env(folder)
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.api_key, "sk-test")
            self.assertEqual(loaded.model, "deepseek-chat")

    def test_streamlit_secret_environment_names(self):
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "secret",
                "OPENAI_API_BASE": "https://api.deepseek.com",
                "MODEL_ID": "openai/deepseek-v4-flash",
            },
            clear=False,
        ):
            loaded = settings_from_env()
        self.assertEqual(loaded.api_key, "secret")
        self.assertEqual(loaded.base_url, "https://api.deepseek.com")
        self.assertEqual(loaded.model, "deepseek-v4-flash")

    def test_merge_sku_insights(self):
        products = pd.DataFrame([{"asin": "B012345678", "title": "Vacuum"}])
        result = merge_sku_insights(
            products,
            {
                "sku_insights": [
                    {
                        "asin": "B012345678",
                        "pain_points": ["软管破裂"],
                        "selling_points": ["吸力强"],
                        "use_scenarios": ["车库"],
                        "differentiation_recommendation": "加强软管",
                        "confidence": "high",
                    }
                ]
            },
        )
        self.assertEqual(result.loc[0, "llm_selling_points"], "吸力强")
        self.assertEqual(result.loc[0, "llm_differentiation"], "加强软管")
        repeated = merge_sku_insights(
            result,
            {
                "sku_insights": [
                    {
                        "asin": "B012345678",
                        "selling_points": ["更新后的卖点"],
                    }
                ]
            },
        )
        self.assertEqual(repeated.loc[0, "llm_selling_points"], "更新后的卖点")
        self.assertFalse(any(column.endswith("_x") for column in repeated.columns))

    @patch("core.llm._call_json")
    def test_competitor_deepseek_enrichment(self, mocked_call):
        mocked_call.return_value = {
            "items": [
                {
                    "_row_id": 0,
                    "model": "X1",
                    "core_specs": "18kPa",
                    "price_strategy": "中端",
                    "competitive_impact": "中",
                    "recommended_action": "对比测试",
                    "summary": "新品",
                    "confidence": "中",
                }
            ]
        }
        source = pd.DataFrame(
            [{"brand": "Bissell", "source_title": "Bissell launches X1", "source_url": "u"}]
        )
        result, mode = enrich_competitor_intelligence(
            source, LLMSettings("key", "https://api.deepseek.com", "model")
        )
        self.assertEqual(result.loc[0, "model"], "X1")
        self.assertEqual(result.loc[0, "recommended_action"], "对比测试")
        self.assertIn("大模型", mode)

    @patch("core.llm._call_json")
    def test_competitor_summary(self, mocked_call):
        mocked_call.return_value = {
            "executive_summary": "竞品强化无绳产品",
            "core_technology_and_selling_points": ["无绳平台"],
            "pricing_and_market_strategy": ["高端定价"],
            "competitive_impact_on_cleva": ["挤压Lawnmaster"],
            "response_plan": ["补充无绳SKU"],
            "priority_watchlist": ["DEWALT新品"],
        }
        source = pd.DataFrame(
            [
                {
                    "brand": "DEWALT",
                    "category": "Lawn & Garden",
                    "product_name": "60V Mower",
                    "source_url": "u",
                }
            ]
        )
        result = summarize_competitor_intelligence(
            source, LLMSettings("key", "https://api.deepseek.com", "model")
        )
        self.assertEqual(result["response_plan"], ["补充无绳SKU"])
        self.assertIn("大模型", result["analysis_mode"])

    def test_competitor_dates_are_split_sorted_and_auditable(self):
        source = pd.DataFrame(
            [
                {
                    "published_at": "2026-07-01",
                    "collected_at": "2026-07-23T10:00:00+00:00",
                    "source_url": "old",
                    "brand": "Bissell",
                },
                {
                    "published_at": "2026-07-20",
                    "collected_at": "2026-07-23T10:00:00+00:00",
                    "source_url": "new",
                    "brand": "DEWALT",
                },
                {
                    "published_at": "2099-01-01",
                    "collected_at": "2026-07-23T10:00:00+00:00",
                    "source_url": "future",
                    "brand": "Ryobi",
                },
            ]
        )
        result = normalize_intelligence_table(source)
        self.assertEqual(result.loc[0, "source_url"], "new")
        self.assertEqual(result.loc[1, "source_url"], "old")
        future = result.loc[result["source_url"] == "future"].iloc[0]
        self.assertEqual(future["published_at"], "")
        self.assertEqual(future["date_status"], "未来日期异常")
        self.assertEqual(future["source_published_at_raw"], "2099-01-01")

    @patch("core.competitor.urlopen")
    def test_competitor_collection_filters_old_news(self, mocked_urlopen):
        now = datetime.now(timezone.utc)

        def rss_date(value):
            return value.strftime("%a, %d %b %Y %H:%M:%S GMT")

        rss = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel>
          <item><title>Brand launches recent vacuum</title><link>recent</link>
            <description>Recent product</description>
            <pubDate>{rss_date(now - timedelta(days=5))}</pubDate></item>
          <item><title>Brand launches old vacuum</title><link>old</link>
            <description>Old product</description>
            <pubDate>{rss_date(now - timedelta(days=60))}</pubDate></item>
          <item><title>Brand launches unknown vacuum</title><link>missing</link>
            <description>Unknown date</description></item>
          <item><title>Brand launches future vacuum</title><link>future</link>
            <description>Bad date</description>
            <pubDate>{rss_date(now + timedelta(days=3))}</pubDate></item>
        </channel></rss>""".encode()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return rss

        mocked_urlopen.return_value = FakeResponse()
        result = collect_google_news(["Brand"], max_per_brand=5, lookback_days=30)
        self.assertEqual(set(result["source_url"]), {"recent", "missing", "future"})
        self.assertNotIn("old", set(result["source_url"]))
        self.assertEqual(
            result.loc[result["source_url"] == "missing", "date_status"].iloc[0],
            "日期缺失",
        )
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("when%3A30d", request.full_url)

    def test_legacy_competitor_date_remains_compatible(self):
        source = pd.DataFrame(
            [
                {
                    "discovered_at": "2026-06-01",
                    "source_url": "legacy",
                    "brand": "Hoover",
                }
            ]
        )
        result = normalize_intelligence_table(source)
        self.assertEqual(result.loc[0, "published_at"], "2026-06-01")
        self.assertEqual(result.loc[0, "date_status"], "历史字段（待复核）")
        self.assertTrue(result.loc[0, "collected_at"])

    @patch("core.llm._call_json")
    def test_competitor_deepseek_prioritizes_latest_items(self, mocked_call):
        mocked_call.return_value = {"items": []}
        source = pd.DataFrame(
            [
                {
                    "published_at": "2026-01-01",
                    "brand": "Old",
                    "source_title": "Old launch",
                    "source_url": "old",
                },
                {
                    "published_at": "2026-07-20",
                    "brand": "New",
                    "source_title": "New launch",
                    "source_url": "new",
                },
            ]
        )
        result, _ = enrich_competitor_intelligence(
            source,
            LLMSettings("key", "https://api.deepseek.com", "model"),
            max_items=1,
        )
        self.assertEqual(result.loc[0, "brand"], "New")
        user_payload = mocked_call.call_args.args[1][1]["content"]
        self.assertIn('"brand": "New"', user_payload)
        self.assertNotIn('"brand": "Old"', user_payload)

    @patch("core.llm._call_json")
    def test_sales_deepseek_diagnostics(self, mocked_call):
        mocked_call.return_value = {
            "items": [
                {
                    "_row_id": 0,
                    "diagnosis": "库存不足",
                    "evidence": "库存为0",
                    "risk_level": "高",
                    "recommendation": "立即补货",
                    "action_priority": "P0",
                }
            ]
        }
        source = pd.DataFrame(
            [
                {
                    "asin": "B012345678",
                    "needs_attention": True,
                    "diagnosis": "规则诊断",
                    "recommendation": "规则建议",
                }
            ]
        )
        result, mode = enrich_sales_diagnostics(
            source, LLMSettings("key", "https://api.deepseek.com", "model")
        )
        self.assertEqual(result.loc[0, "diagnosis"], "库存不足")
        self.assertEqual(result.loc[0, "action_priority"], "P0")
        self.assertIn("大模型", mode)


if __name__ == "__main__":
    unittest.main()
