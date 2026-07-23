# CLEVA Amazon 市场情报与销售分析

这是一个统一的 Streamlit 内部工具，把四项需求放在同一个左侧导航中：

1. Amazon 干湿两用吸尘器 Top 50 与 VOC
2. Amazon Spot Cleaner Top 50 与 VOC
3. 全球竞品新品情报周报
4. Vacmaster 全量及 Kenmore Floor Care 销售分析

## 当前能力

- Chromium 自动滚动 Amazon 榜单，持续懒加载并按 ASIN 去重，目标获取 Top 50
- 在遇到 503/Captcha 时支持 CSV/XLSX 上传兜底
- 保存每周快照，从第二次运行开始计算排名变化
- 上传评论后使用 DeepSeek 分析低星痛点、好评卖点、使用场景和逐 ASIN 差异化建议
- Spot Cleaner 自动生成美系与中国出海品牌的技术对比
- 搜集公开新品新闻，使用 DeepSeek 提取型号、规格、定价策略、竞争影响和应对动作
- 上传 Seller Central 销售、Listings、库存和广告报表
- 自动计算 WoW、MoM、QoQ、HoH、YoY、CTR、CVR、ACoS
- 只对销售额周环比超过 ±5% 的 SKU 调用 DeepSeek 诊断并给出运营建议
- 所有模块均可导出带格式的 Excel

## 重要数据边界

- Amazon 页面存在访问限制，直接采集无法承诺每次成功，因此保留上传兜底。
- `bought in past month` 和由此计算的销售额是估算，不是真实 Seller Central 数据。
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
- 模型：`openai/deepseek-v4-flash`

保存后点击“测试 DeepSeek 连接”。四个模块会根据各自任务调用大模型。

也可以在服务器上编辑 `.env`：

```dotenv
OPENAI_API_KEY=你的密钥
OPENAI_API_BASE=https://api.deepseek.com
MODEL_ID=openai/deepseek-v4-flash
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
MODEL_ID = "openai/deepseek-v4-flash"
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
