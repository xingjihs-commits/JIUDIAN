# cloud-worker — ShadowGuard Cloud (Cloudflare Worker v1.3)

> **职责**：厂家云端控制 PMS 的中央枢纽。
> **运行环境**：Cloudflare Workers + D1 SQLite 数据库。
> **本目录角色**：`worker.js` 是部署源码；PMS 端通过 `cloud_security.py` HMAC 签名调用本服务。
> **关联文档**：`docs/厂家云端控制体系.md` 是面向工程师的完整体系文档；本 README 是云端 API 速查。

---

## 1. 部署

```bash
# 1. 安装依赖
npm install

# 2. 登录 Cloudflare（首次）
npx wrangler login

# 3. 配置 Secrets（生产环境必做）
npx wrangler secret put BOT1_TOKEN       # 客人主 Bot Token
npx wrangler secret put BOT2_TOKEN       # 工作 Bot Token（可选）
npx wrangler secret put ADMIN_PASSWORD   # /admin 后台密码
# 可选：客户端 HMAC 共享密钥（强制验签时开启）
npx wrangler secret put SOLID_CLIENT_SECRET

# 4. 创建 D1 数据库（首次）
npx wrangler d1 create shadowguard-cloud
# 把返回的 database_id 填入 wrangler.toml

# 5. 部署
npx wrangler deploy

# 6. 验证
curl https://<your-worker>.workers.dev/api/health
# 期望: {"ok":true,"service":"shadowguard-cloud","db":true}
```

---

## 2. API 端点完整矩阵

> **签名要求列说明**：
> - `pwd` = URL/Body 参数 `pwd` 必须等于 `ADMIN_PASSWORD` Secret（弱鉴权，仅 HTTPS 边缘保护）
> - `HMAC` = 客户端必须带 `X-Solid-Signature` 头（`solid-hmac-v1`）；**当前 Worker 未强制验签，铺市场后开启**
> - `none` = 公开端点（活码跳转 / Telegram Webhook / 健康检查）
> - `hotel_id` = 通过 hotel_id 隐式鉴权（酒店只能查自己）

### 2.1 健康检查 & 路由

| 路径 | 方法 | 签名 | 用途 | 调用方 | 状态 |
|---|---|---|---|---|---|
| `/api/health` `/health` | GET | none | 健康检查 + DB 连通性 | 监控 / 部署后冒烟 | ✅ 已实现 |
| `/` `/admin` | GET | `?pwd=` | 厂家后台管理 HTML 页面（酒店列表/Bot/授权/广告） | 厂家浏览器 | ✅ 已实现 |
| `/admin/bots` | GET | `?pwd=` | Bot 与活码管理 HTML 页面 | 厂家浏览器 | ✅ 已实现 |
| `/admin/guests` | GET | `?pwd=` | 客人管理与广播 HTML 页面 | 厂家浏览器 | ✅ 已实现 |

### 2.2 酒店生命周期

| 路径 | 方法 | 签名 | 用途 | 调用方 | 状态 |
|---|---|---|---|---|---|
| `/api/hotel-register` | POST | hotel_id | 酒店注册 + 授权验证（写入 `hotels` 表，标记 `license_keys.is_used=1`，返回 `kill_date`） | PMS `LicenseManager.verify_cloud()` | ✅ 已实现 |
| `/api/hotel-poll` | GET | hotel_id | 酒店心跳轮询（5 分钟一次）：更新 `last_seen`、拉取 `notifications`、返回 `hotel_status` / `kill_switch` / `bot_config` | PMS `HeartbeatService._send_heartbeat()` | ✅ 已实现 |
| `/api/hotel-poll?lite=1` | GET | hotel_id | 轻量心跳（仅状态，不更新 last_seen，不拉通知） | PMS 状态栏在线检测 | ✅ 已实现 |
| `/api/hotel-suspend` | POST | `pwd` (body) | 停用/恢复酒店（action: `suspend`/`resume`，停用时下推 `KILL_SWITCH` 通知） | 厂家后台 | ✅ 已实现 |
| `/api/hotels-list` | GET | `?pwd=` | 全量酒店列表（厂家后台用） | 厂家后台 | ✅ 已实现 |
| `/api/ack` | POST | hotel_id | 通知确认（标记 `notifications.acked=1`） | PMS `ManufacturerCommService.process_notifications` | ✅ 已实现 |

### 2.3 授权管理

| 路径 | 方法 | 签名 | 用途 | 调用方 | 状态 |
|---|---|---|---|---|---|
| `/api/license-issue` | POST | `pwd` (body) | 生成新授权码（写入 `license_keys`，返回 `license_key` + `expire_date`） | 厂家 `ManufacturerCommService.issue_license` | ✅ 已实现 |

