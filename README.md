# TT Shop 虚拟商品履约系统

> **关于这个仓库**：TikTok Shop 美区虚拟商品的自动发码履约系统，由我在 Sailson 期间开发。
>
> 这是公开展示版：TikTok 商家对接文档、真实签名样例和字体文件已移除，应用标识替换成了示例值，仓库不含任何凭据。

TikTok Shop 美区虚拟类目自动发码履约系统。

> 完整开发计划见 [docs/PLAN.md](docs/PLAN.md)（权威副本在 `~/.cursor/plans/tt_shop_虚拟商品履约系统_02daf4a4.plan.md`）。

## 当前进度

- [x] Week 0：签名算法 `signer.py` + 21 条单测（含 2 条 live golden）
- [x] Week 0：PoC 脚本跑通 `refresh_token` / `get_authorized_shops` / `getOrderList`
- [x] Week 1：项目脚手架（Pydantic Settings、Alembic、Async SQLAlchemy）
- [x] Week 1：`TokenManager`（Redis 分布式锁防并发刷新 + Fernet 加密落库）
- [x] Week 1：`TTSClient`（4 API 封装 + 错误分类 + 代理支持）+ 4 条单测
- [x] Week 1：卡密库 `code_inventory`（import + `SKIP LOCKED` 取码 + 标记/释放/作废）
- [x] Week 2：DB 表 9 张齐（tts_orders / tts_order_lines / webhook_events / fulfillment_log）
- [x] Week 2：FastAPI + webhook 接收器（验签 + 幂等入库 + arq 投递，5 秒返回 200）
- [x] Week 2：arq worker 骨架 + 端到端 smoke（webhook → 入库 → 入队 → 消费 → processed）
- [x] Week 2：订单级 Redis 锁 + 订单 upsert 服务
- [x] Week 2：发货主编排（get_order_detail → upsert → SKIP LOCKED 取码 → Redeem → mark_used → 日志）
- [x] Week 2：CANCEL 分支（locked→release / used→void）、RETURN 分支（used→void）
- [x] Week 2：fulfillment 5 场景 smoke（SHIP / 幂等 / 无库存 / Redeem 失败回滚 / CANCEL）全过
- [x] Week 2：兜底对账 Job（arq cron / Redis 单实例锁 / 每店 last_ts 推进 / 分页限流）+ 4 场景 smoke
- [x] Week 2：`/admin/reship` 管理 endpoint（Bearer token 鉴权 + 复用 fulfill_order task）+ 4 场景 smoke
- [x] Week 2：错误分类重试 + 死信队列 + 飞书告警 + Sentry wiring + 4 场景 smoke
- [x] Week 3：业务监控指标 Prometheus `/metrics`（6 个 gauge：webhook status / 卡密水位 / 僵尸锁 / line pending / 对账 lag / token 剩余）+ `watchdog_task` arq cron 5 分钟巡检 + 飞书阈值告警 + 4 场景 smoke
- [ ] Week 3：卡密库 off-site 备份 + Postgres 每日快照
- [ ] Week 0 待联调：webhook 真实验签算法确认（见 `app/auth/webhook_signer.py`）
- [ ] Week 0：和 TT 直客对齐（见 [docs/tt_alignment_checklist.md](docs/tt_alignment_checklist.md)）

## 快速开始（开发环境）

