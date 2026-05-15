# Leek Quant 纯A股量化交易平台技术架构与开发文档

## 引言

本报告旨在全面阐述 Leek Quant 量化交易平台的架构设计与实现细节。该平台是一款专为 A 股市场设计的、强调隐私保护和高性能体验的本地化量化交易软件。报告将从系统概览、部署架构、数据层、核心服务、回测引擎、交易执行到前端界面，逐层剖析其技术实现，并提供关键代码示例，为开发者和使用者提供一份详尽的技术蓝图。

## 一、系统概览与核心设计理念

### 1.1 项目背景与定位

Leek Quant 量化交易平台是一款专为 A 股市场设计的、强调隐私保护和高性能体验的本地化量化交易软件。其核心目标是赋能个人投资者和量化爱好者，提供一个功能完备、数据自主可控的量化研究与交易环境。

### 1.2 隐私优先与本地化架构

在金融科技领域，数据隐私和安全至关重要。Leek Quant 采用 B/S (Browser/Server) 架构，其最核心的设计理念是**隐私优先**。系统默认的 Docker 部署方案将 PostgreSQL 数据库、InfluxDB 时序数据库、Redis 缓存以及所有核心服务均运行在用户的本地计算机或私有服务器上 [18]。这种设计哲学确保了用户的核心交易数据、策略逻辑和资产信息完全处于用户的掌控之中，有效规避了公有云服务可能带来的数据泄露风险。

### 1.3 核心功能特性

本平台旨在提供从数据获取、策略研究、回测验证到模拟交易的全流程解决方案，核心功能模块包括：

| 模块 | 功能 | 关键技术/说明 |
|----|----|----|
| **数据获取** | 自动化采集 A 股历史与实时行情数据 | 集成 AKShare 开源财经数据接口，支持多周期 (日/分钟/ Tick) 数据 [30] |
| **数据存储** | 高效、可扩展的数据持久化方案 | 采用 PostgreSQL 存储关系型元数据 (如交易记录、用户信息)，InfluxDB 存储海量时序行情数据 (K线)  |
| **策略研究** | 本地化的 Python 编程环境与策略开发 | 提供基于 Python 的 SDK，支持用户自定义策略逻辑，集成 Jupyter Lab 进行探索性数据分析  |
| **回测引擎** | 高保真、低延时的策略性能验证 | 基于 Hikyuu 量化框架 C++ 核心内核，通过 pybind11 提供 Python 接口，确保回测速度接近实盘体验  |
| **模拟交易** | 贴近实盘的交易执行与撮合 | 基于五档行情 (B1-B5, A1-A5) 构建模拟撮合引擎，支持 T+0/T+1 交易规则，提供真实的交易环境 [18] |
| **前端界面** | 专业、直观的 Web 交互体验 | 采用 React 框架构建 SPA (单页应用) 页面，集成 TradingView Lightweight Charts 和 Monaco Editor，提供专业级的图表分析和代码编辑体验 [3] |

## 二、系统部署与基础设施架构

### 2.1 Docker Compose 一键部署方案

Leek Quant 利用 Docker 容器化技术，将系统解耦为多个独立的服务。这种设计不仅保证了环境的纯净性，也为用户提供了“一键启动”的便捷部署体验。

**核心组件容器化定义**：

系统的基础架构由一个 Docker Compose 文件统一编排，它定义了所有必需的服务 。

- **PostgreSQL**：作为系统的核心关系型数据库，负责存储用户信息、交易记录、策略元数据等。在 Compose 配置中，它通过环境变量进行初始化 [41]。

- **Redis**：作为内存数据库，承担缓存和消息队列的角色，用于存储会话、临时数据、任务队列信息等，保障系统的高速数据交换 [44]。

- **InfluxDB**：专为海量时序数据（如分钟级和 Tick 级行情数据）设计的数据库，确保行情数据的高效写入和查询 。

- **后端服务 (Server)**：基于 FastAPI 的 Python 后端应用容器，运行业务逻辑、API 服务和任务调度器。

- **前端服务 (Web)**：基于 Nginx 的 Web 服务器容器，负责托管和提供 React 构建的静态前端资源 。

以下是一个典型的 docker-compose.yml 文件结构示例，展示了各服务的依赖关系和配置要点：

services:
postgres:
image: postgres:15
container_name: leek-postgres
environment:
POSTGRES_DB: leek
POSTGRES_USER: root
POSTGRES_PASSWORD: 123456
ports:
- "5432:5432"
volumes:
- postgres_data:/var/lib/postgresql/data
healthcheck:
test: \["CMD-SHELL", "pg_isready -U root"\]
interval: 10s
timeout: 5s
retries:
redis:
image: redis:7.0-alpine
container_name: leek-redis
ports:
- "6379:6379"
volumes:
- redis_data:/data
healthcheck:
test: \["CMD", "redis-cli", "ping"\]
interval: 10s
timeout: 5s
retries:
influxdb:
image: influxdb:2.0
container_name: leek-influxdb
environment:
DOCKER_INFLUXDB_INIT_MODE: setup
DOCKER_INFLUXDB_INIT_USERNAME: admin
DOCKER_INFLUXDB_INIT_PASSWORD: admin123
DOCKER_INFLUXDB_INIT_ORG: leek-org
DOCKER_INFLUXDB_INIT_BUCKET: leek-bucket
ports:
- "8086:8086"
volumes:
- influxdb_data:/var/lib/influxdb2
healthcheck:
test: \["CMD", "influx", "ping"\]
interval: 10s
timeout: 5s
retries:
server:
build:
context: .
dockerfile: Dockerfile.server # 后端Dockerfile路径
container_name: leek-server
ports:
- "8080:8080"
environment:
REDIS_URL: redis://redis:6379
DATABASE_URL: postgresql://root:123456@postgres:5432/leek
INFLUXDB_URL: http://influxdb:8086
INFLUXDB_TOKEN: admin-token
depends_on:
postgres:
condition: service_healthy
redis:
condition: service_healthy
influxdb:
condition: service_healthy
volumes:
- ./server:/app # 代码热加载
web:
image: nginx:alpine
container_name: leek-web
ports:
- "80:80"
depends_on:
- server
volumes:
- ./web/dist:/usr/share/nginx/html # 前端构建产物
- ./deploy/web.conf:/etc/nginx/conf.d/default.conf # Nginx配置
volumes:
postgres_data:
redis_data:
influxdb_data:

### 2.2 服务依赖与容器编排

为了确保服务间的有序启动和稳定通信，Leek Quant 的部署架构采用了精细的依赖管理策略。

**服务启动顺序与健康检查**：

一个稳定可靠的系统依赖于正确的启动顺序。Leek Quant 的 Docker Compose 配置中，后端服务 (server) 被配置为严格依赖于 PostgreSQL、Redis 和 InfluxDB 三个存储服务，并且指定了启动条件为 `service_healthy` [4]。这意味着后端容器只会在所有数据库都成功启动并通过了健康检查（即

`pg_isready`、`redis-cli ping`、`influx ping` 返回正常）之后才会启动，有效避免了因数据库未就绪而导致的服务启动失败 。

**容器间通信**：

所有服务都被连接到一个名为 `internal` 的 Bridge 网络中 [43]。Docker Compose 会自动在该网络中为每个服务分配一个 DNS 名称，该名称与

**服务名称 (service name) 一致，并可通过 `/etc/hosts` 文件解析 。因此，后端服务可以通过

`postgres:5432`、`redis:6379`、`influxdb:8086` 这样的地址来访问数据库，而无需关心它们映射到宿主机的实际 IP 和端口 [47]。这种内置的服务发现机制是实现容器间松耦合通信的关键 。

**持久化与热重载**：

为了保证用户数据在容器重启或更新后不丢失，所有数据库服务都通过 Docker Volume 技术将其数据目录持久化到宿主机或命名卷中 。此外，为了提升开发体验，后端服务的代码目录通过

bind mount 挂载到容器内，使得开发者可以在宿主机上修改代码，变更能够立即生效 [48]。这种“持久化存储 + 代码映射”的模式，兼顾了数据安全与开发效率。

\*\*架构设计思想：从“单体”到“微服务”的演进

\*\*：

虽然在用户看来是“一键部署”，但其底层已从简单的单体应用演进为一种轻量级的微服务架构。通过将数据库、Web 服务器和应用逻辑解耦到不同的容器中，系统具备了微服务架构的核心优势：模块化和独立扩展性 [3]。例如，如果后端计算负载增加，可以独立地对该容器进行资源扩容（如分配更多 CPU 和内存），而无需重启整个数据库 。这种设计为系统的未来演进和高可用性（如部署 Redis 哨兵或 PostgreSQL 主从集群）奠定了坚实的基础。

## 三、数据层架构：存储与获取

数据层是量化交易系统的基石。Leek Quant 设计了一套双层存储架构，旨在平衡关系型数据的灵活性与时序数据的高效性。

### 3.1 PostgreSQL 核心数据表设计

PostgreSQL 作为系统的元数据中心，存储着所有非时序类的结构化数据。其核心表结构如下：

- **用户表 (users)**：存储系统用户的基本信息，包括用户名、加密后的密码、API Key 等认证凭据，以及创建时间等 。

- **策略表 (strategies)**：存储用户创建的交易策略的元数据，如策略名称、代码、运行参数、当前状态（运行中/停止）等 。

- **订单表 (orders)**：记录所有交易指令的历史，包括股票代码、价格、数量、订单类型（限价/市价）、状态（成交/撤单）等 [41]。

- **持仓表 (holdings)**：记录每个交易日的收盘后持仓快照，用于计算每日盈亏和复权 。

- **服务日志表 (service_logs)**：将服务端的运行日志持久化存储，便于后续审计和故障排查 。

- **行情数据表 (stock\_daily\_data)**：作为 InfluxDB 的补充，用于存储日级别的复权行情数据，以及多因子打分、资金流向等衍生数据，方便进行关系型查询和复杂分析 [47]。