### 2.4 远程指令与通知下发

> 这些通知类型在 `notifications` 表里通过 `/api/hotel-poll` 下发给 PMS。

| 通知类型 | 触发端点 | 用途 | PMS 处理 |
|---|---|---|---|
| `KILL_SWITCH` | `/api/hotel-suspend` action=suspend | 强制锁机（写入 `kill_switch_date`） | `ManufacturerCommService.process_notifications` → `bus.kill_switch_triggered` |
| `AD_PUSH` | `/api/ad-push` | 主动广告推送（一次性） | 写入 `pending_ad_text` |
| `SET_AD_SIGNATURE` | `/api/set-ad-signature` | 被动广告签名（持久附加 Bot 消息底部） | 写入 `ad_signature` |
| `NEW_ORDER` | `/api/guest-order` / `/api/tg-webhook/{bot_id}` | 客人新订单 | PMS 弹订单确认 |
| `PAYOUT_RESULT` | `/api/payout-approve` | 提现审批结果 | PMS 更新提现状态 |
| `REMOTE_CMD` | (规划) | 远程指令批量下发 | `HeartbeatService._process_remote_commands` 已支持 7 种 cmd_type |
| `UPDATE_AVAILABLE` | (规划) | 版本更新提示 | PMS 写入 `pending_update_version` |

### 2.5 远程指令（cmd_type）— PMS 端处理

> 这些指令由厂家通过 `REMOTE_CMD` 通知或心跳 `commands` 数组下发，PMS `heartbeat_service._process_remote_commands` 处理：

| cmd_type | 作用 | PMS 实现 |
|---|---|---|
| `RESTART_APP` | 2 秒后退出应用（用户重启） | `QTimer.singleShot(2000, app.quit)` |
| `CLEAR_CACHE` | 清除应用缓存目录 | `_clear_app_cache()` |
| `SEND_ALERT` | 弹厂家通知 toast | `bus.show_warning.emit` |
| `LOCK_LEVEL` | 设置锁机级别 | `vendor_lockdown.sync_lock_level(level, source="cloud_remote_cmd")` |
| `PUSH_AD` | 推送广告 | `db.set_config("pending_ad_text", ...)` |
| `SYNC_NOW` | 立即同步离线操作 | `offline_queue.sync_offline_operations()` |
| `DIAG_SNAPSHOT` | 请求诊断快照上传 | `remote_diag.get_full_diagnosis()` → `_upload_diag_snapshot` POST 到 `return_url` |

### 2.6 Bot 管理（厂家）

| 路径 | 方法 | 签名 | 用途 | 调用方 | 状态 |
|---|---|---|---|---|---|
| `/api/bot-upsert` | POST | `?pwd=` | 注册/更新 Bot（写入 `telegram_bots`，含 `max_guests`） | 厂家后台 | ✅ 已实现 |
| `/api/bots-list` | GET | `?pwd=` | Bot 列表 + 酒店绑定关系 | 厂家后台 | ✅ 已实现 |
| `/api/bot-roulette` | POST | `?pwd=` | Bot 轮盘（负载最低分配新酒店） | 厂家后台 | ✅ 已实现 |
| `/api/hotel-bot-bind` | POST | `pwd` (body) | 酒店绑定客人/工作 Bot（写入 `hotel_bot_bindings`） | 厂家后台 / PMS `live_qr_client.bind_hotel_bots` | ✅ 已实现 |

### 2.7 活码（房间贴纸 → Bot 跳转）

| 路径 | 方法 | 签名 | 用途 | 调用方 | 状态 |
|---|---|---|---|---|---|
| `/api/live-qr-sync` | POST | hotel_id (HMAC) | 酒店 PMS 上传房间 token → 云端生成/复用 8 位 code，返回 `live_url` | PMS `live_qr_client.sync_rooms_to_cloud` | ✅ 已实现 |
| `/api/live-qr-list` | GET | `?pwd=` | 全量活码列表（厂家后台用） | 厂家后台 | ✅ 已实现 |
| `/r/{code}` | GET | none | 活码跳转：查 `live_qr_codes` 表 → 解析当前酒店绑定的 Bot → 302 跳到 `t.me/<bot>?start=<room_token>` | 客人扫码（任意浏览器） | ✅ 已实现 |

### 2.8 客人管理（厂家）

| 路径 | 方法 | 签名 | 用途 | 调用方 | 状态 |
|---|---|---|---|---|---|
| `/api/guest-upsert` | POST | hotel_id | 客人登记/更新（写入 `guests` 表） | PMS 自动同步 | ✅ 已实现 |
| `/api/guest-list` | GET | `?pwd=` | 客人列表（可按 `?hotel_id=` 过滤） | 厂家后台 | ✅ 已实现 |
| `/api/guest-broadcast` | POST | `?pwd=` (body) | 厂家广播消息给客人（按 hotel_ids 批量发 TG） | 厂家后台 | ✅ 已实现 |