```bash
# 1. Python 3.11（必需，项目用了 3.10+ 的 X | None 语法）
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 2. 起 Postgres + Redis
#    方案 A（已装了本机 Postgres/Redis，例如 Mac 上的 PG18 + Homebrew redis）：
#      sudo launchctl load /Library/LaunchDaemons/postgresql-18.plist    # 启动 Postgres
#      brew services start redis                                          # 启动 Redis
#      /Library/PostgreSQL/18/bin/psql -h localhost -U postgres <<'SQL'
#      CREATE USER tts WITH PASSWORD 'tts';
#      CREATE DATABASE tts OWNER tts;
#      SQL
#    方案 B（干净容器环境，需先装 Docker Desktop 或 OrbStack）：
#      docker compose up -d

# 3. 配置凭证（测试账号在 docs/虚拟类目商家 Onboarding Guide.docx 的 2.3 节）
cp .env.example .env
# 编辑 .env：
#   - TTS_APP_KEY / TTS_APP_SECRET / TTS_REFRESH_TOKEN（onboarding 文档 2.3）
#   - FERNET_KEY: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   - TTS_HTTP_PROXY: 国内开发机必填（例 http://127.0.0.1:7890）

# 4. 跑数据库迁移
alembic upgrade head

# 5. 跑单测
pytest -q

# 6. Week 0 PoC：真实调一次 TT API（确认凭证 + 代理通）
python scripts/poc_get_order_list.py

# 7. 起 FastAPI（webhook 接收器）
uvicorn app.api.main:app --reload --port 8000
# 健康检查：curl http://127.0.0.1:8000/health/ready

# 8. 起 arq worker（另一个终端）
arq app.workers.settings.WorkerSettings

# 9. 端到端 smoke（webhook → 入库 → 入队 → burst worker 消费）
python scripts/smoke_webhook.py
```

## 目录结构

```
tt-shop/
├── app/
│   ├── config.py                 # Pydantic Settings 统一配置入口
│   ├── auth/
│   │   ├── signer.py             # TT API HMAC-SHA256 签名
│   │   ├── crypto.py             # Fernet 加解密封装
│   │   ├── token_manager.py      # access_token 刷新 + Redis 分布式锁
│   │   └── webhook_signer.py     # TT webhook HMAC 验签（算法待联调确认）
│   ├── observability.py          # Sentry 初始化（DSN 空则 no-op）
│   ├── services/
│   │   ├── tts_client.py         # 4 个 TT 业务 API 的客户端
│   │   ├── code_inventory.py     # 卡密库：import / SKIP LOCKED 取码 / 标记
│   │   ├── order_lock.py         # 订单级 Redis 分布式锁
│   │   ├── orders.py             # TT 订单详情 → tts_orders/tts_order_lines upsert
│   │   ├── fulfillment.py        # 发货主编排（DB 事务和网络 IO 严格分离）
│   │   ├── reconciliation.py     # 兜底对账（单实例锁 + 每店 last_ts）
│   │   └── alerts.py             # 告警出口（飞书群机器人 / fire-and-forget）
│   ├── db/
│   │   ├── base.py               # SQLAlchemy Base + 命名规范
│   │   └── session.py            # Async engine / session factory
│   ├── models/
│   │   ├── shop.py               # tts_shops（shop_cipher 缓存）
│   │   ├── token.py              # tts_tokens（加密 + 乐观锁）
│   │   ├── code.py               # codes（卡密 + 部分索引 ix_codes_pick）
│   │   ├── sku_mapping.py        # sku_mapping（内部 SKU ↔ TT seller_sku）
│   │   ├── order.py              # tts_orders / tts_order_lines
│   │   ├── webhook_event.py      # webhook_events（幂等 unique dedup_key）
│   │   └── fulfillment_log.py    # 对 TT 的每一次 API 调用审计
│   ├── api/
│   │   ├── main.py               # FastAPI app + lifespan（建 arq pool + Sentry init）
│   │   ├── deps.py               # Depends 工厂
│   │   └── routes/
│   │       ├── health.py         # /health/live /health/ready
│   │       ├── webhooks.py       # POST /webhooks/tts
│   │       ├── admin.py          # POST /admin/reship（X-Admin-Token 鉴权）
│   │       └── metrics.py        # GET /metrics（Prometheus 格式）
│   ├── services/metrics.py       # 快照指标收集 + 阈值评估 + Prometheus 渲染
│   └── workers/
│       ├── settings.py           # arq WorkerSettings + cron_jobs
│       ├── tasks.py              # fulfill_order + reconciliation_task + watchdog_task
│       └── redis_settings.py     # REDIS_URL → RedisSettings 转换
├── alembic/
│   ├── env.py
│   └── versions/
│       ├── 0001_initial.py
│       ├── 0002_add_codes_and_sku_mapping.py
│       └── 0003_add_orders_webhooks_logs.py
├── scripts/
│   ├── poc_get_order_list.py     # Week 0 PoC
│   ├── smoke_token_and_shops.py  # TokenManager + get_authorized_shops
│   ├── import_codes.py           # 卡密批量导入 CLI
│   ├── smoke_codes.py            # 卡密库 5 步 smoke
│   ├── smoke_webhook.py          # webhook → arq 端到端 smoke
│   ├── smoke_fulfillment.py      # 发货编排 5 场景 smoke（FakeTTSClient mock）
│   ├── smoke_reconciliation.py   # 对账 Job 4 场景 smoke（分页 / 单例锁 / 失败不推进 / 空窗）
│   ├── smoke_reship.py           # /admin/reship 鉴权 + 入队 4 场景 smoke
│   ├── smoke_dead_letter.py      # 重试/死信/告警状态机 4 场景 smoke
│   └── smoke_metrics.py          # /metrics + evaluate_alerts + watchdog_task 4 场景 smoke
├── tests/unit/
│   ├── test_signer.py            # 签名算法 21 测（含 2 条 live golden）
│   └── test_tts_client.py        # 客户端 4 测（pytest-httpx mock）
├── docs/
│   ├── PLAN.md
│   ├── tt_alignment_checklist.md
│   └── 虚拟类目商家Onboarding Guide.docx
├── alembic.ini
├── pyproject.toml                # pytest / ruff 配置
├── .env.example
├── requirements.txt
├── .gitignore
└── README.md
```