- **代码与映射表**：维护内部代码 (如 \`000001\`) 与通用代码 (如 \`000001.SZ\`) 之间的映射关系，确保数据在不同系统间的一致性 [60]。

**数据分区策略**：

随着交易数据的日积月累，订单表和持仓表的数据量会变得非常庞大。为了保持查询性能，Leek Quant 采用了基于日期范围的数据分区策略。例如，可以按月份或季度为单位对 `orders` 表进行分区 [55]。这样，当查询最近一个月的订单时，数据库引擎只需扫描对应的分区，而非整张

\`\`\`sql\` 表，从而显著提升查询速度 。

\`\`\`sql

-- 示例：创建基于日期范围的分区表

CREATE TABLE orders (

id SERIAL,

user_id INT NOT NULL,

symbol VARCHAR(20) NOT NULL,

price NUMERIC(10, 2),

quantity INT,

order_date DATE NOT NULL,

PRIMARY KEY (id, order_date)

) PARTITION BY RANGE (order_date);

-- 创建具体分区

CREATE TABLE orders_2023_q1 PARTITION OF orders

FOR VALUES FROM ('2023-01-01') TO ('2023-04-01');

CREATE TABLE orders_2023_q2 PARTITION OF orders

FOR VALUES FROM ('2023-04-01') TO ('2023-07-01');

-- 查询时利用分区裁剪 (Partition Pruning)

SELECT \* FROM orders WHERE order_date BETWEEN '2023-02-01' AND '2023-02-28';

\`\`\`

### 3.2 多层回退数据源架构

数据的稳定性和连续性是策略研究的前提。Leek Quant 的数据获取模块被设计成一个具有容错能力的层级系统。

**首选数据源：AKShare**：

AKShare 是一个开源的财经数据接口库，它不生产数据，而是从东方财富网、新浪财经等公开平台“搬运”数据 [14]。由于其开源和免费的特性，它是个人研究者的首选。然而，其稳定性高度依赖于下游数据源的页面结构，存在因网站改版而导致接口失效的风险 [13]。

**数据源故障风险与应对**：

AKShare 并非唯一的数据源，也并非最稳定的数据源。其“开源搬运”的特性决定了它在面对反爬虫机制、网站改版等问题时较为脆弱 。针对这一固有风险，Leek Quant 采用了

**多层回退**的设计。

\*\*多层回退机制 (Fallback Mechanism) **：为了确保数据服务的连续性，系统集成了多个数据源，并定义了清晰的优先级，例如 AKShare -\> Tushare -\> Baostock。当主数据源（如 AKShare）因反爬虫或接口变更而失效时，系统会自动尝试从次优先级的备用数据源（如 Tushare 或 Baostock，需用户配置相应的 token）获取数据 [12]。这种设计极大地提升了数据采集系统的健壮性，保障了策略研究的连续性。

** 数据采集与清洗

**：

数据获取流程通常包括定义股票列表、请求原始数据、标准化格式以及处理异常值等步骤 [9]。为了保证数据的准确性，对从不同来源获取的数据进行字段映射和一致性校验是必不可少的环节 [2]。例如，将不同数据源返回的“最新价”字段统一命名为“close”，并确保价格单位一致。

### 3.3 实时行情数据流

实时行情是驱动盘中交易信号的源头。Leek Quant 的实时行情架构旨在实现低延迟和高吞吐量。

**行情获取与推送**：

系统通过一个独立的异步进程（通常是一个 Celery Beat 定时任务）定期（例如每 5 秒或更短）从 AKShare 获取最新的股票行情数据 [57]。获取到数据后，系统会先将数据存储到

InfluxDB，因为 InfluxDB 的高写入性能使其非常适合这种高频时序数据 [56]。接着，系统会通过 Redis 的发布/订阅 (Pub/Sub) 功能向所有在线的前端用户广播这一最新的行情更新 。

**前端实时展示**：

前端 React 应用通过 WebSocket 连接后端 [3]。当后端将新行情推送到 Redis 的

`stock:realtime` 频道时，订阅了该频道的前端页面会立即收到数据，并动态更新图表和报价板，为交易员提供“秒级”的行情刷新体验 。

\*\*数据架构设计思想：双引擎存储策略

\*\*：

Leek Quant 采用 PostgreSQL + InfluxDB 的双引擎存储架构，是一种针对量化数据类型进行深度优化的结果。量化系统主要处理两种性质截然不同的数据：

1.  **关系型数据**：如用户、订单、持仓，这类数据强调一致性、关联性和事务完整性，是系统的“配置”和“结果”。PostgreSQL 的强事务支持和复杂查询能力，使其成为存储此类数据的完美选择 。

2.  **时序数据**：如分钟 K 线、Tick 数据，这类数据的特点是写入频率极高（可能每秒数万条）、单向追加、极少更新、查询通常按时间范围进行。它们是系统的“输入”。InfluxDB 这类时序数据库在存储结构、压缩和查询优化层面都针对时序数据的特点进行了深度适配，无论是写入性能还是存储效率，都远胜于 PostgreSQL [4]。

将关系型数据与时序数据物理隔离存储，避免了两种数据结构之间的“水土不服”，确保了系统在高并发、大数据量场景下的整体性能和可扩展性。

## 四、应用层核心服务

应用层是系统的业务逻辑中枢，负责处理 HTTP 请求、管理用户会话、调度后台任务以及与数据层进行交互。

### 4.1 基于FastAPI的RESTful API服务

Leek Quant 的后端 API 服务基于 FastAPI 框架构建，并利用 Pydantic 进行数据验证，SQLAlchemy 进行数据库操作 。

**核心依赖注入与请求验证**：

系统使用 Pydantic 模型严格定义所有 API 端点的请求参数和响应格式。以下是一个创建策略的 Pydantic 模型示例，展示了如何对策略名称、代码和运行参数进行结构化和验证：

from pydantic import BaseModel, Field

class StrategyCreateRequest(BaseModel):
name: str = Field(..., max_length=50) # 策略名称，必填，最长50字符
code: str = Field(..., min_length=10) # 策略 Python 代码，必填
parameters: dict = Field(default_factory=dict) # 运行参数字典，默认为空
is_active: bool = Field(default=False) # 是否激活，默认为否

**认证与中间件**：

为了保证 API 的安全性，系统集成了 JWT (JSON Web Token) 认证机制 [45]。用户登录后，后续的每个需要权限的 API 请求都需要在

HTTP Header 中携带这个 Token，后端通过一个 `authenticate_user` 依赖来验证 Token 并获取当前用户信息 。同时，中间件层负责处理 CORS 跨域请求，允许前端应用与后端 API 进行通信 。

**服务层与CRUD操作**：

业务逻辑被封装在服务层（Service Layer） 。与数据库的直接交互则通过 SQLAlchemy 的 ORM 实现，并被封装在 CRUD 模块中 。以下是一个简化的用户创建服务函数示例，展示了典型的数据库会话管理和事务处理模式：

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from .models import User
from .schemas import UserCreateRequest, UserResponse
from .security import get_password_hash

# 创建用户服务函数

async def create_user(db: AsyncSession, user_in: UserCreateRequest) -\> UserResponse:
# 检查用户是否已存在
db_user = await db.execute(select(User).where(User.username == user_in.username))
if db_user.scalar_one_or_none():
raise HTTPException(status_code=400, detail="用户名已存在")

# 创建新用户实例
user = User(
username=user_in.username,
hashed_password=get_password_hash(user_in.password),
email=user_in.email,
)
db.add(user)
await db.commit() # 提交事务
await db.refresh(user)
return UserResponse.model_validate(user) # 转换为响应模型

### 4.2 异步任务调度与执行框架

对于耗时较长的操作（如从网络下载大量历史数据），Leek Quant 采用异步任务队列来避免阻塞主 API 线程，从而保证系统的响应速度。

**核心组件：Celery + Redis**：

系统采用 Celery 作为分布式任务队列，Redis 作为其消息代理（Broker）和结果后端（Result Backend） 。Celery Beat 被用来调度周期性任务，而 Celery Worker 则在后台异步执行具体的任务代码 [44]。

\*\*实现模式：从同步到异步的解耦

\*\*：

一个典型的同步 API 调用（如创建订单）可能会直接写入数据库然后返回，这个过程是连续的、短暂的 。但对于数据获取这类任务，同步执行则不可行，因为它可能需要等待数秒甚至数分钟的网络 IO。

通过引入 Celery，系统实现了从同步到异步的解耦。当用户通过 API 发起“获取股票A过去10年日K线数据”的请求时，后端 API 不会自己直接去执行这个耗时任务，而是将该任务描述（即函数名和参数）放入 Redis 队列，然后立即返回一个确认响应，例如“任务已提交，正在处理中”。一个独立的 Celery Worker 进程会不断地从队列中取出任务并执行，完成后将结果存储到数据库中。前端可以通过另一个 API 来查询任务的执行状态和结果。这种模式使得 Web API 变得轻盈且响应迅速，而耗时任务则由后台的 Worker 进程异步处理，极大地提升了整个系统的吞吐量和用户体验 [3]。

## 五、回测与策略引擎

如果说数据层是粮食仓库，那么回测与策略引擎就是将粮食（数据）加工成面包（交易信号）的工厂。Leek Quant 的核心竞争力在于其高性能的回测能力，这主要得益于其与 Hikyuu 框架的深度集成。

### 5.1 Hikyuu内核集成方案

Hikyuu 是一个为 A 股回测而生的开源框架，其核心计算引擎采用 C++ 编写，专为处理海量行情数据而优化 [36]。Leek Quant 利用 pybind11 这一工具，将 Hikyuu 的 C++ 核心功能以 Python 模块的形式无缝暴露出来 。这意味着策略研究员可以使用 Python 语言编写策略逻辑，但在回测执行时，底层却能享受到 C++ 带来的极致性能。

**Hikyuu核心概念映射**：

为了理解 Leek Quant 的策略执行流程，需要了解几个 Hikyuu 的核心概念 ：

- **Stock**：代表一只特定的证券，如 `sh.600000`。

- **KData**：K 线数据，是进行技术分析的基础。

- **Indicator**：技术指标，如均线（MA）、MACD 等。Hikyuu 内置了丰富的指标库，并且计算速度极快 [38]。

- **Signal**：交易信号生成器，负责根据技术指标产生买入或卖出信号。

- **System**：交易系统，是 Hikyuu 的核心。它将一个完整的交易流程组件化，包括信号指示、资金管理、止损止盈等。开发者可以像搭积木一样组合这些组件，快速构建复杂的交易策略 [36]。

**回测性能优势**：

传统的纯 Python 回测框架（如 Backtrader）在处理全市场股票的历史数据时，往往需要数秒甚至数分钟才能完成一个策略的回测 [36]。而 Leek Quant 集成的

Hikyuu 内核则可以将这个时间缩短到毫秒级 [10]。这种性能的飞跃，使得进行大规模参数优化、全市场扫描以及复杂的滚动回测变得切实可行，极大地提升了策略研究的效率 [39]。

### 5.2 回测流程与结果管理

在 Leek Quant 中，策略回测是一个被精心编排的标准化流程。

**标准化回测工作流**：

1.  **数据加载**：根据用户设定的时间范围和股票代码，从 InfluxDB 中加载相应的 K 线数据到内存，并以 Hikyuu 的 `KData` 格式提供给引擎 [33]。

2.  **引擎初始化**：创建一个 Hikyuu `System` 实例，并根据用户在策略中定义的逻辑，为其装配相应的组件，如信号策略、资金管理策略、止损策略等 。

3.  **策略执行**：调用 `System.run()` 方法，引擎会遍历 K 线数据，逐 K 线地驱动技术指标计算和交易信号生成 [34]。

4.  **订单生成与撮合**：当信号触发时，系统会生成一个订单（Order），其中包含股票代码、价格、数量等信息。这个订单随后会被送入一个与模拟交易模块共用的撮合引擎进行成交判断。

5.  **性能评估**：回测结束后，系统会自动生成一份详尽的性能报告，包含累计收益率、年化收益率、夏普比率、最大回撤、胜率等数十项关键统计指标 。

**Hikyuu System 组件装配示例**：

Hikyuu 的魅力在于其组件化设计。以下 Python 代码示例展示了如何装配一个简单的双均线交叉交易系统。这个例子清晰地体现了 Hikyuu “搭积木”式的开发范式 ：

import hikyuu as hk

# 1. 定义策略参数

init_cash = 100000

fast_n =[20] # 快线周期

slow_n =[30] # 慢线周期

# 2. 创建技术指标 (使用 C++ 加速)

fast_ma = hk.MA(fast_n, hk.Colour.RED) # 创建快线均线

slow_ma = hk.MA(slow_n, hk.Colour.BLUE) # 创建慢线均线

# 3. 创建信号策略

# 当快线上穿慢线时买入，下穿时卖出

my_signal = hk.SG_Cross(fast_ma, slow_ma)

# 4. 创建资金管理策略 (固定数量买入)

my_money = hk.MM_FixedCount(1000, 1000) # 每次买入1000元，最大持股10000元

# 5. 创建止损策略 (固定百分比止损)

my_stop = hk.ST_FixedPercent(0.05) # 亏损5%止损

# 6. 组合成交易系统

my_system = hk.SYS_Simple(

signal=my_signal,

money=my_money,

stoploss=my_stop,

# 设置回测账户初始资金

tm=hk.TradeManager(cash=init_cash),

)

# 7. 运行回测

stock = hk.getStock("sh.600000") # 获取股票

query = hk.Query(20200101, 20231231) # 设置回测时间范围

my_system.run(stock, query) # 启动回测

# 8. 输出性能报告

my_system.performance().report() # 打印详细的回测报告

**回测结果与报告**：

回测性能报告会详细展示交易明细、资金曲线和风险指标 。用户可以在 Leek Quant 的前端界面上查看这些报告，并可以将结果导出或与团队成员分享 。这种标准化的流程确保了策略评估的客观性和可重复性。

\*\*架构设计思想：策略引擎的“即插即用”

\*\*：

Leek Quant 通过集成 Hikyuu，不仅提供了一个快速的回测引擎，更重要的是引入了一套**系统化**的策略开发哲学。它将一个交易策略分解为多个正交的、可复用的组件（信号、资金、止损等）。开发者不再是写一个庞大而耦合的 `if-else` 策略脚本，而是为策略选择不同的“零件”。

这种模块化的设计极大地促进了策略的**标准化**和**可维护性**。例如，一个验证有效的止损策略可以被多个不同的信号策略共用。团队可以像建立零件库一样积累策略组件，新策略的开发速度将得到显著提升。这正是 Leek Quant 试图通过集成 Hikyuu 来实现的“工业化”策略研究能力。

## 六、交易与信号执行

交易执行层是连接策略信号与实际资金变动的桥梁。Leek Quant 的模拟交易系统设计高度还原了真实交易市场的规则和流程，旨在为策略验证提供一个逼真且可控的环境。

### 6.1 五档信号生成与状态机逻辑

交易信号是策略逻辑的输出，但在订单生成之前，系统需要对信号进行标准化和过滤，以确保其有效性和合规性。

**标准化交易信号**：

一个策略逻辑生成的原始信号通常是包含股票代码、买卖方向、目标仓位或数量的“指令” 。在执行前，Leek Quant 会将这些指令进行标准化处理，确保指令包含确切的价格、数量、订单类型（如市价单、限价单）等必要信息，以满足后续撮合和风控的需求 [3]。

**A股特定规则过滤**：

A 股市场有严格的交易规则，例如价格涨跌幅限制（涨跌停板）、交易时间、T+1 交易制度（当日买入的股票，下一交易日才能卖出）等 [26]。信号过滤器会剔除那些不符合规则的信号，例如在当前价格已经涨停时发出的市价买入信号，或者在非交易时段（如下午3点后）产生的任何交易信号 [27]。

**有限状态机 (FSM) 的应用**：

为了确保订单执行逻辑的严谨性，特别是在处理止损、止盈或追踪止损等复杂逻辑时，Leek Quant 借鉴了有限状态机（FSM）的模型 [19]。

- **状态定义**：例如，一个止损单在被触发前处于 **ACTIVE** (激活) 状态；当价格触及止损价时，状态变为 **TRIGGERED** (已触发)，并生成一个市价卖单；如果用户在此之前手动取消了止损指令，则状态变为 **CANCELLED** (已取消) [28]。

- **状态转移**：FSM 明确定义了每种状态可以合法转移到哪些其他状态。这种设计从根本上杜绝了非法状态转移导致的逻辑错误，例如避免了同一个止损单被触发两次。

### 6.2 模拟交易引擎完整工作流

模拟交易引擎的核心任务是撮合订单，并在此过程中模拟真实的市场深度和价格发现机制。

**基于订单簿的撮合逻辑**：

与简单的“收盘价撮合”不同，Leek Quant 的模拟引擎基于实时更新的**订单簿 (Order Book)** 进行撮合 [30]。

- **五档行情**：引擎维护着一个股票的最新五档买卖报价（B1-B5 为买盘，A1-A5 为卖盘）以及对应的挂单量 [29]。

- **撮合算法**：当一个新订单到来时，引擎会根据订单类型进行匹配。

  - **市价单**：会以当前最优的卖一价（A1）买入，或最优的买一价（B1）卖出。

  - **限价单**：只有当订单价格优于（买入时）或等于（卖出时）对手盘的最优价格时，才会立即成交。否则，该订单会被放入订单簿中，等待新的对手盘订单来匹配。

**完整交易执行流程**：

1.  **信号接收**：执行引擎收到来自回测引擎或实盘策略模块的标准交易信号。

2.  **事前风控检查**：在执行订单前，系统会进行一系列检查，包括可用资金/证券是否充足、是否违反 T+1 规则、下单价格是否偏离市价过远（防乌龙指）等 [27]。

3.  **订单生成与提交**：检查通过后，订单被正式创建。订单的生命周期状态从 **NEW** 开始。

4.  **模拟撮合**：根据当前订单簿状态，执行撮合逻辑。

5.  **成交处理与状态更新**：如果发生成交，引擎会更新订单状态为 **FILLED** (全部成交) 或 **PARTIALLY_FILLED** (部分成交)，并实时更新用户的虚拟持仓和资金账户 。

6.  **结果反馈**：成交结果会通过事件总线广播出去，并被记录到数据库 。

**撮合算法伪代码示例**：

以下是一个简化的基于订单簿的市价单撮合算法伪代码，展示了核心逻辑：

def match_market_order(order, order_book):
"""

:param order: Order 对象 (side: 'BUY' or 'SELL', volume)
:param order_book: 字典，包含 'BID' 和 'ASK' 的排序列表，如 {'BID': [(price, volume)], 'ASK': [...]}
:return: 成交列表，每个元素为 (成交价, 成交量)
"""
trades = []
remaining_volume = order.volume

if order.side == 'BUY':
# 买入：与卖盘 (ASK) 匹配，从最低卖价开始
for price, volume in order_book['ASK']:
if remaining_volume == 0: break
if volume == 0: continue # 跳过零量档位

# 计算可成交量
trade_volume = min(remaining_volume, volume)
trades.append((price, trade_volume))
remaining_volume -= trade_volume

# 如果吃光所有卖盘仍有剩余，此部分通常会被拒绝或进入队列（取决于实现）
# 但在本模拟引擎中，通常假设市场深度足够。
elif order.side == 'SELL':
# 卖出：与买盘 (BID) 匹配，从最高买价开始
for price, volume in reversed(order_book['BID']):
if remaining_volume == 0: break
if volume == 0: continue
trade_volume = min(remaining_volume, volume)
trades.append((price, trade_volume))
remaining_volume -= trade_volume

return trades

\*\*架构设计思想：从“黑盒”到“白盒”的模拟

\*\*：

许多早期的交易系统或游戏（如《模拟交易》早期版本）将撮合逻辑视为一个“黑盒”，用户下单后只能看到一个最终结果，无法理解价格是如何形成的 [31]。Leek Quant 的模拟交易引擎则是一个“白盒”系统。它向用户（尤其是策略开发者）清晰地展示了市场深度（订单簿）和撮合的全过程。

这种透明化的设计具有巨大的优势。首先，它让开发者能够调试和验证他们的策略逻辑，理解为什么一个订单在特定时间成交了，或者为什么没有成交。其次，它为更高级的功能，如成交量加权平均价格（VWAP）、时间加权平均价格（TWAP）等算法交易策略的实现奠定了基础 [27]。最后，它为用户提供了接近真实的交易心理体验，因为市场的涨跌停、买一卖一的挂单量等真实存在的约束条件，都会直接影响用户的交易决策和结果 [29]。这种深度模拟是 Leek Quant 在用户体验上的一个核心差异化设计。

## 七、表现层：Web前端架构

表现层是用户与系统交互的直接窗口。Leek Quant 的前端设计旨在为金融专业人士提供功能强大、操作便捷的图形化界面。

### 7.1 React + Vite 技术栈

Leek Quant 的前端应用采用现代化的 JavaScript 技术栈构建，以确保开发效率和运行性能。

- **React**：用于构建用户界面的 JavaScript 库。系统利用 React 的组件化模型，将复杂的 UI 分解为可复用、可维护的独立组件 [51]。

- **Vite**：作为下一代前端构建工具，Vite 提供了极快的冷启动速度和热模块替换（HMR）能力，极大地改善了开发者的开发体验 。

- **TypeScript**：作为 JavaScript 的超集，TypeScript 为项目添加了静态类型检查，有助于在开发阶段捕获潜在错误，提升代码的健壮性 。

- **状态管理**：全局状态管理由 Zustand 库处理，它以其简洁的 API 和优秀的性能而著称，负责管理如用户登录信息、全局交易设置等跨组件共享的状态 。

- **组件库**：UI 基础组件采用 shadcn/ui 库，它提供了一套设计精美、高度可定制的组件（如按钮、表格、对话框等），帮助快速构建一致性的用户界面 。

- **开发环境**：前端代码在容器内使用 Node.js 环境运行，通过 bind mount 将宿主机代码挂载到容器中，从而实现代码的热重载，开发者可以即时看到修改后的效果 。

### 7.2 专业金融图表与代码编辑

为了满足量化交易的特殊需求，前端集成了两款专业级的工具库。

**TradingView Lightweight Charts**：

对于金融数据可视化，尤其是 K 线图和技术分析图表，TradingView 的 Lightweight Charts 是目前业界公认的标杆 。

- **特性**：该库基于 HTML5 Canvas 渲染，而非传统的 DOM 操作，使其能够处理数十万数据点并保持流畅交互 [50]。它原生支持蜡烛图（K 线图）、折线图、成交量柱状图等最常用的金融图表类型 [53]。

- **集成**：Leek Quant 通过 `kaktana-react-lightweight-charts` 这个 React 封装库，将 Lightweight Charts 无缝集成到 React 组件体系中 [54]。

- **应用**：在前端，用户可以利用它来浏览历史行情、监控实时走势图，并进行专业的技术分析 [3]。

**Monaco Editor**：

策略编写是量化交易的核心。为了给用户提供最佳的代码编辑体验，Leek Quant 集

成了微软开源的 Monaco Editor，也就是著名的 VS Code 编辑器的内核 。

- **特性**：Monaco 提供了强大的代码编辑功能，包括语法高亮、智能代码补全、代码折叠、错误提示等 [3]。

- **应用**：在“我的策略”页面，用户面对的不再是一个简单的文本框，而是一个功能完备的代码编辑器。这对于编写、调试和优化复杂的交易策略至关重要，能显著提升用户的开发效率和体验 。

## 八、高级功能：多因子打分与风险管理

除了基础的回测与交易功能，Leek Quant 还提供了一系列高级功能，旨在帮助用户构建更为稳健和理性的投资策略。

### 8.1 多因子股票打分与排名模块

多因子选股是机构投资者广泛使用的系统化选股方法。Leek Quant 内置了一个多因子打分模块，帮助用户从海量股票中筛选出优质标的。

**常用因子类别**：

该模块集成了量化投资中公认的有效因子类别 [20]。

- **价值因子**：如低市盈率（PE）、市净率（PB）、市销率（PS），用于寻找被低估的股票。

- **质量因子**：如高净资产收益率（ROE）、高毛利率、低负债率，用于评估公司的盈利质量和财务健康度。

- **动量因子**：如近1个月或1年的涨幅，捕捉“强者恒强”的趋势效应。

- **成长因子**：如营收增长率、利润增长率，寻找成长性高的公司。

- **风险因子**：如低波动率、低换手率，控制组合的整体风险。

**打分流程与默认权重**：

系统为每个因子预设了默认权重（例如价值因子权重为1.0）。用户也可以根据自己的投资理念调整权重 [20]。每个因子值会经过标准化处理（例如，将PE取倒数，因为低PE是好的），然后乘以权重，最后对所有因子的得分进行加总，得到每只股票的综合得分 [37]。

\*\*实现模式：因子有效性验证与清洗

\*\*：

并非所有因子在所有市场环境下都有效。一个先进的系统需要能够对因子的有效性进行回测和验证 [8]。Leek Quant 提供了工具来检验一个因子是否与股票的未来收益存在显著的相关性。

同时，在计算因子前，系统会对原始数据进行清洗，以处理异常值和缺失数据。例如，在处理市盈率因子时，会剔除 ST 股票、亏损股票（PE为负）以及极个别的异常高值 [8]。这种数据清洗是确保最终打分结果可靠、避免“垃圾进、垃圾出”的关键步骤。

### 8.2 系统风险与应对措施

量化交易不仅关乎盈利，更关乎风险控制。Leek Quant 在架构层面就植入了风险管理的基因。

**系统风险来源**：

- **数据源失效**：这是量化系统最大的外部风险之一。如果上游数据源（如 AKShare 依赖的网站）改版或封禁，会导致策略产生错误的信号 [3]。

- **网络中断**：可能导致无法获取实时行情，或者在关键时刻无法下达交易指令 。

- **软件 Bug**：任何复杂的软件都可能存在缺陷，这些 Bug 可能导致错误的交易行为 。

- **硬件故障**：服务器宕机、磁盘损坏等硬件问题可能中断交易流程 。

**核心应对措施**：

针对上述风险，Leek Quant 采取了多层次的防御策略。

1.  **数据冗余**：通过多层回退数据源，确保在主数据源失效时，系统能够无缝切换到备用数据源 [30]。

2.  **断线重连与数据补录**：系统被设计为能够自动处理网络中断，并在连接恢复后，自动补录缺失的行情数据，保证策略执行的连续性 [18]。

3.  **全面的日志记录**：系统的每一个重要动作，从信号生成、过滤到订单成交，都会被详细记录到 `service_logs` 表中 。这些日志是事后审计、故障排查和策略优化的宝贵资料。

4.  **数据清洗与校验**：在数据进入策略逻辑之前，系统会进行严格的清洗和异常值检测，避免因个别“脏数据”而引发“闪崩”等极端交易行为 。

\*\*风险管理哲学：冗余与自治

\*\*：

Leek Quant 的风险管理设计体现了“冗余与自治”的核心哲学。

- **冗余 (Redundancy)**：体现在数据源的多路径获取。它不依赖于任何一个单一的服务提供商，从而构建了一个弹性的数据供应链。

- **自治 (Autonomy)**：体现在系统的本地化部署和数据的自主可控。用户拥有数据的完全所有权，这本身就是最根本的风险控制手段。此外，系统在网络恢复后的自动数据补录功能，也体现了一种“自治式”的自我修复能力，减少了人工干预的延迟和错误 。

这种设计哲学将风险控制从被动应对转变为主动预防，最大程度地保障了用户策略研究的稳定性和安全性。

## 结论

Leek Quant 量化交易平台通过其**隐私优先的本地化架构**、**高性能的回测引擎**、**真实的模拟交易体验**以及**专业级的前端界面**，为 A 股市场的个人投资者和量化爱好者提供了一站式的解决方案。

其技术架构的核心亮点在于：

1.  **稳健的基础设施**：通过 Docker Compose 实现的服务解耦与容器化部署，保证了环境的一致性和部署的便捷性。

2.  **优化的数据管理层**：采用 PostgreSQL + InfluxDB 的双引擎存储，高效地解决了关系型元数据与海量时序行情数据的存储与查询问题，并通过多层回退机制保障了数据供应链的连续性。

3.  **极致的策略回测性能**：深度集成 Hikyuu 的 C++ 内核，通过 pybind11 提供 Python 接口，实现了接近实盘速度的毫秒级回测，并采用组件化思想构建了标准化、可复用的策略开发范式。

4.  **真实的交易模拟环境**：基于五档行情和订单簿的撮合机制，配合 A 股特有的规则过滤器，为用户提供了高保真的交易体验。

5.  **专业的交互体验**：基于 React 和 Vite 构建的前端界面，集成了 TradingView 专业金融图表和 Monaco 代码编辑器，为用户提供了媲美本地专业软件的操作体验。

综上所述，Leek Quant 不仅仅是一个软件工具，更是一个融合了现代软件工程理念、高性能计算技术以及专业量化交易思想的综合性平台，旨在帮助用户从数据到决策，从研究到实战，都能在一个安全、高效、透明的环境中进行。

## 信息来源

[1]  [量化交易平台核心技术算法与架构体系技术](https://m.blog.csdn.net/weixin_37647148/article/details/157801804)

[2]  [vnpy](https://www.vnpy.com/)

[3]  [开源量化交易系统构建指南：从零基础到策略部署的实战进阶](https://m.blog.csdn.net/gitblog_00743/article/details/159309499)

[4]  [量化交易系统架构-洞察及研究](https://m.renrendoc.com/paper/505118439.html)

[5]  [国内主流的量化交易平台整理](https://guba.sina.cn/?bid=9279&s=thread&tid=2357414)

[6]  [量化交易框架，python量化， quant 框架](https://m.blog.csdn.net/qq_33919114/article/details/151141734)

[7]  [Backtrader回测数据准备全攻略：从Tushare到Akshare的平滑迁移指南](https://m.blog.csdn.net/weixin_29098117/article/details/159220195)

[8]  [如何把akshare行情无缝切换miniqmt数据源](https://m.blog.csdn.net/qq_39970492/article/details/152556683)

[9]  [5个免费股票数据API实测对比：从AkShare到BaoStock，哪个最适合你的AI量化项目？](https://m.blog.csdn.net/weixin_29290947/article/details/159063254)

[10]  [A数据获取](https://m.blog.csdn.net/qiqzhang/article/details/157899800)

[11]  [akshare批量获取etf并保存到csv的源码 | 策略榜单，最高年化收益434%，夏普5.16，回撤10%，附代码包下载](https://m.blog.csdn.net/weixin_38175458/article/details/149459747)

[12]  [Baostock](https://www.baostock.com/)

[13]  [高效解决AKShare股票接口数据异常问题解决方案](https://m.blog.csdn.net/gitblog_01127/article/details/158259327)

[14]  [攻克AKShare数据获取难题：从异常处理到架构升级的全链路优化](https://m.blog.csdn.net/gitblog_00151/article/details/158912151)

[15]  [股票交易五档如何运用？这一机制对交易有何影响？](https://zhidao.baidu.com/question/1618440972461170907.html)

[16]  [如何理解五档成交的机制？五档成交对交易有何影响？](https://m.hexun.com/funds/2025-01-31/217083870.html)

[17]  [五档即时成交](https://baike.baidu.com/item/%E4%BA%94%E6%A1%A3%E5%8D%B3%E6%97%B6%E6%88%90%E4%BA%A4)

[18]  [2026年量化交易信号系统设计_策略信号生成与执行流程](https://m.blog.csdn.net/tqsdk_God/article/details/157765646)

[19]  [【动态规划篇】专题(四)：状态机模型——股票交易的艺术](https://m.blog.csdn.net/2301_79849925/article/details/158348301)

[20]  [10大经典量化策略：实战逻辑+买卖信号+风险点](https://m.blog.csdn.net/Si15166622538/article/details/159767163)

[21]  [五个步骤构建你的第一个量化交易策略，2026年实战教程](https://m.toutiao.com/a7634728362522444288/)

[22]  [OpenClaw交易助手：从事件驱动架构到实盘部署的量化系统实践](https://m.blog.csdn.net/weixin_42566209/article/details/161062484)

[23]  [基于Python的A股模拟交易系统【附代码】](https://m.blog.csdn.net/2301_80160362/article/details/159649020)

[24]  [deepseek模拟成熟交易员交易全过程，保姆级示范，可直接试用](https://m.toutiao.com/article/7491661892155441683/)

[25]  [A股量化系统如何解决T+1限制与实时信号冲突？](https://ask.csdn.net/questions/9411868)

[26]  [对沪深交易规则的一点思考和建议](https://xueqiu.com/9508433096/372243377)

[27]  [量化交易执行引擎QuantClaw：从架构设计到实战部署全解析](https://m.blog.csdn.net/weixin_42524165/article/details/160873514)

[28]  [基于Java的模拟交易引擎测试系统设计与实现](https://m.blog.csdn.net/weixin_42605397/article/details/155360274)

[29]  [模拟交易（TapTap 测试版）](https://www.taptap.cn/app/788390?os=pc)

[30]  [模拟交易（TapTap 测试版）](http://www.taptap.com/app/788390/all-info?platform=android)

[31]  [《模拟交易（TapTap 测试版）》重大规则变更公告](https://www.taptap.cn/moment/791685374983276473)

[32]  [T+1](https://baike.baidu.com/item/%22T+1%22%E4%BA%A4%E6%98%93%E5%88%B6%E5%BA%A6/15206815)

[33]  [使用jqdata和hikyuu平台进行C++/python混合策略编写的方法](https://m.blog.csdn.net/maggiemaggiemay/article/details/94728166)

[34]  [Hikyuu 1.1.1 发布，量化交易研究框架](https://m.blog.csdn.net/weixin_34067049/article/details/89687775)

[35]  [Hikyuu 的扩展与二次开发](https://m.blog.csdn.net/gitblog_00469/article/details/150498491)

[36]  [Hikyuu：一个让A股回测提速百倍的开源框架](https://yunpan.plus/t/293-1-1)

[37]  [Hikyuu Quant Framework 技术文档](https://m.blog.csdn.net/gitblog_01413/article/details/150514084)

[38]  [Hikyuu教程 | 如何进行策略回测参数优化](https://m.blog.csdn.net/KongDong/article/details/143061204)

[39]  [Hikyuu教程 | 滚动回测与滚动寻优系统](https://m.blog.csdn.net/KongDong/article/details/143061329?biz_id=102&ops_request_misc=&request_id=&utm_term=hikyuu)

[40]  [使用Docker Compose部署PostgreSQL：从入门到实践](https://m.blog.csdn.net/mliev/article/details/158348849)

[41]  [Docker 部署 PostgreSQL 数据库教程](https://m.blog.csdn.net/liuguizhong/article/details/153178906)

[42]  [Docker Compose 启动 PostgreSQL 数据库](https://m.blog.csdn.net/u014394049/article/details/142315190)

[43]  [Docker Compose安装部署PostgreSQL数据库](https://m.blog.csdn.net/u011019141/article/details/143949345?biz_id=102&ops_request_misc=&request_id=&utm_term=docker%E5%AE%89%E8%A3%85%E9%83%A8%E7%BD%B2postgresql)

[44]  [使用Docker Compose定义服务依赖：构建高可用Django+PostgreSQL+Redis架构](https://m.blog.csdn.net/li_Michael/article/details/150107866)

[45]  [FastAPI快速启动模板：极速构建高性能API的终极方案](https://m.blog.csdn.net/gitblog_00739/article/details/155847417)

[46]  [【基于 Docker Compose 搭建 PostgreSQL 主从复制集群（1 主 2 从 + Pgpool 故障转移）】](https://m.blog.csdn.net/weixin_45482763/article/details/151393600)

[47]  [2025最新 Docker (WSL 2)部署 PostgreSQL 和 Redis数据库(超详细)](https://m.blog.csdn.net/Moss_co/article/details/149545959)

[48]  [FastAPI 的 Docker Compose 配置文件](https://m.blog.csdn.net/xuukai/article/details/146604872)

[49]  [TradingView Lightweight Charts：Android平台终极集成指南](https://m.blog.csdn.net/gitblog_01115/article/details/157204705)

[50]  [TradingView Lightweight Charts：高性能金融图表开发实战指南](https://m.blog.csdn.net/gitblog_00930/article/details/155551197)

[51]  [简化React实现的Tradingview图表模块](https://wenku.csdn.net/doc/169zad5g4m)

[52]  [TradingView Lightweight Charts轻量级金融图表终极指南](https://m.blog.csdn.net/gitblog_00477/article/details/155551250)

[53]  [专为金融数据可视化设计的高性能 HTML5 图表库Lightweight Charts](https://baijiahao.baidu.com/s?id=1832046759812396926)

[54]  [TradingView Lightweight Charts 入门指南：构建高效金融图表应用](https://m.blog.csdn.net/gitblog_00201/article/details/148393849)

[55]  [在Postgresql中对空间数据进行表分区的实践](https://m.blog.csdn.net/eqmaster/article/details/143232875)

[56]  [PostgreSQL如何创建分区表](https://m.blog.csdn.net/justdoself/article/details/142459298)

[57]  [postgresql实现对已有数据表分区处理的操作详解例子解析](https://m.blog.csdn.net/jimn2000/article/details/142677531)

[58]  [在PostgreSQL中使用分区技术](https://m.blog.csdn.net/waiwai0511/article/details/150972952)

[59]  [PostgreSQL：分区与大型数据管理.docx](https://max.book118.com/html/2025/0917/8041121045007133.shtm)

[60]  [PostgreSQL分区策略：从原理到实践的深度优化指南](https://developer.baidu.com/article/detail.html?id=4113687)

[61]  [PostgreSQL表分区简单介绍和操作方法](https://m.blog.csdn.net/qq_39496303/article/details/152082571)

[62]  [如何在PHP中实现PostgreSQL数据库分区的详细步骤？](https://m.php.cn/faq/1390054.html)