### 2.9 Telegram Bot 接入

| 路径 | 方法 | 签名 | 用途 | 调用方 | 状态 |
|---|---|---|---|---|---|
| `/api/tg-webhook/{bot_id}` | POST | none (Telegram 来源) | 多 Bot Webhook 入口（每 Bot 单独 setWebhook） | Telegram 服务器 | ✅ 已实现 |
| `/api/guest-order` | POST | hotel_id | 旧版单 Bot Webhook（兼容 1.0 客户端） | Telegram 服务器 | ✅ 已实现 |
| `/api/payout-approve` | POST | hotel_id | 老板审批提现（写入 `payouts.status`） | 老板 Telegram 按钮 | ✅ 已实现 |

### 2.10 ⚠️ 待新增端点（sub-g 预留 — 采集器云端回传/下发任务）

> sub-g 正在改造采集器，需要以下新端点支持云端握手包管理。
> 命名规范沿用 `/api/handover-*`，与 `/api/live-qr-*` 风格一致。

| 路径 | 方法 | 签名 | 用途 | 调用方 | 状态 |
|---|---|---|---|---|---|
| `/api/handover-push` | POST | `pwd` (body) + 文件 | 采集器上传新握手包（zip + MANIFEST.json），存到 R2 或 D1 BLOB | 采集器 `cloud_push_handover()` | 🚧 sub-g 待实现 |
| `/api/handover-list` | GET | `?pwd=` | 厂家查询已上传的握手包列表（按酒店/品牌/日期过滤） | 厂家后台 | 🚧 sub-g 待实现 |
| `/api/handover-pull` | GET | hotel_id (HMAC) | PMS 从云端拉取最新握手包并本地导入 | PMS `lock_deploy.handover_importer` | 🚧 sub-g 待实现 |
| `/api/handover-delete` | POST | `pwd` (body) | 厂家删除过期握手包 | 厂家后台 | 🚧 sub-g 待实现 |
| `/api/collector-task-poll` | GET | hotel_id (HMAC) | 采集器轮询云端下发的采集任务（按酒店/品牌） | 采集器 `cloud_task_poll()` | 🚧 sub-g 待实现 |
| `/api/collector-task-ack` | POST | hotel_id (HMAC) | 采集器确认任务接收/完成 | 采集器 `cloud_task_ack()` | 🚧 sub-g 待实现 |

**PMS 端配套**（sub-h 已在 `vendor_console_tab.py` 区块③预留按钮）：
- ☁️ 云端拉取按钮当前 `setEnabled(False)`，等 sub-g 完成 worker 端点后激活
- 跳转门锁探测详情按钮已对接现有 `_build_lock_tab`

---

## 3. D1 数据库 Schema

> 完整建表 SQL 在 `worker.js` 的 `initDB()` 函数中。

| 表名 | 主键 | 用途 |
|---|---|---|
| `hotels` | hotel_id | 酒店注册表（hotel_name/machine_code/license_key/salesperson_id/region/status/kill_date/last_seen） |
| `license_keys` | license_key | 授权码池（features_json/salesperson_id/expire_date/is_used/hotel_id） |
| `notifications` | notify_id | 通知队列（hotel_id/notify_type/payload_json/acked/created_at）— 7 天 ACKED / 30 天 PENDING 自动清理 |
| `telegram_bots` | bot_id | Bot 注册表（bot_token/bot_username/bot_role/label/status/max_guests） |
| `hotel_bot_bindings` | hotel_id | 酒店↔Bot 绑定（guest_bot_id/work_bot_id） |
| `live_qr_codes` | code | 活码表（hotel_id/room_id/token/status）— code 8 位 hex，唯一 |
| `orders` | order_id | 客人订单（hotel_id/room_id/items_json/total） |
| `payouts` | payout_id | 提现申请（hotel_id/amount/reason/status） |
| `guests` | (hotel_id, guest_id) | 客人登记（room_id/name/phone/check_in/check_out） |
| `admin_audit` | audit_id | 厂家操作审计（admin_user/action/detail/created_at） |

---

## 4. 鉴权与签名策略

### 4.1 当前策略（铺市场初期，兼容优先）

```
公开端点 (none)        : /api/health, /r/{code}, /api/tg-webhook/*, /api/guest-order
hotel_id 隐式鉴权      : /api/hotel-register, /api/hotel-poll, /api/ack,
                         /api/live-qr-sync, /api/guest-upsert
adminPwd 鉴权 (?pwd=)  : /admin*, /api/hotels-list, /api/bots-list, /api/live-qr-list,
                         /api/bot-upsert, /api/bot-roulette, /api/guest-list,
                         /api/guest-broadcast
adminPwd 鉴权 (body)   : /api/license-issue, /api/ad-push, /api/set-ad-signature,
                         /api/hotel-suspend, /api/hotel-bot-bind
```

