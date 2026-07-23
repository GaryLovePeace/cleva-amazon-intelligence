# CLEVA Amazon 市场情报与销售分析

这是一个统一的 Streamlit 内部工具，把四项需求放在同一个左侧导航中：

1. Amazon 干湿两用吸尘器 Top 50 与 VOC
2. Amazon Spot Cleaner Top 50 与 VOC
3. 全球竞品新品情报周报
4. Vacmaster 全量及 Kenmore Floor Care 销售分析

## 当前能力

- Chromium 自动滚动 Amazon 榜单，持续懒加载并按 ASIN 去重，目标获取 Top 50
- 可按 10–50 个 ASIN 分批访问产品详情页，补全主图、型号、容量、马力/吸力、功率、
  尺寸、软管、电源线、过滤、配件、保修和 Spot Cleaner 水箱/蒸汽等字段
- 在遇到 503/Captcha 时支持 CSV/XLSX 上传兜底
- 保存每周快照，从第二次运行开始计算排名变化
- 上传评论后使用 DeepSeek 分析低星痛点、好评卖点、使用场景和逐 ASIN 差异化建议
- 自动生成价格带、品牌基准、Vacmaster 定价定位、能力差异及产品机会分析
- 产品商业分析与评论 VOC 已完全拆分：详情采集后可直接生成商业报告，
  只有上传评论后才会出现 VOC 分析结果
- 需求一、二均可下载商业分析 Excel 和独立 HTML 报告
- Spot Cleaner 自动生成美系与中国出海品牌的技术对比
- 搜集公开新品新闻，区分“文章发布日期”和“系统采集时间”，支持最近 7/30/90 天、
  最近 1 年或不限时间筛选
- 全部品牌线索会按文章发布日期统一从新到旧排序；日期缺失、无效或未来异常会单独标记，
  不再用当天日期代替
- 使用 DeepSeek 优先分析最新线索，并提取型号、规格、定价策略、竞争影响和应对动作
- 需求三采用小批次 DeepSeek 分析；部分批次失败时保留已完成结果，并生成核心技术、
  定价策略、CLEVA 竞争影响、应对方案和重点关注清单
- 需求三可下载包含管理层摘要的 Excel 与 HTML 报告
- 上传 Seller Central 销售、Listings、库存和广告报表
- 自动计算 WoW、MoM、QoQ、HoH、YoY、CTR、CVR、ACoS
- 只对销售额周环比超过 ±5% 的 SKU 调用 DeepSeek 诊断并给出运营建议
- 所有模块均可导出带格式的 Excel

## 重要数据边界

- Amazon 页面存在访问限制，直接采集无法承诺每次成功，因此保留上传兜底。
- Top 50 详情补全需要逐页访问，建议先运行 10 个验证；连续遇到验证码时系统会提前停止。
- `bought in past month` 和由此计算的销售额是估算，不是真实 Seller Central 数据。
- Amazon 官方产品接口通常不能提供所有竞品的完整评论正文，VOC 仍需要评论文件或
  其他合规、授权的数据来源。
- 需求三的“文章发布日期”来自 Google News RSS，仅代表新闻线索时间，不等同于产品
  正式上市日期；转载旧闻、日期异常及重要结论仍需回到原始来源复核。
- 需求四必须上传公司内部 Seller Central 报表，公开店铺页面不能提供真实销售表现。
- Kenmore 仅应包含 CLEVA 负责的 Floor Care SKU，正式使用前建议导入公司 SKU 白名单。

## Mac 本地运行

双击 `run_mac.command`。首次运行会安装 Python 依赖和 Chromium 浏览器组件。
如果 macOS 阻止运行，可在终端执行：

```bash
cd cleva-amazon-intelligence
chmod +x run_mac.command
xattr -d com.apple.quarantine run_mac.command
./run_mac.command
```

浏览器打开 `http://localhost:8501`。

## 手动运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
streamlit run app.py
```

## DeepSeek 配置

Mac 本地测试可以进入应用左侧的“数据中心与设置”，填写：

- DeepSeek API Key
- Base URL：`https://api.deepseek.com`
- 模型：`deepseek-v4-flash`

保存后点击“测试 DeepSeek 连接”。四个模块会根据各自任务调用大模型。

也可以在服务器上编辑 `.env`：

```dotenv
OPENAI_API_KEY=你的密钥
OPENAI_API_BASE=https://api.deepseek.com
MODEL_ID=deepseek-v4-flash
LOCAL_DATA_DIR=data
```

不配置大模型时，榜单、Excel、报表合并和本地关键词 VOC 仍然可以使用，但页面会明确显示当前只使用本地规则。

## Streamlit Community Cloud Secrets

上线后不要在网页中填写或保存 API Key。在 Streamlit Cloud 中进入：

`App → Settings → Secrets`

填写：

```toml
OPENAI_API_KEY = "你的DeepSeek API Key"
OPENAI_API_BASE = "https://api.deepseek.com"
MODEL_ID = "deepseek-v4-flash"
```

顶层 Secrets 会作为环境变量提供给应用。检测到 Secrets 后，应用会隐藏网页密钥编辑表单，
只显示“已从 Streamlit Secrets / 服务器环境变量读取密钥”。

`.streamlit/secrets.toml` 已加入 `.gitignore`，不得上传 GitHub。

## Docker 运行

```bash
cp .env.example .env
docker compose up -d --build
```

访问 `http://服务器IP:8501`。服务器部署时建议由 IT 在前面配置 Nginx、HTTPS 和公司身份验证。

## 建议的业务操作

- 每周一：先运行需求一、二并保存快照。
- 需求三：检查自动收集结果，补充状态、型号、规格和可信度，再保存周报。
- 需求四：上传同一截止日期下的 Sales & Traffic、Listings/库存及广告报表。
- 首次使用：历史比较字段可能为空；上传足够历史日期后会自动产生多周期对比。

## 目录

```text
app.py                    Streamlit 总入口与统一界面
core/amazon.py            Amazon 榜单采集与标准化
core/voc.py               评论标准化与本地 VOC
core/llm.py               OpenAI-compatible / DeepSeek 分析
core/competitor.py        竞品公开信息搜集
core/seller_reports.py    Seller Central 报表合并与指标计算
core/reports.py           Excel 导出
core/storage.py           本地 SQLite 快照
packages.txt              Streamlit Cloud Chromium 系统依赖
```
