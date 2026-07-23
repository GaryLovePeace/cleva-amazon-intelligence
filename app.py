from __future__ import annotations

import os
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from core.amazon import (
    enrich_product_details_browser,
    fetch_bestseller_products,
    normalize_product_table,
)
from core.competitor import DEFAULT_BRANDS, collect_google_news, normalize_intelligence_table
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
    test_connection,
)
from core.reports import (
    build_bsr_html,
    build_bsr_workbook,
    build_competitor_html,
    build_competitor_workbook,
    build_sales_workbook,
    dataframe_to_csv_bytes,
    template_workbook,
)
from core.seller_reports import build_sales_analysis
from core.storage import SnapshotStore
from core.voc import analyze_reviews, normalize_reviews


APP_TITLE = "CLEVA Amazon 市场情报与销售分析"
WET_DRY_URL = "https://www.amazon.com/gp/bestsellers/hi/553022/ref=pd_zg_hrsr_hi"
SPOT_URL = "https://www.amazon.com/gp/bestsellers/home-garden/1063922/ref=pd_zg_hrsr_home-garden"
VACMASTER_STORE = "https://www.amazon.com/stores/Vacmaster/page/DFC52640-DE31-4B55-94C5-31A9A71D9BEF"
KENMORE_STORE = "https://www.amazon.com/stores/page/E4D32E8D-555F-4F56-B6FB-B14824EC578C"