### 4.2 演进路线（强制 HMAC 验签）

1. **阶段 1**（当前）：客户端默认带 `X-Solid-Signature` 头，Worker 不验签
2. **阶段 2**（铺市场 6 个月后）：Worker 加 `verifySolidSignature()` 中间件，对未带签名的请求记审计日志但放行
3. **阶段 3**（强制）：开启 `ENFORCE_SIGNATURE=true`，未签名或签名错的请求直接 401

详见 `docs/厂家云端控制体系.md` § 4.3。

---

## 5. 关键调用链路

### 5.1 PMS 启动激活流程

```
PMS 启动
  ↓
LicenseManager.is_activation_required()
  ↓ 是
VendorActivationScreen (全屏激活页)
  ↓ 用户输入授权码
LicenseManager.activate_with_code(code)
  ↓
verify_local_code(code)            ← 本地 SHA-256 校验
  ↓ 成功
verify_cloud(code)                  ← 异步云端注册
  ↓ POST /api/hotel-register
Worker: 写 hotels 表, 标记 license_keys.is_used=1
  ↓ 返回 kill_date
PMS: 写 kill_switch_date 到 system_config
  ↓
启动完成
```

### 5.2 心跳轮询 + 远程指令处理

```
PMS HeartbeatService 线程 (5 min)
  ↓
GET /api/hotel-poll?hotel_id=HT_xxx
  ↓
Worker: 更新 hotels.last_seen, 返回 notifications[]
  ↓
PMS: process_notifications(notifications)
  ↓ 逐条处理
KILL_SWITCH  → bus.kill_switch_triggered.emit → vendor_lockdown.sync_lock_level
AD_PUSH      → db.set_config("pending_ad_text", ...)
SET_AD_SIGNATURE → db.set_config("ad_signature", ...)
REMOTE_CMD   → HeartbeatService._process_remote_commands
                ├── RESTART_APP / CLEAR_CACHE / SEND_ALERT
                ├── LOCK_LEVEL / PUSH_AD / SYNC_NOW
                └── DIAG_SNAPSHOT → POST return_url 上传快照
  ↓
POST /api/ack (批量确认)
```

### 5.3 活码扫描 → Bot 跳转

```
客人扫房间贴纸二维码 https://worker.example.com/r/ABC12345
  ↓
Worker: apiLiveQrResolve(req, db, env, code="ABC12345")
  ↓
SELECT hotel_id, room_id, token FROM live_qr_codes WHERE code=?
  ↓
SELECT guest_bot_id FROM hotel_bot_bindings WHERE hotel_id=?
  ↓
SELECT bot_username FROM telegram_bots WHERE bot_id=?
  ↓
302 Location: https://t.me/<bot_username>?start=<room_token>
  ↓
客人 Telegram 自动打开 Bot 对话
```

---

## 6. 维护与监控

### 6.1 日志

- Worker 日志：`npx wrangler tail` 实时查看
- 厂家操作审计：`admin_audit` 表（apiLicenseIssue / apiAdPush / apiSetAdSignature / apiHotelSuspend 自动写）

### 6.2 D1 数据库维护

```bash
# 备份数据库
npx wrangler d1 export shadowguard-cloud --output backup.sql

# 执行 SQL（紧急修复用）
npx wrangler d1 execute shadowguard-cloud --command "SELECT * FROM hotels LIMIT 10"

# 清理过期通知（也可等 Worker 自动清理）
npx wrangler d1 execute shadowguard-cloud --command \
  "DELETE FROM notifications WHERE acked=1 AND created_at < datetime('now', '-7 days')"
```

### 6.3 健康检查脚本

```bash
#!/bin/bash
# health-check.sh
WORKER_URL="${1:-https://shadowguard-cloud.workers.dev}"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$WORKER_URL/api/health")
if [ "$STATUS" = "200" ]; then
  echo "✅ Worker healthy"
  curl -s "$WORKER_URL/api/health" | jq .
else
  echo "❌ Worker unhealthy: HTTP $STATUS"
  exit 1
fi
```

---

## 7. 变更日志

| 日期 | 版本 | 变更 | 作者 |
|---|---|---|---|
| 2026-06-22 | 1.0 | 初版完整 README：25 个端点矩阵 + sub-g 预留 6 个握手包端点 + D1 schema + 鉴权策略 + 调用链路 | sub-h 厂家云端体系梳理员 |
