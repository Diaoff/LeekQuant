"""Add database table and column comments.

Revision ID: 202605200001
Revises: 202605180003
Create Date: 2026-05-20 00:01:00
"""
from collections.abc import Sequence

from alembic import op

revision: str = "202605200001"
down_revision: str | None = "202605180003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_COMMENTS = {
    "users": "本地用户表，保存轻量账号身份与启用状态",
    "stock_basic": "A股股票基础信息表，保留上市、退市、交易所和市场板块信息",
    "trade_calendar": "A股交易日历表，作为所有交易日判断的唯一来源",
    "daily_kline": "A股日K线行情表，按交易日期分区存储",
    "data_update_state": "数据同步状态表，记录各类数据源同步成功和失败状态",
    "task_runs": "后台任务执行记录表，保存任务入参、结果和耗时",
    "alert_events": "系统告警事件表，记录数据同步和运行异常",
    "stock_fundamentals": "股票基本面指标表，按股票和报告日期保存估值及财务快照",
    "watchlist": "用户自选股表，支持分组、排序和备注",
    "stock_pools": "股票池定义表，保存静态或动态筛选条件",
    "stock_pool_items": "股票池成分表，保存股票池与股票的关联及评分原因",
    "strategies": "策略定义表，保存用户策略源码、配置、版本和状态",
    "backtest_results": "回测结果表，保存回测参数、指标、交易记录和净值曲线",
    "signal_log": "策略信号日志表，记录五档信号及目标仓位",
}


