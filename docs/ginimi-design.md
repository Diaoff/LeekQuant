这是为您准备的 **Leek Quant** 纯A股量化交易平台技术架构与开发文档。该文档旨在作为项目的“真理来源”，可直接用于指导开发或同步至其他大模型进行任务拆解。
# Leek Quant 开发架构文档 (v1.0)
## 1. 系统架构设计
Leek Quant 采用前后端分离、微服务化部署的架构，核心逻辑遵循“本地优先”与“高性能回测”原则。
### 1.1 逻辑架构
 * **接入层**：Nginx 转发，支持 RESTful API 与 WebSocket 实行情。
 * **应用层 (FastAPI)**：处理业务逻辑、策略CRUD、账户管理、信号查询。
 * **任务层 (Celery + Redis)**：
   * **Worker**: 执行耗时的回测（Hikyuu）、因子计算、数据清洗。
   * **Beat**: 负责每日收盘后增量数据拉取、定时信号生成。
 * **计算内核**：
   * **Hikyuu (C++)**: 负责 A 股 T+1 规则下的高性能回测。
   * **MyTT**: 策略层快速指标计算。
 * **持久层 (PostgreSQL)**：统一存储市场数据、因子、用户策略及模拟交易记录。
## 2. PostgreSQL 数据库详细设计
采用模块化设计，重点强化模拟交易的会计完整性。
### 2.1 市场与基础数据
 * stock_basic: 存储代码、名称、上市日期、**is_st (布尔)**、**is_delisted (布尔)**。
 * daily_kline: 字段包括 code, trade_date, open, close, high, low, volume, amount, adj_factor。
   * *优化*：按 trade_date 进进行年分区。
 * trade_calendar: 存储 A 股交易日及其开市状态。
### 2.2 模拟交易 6 表体系 (核心)
 1. sim_accounts: account_id, user_id, init_cash, avail_cash, frozen_cash, total_asset
 2. sim_positions: account_id, code, quantity, frozen_qty (T+1锁定), cost_price, current_price
 3. sim_orders: order_id, account_id, code, direction (买/卖), type (限价/市价), status (待成交/撤单/已成), price, qty
 4. sim_trades: trade_id, order_id, price, qty, amount, fee_commission, fee_tax (印花税), fee_transfer (过户费)
 5. sim_cash_flow: flow_id, account_id, type (交易/分红), delta_cash, balance, ref_id
 6. sim_daily_nav: account_id, date, total_asset, daily_return, cumulative_nav
## 3. 核心模块详细设计
### 3.1 数据源三层回退与增量拉取
**逻辑流程：**
 1. **增量判定**：检查 daily_kline 中特定 code 的 max(trade_date)。
 2. **Tier 1 (AData)**：首选 AData 获取最新日线与基础信息。
 3. **Tier 2 (Baostock)**：若 AData 接口超时或返回空，切至 Baostock 获取基本面及补全 K 线。
 4. **Tier 3 (AkShare)**：若上述失败，调用 AkShare（爬虫机制）作为兜底确保数据完整。
 5. **数据落库**：统一清洗为标准 DataFrame，批量写入 PostgreSQL。
### 3.2 Hikyuu 回测引擎集成方案
 * **适配层设计**：开发 LeekHikyuuAdapter。
 * **规则注入**：在 Hikyuu 的 TradeManager 中显式配置：
   * set_buy_delay(True) (次日买入，模拟 T+1)。
   * set_sell_delay(True) (次日卖出)。
   * 注入 CostStar（手续费模型）：设置印花税（卖出 0.1%）、佣金（万 2.5，最低 5 元）。
 * **数据对接**：编写 PostgreSQLToKData 驱动，让 Hikyuu 直接读取本地数据库。
### 3.3 五档信号状态机逻辑
策略输出 SignalType，后端通过状态机映射为具体动作：

| 当前持仓 | 策略输出 | 动作 | 模拟交易执行 |
| :--- | :--- | :--- | :--- |
| 空仓 | 观望 / 卖出 / 减仓 | 无操作 | 无 |
| 空仓 | 买入 / 增持 | **买入** | 全仓或预设比例买入 |
| 有仓 | 增持 | **增持** | 加码至目标仓位 |
| 有仓 | 减仓 | **减仓** | 卖出 50% 或减至目标仓位 |
| 有仓 | 卖出 | **卖出** | 清仓 (受 T+1 限制) |
| 有仓 | 观望 | **观望** | 继续持有 |

### 3.4 多因子打分模块 (Ref: Qlib)
 1. **因子定义**：支持类 Qlib 表达式（如 Rank((Close-Open)/Open)）。
 2. **计算引擎**：利用 MyTT 进行向量化计算，Celery 异步处理。
 3. **标准化**：对因子进行 **去极值 (Winsorize)** 和 **标准化 (Z-Score)**。
 4. **排名逻辑**：Score = Σ (Factor_i * Weight_i)，输出全市场 Top N 股票池。
## 4. 前端组件与交互规划
 * **看板**：使用 shadcn/ui 的 Dashboard 模板，展示总资产、日收益、持仓占比（饼图）。
 * **行情图表**：Lightweight Charts 集成 MyTT 指标线。
 * **代码编辑**：Monaco Editor 配置 Python 语法高亮，通过 extraLibs 提供 MyTT 与 Hikyuu 的 API 自动补全。
 * **实时推送**：WebSocket 连接后端 Redis，当行情变动触发 signal_log 时，前端 Toast 强提醒。
## 5. Docker Compose 部署配置
```yaml
services:
  db:
    image: postgres:15-alpine
    volumes: - ./data/postgres:/var/lib/postgresql/data
  redis:
    image: redis:7-alpine
  backend:
    build: ./backend
    command: uvicorn main:app --host 0.0.0.0
    depends_on: [db, redis]
  worker:
    build: ./backend
    command: celery -A tasks.celery_app worker
  beat:
    build: ./backend
    command: celery -A tasks.celery_app beat
  frontend:
    build: ./frontend
    ports: - "3000:80"
```
## 6. 风险与应对措施
 * **数据一致性风险**：三层数据源可能存在复权因子微小差异。
   * *应对*：以 AData 为准，切换数据源时强制重新计算历史复权。
 * **模拟交易撮合误差**：WebSocket 实时价与实际成交价有滑点。
   * *应对*：模拟交易提供“收盘价撮合”与“最新价撮合”两种模式。
 * **内存压力**：全市场因子计算消耗内存。
   * *应对*：利用 PostgreSQL 进行初步数据过滤，Celery Worker 分批次计算。
## 7. 开发里程碑
 1. **M1 (数据基座)**：完成 Docker 部署及 A 股全历史 K 线增量同步。
 2. **M2 (策略核心)**：集成 Monaco 编辑器，调通 Hikyuu 简单回测流程。
 3. **M3 (交易系统)**：完成模拟交易 6 表逻辑及 T+1 撮合引擎。
 4. **M4 (智能增强)**：上线多因子打分系统与 WebSocket 实时看板。
**提示**：本平台仅供量化研究与模拟交易使用，实盘交易请遵循相关法律法规。