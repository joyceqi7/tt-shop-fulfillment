# tt-shop-admin

Game Code Hub 后台管理控制台（Next.js 14 + Tailwind + shadcn/ui）。

## 本地开发

```bash
cd admin
cp .env.local.example .env.local   # 默认指向 http://127.0.0.1:8000，按需改
npm install
npm run dev                        # http://localhost:3000
```

后端需要先跑起来（项目根目录）：

```bash
uvicorn app.api.main:app --port 8000 --reload
```

并且 `.env` 里要有：

- `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` — 后台登录账户
- `SESSION_SECRET` — cookie 签名密钥（≥ 32 字节随机串）

生成密码哈希：

```bash
python scripts/gen_admin_hash.py
```

## 设计原则

- 暗色优先（`<html class="dark">` 默认开启）
- 字体：Inter（UI） + JetBrains Mono（数字 / id / 哈希）
- 核心交互：键盘可达、低饱和、高数据密度
- 列表里所有金额、订单号、批次号用 `mono` class（等宽数字对齐）

## 路由

```
/login                         登录
/dashboard                     总览（KPI + 趋势图 + 库存告警）
/orders                        订单列表 + 详情 + 重发
/inventory                     库存 + 批次 + 码明细
/skus                          SKU 映射 CRUD
/suppliers                     供货商
/purchases                     采购单（含付款状态）
/finance                       财务（毛利 / 应付 / 月度趋势）
/audit                         审计日志
```

## 与后端契约

所有接口走 `/api/admin/*`：

- 浏览器侧：Next 自带 rewrite 转给 `API_BASE_URL`
- 生产环境：Caddy 在边界做路由（`@api path /api/admin/*` → `api:8000`）

cookie 名固定为 `tts_admin_session`，由后端 `app.services.admin_session` 签发。
