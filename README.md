# MetricPilot

**把自然语言分析落到可追溯的 SQL 和查询单元格上。**

MetricPilot 是独立的 CSV 数据分析 Agent 开源项目。上传业务数据后，Agent 可以检查表结构、
查看数据画像、调用受限的只读 SQL 工具，并生成带查询证据的指标卡片、表格和柱状图。
每个发布的指标都引用本次运行的 `query_id + row + column`，服务端逐项检查数值是否相等。

默认演示无需 API Key：固定问题路由到**真实执行的 SQLite 查询**。自由提问和导入数据的通用
分析需要切换 live 模式，连接支持 Chat Completions 工具调用的模型。

[English](docs/README.en.md) · [架构](docs/architecture.md) · [安全边界](SECURITY.md) ·
[API 合同](docs/api-contract.md) · [贡献](CONTRIBUTING.md)

![MetricPilot data studio](docs/assets/dashboard.jpg)

## 三分钟运行

Python 3.11 或 3.12，Windows / macOS / Linux；无 Node 构建步骤。

```bash
git clone https://github.com/euphoira/metricpilot.git
cd metricpilot
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
metricpilot serve
```

macOS / Linux：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
metricpilot serve
```

打开 [本地数据工作台](http://127.0.0.1:8011)。选择 `Demo Commerce` 和一个示例问题，查看
指标、图表、实际 SQL、查询结果和事件轨迹。历史结果保存在 `.metricpilot/metricpilot.sqlite3`。

```bash
metricpilot demo
metricpilot demo --question "按地区比较收入" --output artifacts/regions.md
metricpilot eval
```

`demo` 和 `eval` 命令始终离线运行，即使环境变量配置了 live 模式也不会调用付费 API。
评测包含七个固定分析场景和一个未覆盖问题拒答场景，共八项。它验证工作流和查询结果引用，
**不是模型准确率评测，也不证明任意 SQL 的业务口径正确**。

## 导入自己的数据

工作台支持上传 UTF-8 CSV 文件，或粘贴 CSV 文本。示例：

```csv
department,month,revenue,cost
Sales,2026-01,12000,7200
Support,2026-01,6000,4200
Sales,2026-02,15000,8600
```

导入最多一 MiB、一万行、三十列；列名以英文字母开头，仅含字母、数字和下划线。
空单元格转为 `NULL`，数字列推断为整数或浮点数，带前导零的标识符保留为文本。
数据集名称只作为展示标签，SQL 表名由服务端生成。原始上传文件路径不会传给数据库。

导入后可查看列结构；离线模式对用户数据和未覆盖的问题会明确拒答。配置 live 模式后再做自由分析。

## 配置 live 模式

复制 `.env.example` 为 `.env`，填写：

```dotenv
METRICPILOT_MODE=live
METRICPILOT_BASE_URL=https://api.openai.com/v1
METRICPILOT_API_KEY=your-key-here
METRICPILOT_MODEL=your-tool-calling-model
METRICPILOT_DATA_DIR=.metricpilot
```

重新启动 `metricpilot serve`。必须选择支持 **Chat Completions function calling** 的兼容模型；
仅支持 Responses API 的模型不适用此适配器。不同服务商的工具调用兼容性需要自行验证。
接口协议参考 [OpenAI function calling 文档](https://developers.openai.com/api/docs/guides/function-calling)。

CSV 存储和 SQL 执行在本机；**live 模式会把问题、表结构、样本和查询结果发送给配置的模型服务商**。
请根据数据的使用权限决定哪些数据可以导入。密钥只从本地环境读取，不进入浏览器配置响应。
本项目没有使用付费 API 测试真实模型效果；自动化测试使用可控模拟服务验证协议和错误处理。

## 实际实现了什么

| 组件 | 行为 |
| --- | --- |
| CSV 接入 | 严格解析、行列/字节预算、类型推断、不可变快照、数据摘要 |
| Agent | `inspect_schema → profile_dataset → query_sql → submit_report` 的模型工具循环 |
| SQL 执行 | 每次新建仅包含选中数据集的内存库；`query_only`、authorizer、函数白名单 |
| 资源预算 | SQL 长度、表达式深度、SQLite opcode/时间、结果行数/字节数、模型/工具调用次数 |
| 指标引用 | 仅引用本次运行持久化的查询；精确验证查询 ID、行、列和值 |
| 产品界面 | 本地数据工作台、导入、示例、指标、表格、SVG 图表、历史、查询轨迹、Markdown 导出 |
| 工程验证 | CSV/SQL 对抗测试、流式服务超时、拒答/截断响应、跨运行引用、API 同源与请求预算测试 |

运行最多八轮模型调用、十六次工具调用。SQL 结果最多二百行、一百二十八 KiB；超限会标记
截断。模型单次请求采用总时限及有界重试。数据库最多二十个数据集、五百次运行；可切换
`METRICPILOT_DATA_DIR` 创建新工作区。

## 需要理解的边界

- `verified` 表示报告值与已执行查询的指定单元格一致，不表示 SQL 业务语义正确。
  例如错误筛选条件或 `UNION SELECT 999` 仍可能形成合法 SQL。请检查查询口径与数据质量。
- 自由说明文字要求定性表达，数字放在结构化指标中；服务端拒绝说明中的数字字符。
  这不是自然语言真伪判定器，也不会验证业务因果关系。
- 仅支持 SELECT；暂不支持 CTE、窗口函数、多数据集 JOIN、数据编辑或任意 Python 代码。
  柱状图目前要求非负数值。浮点计算不适合作为精确会计记账系统。
- 面向可信用户的单进程 localhost 工具，没有登录、多租户、分布式队列或公网部署能力。
  中断的运行会记录为 interrupted，需新建分析；不会把未完成结果当作报告发布。
- 数据与查询证据存储于本机 SQLite，未做静态加密。上传和生成结果均视为不可信文本。

## 开发与验证

```bash
python -m pytest -q
python -m ruff check .
python -m build
metricpilot eval --output artifacts/eval-results.json
```

GitHub Actions 配置 Windows / Ubuntu、Python 3.11 / 3.12 的测试、lint、构建与离线评测。
本地测试通过不代表远程 CI 已通过，请以仓库 Actions 结果为准。

```text
src/metricpilot/
  api.py          # 本地 HTTP 边界与后台运行
  data.py         # CSV、SQLite 数据仓库、受限 SQL
  engine.py       # 工具循环、指标引用校验、报告
  provider.py     # Chat Completions 适配器
  demo.py         # 演示数据、确定性 SQL 场景
  evaluation.py   # 离线回归评测
  web/            # 无构建步骤的数据工作台
tests/            # 数据、安全、协议、工作流、API 测试
```

适合在面试中讨论的设计点：为什么查询放在隔离内存库、authorizer 与文本关键词拦截的区别、
结果引用能验证什么、流式响应如何控制总时限，以及如何区分可重复演示和真实模型评测。
介绍项目时应使用自己复现过的功能与测试结果，不要把固定场景通过率描述成生产效果。

MIT License。
