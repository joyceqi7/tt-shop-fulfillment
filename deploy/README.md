# TT Shop 履约系统 —— 部署 + 故障 SOP Runbook

> 单 ECS 一站式部署（MVP 阶段），跑 caddy / api / worker / postgres / redis 五个容器。
> 服务器：阿里云 **海外 ECS（推荐美西-硅谷 / 新加坡）**，4c8g 起步，60GB SSD。
> 域名：`tts-api.sailson.com` —— 子域名挂在公司主域名 `sailson.com` 下（DNS 托管在公司官网同一处）。

---

## 目录

1. [初次部署：从零到上线](#1-初次部署从零到上线)
2. [日常运维命令速查](#2-日常运维命令速查)
3. [故障 SOP（按场景）](#3-故障-sop)
4. [数据回滚 / 灾难恢复](#4-数据回滚--灾难恢复)
5. [密钥轮换](#5-密钥轮换)
6. [上线 checklist（cutover 当天）](#6-上线-checklist-cutover-当天)

---

## 1. 初次部署：从零到上线

### 1.1 买服务器（阿里云）

| 项目 | 推荐配置 | 备注 |
|---|---|---|
| 实例 | ECS 共享通用型 g6e.large（2c8g）或 g7.xlarge（4c16g） | **必须**选海外区域：`us-west-1`（硅谷）或 `ap-southeast-1`（新加坡） |
| 系统盘 | ESSD 60GB（PL1） | 不够再扩，便宜 |
| 镜像 | Ubuntu 22.04 LTS 64位 | |
| 公网 | 按量带宽 5Mbps | TT API 不大，5M 够用；流量大了改包月 |
| 安全组 | 入：22(SSH 限白名单)、80、443 | 出：全开 |

**!!! 注意 !!!**
- 阿里云"国际站"和"中国站"账号体系不互通；**用国内站账号买海外区 ECS** 即可（会要公司实名）。
- 买完先在控制台改 root 密码 / 上 SSH key，关掉密码登录。

### 1.2 域名 + DNS

1. 域名 `sailson.com` 是公司主域名（官网跑在上面），DNS 托管位置问运维同事。
2. 在对应 DNS 控制台加一条 A 记录：
   - 主机记录 / 名：`api`
   - 值：ECS 的 EIP 公网 IP
   - TTL：600（10 分钟，便于改）
   - 代理（如果在 Cloudflare）：**先选灰云（DNS only）**，让 Caddy 拿到 Let's Encrypt 证书；上线稳定后视情况切橙云。
3. **如果 DNS 在 Cloudflare 且切了橙云**，要去 SSL/TLS → Overview 选 **"Full (strict)"**，否则 webhook 握手会出问题。
4. 验证：`dig @8.8.8.8 tts-api.sailson.com +short` 应该返回 EIP。

### 1.3 装基础环境

```bash
ssh root@<ecs-public-ip>
# 拉代码
git clone <git-url> /opt/tt-shop
cd /opt/tt-shop
# 一键 bootstrap：装 docker / 防火墙 / 数据目录 / systemd
bash deploy/bootstrap.sh
```

### 1.4 准备 .env.production

```bash
# 本地：
cp deploy/env.production.example .env.production
# 编辑：填 TTS_*、POSTGRES_PASSWORD、FERNET_KEY、ADMIN_TOKEN、SENTRY_DSN、FEISHU_WEBHOOK_URL ...
# 生成强随机串：
openssl rand -base64 32     # ADMIN_TOKEN
openssl rand -base64 24     # POSTGRES_PASSWORD
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY

# 上传到服务器：
scp .env.production root@<ecs>:/opt/tt-shop/
ssh root@<ecs> "chmod 600 /opt/tt-shop/.env.production"
```

### 1.5 启动

```bash
# 在服务器上：
cd /opt/tt-shop
systemctl start tt-shop
systemctl status tt-shop
docker compose -f deploy/docker-compose.prod.yml ps
docker compose -f deploy/docker-compose.prod.yml logs -f api
```

### 1.6 验证

```bash
# 健康检查
curl https://tts-api.sailson.com/health/ready
# {"status":"ok","db":"ok","redis":"ok"}

# Metrics（确保只有内网能访问；下文会限制）
curl https://tts-api.sailson.com/metrics | head -20
```

### 1.7 配置每日备份

```bash
crontab -e
# 加：
0 3 * * * /opt/tt-shop/scripts/backup_postgres.sh >> /var/log/tt-shop-backup.log 2>&1
```

### 1.8 报 webhook URL 给 TT 直客

把这一段发给直客群（不需要邮件模板，直接复制）：

> 团队你好，我们 TT Shop 履约系统已上线 staging，请帮忙在 tcc 配置：
> - Shop ID / Cipher：`<填>`
> - Webhook URL：`https://tts-api.sailson.com/webhooks/tts`
> - 事件：`ORDER_STATUS_CHANGE`
> - 验签：HMAC-SHA256（用我们 App Secret）
> 联调期间请提供 TT Shop 出口 IP 段，我们会在 Cloudflare WAF 加白名单。

---

## 2. 日常运维命令速查

| 任务 | 命令 |
|---|---|
| 看所有容器状态 | `cd /opt/tt-shop && docker compose -f deploy/docker-compose.prod.yml ps` |
| 看 api 日志 | `docker compose -f deploy/docker-compose.prod.yml logs -f --tail=200 api` |
| 看 worker 日志 | `docker compose -f deploy/docker-compose.prod.yml logs -f --tail=200 worker` |
| 重启 api（不停 db）| `docker compose -f deploy/docker-compose.prod.yml restart api` |
| 全部重启 | `systemctl restart tt-shop` |
| 拉新镜像 + 重启 | `cd /opt/tt-shop && git pull && systemctl reload tt-shop` |
| 进 db | `docker exec -it tts-postgres psql -U tts -d tts` |
| 进 redis | `docker exec -it tts-redis redis-cli` |
| 看 arq 队列长度 | `docker exec tts-redis redis-cli llen arq:queue` |
| 看磁盘 | `df -h /data` |
| 看哪个容器吃资源 | `docker stats --no-stream` |
| 手动跑一次对账 | `docker exec tts-worker python -m app.workers.run_once reconciliation_task` |

---

## 3. 故障 SOP

### 3.1 飞书告警：`卡密水位低于阈值`

**症状**：`tts_codes_free_by_sku{sku=...}` 低于 `STOCK_ALERT_THRESHOLD`。

**处理**：
1. 找负责供货的同学补卡密（最长 24h）。
2. 通过 `/admin/import_codes`（如已上线）或 SQL 直灌 `codes` 表。
3. **不要**临时关掉告警 —— 缺货会让客户在 TT 投诉。

### 3.2 飞书告警：`access_token 即将过期`

**症状**：`tts_token_expire_in_seconds{shop=...}` < `TOKEN_EXPIRE_ALERT_SECONDS` 且 worker 没主动 refresh。

**处理**：
1. 检查 worker 是否健康：`docker compose ... ps`、`logs worker`。
2. 手动 refresh：
   ```bash
   docker exec tts-api python -c "
   import asyncio; from app.services.tts.token import refresh_token_now
   asyncio.run(refresh_token_now('<seller_id>'))
   "
   ```
3. 如果 refresh 失败：检查 `TTS_REFRESH_TOKEN` 是否被吊销 → 走 OAuth 重新授权。

### 3.3 飞书告警：`对账滞后 > 30min`

**症状**：`tts_reconciliation_lag_seconds{shop=...}` 超阈值。

**处理**：
1. 看 worker 日志最近一次 `reconciliation_task` 是否报错。
2. 看 Redis 全局锁是否卡住：
   ```bash
   docker exec tts-redis redis-cli get reconciliation:run
   docker exec tts-redis redis-cli ttl reconciliation:run
   ```
3. 如果锁的 TTL > 600s 还没释放：手动删（worker 已死）：
   ```bash
   docker exec tts-redis redis-cli del reconciliation:run
   ```

### 3.4 飞书告警：`webhook_events dead 数量上涨`

**症状**：`tts_webhook_events_by_status{status="dead"}` 增长。

**处理**：
1. 看 Sentry 最近的 fulfill_order 异常聚合。
2. 拿 dead 的 dedup_key / order_id，用 `/admin/reship` 手动重投：
   ```bash
   curl -X POST https://tts-api.sailson.com/admin/reship \
     -H "X-Admin-Token: $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"order_id":"<order_id>","reason":"manual reship after fix"}'
   ```
3. 修代码 → CI 推镜像 → `systemctl reload tt-shop`。

### 3.5 整个站点 5xx / 不响应

按这个顺序排查（**先看，再改**）：
1. `systemctl status tt-shop` —— systemd 视角是否在跑。
2. `docker compose -f deploy/docker-compose.prod.yml ps` —— 哪个容器 unhealthy/exit。
3. `docker compose ... logs --tail=200 <服务名>` —— 看错误。
4. `df -h /data` —— 磁盘是否爆（Postgres 满会全锁死）。
5. `free -h` —— 内存是否爆（worker OOM 会被 docker kill）。
6. `curl http://127.0.0.1:8000/health/ready`（在容器内 `docker exec tts-api curl ...`）—— 区分是 caddy 还是 app 的问题。

应急回滚：
```bash
cd /opt/tt-shop
git log --oneline -10                    # 找上一个稳定 commit
git checkout <last-good-sha>
systemctl reload tt-shop                 # 重新拉旧镜像 + up
```

### 3.6 Postgres 起不来

```bash
docker compose -f deploy/docker-compose.prod.yml logs postgres --tail=200
# 常见：
# - WAL/data 文件损坏 → 走 4. 灾难恢复
# - 磁盘满 → 清 /data/backups 老备份；扩盘
# - PID 锁冲突（容器 kill -9 后偶发）→ 删 /data/pg/postmaster.pid 后再起
```

---

## 4. 数据回滚 / 灾难恢复

### 4.1 部分回滚（误改某张表）

不要走全库恢复。临时拉一个新 Postgres 容器 mount 备份，导出表，再 COPY 回主库。脚本待补。

### 4.2 全库灾难恢复（机器炸了 / 数据被勒索）

```bash
# 在新服务器上：
bash deploy/bootstrap.sh
scp .env.production root@<新机器>:/opt/tt-shop/
systemctl start tt-shop                  # 起空库
# 等 postgres healthy
bash scripts/restore_postgres.sh oss://sailson-shop-backup/postgres-daily/<最近一份>
systemctl restart tt-shop
```

**RPO（数据丢失上限）**：24h（每日备份）。如果业务量起来，把 cron 改成 6h 一次。
**RTO（恢复时间）**：1h 内（含开机器）。

---

## 5. 密钥轮换

### 5.1 Fernet 主密钥（卡密加密）

⚠️ **不能直接换**，所有库里的 `code_encrypted` 都是用旧密钥加密的。
正确做法（待开发支持 multi-key）：
1. 应用支持 `FERNET_KEYS=新,旧` 多密钥 list（解密时按顺序试）。
2. 后台跑迁移任务，把所有 codes 用新 key 重加密。
3. 迁移完后把旧 key 从配置里去掉。

### 5.2 ADMIN_TOKEN

直接改 `.env.production` → `systemctl reload tt-shop`。

### 5.3 TTS_APP_SECRET / REFRESH_TOKEN

走 TT Partner Center 重新生成 → 改 `.env.production` → reload。

---

## 6. 上线 checklist（cutover 当天）

- [ ] Sentry DSN 配好，能收到测试错误
- [ ] 飞书告警群机器人测过（手动 trigger 一次 watchdog）
- [ ] Cloudflare DNS 切橙云 + Full(strict) SSL
- [ ] Cloudflare WAF 加 TT IP 段白名单（拿到后再加）
- [ ] Caddy 证书签发成功（`docker exec tts-caddy ls /data/caddy/certificates/`）
- [ ] Webhook 端点 /webhooks/tts 在 TT tcc 那边状态 active
- [ ] `WEBHOOK_VERIFY_DISABLED=false` 已确认
- [ ] `TTS_API_QPS` 已按直客给的真值改
- [ ] cron 备份任务已加，第一次手动跑成功并落 OSS
- [ ] 跑过一次 Onboarding 2.6.2 的 4 个真实订单场景（正常 / 取消 / 退款 / 重发）
- [ ] 监控值班排好（前 72h 至少一人随时在群）