PRODUCT_TEMPLATE_COLUMNS = [
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
REVIEW_TEMPLATE_COLUMNS = ["asin", "rating", "review_title", "review_text", "review_date"]
INTEL_TEMPLATE_COLUMNS = [
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
SALES_TEMPLATE_COLUMNS = [
    "date",
    "asin",
    "sku",
    "brand",
    "store_category",
    "title",
    "units",
    "revenue",
    "sessions",
    "page_views",
    "buy_box_percentage",
    "inventory",
    "bsr",
    "bsr_change",
]
ADS_TEMPLATE_COLUMNS = ["date", "asin", "sku", "impressions", "clicks", "spend", "ad_sales", "orders"]


def load_local_env(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def init_page() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide")
    st.markdown(
        """
        <style>
        .stApp {background: #f6f8fb;}
        [data-testid="stSidebar"] {background: #eef1f6;}
        [data-testid="stSidebar"] .stRadio > label {display:none;}
        [data-testid="stMetric"] {
          background:white; border:1px solid #e4e9f0; border-radius:14px;
          padding:14px 16px; box-shadow:0 2px 8px rgba(25,42,70,.04);
        }
        .hero {
          padding:24px 26px; border-radius:18px; color:white;
          background:linear-gradient(125deg,#112a46,#1f5e82 62%,#40a6a0);
          margin-bottom:18px;
        }
        .hero h1 {font-size:30px; margin:0 0 8px 0;}
        .hero p {margin:0; opacity:.88;}
        .module-card {
          min-height:155px; padding:18px; border:1px solid #e4e9f0;
          border-radius:15px; background:white;
        }
        .module-card h3 {font-size:18px; margin:0 0 8px;}
        .muted {color:#667085; font-size:14px;}
        .ok-pill {color:#116149;background:#e9f8f1;padding:4px 10px;border-radius:99px;}
        .warn-pill {color:#8a4b08;background:#fff2df;padding:4px 10px;border-radius:99px;}
        div[data-testid="stDataFrame"] {background:white; border-radius:12px;}
        .stButton > button, .stDownloadButton > button {border-radius:10px;}
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource
def get_store() -> SnapshotStore:
    data_dir = os.getenv("LOCAL_DATA_DIR", "data")
    return SnapshotStore(data_dir)


def read_uploaded_table(uploaded) -> pd.DataFrame:
    if uploaded is None:
        return pd.DataFrame()
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded)
    raise ValueError("仅支持 CSV、XLSX 或 XLS 文件。")


def hero(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def render_overview(store: SnapshotStore) -> None:
    hero(APP_TITLE, "一个入口完成 Amazon 榜单监控、VOC、竞品新品情报与店铺销售分析")
    summary = store.summary()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("已保存快照", summary["total_snapshots"])
    c2.metric("榜单产品记录", summary["bsr_rows"])
    c3.metric("竞品情报记录", summary["intel_rows"])
    c4.metric("销售分析记录", summary["sales_rows"])

    st.subheader("四个业务模块")
    cols = st.columns(4)
    cards = [
        ("01", "干湿两用吸尘器", "Top 50、排名变化、评论VOC和差异化建议"),
        ("02", "Spot Cleaner", "便携织物清洗机筛选、专属参数和VOC"),
        ("03", "全球竞品新品", "官网、新闻、零售商、法规专利和社媒线索"),
        ("04", "店铺销售分析", "Vacmaster全量及Kenmore Floor Care多周期对比"),
    ]
    for col, (number, title, text) in zip(cols, cards):
        with col:
            st.markdown(
                f'<div class="module-card"><span class="muted">需求 {number}</span>'
                f"<h3>{title}</h3><p>{text}</p></div>",
                unsafe_allow_html=True,
            )

    st.info(
        "推荐流程：需求一、二使用直接采集或上传兜底；需求三自动搜集公开信息；"
        "需求四上传 Seller Central 报表。所有结果都可编辑、保存并导出 Excel。"
    )
    recent = store.list_snapshots(limit=8)
    if not recent.empty:
        st.subheader("最近运行")
        st.dataframe(
            recent[["collected_at", "module", "label", "row_count"]],
            width="stretch",
            hide_index=True,
        )


def product_source_panel(category_key: str, url: str) -> pd.DataFrame:
    state_key = f"{category_key}_products"
    st.markdown("#### 1. 获取 Top 50")
    mode = st.radio(
        "数据方式",
        ["直接采集 Amazon", "上传 CSV / Excel"],
        horizontal=True,
        key=f"{category_key}_mode",
    )
    if mode == "直接采集 Amazon":
        st.text_input("榜单链接", value=url, disabled=True, key=f"{category_key}_url")
        collect_method = st.radio(
            "采集引擎",
            ["Chromium 自动滚动（推荐，可加载50条）", "普通网页请求（较快，可能只有30条）"],
            horizontal=True,
            key=f"{category_key}_collect_method",
        )
        st.caption(
            "Chromium 会像人工浏览一样持续向下滚动、等待懒加载并按 ASIN 去重。"
            "如果 Amazon 返回验证码，请改用上传方式；系统不会生成虚假榜单。"
        )
        if st.button("开始采集", type="primary", key=f"{category_key}_fetch"):
            with st.spinner("正在打开浏览器、自动滚动并收集 Top 50……"):
                try:
                    result = fetch_bestseller_products(
                        url,
                        limit=50,
                        use_browser=collect_method.startswith("Chromium"),
                    )
                    st.session_state[state_key] = result.data
                    if result.warnings:
                        for warning in result.warnings:
                            st.warning(warning)
                    st.success(f"采集完成：{len(result.data)} 条产品记录。")
                except Exception as exc:
                    st.error(f"本次采集未成功：{exc}")
    else:
        uploaded = st.file_uploader(
            "上传产品榜单",
            type=["csv", "xlsx", "xls"],
            key=f"{category_key}_product_upload",
        )
        if uploaded is not None:
            try:
                st.session_state[state_key] = normalize_product_table(read_uploaded_table(uploaded))
            except Exception as exc:
                st.error(f"文件读取失败：{exc}")

    products = st.session_state.get(state_key, pd.DataFrame(columns=PRODUCT_TEMPLATE_COLUMNS))
    if not products.empty:
        products = normalize_product_table(products)
        st.markdown("#### 2. 补全产品详情与商业参数")
        detail_success = int(products["detail_status"].isin(["成功", "部分成功"]).sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Top榜单产品", len(products))
        c2.metric("已补全详情", detail_success)
        c3.metric("待补全", max(len(products) - detail_success, 0))
        detail_limit = st.slider(
            "本次补全多少个产品详情页",
            min_value=1,
            max_value=len(products),
            value=min(10, len(products)),
            key=f"{category_key}_detail_limit",
            help="建议先测试10个；完整补全Top 50耗时更长，也更容易触发Amazon验证码。",
        )
        st.caption(
            "详情采集会逐一访问ASIN页面，提取型号、容量、马力/吸力、功率、尺寸、"
            "软管、电源线、过滤、配件、保修和Spot Cleaner水箱/蒸汽等字段。"
        )
        if st.button(
            "采集并补全产品详情",
            type="primary",
            key=f"{category_key}_detail_fetch",
        ):
            progress = st.progress(0, text="准备打开产品详情页……")

            def update_detail_progress(current: int, total: int, label: str) -> None:
                progress.progress(
                    min(current / max(total, 1), 1.0),
                    text=f"正在补全 {current}/{total}：{label}",
                )

            try:
                result = enrich_product_details_browser(
                    products,
                    max_products=detail_limit,
                    on_progress=update_detail_progress,
                )
                products = result.data
                st.session_state[state_key] = products
                progress.progress(1.0, text="详情补全完成")
                for warning in result.warnings:
                    st.warning(warning)
                completed = int(products["detail_status"].isin(["成功", "部分成功"]).sum())
                st.success(f"当前已补全 {completed}/{len(products)} 个产品详情。")
                st.rerun()
            except Exception as exc:
                progress.empty()
                st.error(f"详情补全未完成：{exc}")
        edited = st.data_editor(
            products,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "product_url": st.column_config.LinkColumn("Amazon链接"),
                "image_url": st.column_config.ImageColumn("产品主图"),
                "price": st.column_config.NumberColumn("当前价格", format="$%.2f"),
                "list_price": st.column_config.NumberColumn("原价", format="$%.2f"),
                "discount_percent": st.column_config.NumberColumn("折扣率", format="%.1f%%"),
            },
            key=f"{category_key}_editor",
        )
        st.session_state[state_key] = edited
        return edited
    st.caption("尚无产品数据。可先下载数据中心里的模板填写。")
    return products


def render_bsr_module(
    store: SnapshotStore,
    *,
    category_key: str,
    title: str,
    subtitle: str,
    url: str,
) -> None:
    hero(title, subtitle)
    products = product_source_panel(category_key, url)
    data_dir = os.getenv("LOCAL_DATA_DIR", "data")
    config = settings_from_env(data_dir)

    st.markdown("#### 3. 产品与商业分析报告")
    if config.enabled:
        st.success(f"DeepSeek 已连接：{config.model}。商业分析与VOC将分别调用。")
    else:
        st.warning("DeepSeek尚未配置，将生成本地价格带和品牌基准分析。")
    st.caption(
        "该报告只使用Top榜单和已补全的产品参数，不需要评论文件。详情补全越完整，"
        "Vacmaster定价和能力差异结论越可靠。"
    )
    commercial_key = f"{category_key}_commercial_analysis"
    if st.button(
        "生成产品商业分析报告",
        type="primary",
        key=f"{category_key}_commercial_run",
    ):
        if products.empty:
            st.warning("请先获取Top榜单并尽量补全产品详情。")
        else:
            with st.spinner("正在分析价格带、品牌基准、能力差异和Vacmaster机会……"):
                st.session_state[commercial_key] = analyze_market(
                    products,
                    pd.DataFrame(),
                    [],
                    category_key,
                    config,
                )

    commercial = st.session_state.get(commercial_key)
    if commercial:
        c1, c2, c3 = st.columns(3)
        c1.metric("产品数", len(products))
        c2.metric("品牌数", products["brand"].replace("", pd.NA).nunique())
        c3.metric(
            "详情已补全",
            int(products["detail_status"].isin(["成功", "部分成功"]).sum()),
        )
        st.markdown("##### 市场格局")
        st.write(commercial.get("market_landscape", "暂无结论"))
        if commercial.get("price_band_analysis"):
            st.markdown("##### 价格带分析")
            st.dataframe(
                pd.DataFrame(commercial["price_band_analysis"]),
                width="stretch",
                hide_index=True,
            )
        if commercial.get("brand_benchmark"):
            st.markdown("##### 品牌价格与能力基准")
            st.dataframe(
                pd.DataFrame(commercial["brand_benchmark"]),
                width="stretch",
                hide_index=True,
            )
        positioning = commercial.get("vacmaster_positioning")
        if positioning:
            st.markdown("##### Vacmaster定价与能力定位")
            if isinstance(positioning, dict):
                st.dataframe(
                    pd.DataFrame(
                        [{"分析项": key, "结论": value} for key, value in positioning.items()]
                    ),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.write(positioning)
        if commercial.get("capability_comparison"):
            st.markdown("##### Vacmaster与竞品能力差异")
            st.dataframe(
                pd.DataFrame(commercial["capability_comparison"]),
                width="stretch",
                hide_index=True,
            )
        if commercial.get("brand_comparison"):
            st.markdown("##### 品牌与技术路线")
            st.write(commercial["brand_comparison"])
        st.markdown("##### 产品机会与应对建议")
        for item in commercial.get("opportunity_gaps", []):
            st.markdown(f"- {item}")
        for item in commercial.get("recommendations", []):
            st.markdown(f"- {item}")
        st.caption(f"分析方式：{commercial.get('analysis_mode', '本地规则')}")

        previous = store.previous_snapshot(category_key, current=products)
        d1, d2 = st.columns(2)
        d1.download_button(
            "下载商业分析 Excel",
            build_bsr_workbook(
                products,
                pd.DataFrame(),
                commercial,
                previous,
                title,
            ),
            f"{category_key}_commercial_{date.today():%Y%m%d}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{category_key}_commercial_excel",
        )
        d2.download_button(
            "下载商业分析 HTML",
            build_bsr_html(products, commercial, title),
            f"{category_key}_commercial_{date.today():%Y%m%d}.html",
            "text/html",
            key=f"{category_key}_commercial_html",
        )

    st.markdown("#### 4. 评论与 VOC（独立分析）")
    reviews_file = st.file_uploader(
        "上传评论文件（可选）",
        type=["csv", "xlsx", "xls"],
        key=f"{category_key}_reviews_upload",
        help="建议字段：ASIN、星级、评论标题、评论正文、评论日期。",
    )
    st.caption(
        "VOC只分析上传的评论，不影响上面的产品商业报告。Amazon官方接口通常不能提供"
        "所有竞品的完整评论正文，因此没有评论文件时不会调用DeepSeek生成VOC。"
    )
    reviews_key = f"{category_key}_reviews"
    if reviews_file is not None:
        try:
            st.session_state[reviews_key] = normalize_reviews(read_uploaded_table(reviews_file))
        except Exception as exc:
            st.error(f"评论文件读取失败：{exc}")
    reviews = st.session_state.get(reviews_key, pd.DataFrame(columns=REVIEW_TEMPLATE_COLUMNS))
    if not reviews.empty:
        st.dataframe(reviews.head(200), width="stretch", hide_index=True)
    voc_key = f"{category_key}_voc_analysis"
    if st.button(
        "生成 VOC 报告",
        key=f"{category_key}_voc_run",
        disabled=reviews.empty,
    ):
        if not products.empty:
            local_voc = analyze_reviews(reviews, category_key)
            with st.spinner("正在分析低星痛点、高星卖点、使用场景和ASIN建议……"):
                voc = analyze_voc_report(products, reviews, local_voc, category_key, config)
            products = merge_sku_insights(products, voc)
            st.session_state[f"{category_key}_products"] = products
            st.session_state[voc_key] = voc

    voc = st.session_state.get(voc_key)
    if voc:
        st.write(voc.get("voc_summary", ""))
        st.markdown("##### VOC 痛点")
        pain_points = voc.get("pain_points", [])
        if pain_points:
            st.dataframe(pd.DataFrame(pain_points), width="stretch", hide_index=True)
        else:
            st.caption("暂无足够评论生成痛点占比。")
        st.markdown("##### 好评核心卖点")
        selling_points = voc.get("selling_points", [])
        if selling_points:
            st.dataframe(pd.DataFrame(selling_points), width="stretch", hide_index=True)
        else:
            st.caption("暂无足够4-5星评论生成卖点。")
        st.markdown("##### VOC改进建议")
        for item in voc.get("recommendations", []):
            st.markdown(f"- {item}")
        st.caption(f"分析方式：{voc.get('analysis_mode', '本地规则')}")

    st.markdown("#### 5. 保存快照")
    if st.button("保存本次快照", key=f"{category_key}_save"):
        if products.empty:
            st.warning("没有可保存的数据。")
        else:
            store.save_snapshot(
                module=category_key,
                label=f"{title} {datetime.now():%Y-%m-%d}",
                data=products,
                source_url=url,
            )
            st.success("快照已保存。下次运行后可计算排名变化。")


def render_competitor_module(store: SnapshotStore) -> None:
    hero("需求三｜全球竞品新品情报", "每周搜集新品、预告、上市、认证、专利与社媒线索")
    st.markdown("#### 1. 选择监控品牌")
    brands = st.multiselect("品牌", DEFAULT_BRANDS, default=DEFAULT_BRANDS)
    max_per_brand = st.slider("每个品牌最多获取", 1, 10, 3)
    state_key = "competitor_intel"
    col1, col2 = st.columns([1, 1])
    if col1.button("搜集公开新品信息", type="primary"):
        with st.spinner("正在查询公开信息并去重……"):
            try:
                result = collect_google_news(brands, max_per_brand=max_per_brand)
                st.session_state[state_key] = result
                st.session_state.pop("competitor_summary", None)
                st.session_state.pop("competitor_analysis_mode", None)
                st.success(f"获得 {len(result)} 条候选情报。")
            except Exception as exc:
                st.error(f"本次搜集失败：{exc}")
    uploaded = col2.file_uploader("或上传补充情报", type=["csv", "xlsx", "xls"])
    if uploaded is not None:
        incoming = normalize_intelligence_table(read_uploaded_table(uploaded))
        current = st.session_state.get(state_key, pd.DataFrame())
        st.session_state[state_key] = normalize_intelligence_table(
            pd.concat([current, incoming], ignore_index=True)
        )
        st.session_state.pop("competitor_summary", None)

    intel = st.session_state.get(state_key, pd.DataFrame(columns=INTEL_TEMPLATE_COLUMNS))
    if not intel.empty:
        intel = normalize_intelligence_table(intel)
        intel = st.data_editor(
            intel,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "source_url": st.column_config.LinkColumn("来源链接"),
                "confidence": st.column_config.SelectboxColumn(
                    "可信度", options=["高", "中", "低"]
                ),
                "status": st.column_config.SelectboxColumn(
                    "状态", options=["Active Sales", "Pre-order", "Teaser", "Leak", "待判断"]
                ),
            },
        )
        st.session_state[state_key] = intel
        config = settings_from_env(os.getenv("LOCAL_DATA_DIR", "data"))
        analysis_limit = st.slider(
            "本次使用DeepSeek分析多少条候选情报",
            min_value=1,
            max_value=min(80, len(intel)),
            value=min(30, len(intel)),
            help="候选信息较多时建议先分析最近30条，避免一次产生过多请求或触发接口限流。",
        )
        if config.enabled:
            if st.button("DeepSeek分析并生成竞品报告", type="primary"):
                with st.spinner("正在分批提取规格与策略，并形成CLEVA竞争影响报告……"):
                    intel, mode = enrich_competitor_intelligence(
                        intel, config, max_items=analysis_limit
                    )
                    summary = summarize_competitor_intelligence(intel, config)
                st.session_state[state_key] = intel
                st.session_state["competitor_analysis_mode"] = mode
                st.session_state["competitor_summary"] = summary
                st.success("竞品明细和管理层摘要已生成。")
        else:
            st.warning("未配置 DeepSeek，当前只有关键词分类；规格、策略和竞争影响需人工填写。")
        st.caption(
            f"分析方式：{st.session_state.get('competitor_analysis_mode', '尚未调用DeepSeek')}"
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("候选信息", len(intel))
        c2.metric("涉及品牌", intel["brand"].nunique())
        c3.metric("高可信信息", int((intel["confidence"] == "高").sum()))
    else:
        st.caption("尚无竞品情报。自动搜索失败时可上传人工收集的表格。")

    summary = st.session_state.get("competitor_summary")
    if summary:
        st.markdown("#### 2. 竞品情报分析报告")
        st.markdown("##### 管理层摘要")
        st.write(summary.get("executive_summary", "暂无"))
        sections = [
            ("核心技术规格与卖点", "core_technology_and_selling_points"),
            ("定价与市场策略", "pricing_and_market_strategy"),
            ("对CLEVA（Vacmaster/Lawnmaster）的竞争影响", "competitive_impact_on_cleva"),
            ("应对方案", "response_plan"),
            ("重点关注清单", "priority_watchlist"),
        ]
        for heading, key in sections:
            st.markdown(f"##### {heading}")
            values = summary.get(key, [])
            if not isinstance(values, list):
                values = [values] if values else []
            if values:
                for value in values:
                    st.markdown(f"- {value}")
            else:
                st.caption("当前信息不足，需补充来源或人工判断。")
        st.caption(f"汇总方式：{summary.get('analysis_mode', '本地汇总')}")

    c1, c2, c3 = st.columns(3)
    if c1.button("保存本周情报"):
        if intel.empty:
            st.warning("没有可保存的数据。")
        else:
            store.save_snapshot("competitor", f"竞品周报 {date.today():%Y-%m-%d}", intel)
            st.success("竞品情报已保存。")
    if not intel.empty:
        c2.download_button(
            "下载竞品情报 Excel",
            build_competitor_workbook(intel, summary),
            f"competitor_intelligence_{date.today():%Y%m%d}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        if summary:
            c3.download_button(
                "下载竞品情报 HTML",
                build_competitor_html(intel, summary),
                f"competitor_intelligence_{date.today():%Y%m%d}.html",
                "text/html",
            )


def render_sales_module(store: SnapshotStore) -> None:
    hero("需求四｜官方店铺 SKU 销售分析", "Vacmaster 全量产品 + Kenmore Floor Care")
    st.warning(
        "公开店铺链接不能提供真实销量。请上传 Seller Central 报表；系统负责合并、对比、诊断与导出。"
    )
    with st.expander("已确认的店铺范围", expanded=False):
        st.markdown(f"- [Vacmaster 全部产品]({VACMASTER_STORE})")
        st.markdown(f"- [Kenmore 仅 Floor Care]({KENMORE_STORE})")
        st.caption("Kenmore 的目标 SKU 最好由公司提供白名单，以避免纳入非 CLEVA 产品。")

    st.markdown("#### 1. 上传报表")
    c1, c2, c3 = st.columns(3)
    business_file = c1.file_uploader(
        "销售与流量报表（必需）", type=["csv", "xlsx", "xls"], key="sales_business"
    )
    listings_file = c2.file_uploader(
        "Listings / 库存报表（可选）", type=["csv", "xlsx", "xls"], key="sales_listings"
    )
    ads_file = c3.file_uploader(
        "广告报表（可选）", type=["csv", "xlsx", "xls"], key="sales_ads"
    )
    as_of = st.date_input("本次报告截止日期", value=date.today())
    state_key = "sales_analysis"
    if st.button("生成多周期分析", type="primary"):
        if business_file is None:
            st.warning("请先上传销售与流量报表。")
        else:
            try:
                business = read_uploaded_table(business_file)
                listings = read_uploaded_table(listings_file) if listings_file else pd.DataFrame()
                ads = read_uploaded_table(ads_file) if ads_file else pd.DataFrame()
                history = store.all_rows("sales")
                result = build_sales_analysis(
                    business=business,
                    listings=listings,
                    ads=ads,
                    as_of=pd.Timestamp(as_of),
                    saved_history=history,
                )
                config = settings_from_env(os.getenv("LOCAL_DATA_DIR", "data"))
                if config.enabled:
                    result, diagnosis_mode = enrich_sales_diagnostics(result, config)
                    st.session_state["sales_diagnosis_mode"] = diagnosis_mode
                else:
                    st.session_state["sales_diagnosis_mode"] = "本地规则（未调用DeepSeek）"
                st.session_state[state_key] = result
                st.success(f"完成 {len(result)} 个 SKU/ASIN 的分析。")
            except Exception as exc:
                st.error(f"报表处理失败：{exc}")

    result = st.session_state.get(state_key, pd.DataFrame())
    if not result.empty:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("SKU / ASIN", len(result))
        c2.metric("本期销量", f"{result['units_current'].sum():,.0f}")
        c3.metric("本期销售额", f"${result['revenue_current'].sum():,.2f}")
        c4.metric("异常项", int(result["needs_attention"].sum()))
        st.caption(
            f"异常诊断方式：{st.session_state.get('sales_diagnosis_mode', '本地规则')}"
        )
        st.dataframe(
            result,
            width="stretch",
            hide_index=True,
            column_config={"product_url": st.column_config.LinkColumn("Amazon链接")},
        )
        c1, c2 = st.columns(2)
        if c1.button("保存销售分析"):
            store.save_snapshot(
                "sales",
                f"销售分析 截止 {as_of:%Y-%m-%d}",
                result,
                metadata={"as_of": str(as_of)},
            )
            st.success("销售分析已保存。")
        c2.download_button(
            "下载销售分析 Excel",
            build_sales_workbook(result, as_of),
            f"seller_sales_analysis_{as_of:%Y%m%d}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def render_data_center(store: SnapshotStore) -> None:
    hero("数据中心与设置", "模板、历史快照、模型配置和部署状态")
    st.subheader("模板下载")
    templates = {
        "Top 50 产品模板": PRODUCT_TEMPLATE_COLUMNS,
        "评论模板": REVIEW_TEMPLATE_COLUMNS,
        "竞品情报模板": INTEL_TEMPLATE_COLUMNS,
        "销售与流量模板": SALES_TEMPLATE_COLUMNS,
        "广告报表模板": ADS_TEMPLATE_COLUMNS,
    }
    cols = st.columns(len(templates))
    for col, (name, columns) in zip(cols, templates.items()):
        col.download_button(
            name,
            template_workbook(columns, name),
            f"{name}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.subheader("DeepSeek 配置")
    data_dir = os.getenv("LOCAL_DATA_DIR", "data")
    config = settings_from_env(data_dir)
    c1, c2, c3 = st.columns(3)
    c1.metric("连接状态", "已配置" if config.enabled else "未配置")
    c2.metric("当前模型", config.model or "未设置")
    env_key_configured = bool(os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY"))
    c3.metric("配置方式", "Streamlit Secrets / 环境变量" if env_key_configured else "本地界面配置")

    if env_key_configured:
        st.success("已从 Streamlit Secrets / 服务器环境变量读取密钥，页面不允许修改或显示。")
        base_url_input = config.base_url
        model_input = config.model
        api_key_input = ""
    else:
        with st.form("deepseek_settings"):
            api_key_input = st.text_input(
                "DeepSeek API Key",
                type="password",
                value="",
                placeholder="已保存，可留空不修改" if config.api_key else "sk-...",
                help="仅用于Mac本地测试；上线时请使用Streamlit Secrets。",
            )
            base_url_input = st.text_input(
                "OPENAI_API_BASE", value=config.base_url or "https://api.deepseek.com"
            )
            model_input = st.text_input(
                "MODEL_ID", value=config.model or "openai/deepseek-v4-flash"
            )
            save_clicked = st.form_submit_button("保存本地 DeepSeek 配置", type="primary")
        if save_clicked:
            new_settings = LLMSettings(
                api_key=api_key_input.strip() or config.api_key,
                base_url=base_url_input.strip(),
                model=model_input.strip(),
            )
            if not new_settings.enabled:
                st.error("请完整填写 API Key、Base URL 和模型名称。")
            else:
                save_settings(data_dir, new_settings)
                st.success("DeepSeek本地配置已保存。四个需求模块将按需使用该模型。")
                st.rerun()

    test_key = st.text_input(
        "临时 API Key（仅在尚未保存时用于连接测试）",
        type="password",
        key="deepseek_test_key",
        label_visibility="collapsed",
        placeholder="如已保存密钥，此处留空即可",
    )
    if st.button("测试 DeepSeek 连接"):
        candidate = LLMSettings(
            api_key=test_key.strip() or config.api_key,
            base_url=config.base_url,
            model=config.model,
        )
        with st.spinner("正在连接 DeepSeek……"):
            try:
                message = test_connection(candidate)
                st.success(f"连接成功：{message}")
            except Exception as exc:
                st.error(f"连接失败：{exc}")

    st.caption("未配置大模型时，榜单、报表、Excel和本地关键词VOC仍可使用。")

    st.subheader("历史快照")
    snapshots = store.list_snapshots(limit=200)
    if snapshots.empty:
        st.caption("暂无历史快照。")
    else:
        st.dataframe(snapshots, width="stretch", hide_index=True)
        st.download_button(
            "下载快照索引 CSV",
            dataframe_to_csv_bytes(snapshots),
            "snapshot_index.csv",
            "text/csv",
        )


def main() -> None:
    load_local_env()
    init_page()
    store = get_store()
    st.sidebar.markdown("## CLEVA")
    st.sidebar.caption("Amazon Intelligence Hub")
    page = st.sidebar.radio(
        "导航",
        [
            "总览",
            "需求一｜干湿两用吸尘器",
            "需求二｜Spot Cleaner",
            "需求三｜全球竞品新品",
            "需求四｜店铺销售分析",
            "数据中心与设置",
        ],
    )
    st.sidebar.divider()
    st.sidebar.caption("数据仅用于内部市场分析；估算数据会在报告中明确标注。")

    if page == "总览":
        render_overview(store)
    elif page.startswith("需求一"):
        render_bsr_module(
            store,
            category_key="wet_dry",
            title="需求一｜干湿两用吸尘器 Top 50 & VOC",
            subtitle="Amazon US 类目 553022｜榜单、排名变化、评论痛点与策略建议",
            url=WET_DRY_URL,
        )
    elif page.startswith("需求二"):
        render_bsr_module(
            store,
            category_key="spot_cleaner",
            title="需求二｜Spot Cleaner Top 50 & VOC",
            subtitle="Amazon US 类目 1063922｜自动排除非便携式产品并提取专属参数",
            url=SPOT_URL,
        )
    elif page.startswith("需求三"):
        render_competitor_module(store)
    elif page.startswith("需求四"):
        render_sales_module(store)
    else:
        render_data_center(store)


if __name__ == "__main__":
    main()
