# Leek Quant开发架构文档

纯 A 股量化交易平台，基于 QuantDinger 裁剪优化，本地优先、隐私至上，Docker Compose 一键部署。

---

## 目录

1. \[项目概述\]\(\#项目概述\)

2. \[系统架构设计\]\(\#系统架构设计\)

3. \[数据库设计（PostgreSQL）\]\(\#数据库设计postgresql\)

4. \[数据源层设计\]\(\#数据源层设计\)

5. \[回测引擎集成方案\]\(\#回测引擎集成方案\)

6. \[五档信号状态机\]\(\#五档信号状态机\)

7. \[模拟交易引擎\]\(\#模拟交易引擎\)

8. \[多因子打分模块\]\(\#多因子打分模块\)

9. \[前端页面与组件规划\]\(\#前端页面与组件规划\)

10. \[Docker Compose 部署配置\]\(\#docker\-compose\-部署配置\)

11. \[开发里程碑\]\(\#开发里程碑\)

12. \[风险与应对措施\]\(\#风险与应对措施\)

---

## 项目概述

### 项目定位

- 从 QuantDinger 多市场平台做减法，仅保留 A 股专属能力，极致轻量化

- 本地优先、隐私至上，所有数据与策略完全保留在用户本地设备

- 支持 Docker Compose 一键部署，无需复杂环境配置

### 核心目标

- 零成本量化入门，为个人投资者提供专业级 A 股量化工具

- 最大化复用成熟开源组件，最小化自研工作量

- 完整覆盖 A 股交易规则，保证回测与模拟交易的真实性

---

## 系统架构设计

整体采用分层架构，从前端到存储层职责清晰，异步任务处理耗时操作，保证系统响应性。

```mermaid
flowchart TB
    subgraph 前端层 Frontend
        A[React App] --> B[Monaco Editor<br/>策略编辑]
        A --> C[TradingView Charts<br/>行情可视化]
        A --> D[shadcn/ui UI组件]
    end

    subgraph API层 API Gateway
        E[FastAPI 后端服务] --> F[REST API]
        E --> G[WebSocket 实时推送]
    end

    subgraph 任务调度层 Task Scheduler
        H[Celery Worker<br/>异步任务处理]
        I[Celery Beat<br/>定时任务]
    end

    subgraph 数据处理层 Data Processing
        J[数据源适配层] --> J1[AData Tier1]
        J --> J2[Baostock Tier2]
        J --> J3[AkShare Tier3]
        K[实时行情解析] --> K1[东方财富 WebSocket]
        L[Hikyuu 回测引擎]
        M[MyTT 指标库]
        N[Qlib 因子引擎]
    end

    subgraph 存储层 Storage
        O[PostgreSQL<br/>统一持久化存储]
        P[Redis<br/>缓存/队列/广播]
    end

    %% 连接关系
    A --> E
    E --> O
    E --> P
    H --> O
    H --> P
    I --> H
    J --> O
    K --> P
    L --> H
    M --> L
    N --> H
    P --> G```

---

## 数据库设计（PostgreSQL）

采用 PostgreSQL 作为统一存储，替代多存储方案，简化部署与维护。针对时间序列数据采用分区表优化性能。

### 1\. 基础市场数据表

#### `stock\_basic` \- 股票基础信息

|字段|类型|说明|
|---|---|---|
|code|VARCHAR\(16\)|股票代码（主键）|
|name|VARCHAR\(64\)|股票名称|
|industry|VARCHAR\(32\)|所属行业|
|area|VARCHAR\(32\)|所属地区|
|market|VARCHAR\(16\)|市场类型（主板 / 创业板 / 科创板）|
|list\_date|DATE|上市日期|
|delist\_date|DATE|退市日期|
|is\_st|BOOLEAN|ST 标识|
|is\_delisted|BOOLEAN|退市标识|
|created\_at|TIMESTAMP|创建时间|
|updated\_at|TIMESTAMP|更新时间|

#### `daily\_kline` \- 日线 K 线数据（按年分区）

采用 PostgreSQL 范围分区表，按交易日期按年分区，大幅提升大数量下的查询性能。

**父表创建 SQL：**

```sql
CREATE TABLE daily_kline (
    code VARCHAR(16) NOT NULL,
    trade_date DATE NOT NULL,
    open NUMERIC(10,2) NOT NULL,
    high NUMERIC(10,2) NOT NULL,
    low NUMERIC(10,2) NOT NULL,
    close NUMERIC(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    amount NUMERIC(20,2) NOT NULL,
    adj_factor NUMERIC(10,6) NOT NULL,
    is_suspended BOOLEAN DEFAULT false,
    PRIMARY KEY (code, trade_date)
) PARTITION BY RANGE (trade_date);
```

**年度分区创建示例：**

```sql
-- 2024年数据分区
CREATE TABLE daily_kline_2024 PARTITION OF daily_kline
FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

-- 2025年数据分区
CREATE TABLE daily_kline_2025 PARTITION OF daily_kline
FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
```

#### `trade\_calendar` \- 交易日历

|字段|类型|说明|
|---|---|---|
|cal\_date|DATE|日期（主键）|
|is\_open|BOOLEAN|是否交易日|
|pretrade\_date|DATE|上一个交易日|

#### `stock\_fundamentals` \- 基本面数据

|字段|类型|说明|
|---|---|---|
|code|VARCHAR\(16\)|股票代码|
|report\_date|DATE|报告期|
|pe|NUMERIC\(10,2\)|市盈率|
|pb|NUMERIC\(10,2\)|市净率|
|roe|NUMERIC\(10,2\)|净资产收益率|
|revenue|NUMERIC\(20,2\)|营业收入|
|profit|NUMERIC\(20,2\)|净利润|
|financials|JSONB|完整三大报表数据|
|PRIMARY KEY \(code, report\_date\)|||

### 2\. 用户与策略数据表

#### `watchlist` \- 自选股

|字段|类型|说明|
|---|---|---|
|user\_id|BIGINT|用户 ID|
|group\_name|VARCHAR\(32\)|分组名称|
|code|VARCHAR\(16\)|股票代码|
|added\_at|TIMESTAMP|添加时间|
|PRIMARY KEY \(user\_id, group\_name, code\)|||

#### `stock\_pools` / `stock\_pool\_items` \- 动态股票池

- `stock\_pools`: 股票池定义，包含筛选规则

- `stock\_pool\_items`: 股票池成员，每日更新

#### `strategies` \- 策略配置

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|策略 ID|
|user\_id|BIGINT|用户 ID|
|name|VARCHAR\(64\)|策略名称|
|description|TEXT|策略描述|
|source\_code|TEXT|Python 源码|
|config|JSONB|策略配置参数|
|created\_at|TIMESTAMP|创建时间|
|updated\_at|TIMESTAMP|更新时间|

### 3\. 回测与信号数据表

#### `backtest\_results` \- 回测结果

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|回测 ID|
|strategy\_id|BIGINT|关联策略 ID|
|user\_id|BIGINT|用户 ID|
|start\_date|DATE|回测开始日期|
|end\_date|DATE|回测结束日期|
|initial\_cash|NUMERIC\(20,2\)|初始资金|
|performance|JSONB|绩效指标（年化收益、夏普比率等）|
|trades|JSONB|交易记录|
|nav\_curve|JSONB|净值曲线|
|status|VARCHAR\(16\)|任务状态|
|created\_at|TIMESTAMP|创建时间|

#### `signal\_log` \- 五档信号日志

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|信号 ID|
|strategy\_id|BIGINT|策略 ID|
|user\_id|BIGINT|用户 ID|
|code|VARCHAR\(16\)|股票代码|
|trade\_date|DATE|交易日期|
|signal\_type|VARCHAR\(16\)|信号类型：buy/add/hold/reduce/sell|
|target\_weight|NUMERIC\(5,2\)|目标仓位权重|
|current\_weight|NUMERIC\(5,2\)|当前仓位权重|
|market\_snapshot|JSONB|行情快照|
|created\_at|TIMESTAMP|创建时间|

### 4\. 因子与打分数据表

#### `factor\_values` \- 因子值

|字段|类型|说明|
|---|---|---|
|code|VARCHAR\(16\)|股票代码|
|trade\_date|DATE|交易日期|
|factor\_name|VARCHAR\(32\)|因子名称|
|factor\_value|NUMERIC\(20,6\)|因子值|
|PRIMARY KEY \(code, trade\_date, factor\_name\)|||

#### `scoring\_rank` \- 打分排名

|字段|类型|说明|
|---|---|---|
|trade\_date|DATE|交易日期|
|code|VARCHAR\(16\)|股票代码|
|total\_score|NUMERIC\(10,2\)|综合得分|
|rank|INT|排名|
|factor\_scores|JSONB|各因子得分|
|PRIMARY KEY \(trade\_date, code\)|||

#### `factor\_analysis` \- 因子分析结果

|字段|类型|说明|
|---|---|---|
|factor\_name|VARCHAR\(32\)|因子名称|
|trade\_date|DATE|分析日期|
|ic|NUMERIC\(10,4\)|信息系数|
|ir|NUMERIC\(10,4\)|信息比率|
|icir|NUMERIC\(10,4\)|ICIR|

### 5\. 模拟交易 6 表完整体系

#### `sim\_accounts` \- 模拟账户

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|账户 ID|
|user\_id|BIGINT|用户 ID|
|name|VARCHAR\(64\)|账户名称|
|initial\_cash|NUMERIC\(20,2\)|初始资金|
|available\_cash|NUMERIC\(20,2\)|可用现金|
|total\_assets|NUMERIC\(20,2\)|总资产|
|created\_at|TIMESTAMP|创建时间|
|updated\_at|TIMESTAMP|更新时间|

#### `sim\_positions` \- 当前持仓

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|持仓 ID|
|account\_id|BIGINT|账户 ID|
|code|VARCHAR\(16\)|股票代码|
|volume|INT|持仓股数|
|available\_volume|INT|可用股数（T\+1 可用）|
|cost\_price|NUMERIC\(10,2\)|成本价|
|current\_price|NUMERIC\(10,2\)|当前价|
|market\_value|NUMERIC\(20,2\)|市值|
|float\_profit|NUMERIC\(20,2\)|浮动盈亏|
|profit\_rate|NUMERIC\(5,2\)|盈亏比例|
|PRIMARY KEY \(account\_id, code\)|||

#### `sim\_orders` \- 委托单

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|委托 ID|
|account\_id|BIGINT|账户 ID|
|order\_id|VARCHAR\(32\)|委托编号|
|code|VARCHAR\(16\)|股票代码|
|order\_type|VARCHAR\(16\)|委托类型：limit/Market|
|side|VARCHAR\(16\)|方向：buy/sell|
|price|NUMERIC\(10,2\)|委托价格|
|volume|INT|委托数量|
|filled\_volume|INT|已成交数量|
|status|VARCHAR\(16\)|状态：pending/filled/canceled|
|created\_at|TIMESTAMP|委托时间|

#### `sim\_trades` \- 成交记录

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|成交 ID|
|order\_id|BIGINT|关联委托 ID|
|account\_id|BIGINT|账户 ID|
|code|VARCHAR\(16\)|股票代码|
|side|VARCHAR\(16\)|方向|
|price|NUMERIC\(10,2\)|成交价格|
|volume|INT|成交数量|
|amount|NUMERIC\(20,2\)|成交金额|
|commission|NUMERIC\(10,2\)|佣金|
|stamp\_tax|NUMERIC\(10,2\)|印花税|
|transfer\_fee|NUMERIC\(10,2\)|过户费|
|trade\_time|TIMESTAMP|成交时间|

#### `sim\_cash\_flow` \- 资金流水

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|流水 ID|
|account\_id|BIGINT|账户 ID|
|trade\_id|BIGINT|关联成交 ID|
|flow\_type|VARCHAR\(16\)|类型：trade/dividend/interest|
|amount|NUMERIC\(20,2\)|变动金额|
|balance|NUMERIC\(20,2\)|变动后余额|
|flow\_time|TIMESTAMP|流水时间|

#### `sim\_daily\_nav` \- 每日净值快照

|字段|类型|说明|
|---|---|---|
|id|BIGSERIAL|快照 ID|
|account\_id|BIGINT|账户 ID|
|trade\_date|DATE|交易日期|
|total\_assets|NUMERIC\(20,2\)|当日总资产|
|daily\_return|NUMERIC\(10,4\)|日收益率|
|cumulative\_nav|NUMERIC\(10,4\)|累计净值|
|max\_drawdown|NUMERIC\(10,4\)|最大回撤|
|PRIMARY KEY \(account\_id, trade\_date\)|||

---

## 数据源层设计

实现三层数据源回退机制，保证数据的可靠性与完整性，同时支持增量更新，减少网络开销。

### 1\. 三层回退机制

优先使用高质量主源，失败时自动降级到备用源，最后兜底源，确保数据获取不中断。

```mermaid
flowchart LR
    A[数据请求] --> B{检查AData健康状态}
    B -->|正常| C[调用AData接口]
    C --> D{请求成功?}
    D -->|成功| E[数据清洗标准化]
    D -->|失败| F[AData失败计数+1]
    F --> G{失败>=2次?}
    G -->|是| H[切换到Baostock]
    G -->|否| B
    H --> I[调用Baostock接口]
    I --> J{请求成功?}
    J -->|成功| E
    J -->|失败| K[Baostock失败计数+1]
    K --> L{失败>=2次?}
    L -->|是| M[切换到AkShare]
    L -->|否| H
    M --> N[调用AkShare接口]
    N --> O{请求成功?}
    O -->|成功| E
    O -->|失败| P[触发告警,任务重试]
    E --> Q[写入PostgreSQL]```

#### 数据源分工

|层级|数据源|职责|特点|
|---|---|---|---|
|Tier1|AData|主数据源，K 线、股票列表|更新快、数据准、接口稳定|
|Tier2|Baostock|备用数据源，基本面数据|财务数据完整、免费|
|Tier3|AkShare|兜底数据源，分钟线、补充数据|覆盖全、社区维护|

### 2\. 增量拉取机制

每日收盘后自动增量更新，仅拉取新增数据，避免全量重下，提升更新效率。

**核心逻辑：**

1. 对于每只股票，查询数据库中该股票的最新`trade\_date`

2. 如果无数据，则全量拉取上市以来所有数据

3. 如果有数据，则从`最新trade\_date \+ 1天`开始拉取到当日

4. 对拉取到的数据进行清洗、去重、复权因子校准

5. 批量写入数据库

**优化点：**

- 批量拉取，减少请求次数

- 请求间隔 0\.5s，避免 IP 被封

- 错峰更新，不同数据源错开更新时间

- 断点续传，更新中断后下次继续

### 3\. 实时行情推送

基于东方财富免费 WebSocket 接口，实现毫秒级实时行情推送，通过 Redis 广播实现多端同步。

**工作流程：**

1. 独立守护进程连接东方财富 WebSocket 服务器

2. 根据用户自选股 / 关注标的，自动订阅对应股票

3. 接收推送消息，解析二进制 / JSON 数据，提取价格、成交量等核心字段

4. 将解析后的行情数据发布到 Redis Pub/Sub 频道

5. FastAPI 后端订阅 Redis 频道，通过 WebSocket 推送给前端用户

6. 断线自动重连，心跳保活，保证连接稳定性

---

## 回测引擎集成方案

深度集成 Hikyuu 高性能回测引擎，复用其内置的 A 股规则支持，无需自研复杂的回测逻辑。

### 1\. Hikyuu 集成优势

- **C\+\+ 内核**：毫秒级回测速度，支持百万级 K 线数据快速回测

- **原生 A 股规则**：内置 T\+1、涨跌停限制、ST 股票规则、分红除权处理

- **一字板过滤**：自动识别一字涨跌停，过滤无效交易信号

- **Python 绑定**：通过 pybind11 实现零拷贝调用，Python 层可直接使用

- **内置指标**：深度集成 TA\-Lib，兼容 MyTT 指标库

### 2\. 适配层设计

我们实现轻量适配层，将平台的策略与数据转换为 Hikyuu 可识别的格式，同时屏蔽 Hikyuu 的内部细节。

```python
# 适配层核心流程
class HikyuuAdapter:
    def __init__(self):
        self.hikyuu = hikyuu
        # 初始化Hikyuu环境
        
    def load_data(self, code, start_date, end_date):
        # 从PostgreSQL加载我们的K线数据
        k_data = get_daily_kline(code, start_date, end_date)
        # 转换为Hikyuu的KData格式
        return convert_to_hikyuu_kdata(k_data)
    
    def run_backtest(self, strategy_code, k_data, params):
        # 编译用户的策略代码
        strategy = compile_strategy(strategy_code, MyTT)
        # 初始化Hikyuu回测环境
        tm = crtTM()  # 交易管理
        sys = crtSys(strategy, tm)  # 交易系统
        # 执行回测
        sys.run(k_data)
        # 提取回测结果
        return extract_backtest_result(sys, tm)
```

### 3\. 异步回测任务

回测作为耗时操作，通过 Celery 异步执行，避免阻塞 API 服务。

**流程：**

1. 用户提交回测请求，FastAPI 创建任务记录，状态为 pending

2. 提交任务到 Celery 队列

3. Celery Worker 异步执行回测：

    - 加载数据

    - 调用 Hikyuu 执行回测

    - 计算绩效指标

    - 写入回测结果到 PostgreSQL

4. 更新任务状态为 completed/failed

5. 前端通过轮询或 WebSocket 推送获取结果

---

## 五档信号状态机

实现标准化的五档信号体系，将策略输出的信号转换为实际的交易操作，自动适配持仓状态与 A 股规则。

### 信号定义

|信号类型|目标仓位|说明|
|---|---|---|
|买入 \(Buy\)|100%|强烈看多，满仓配置|
|增持 \(Add\)|50%|看多，半仓配置|
|观望 \(Hold\)|不变|中性，维持当前仓位|
|减仓 \(Reduce\)|25%|看空，保留少量仓位|
|卖出 \(Sell\)|0%|强烈看空，清仓|

### 状态流转表

根据当前持仓状态，自动调整实际操作，保证信号的可执行性：

|当前仓位|买入信号|增持信号|观望信号|减仓信号|卖出信号|
|---|---|---|---|---|---|
|0%（空仓）|买入至 100%|买入至 50%|无操作|无操作|无操作|
|25%|加仓至 100%|加仓至 50%|无操作|无操作|清仓至 0%|
|50%|加仓至 100%|无操作|无操作|减仓至 25%|清仓至 0%|
|100%（满仓）|无操作|无操作|无操作|减仓至 50%|清仓至 0%|

### 特殊规则处理

1. **T\+1 限制**：当天买入的股票，当天不能卖出，减仓 / 卖出操作仅能操作历史持仓

2. **涨跌停限制**：

    - 涨停时，买入委托可能无法成交，自动顺延到下一个交易日

    - 跌停时，卖出委托可能无法成交，自动顺延到下一个交易日

3. **ST 股票**：自动适配 5% 的涨跌幅限制

4. **停牌处理**：停牌股票无法交易，信号自动顺延到复牌日

---

## 模拟交易引擎

完整模拟 A 股真实交易流程，实现从委托到成交到净值的全链路模拟，让用户在实盘前充分验证策略。

### 工作流

```Plain Text
用户委托 -> 规则校验 -> 订单撮合 -> 费用计算 -> 持仓更新 -> 资金流水 -> 净值更新
```

### 核心流程

1. **委托提交**

    - 用户提交委托，支持限价单 / 市价单，买入 / 卖出

    - 校验：资金是否足够、持仓是否足够、T\+1 规则、涨跌停限制

2. **撮合处理**

    - 模拟 A 股价格优先、时间优先的撮合规则

    - 市价单：以当前盘口价格成交

    - 限价单：价格达到委托价时成交

    - 支持部分成交

3. **交易费用计算**
完全模拟真实 A 股交易费用：

    - **印花税**：卖出时收取，成交金额的 0\.1%

    - **佣金**：双向收取，最低 5 元，默认 0\.03%

    - **过户费**：双向收取，成交金额的 0\.002%

4. **持仓与资金更新**

    - 更新持仓股数、成本价、可用仓位

    - 记录资金流水，每一笔变动都可追溯

    - 收盘后计算当日总资产、日收益、累计净值

5. **每日快照**
每个交易日收盘后，自动生成账户净值快照，记录：

    - 当日总资产

    - 日收益率

    - 累计净值

    - 最大回撤

---

## 多因子打分模块

参考 Qlib 的因子计算范式，轻量实现多因子选股模型，支持因子有效性分析与股票打分排名。

### 1\. 因子体系

内置四大类常用因子，覆盖估值、成长、质量、动量维度：

|因子类型|因子列表|
|---|---|
|估值因子|PE、PB、PS、PCF、PEG|
|成长因子|营收增速、净利润增速、ROE、ROA|
|质量因子|资产负债率、现金流比率、毛利率|
|动量因子|1 月反转、3 月动量、6 月动量、换手率|

### 2\. 因子计算

- **简单因子**：直接通过 PostgreSQL 聚合计算，利用数据库性能

- **复杂因子**：提交到 Celery Worker 异步计算，参考 Qlib 的因子表达式

- **标准化**：对因子值进行横截面标准化，消除量纲影响

### 3\. 因子分析

自动计算因子的有效性指标，帮助用户筛选有效因子：

- **IC（信息系数）**：因子值与下期收益的相关系数

- **IR（信息比率）**：IC 的均值除以 IC 的标准差

- **ICIR**：信息系数的信息比率，衡量因子的稳定性

### 4\. 打分排名

对全市场股票进行多因子加权打分，生成选股排名：

1. 对每个因子，对股票进行排序打分

2. 根据用户配置的因子权重，计算综合得分

3. 生成股票排名，topN 即为推荐买入标的

4. 每日自动更新，支持动态调仓

---

## 前端页面与组件规划

基于 React \+ Vite \+ Tailwind CSS \+ shadcn/ui 构建现代化前端界面，提供流畅的用户体验。

### 页面规划

1. **首页 Dashboard**

    - 大盘概览：指数行情、涨跌分布

    - 自选股看板：实时行情、涨跌幅

    - 策略概览：我的策略、最近信号

    - 模拟账户概览：资产、收益

2. **股票池页面**

    - 全市场股票列表，支持筛选、搜索

    - ST / 退市状态标识

    - 动态筛选器，支持多条件组合筛选

    - 股票详情：K 线、基本面、指标

3. **自选股页面**

    - 分组管理，支持自定义分组

    - 自选股实时行情看板

    - 批量操作：添加 / 删除 / 分组移动

4. **策略中心**

    - 策略列表：我的策略、模板策略

    - **策略编辑器**：Monaco Editor，内置 MyTT 函数提示

    - 回测配置：参数设置、时间范围、初始资金

    - 回测结果：绩效指标、净值曲线、交易记录

5. **信号中心**

    - 五档信号列表，按日期 / 股票筛选

    - 信号历史记录

    - 信号统计：胜率、收益分布

6. **模拟交易**

    - 账户信息：资产、现金、收益

    - 持仓管理：当前持仓、浮动盈亏

    - 委托管理：委托单、撤单

    - 成交记录：历史成交、费用明细

    - 资金流水：完整资金变动记录

    - 净值曲线：账户收益走势

7. **因子选股**

    - 因子列表：因子详情、IC/IR 分析

    - 股票打分排名：全市场股票得分排名

    - 因子权重配置：自定义因子权重

    - 调仓记录：历史调仓记录

8. **系统设置**

    - 数据更新：手动 / 自动更新配置

    - 账户管理：多账户切换

    - 系统状态：数据状态、任务状态

    - 告警配置：监控告警设置

### 核心组件

- **Monaco Editor**：策略代码编辑，内置 MyTT 函数自动补全

    - 提取 MyTT 所有函数列表，注册为 Monaco 补全项

    - 支持函数参数提示、文档说明

    - 语法高亮、错误提示

- **TradingView Lightweight Charts**：K 线、净值曲线可视化，高性能

- **实时行情组件**：WebSocket 推送，自动更新价格

- **数据表格**：支持排序、筛选、分页的高性能表格

---

## Docker Compose 部署配置

一键部署所有服务，无需复杂的环境配置，用户仅需执行`docker\-compose up \-d`即可启动整个平台。

```yaml
version: '3.8'

services:
  # PostgreSQL 数据库
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: leek
      POSTGRES_PASSWORD: leek_quant_2024
      POSTGRES_DB: leek_quant
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U leek -d leek_quant"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis 缓存/队列/广播
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # FastAPI 后端服务
  fastapi:
    build:
      context: ./backend
      dockerfile: Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://leek:leek_quant_2024@postgres:5432/leek_quant
      REDIS_URL: redis://redis:6379/0
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app
    command: uvicorn main:app --host 0.0.0.0 --port 8000

  # Celery Worker 异步任务处理
  celery_worker:
    build:
      context: ./backend
      dockerfile: Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://leek:leek_quant_2024@postgres:5432/leek_quant
      REDIS_URL: redis://redis:6379/0
    volumes:
      - ./backend:/app
    command: celery -A worker worker --loglevel=info

  # Celery Beat 定时任务
  celery_beat:
    build:
      context: ./backend
      dockerfile: Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql://leek:leek_quant_2024@postgres:5432/leek_quant
      REDIS_URL: redis://redis:6379/0
    volumes:
      - ./backend:/app
    command: celery -A worker beat --loglevel=info

  # React 前端服务
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    depends_on:
      fastapi:
        condition: service_started
    ports:
      - "80:80"
    volumes:
      - ./frontend:/app
      - /app/node_modules

volumes:
  postgres_data:
  redis_data:
```

---

## 开发里程碑

|阶段|内容|预计时间|
|---|---|---|
|1|基础环境搭建 \+ 数据拉取与 K 线存储（含增量更新）|1 周|
|2|股票池管理 \+ 自选股 API \+ 前端基础页面|1 周|
|3|策略编辑器 \+ Hikyuu 回测集成（A 股规则适配）|2 周|
|4|五档信号日志 \+ 完整模拟交易模块|2 周|
|5|多因子打分（因子计算、IC 分析、排行榜）\+ 实时行情推送 \+ 前端可视化|2 周|
|6|模拟交易净值曲线、参数敏感性分析、多账户优化、文档完善|1 周|

**总预计时间：9 周**

---

## 风险与应对措施

|风险点|影响|应对措施|
|---|---|---|
|数据源 IP 被封|无法拉取数据|1\. 增加请求间隔，错峰更新<br>2\. 多数据源自动切换<br>3\. 代理 IP 备用方案<br>4\. 本地缓存已拉取数据|
|实时行情断线|无法获取实时价格|1\. 自动重连机制，断线后 3s 重试<br>2\. 心跳检测，超时自动重连<br>3\. 重连后自动重新订阅标的|
|Hikyuu 版本兼容|回测引擎异常|1\. 固定 Hikyuu 版本号<br>2\. 适配层封装，隔离内部变化<br>3\. 单元测试覆盖核心功能|
|数据一致性问题|数据错误影响回测|1\. 增量更新前校验数据<br>2\. 事务性写入，保证原子性<br>3\. 数据清洗，过滤异常值<br>4\. 定期数据校验任务|
|性能瓶颈|大数量下查询慢|1\. 分区表优化，按年拆分 K 线数据<br>2\. 索引优化，覆盖常用查询<br>3\. Redis 缓存热点数据<br>4\. 异步任务处理耗时操作|
|磁盘空间增长|数据量过大|1\. 自动清理过期的临时数据<br>2\. 数据压缩，减少存储占用<br>3\. 支持用户配置数据保留期限|
|并发任务过载|系统卡顿|1\. Celery 任务队列限流<br>2\. 任务优先级，保证核心任务优先<br>3\. 资源监控，过载时自动限流|

> （注：文档部分内容可能由 AI 生成）