## 关键设计摘要

- **并发安全三道防线**：Token 刷新 Redis 互斥锁 / 订单级 Redis 锁 / `FOR UPDATE SKIP LOCKED` 取码（详见 PLAN.md §十）
- **DB 事务 ↔ 网络 IO 严格分离**：fulfillment 内部 3 段式事务（取码→调 redeem→落账），事务里绝不跨网络 IO，避免拖长 PG 行锁 / 连接池耗尽
- **队列 + 调度统一用 arq**：`fulfill_order` 是事件驱动任务，`reconciliation_task` 挂在 `WorkerSettings.cron_jobs`（每 10 分钟）；同一 worker 进程，单技术栈不引入 APScheduler
- **错误分类 + 死信 + 告警闭环**：`_is_non_retryable` 判定（业务错 / ValueError 不重试）；webhook_events 扩展 `retrying` / `dead` 状态；`attempt=max_tries` 时自动发飞书 + Sentry capture
- **对账的兜底承诺**：即使 webhook 全丢，每 10 分钟 `get_order_list(update_time ≥ last_ts)` 扫所有受影响订单补发货；`last_ts` 仅在整轮扫完才推进，失败自动下次重试整段时间窗
- **手动干预入口 `/admin/reship`**：联调期对方说"这单没收到"，管理员一 POST 即可造虚拟 webhook_event 复用 `fulfill_order` task，幂等性完全由现有 pending 过滤机制保证
- **全量订单状态机**：含 CANCEL / RETURN / 平台 SLA 强制 DELIVERED 的码回收闭环
- **可观测性双通道**：Sentry 兜异常 + Prometheus `/metrics`（snapshot gauge，避免多进程 counter 同步麻烦）+ `watchdog_task` arq cron 把超阈值指标翻成飞书文字告警；僵尸锁仅告警不自动释放（保守策略，防与还在运行的 worker 冲突）

## 安全

- `.env` 已在 `.gitignore`，**绝不提交**
- 测试账号凭证在 docx 中明文给出，请勿再外传
- access_token / refresh_token / 卡密敏感字段均用 Fernet 加密存库