COLUMN_COMMENTS = {
    "users": {
        "id": "用户主键",
        "username": "登录用户名，本地唯一",
        "password_hash": "密码哈希；本地免登录用户使用占位值",
        "display_name": "页面显示名称",
        "is_active": "用户是否启用",
        "created_at": "记录创建时间",
        "updated_at": "记录最后更新时间",
    },
    "stock_basic": {
        "ts_code": "标准股票代码，格式如 600000.SH / 000001.SZ",
        "symbol": "6位股票数字代码",
        "name": "股票简称",
        "market": "市场板块，如 主板 / 创业板 / 科创板 / 北交所",
        "exchange": "交易所代码，SSE 表示上交所，SZSE 表示深交所",
        "industry": "所属行业",
        "area": "公司所在地区",
        "list_date": "上市日期",
        "delist_date": "退市日期；为空表示未退市或未知",
        "is_st": "是否 ST 或 *ST 股票",
        "is_delisted": "是否已退市",
        "data_source": "股票基础信息来源，如 adata / baostock / akshare",
        "created_at": "记录创建时间",
        "updated_at": "记录最后更新时间",
    },
    "trade_calendar": {
        "cal_date": "自然日期",
        "is_open": "是否为A股交易日",
        "pretrade_date": "上一个交易日",
        "nexttrade_date": "下一个交易日",
        "is_weekend": "是否周末",
        "is_holiday": "是否工作日假期或非交易日",
        "source": "交易日历数据来源",
        "updated_at": "记录最后更新时间",
    },
    "daily_kline": {
        "ts_code": "标准股票代码，关联 stock_basic.ts_code",
        "trade_date": "交易日期",
        "open": "开盘价，单位元",
        "high": "最高价，单位元",
        "low": "最低价，单位元",
        "close": "收盘价，单位元",
        "pre_close": "前收盘价，单位元",
        "volume": "成交量，单位股",
        "amount": "成交额，单位元",
        "turnover_rate": "换手率，小数或数据源原始比例",
        "adj_factor": "复权因子；同步时保留已有非空值",
        "is_suspended": "当日是否停牌",
        "is_limit_up": "当日是否涨停",
        "is_limit_down": "当日是否跌停",
        "data_source": "K线数据来源",
        "raw_payload": "原始数据源返回内容，便于排查字段映射",
        "created_at": "记录创建时间",
        "updated_at": "记录最后更新时间",
    },
    "data_update_state": {
        "id": "同步状态主键",
        "data_type": "数据类型，如 stock_basic / daily_kline / trade_calendar",
        "ts_code": "股票代码；全局任务为空",
        "source": "数据源名称",
        "last_trade_date": "该数据源已成功同步到的最新交易日",
        "last_success_at": "最近一次同步成功时间",
        "last_failure_at": "最近一次同步失败时间",
        "failure_count": "连续失败次数",
        "error_message": "最近一次失败的错误信息",
        "updated_at": "记录最后更新时间",
    },
    "task_runs": {
        "id": "任务运行记录主键",
        "task_name": "任务名称",
        "task_id": "Celery 或外部任务ID",
        "status": "任务状态：pending/running/success/failed/cancelled",
        "started_at": "任务开始时间",
        "finished_at": "任务结束时间",
        "duration_ms": "任务耗时，单位毫秒",
        "payload": "任务输入参数快照",
        "result": "任务输出结果快照",
        "error_message": "任务失败错误信息",
    },
    "alert_events": {
        "id": "告警事件主键",
        "level": "告警级别：info/warning/error/critical",
        "category": "告警分类",
        "title": "告警标题",
        "message": "告警详情",
        "payload": "告警上下文数据",
        "is_resolved": "告警是否已处理",
        "created_at": "告警创建时间",
        "resolved_at": "告警处理时间",
    },
    "stock_fundamentals": {
        "ts_code": "标准股票代码，关联 stock_basic.ts_code",
        "report_date": "报告日期或指标归属日期",
        "announce_date": "公告日期",
        "pe_ttm": "滚动市盈率 TTM",
        "pb": "市净率",
        "ps_ttm": "滚动市销率 TTM",
        "pcf_ttm": "滚动市现率 TTM",
        "roe": "净资产收益率",
        "roa": "总资产收益率",
        "market_cap": "总市值，单位元",
        "float_market_cap": "流通市值，单位元",
        "dividend_yield": "股息率",
        "revenue": "营业收入，单位元",
        "net_profit": "净利润，单位元",
        "revenue_growth": "营收同比增长率",
        "net_profit_growth": "净利润同比增长率",
        "gross_margin": "销售毛利率",
        "debt_to_equity": "资产负债率或债务权益比，按数据源口径",
        "current_ratio": "流动比率",
        "free_cash_flow": "自由现金流，单位元",
        "income_statement": "利润表原始或扩展字段 JSON",
        "balance_sheet": "资产负债表原始或扩展字段 JSON",
        "cashflow_statement": "现金流量表原始或扩展字段 JSON",
        "data_source": "基本面数据来源",
        "created_at": "记录创建时间",
        "updated_at": "记录最后更新时间",
    },
    "watchlist": {
        "id": "自选股记录主键",
        "user_id": "所属用户ID",
        "group_name": "自选股分组名称",
        "ts_code": "标准股票代码，关联 stock_basic.ts_code",
        "sort_order": "组内排序值，越小越靠前",
        "note": "用户备注",
        "added_at": "加入自选股时间",
        "updated_at": "记录最后更新时间",
    },
    "stock_pools": {
        "id": "股票池主键",
        "user_id": "所属用户ID",
        "name": "股票池名称",
        "description": "股票池说明",
        "filters": "动态股票池筛选条件 JSON",
        "is_dynamic": "是否按 filters 动态重建成分",
        "last_built_at": "最近一次重建股票池时间",
        "created_at": "记录创建时间",
        "updated_at": "记录最后更新时间",
    },
    "stock_pool_items": {
        "pool_id": "股票池ID，关联 stock_pools.id",
        "ts_code": "标准股票代码，关联 stock_basic.ts_code",
        "score": "股票在池中的评分",
        "reason": "入池原因、因子明细或筛选解释 JSON",
        "added_at": "加入股票池时间",
    },
    "strategies": {
        "id": "策略主键",
        "user_id": "策略所属用户ID",
        "pool_id": "默认股票池ID，可为空",
        "name": "策略名称",
        "description": "策略说明",
        "source_code": "策略 Python 源码",
        "config": "策略配置 JSON，如参数、风控和运行设置",
        "version": "策略版本号",
        "status": "策略状态：draft/active/paused/archived",
        "created_at": "记录创建时间",
        "updated_at": "记录最后更新时间",
        "archived_at": "策略归档时间",
    },
    "backtest_results": {
        "id": "回测结果主键",
        "user_id": "回测所属用户ID",
        "strategy_id": "关联策略ID；策略删除后置空",
        "pool_id": "回测使用的股票池ID；股票池删除后置空",
        "task_id": "异步回测任务ID",
        "start_date": "回测开始日期",
        "end_date": "回测结束日期",
        "initial_cash": "回测初始资金，单位元",
        "benchmark_code": "基准指数或股票代码",
        "params_snapshot": "回测参数快照 JSON",
        "total_return": "总收益率，小数表示",
        "annual_return": "年化收益率，小数表示",
        "sharpe_ratio": "夏普比率",
        "max_drawdown": "最大回撤，小数表示",
        "annual_vol": "年化波动率，小数表示",
        "win_rate": "胜率，小数表示",
        "trade_count": "交易笔数",
        "performance": "完整绩效指标 JSON",
        "trade_records": "交易明细 JSON 数组",
        "equity_curve": "账户净值曲线 JSON 数组",
        "status": "回测状态：pending/running/success/failed/cancelled",
        "error_message": "回测失败错误信息",
        "created_at": "记录创建时间",
        "started_at": "回测开始执行时间",
        "finished_at": "回测结束执行时间",
    },
    "signal_log": {
        "id": "信号日志主键",
        "user_id": "信号所属用户ID",
        "strategy_id": "产生信号的策略ID",
        "account_id": "模拟交易账户ID；未接账户时为空",
        "ts_code": "标准股票代码，关联 stock_basic.ts_code",
        "trade_date": "信号对应交易日",
        "signal_type": "五档信号：买入/增持/减仓/卖出/观望",
        "target_position": "目标仓位比例，0 到 1",
        "current_position": "信号产生前当前仓位比例，0 到 1",
        "action": "信号映射后的执行动作",
        "confidence": "信号置信度，0 到 1",
        "reason": "信号原因说明",
        "snapshot": "信号计算上下文 JSON",
        "created_at": "记录创建时间",
    },
}


def _quote_comment(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _comment_sql(scope: str, target: str, comment: str | None) -> str:
    value = "NULL" if comment is None else _quote_comment(comment)
    return f"COMMENT ON {scope} {target} IS {value}"


def _comment_statements(clear: bool = False) -> list[str]:
    statements: list[str] = []
    for table, table_comment in TABLE_COMMENTS.items():
        statements.append(_comment_sql("TABLE", table, None if clear else table_comment))
    for table, columns in COLUMN_COMMENTS.items():
        for column, column_comment in columns.items():
            statements.append(_comment_sql("COLUMN", f"{table}.{column}", None if clear else column_comment))
    return statements


UPGRADE_STATEMENTS = _comment_statements()
DOWNGRADE_STATEMENTS = _comment_statements(clear=True)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
