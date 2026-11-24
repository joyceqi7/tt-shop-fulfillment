# 要找 TT 直客对齐的信息清单

> 这是 Week 0 PoC 里"和直客对齐"任务的落地清单。建议**一次性**发给直客，避免来回拉扯。

## P0 —— Week 1 就需要（不拿到会影响开发）

### 1. API 限流 QPS
- **每个接口的 QPS 上限**（特别关注以下 4 个）：
  - `getOrderList` (/order/202309/orders/search)
  - `getOrderDetail` (/order/202507/orders)
  - `redeemInfoCallback`（Scope: `seller.redeem_info.write`）
  - `refreshToken`
- **触发 429 后的 Retry-After 策略是啥？**
- **QPS 是按 app_key 维度还是按 app_key+shop 维度？**

### 2. Webhook 规格确认
- **验签算法**：是 `sign` header？还是 body 里的 `sign` 字段？具体怎么计算（HMAC-SHA256 with app_secret？）
- **幂等 key 字段**：发给我们的 webhook payload 里有没有独立的 `event_id` / `message_id`？还是要用 `shop_id + order_id + status + timestamp` 组合做幂等？
- **最大处理时长**：5 秒内回 200 就够了？超时后 TT 会重试几次、间隔多久？
- **来源 IP 段**：TT webhook 来源的 IP 白名单（用于 Cloudflare WAF）
- **重复推送策略**：同一事件可能重复推送几次？在什么情况下？

### 3. Webhook URL 报备流程
- **把 URL prefix 报给你之后，TT 研发配 tcc 需要多久？**（决定 staging 什么时候报）
- **是否可以一次报两个 prefix**（一个 staging、一个 prod），同时配？

## P1 —— Week 2~3 才需要（但越早越好）

### 4. 订单状态机细节
- `DELIVERED` 触发条件 4 —— 剩余子单因 SLA 超时被平台强制置 delivered 时，**我们是否会收到任何通知**？还是只能靠拉详情发现？
- `CANCEL` webhook 的 payload 里，会不会告诉我们**哪些子单已经成功发码、哪些没发**？（决定码回收逻辑）
- **部分退款**场景：主单 A 有 3 个子单，用户只退其中 1 个，我们收到什么？（有没有专门的 webhook？）
- **售后期**时长是多久？COMPLETED 状态以什么事件触发？

### 5. 卡密回收政策
- 平台因 SLA 超时强制 DELIVERED 的子单，**如果我们其实还没发码**，这张码能不能回收复用？
- 用户退货/退款后，已发给用户的码如何处理？合同是否允许要求用户返还？（涉及法务，直客可能要转给法务/BD 回答）

### 6. 真实联调排期
- 按 Onboarding 2.6.2 走真实订单联调，**TT 研发那边需要提前多久预约**？
- **GNE 超时配置**：文档里说"购后研发把 tcc capture 超时时间配置为 3min"，这个是 TT 研发操作还是我们配？
- **测试账号能不能直接走真实联调**？还是一定要生产店铺开通后才能？

## P2 —— 生产切换前对齐

### 7. Partner Center 注册
- Partner Center 开发者账号的邮箱要求（Onboarding 提到"邮箱与 Seller Center 一致"）——**Seller Center 用的邮箱是哪个？**
- 创建 Custom App 时的 Redirect URL 格式是啥？一定是 HTTPS？域名有要求吗？
- 应用的 Scope 申请流程：`seller.order.info` 和 `seller.redeem_info.write` 需不需要额外审批？

### 8. 凭证轮换
- 测试账号的凭证（已经在 docx 里明文给出）**用完后需不需要轮换**？
- 生产 App 的 app_secret / refresh_token 多久轮换一次？有没有自动轮换机制？

## 模板：一次性发给直客的邮件/飞书草稿

```
直客你好，

我这边开发已经开工，有以下几个信息需要你帮忙确认下，尤其是 P0 的 3 个问题会影响 Week 1 的开发进度：

【P0】
1. 4 个核心 API（getOrderList / getOrderDetail / redeemInfoCallback / refreshToken）的 QPS 上限分别是多少？QPS 是按 app_key 还是 app_key+shop 维度？
2. Webhook 验签方式（sign 放哪、怎么算）？payload 里有没有独立的 event_id 字段做幂等？TT webhook 来源的 IP 白名单能给我们吗？
3. Webhook URL prefix 报给 TT 研发后，配置 tcc 需要多久？能不能一次报两个（staging + prod）？

【P1】（Week 2~3 需要，但越早越好）
4. 平台因 SLA 超时强制 DELIVERED 的子单，会不会通知我们？CANCEL webhook 里会不会告诉我们哪些子单已经成功发码？
5. 按 2.6.2 做真实订单联调要提前多久预约？测试账号能直接走真实联调吗？

谢谢！
```

## 到手后填回到代码里的地方

- QPS 上限 → `.env` 的 `TTS_API_QPS`
- Webhook 幂等 key 字段 → `app/api/webhook.py`（待做）+ `webhook_events` 表的唯一索引
- TT 来源 IP 段 → Cloudflare WAF 规则
- Retry-After 策略 → `app/services/tts_client.py`（待做）的 429 处理
