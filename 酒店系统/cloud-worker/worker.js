/**
 * ======================================================
 * ShadowGuard Cloud — Cloudflare Worker (v1.3)
 * Secrets: wrangler secret put BOT1_TOKEN / BOT2_TOKEN / ADMIN_PASSWORD
 * 轮询: ?lite=1 仅状态（心跳）；全量轮询拉通知并清理 TTL
 * ======================================================
 * 路由:
 *   POST /api/hotel-register      酒店注册+授权验证
 *   GET  /api/hotel-poll          酒店轮询（含通知下发）
 *   POST /api/ack                 通知确认
 *   POST /api/guest-order         Telegram Bot Webhook
 *   POST /api/payout-approve      老板审批
 *   POST /api/license-issue       生成授权码
 *   POST /api/ad-push             主动广告推送（一次性广播）
 *   POST /api/set-ad-signature    被动广告签名下发（持久附加在Bot消息底部）
 *   POST /api/hotel-suspend       停用/恢复酒店
 *   GET  /api/hotels-list         酒店列表（JSON）
 *   GET  /r/{code}                活码跳转 → 对应酒店 Bot + 房间深链接
 *   POST /api/bot-upsert          注册/更新 Bot（厂家，含 max_guests）
 *   GET  /api/bots-list           Bot 列表（厂家）
 *   POST /api/hotel-bot-bind      酒店绑定客人/工作 Bot
 *   POST /api/live-qr-sync        酒店同步房间活码
 *   GET  /api/live-qr-list        活码列表（厂家）
 *   POST /api/tg-webhook/{bot_id} 多 Bot Webhook（每 Bot 单独 setWebhook）
 *   POST /api/bot-roulette        Bot 轮盘（负载最低分配）
 *   POST /api/guest-upsert        客人登记/更新
 *   GET  /api/guest-list          客人列表（厂家）
 *   POST /api/guest-broadcast     厂家广播消息给客人
 *   GET  /admin                   厂家后台管理页面
 *   GET  /admin/bots              Bot 与活码管理
 *   GET  /admin/guests            客人管理与广播
 * ======================================================
 */

// ── 运行时配置（由 env / Secrets 注入，勿在仓库写 Token）──
const NOTIF_TTL_ACKED_DAYS = 7;
const NOTIF_TTL_PENDING_DAYS = 30;

function cfg(env) {
  return {
    bot1: env.BOT1_TOKEN || "",
    bot2: env.BOT2_TOKEN || "",
    adminPwd: env.ADMIN_PASSWORD || env.ADMIN_PWD || "",
  };
}

// ── 数据库初始化 ──
async function initDB(db) {
  const stmts = [
    `CREATE TABLE IF NOT EXISTS hotels (hotel_id TEXT PRIMARY KEY, hotel_name TEXT NOT NULL DEFAULT '未命名酒店', machine_code TEXT, license_key TEXT, salesperson_id TEXT, region TEXT DEFAULT '', status TEXT DEFAULT 'ACTIVE', kill_date TEXT DEFAULT '2099-12-31', created_at TEXT DEFAULT (datetime('now')), last_seen TEXT DEFAULT (datetime('now')));`,
    `CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, hotel_id TEXT NOT NULL, room_id TEXT NOT NULL, items_json TEXT NOT NULL DEFAULT '[]', total REAL NOT NULL DEFAULT 0, status TEXT DEFAULT 'PENDING', created_at TEXT DEFAULT (datetime('now')));`,
    `CREATE TABLE IF NOT EXISTS payouts (payout_id TEXT PRIMARY KEY, hotel_id TEXT NOT NULL, amount REAL NOT NULL, reason TEXT DEFAULT '', status TEXT DEFAULT 'PENDING', tx_id TEXT, created_at TEXT DEFAULT (datetime('now')));`,
    `CREATE TABLE IF NOT EXISTS notifications (notify_id TEXT PRIMARY KEY, hotel_id TEXT NOT NULL, notify_type TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', acked INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')));`,
    `CREATE TABLE IF NOT EXISTS license_keys (license_key TEXT PRIMARY KEY, hotel_id TEXT, features_json TEXT DEFAULT '{}', salesperson_id TEXT, expire_date TEXT DEFAULT '2099-12-31', issued_at TEXT DEFAULT (datetime('now')), is_used INTEGER DEFAULT 0);`,
    `CREATE TABLE IF NOT EXISTS telegram_bots (bot_id TEXT PRIMARY KEY, bot_token TEXT NOT NULL, bot_username TEXT NOT NULL, bot_role TEXT DEFAULT 'guest', label TEXT DEFAULT '', status TEXT DEFAULT 'ACTIVE', max_guests INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));`,
    `CREATE TABLE IF NOT EXISTS hotel_bot_bindings (hotel_id TEXT PRIMARY KEY, guest_bot_id TEXT, work_bot_id TEXT, updated_at TEXT DEFAULT (datetime('now')));`,
    `CREATE TABLE IF NOT EXISTS live_qr_codes (code TEXT PRIMARY KEY, hotel_id TEXT NOT NULL, room_id TEXT NOT NULL, token TEXT NOT NULL, guest_bot_id TEXT, status TEXT DEFAULT 'ACTIVE', scan_count INTEGER DEFAULT 0, last_scan_at TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')), UNIQUE(hotel_id, room_id));`,
    `CREATE TABLE IF NOT EXISTS guests (guest_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, tg_username TEXT DEFAULT '', tg_first_name TEXT DEFAULT '', hotel_id TEXT NOT NULL, bot_id TEXT NOT NULL, room_id TEXT DEFAULT '', status TEXT DEFAULT 'ACTIVE', meta_json TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')), last_active TEXT DEFAULT (datetime('now')));`
  ];
  for (const s of stmts) {
    try { await db.exec(s); } catch (e) { console.error('initDB:', e.message); }
  }
}

function genLiveCode() {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let s = '';
  for (let i = 0; i < 8; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return s;
}

function requireAdminPwd(body, env) {
  const pwd = body?.pwd || body?.admin_pwd || '';
  const expected = cfg(env).adminPwd;
  return !!expected && pwd === expected;
}

async function requireAdminRequest(req, env) {
  let body = {};
  try { body = await req.clone().json(); } catch (_) {}
  const headerPwd = req.headers.get('x-admin-password') || '';
  const expected = cfg(env).adminPwd;
  if (!expected) return { ok: false, body, response: Response.json({ ok: false, error: '未配置 ADMIN_PASSWORD，管理接口已锁定' }, { status: 503 }) };
  if ((body?.pwd || body?.admin_pwd || headerPwd) !== expected) {
    return { ok: false, body, response: Response.json({ ok: false, error: '密码错误' }, { status: 403 }) };
  }
  return { ok: true, body };
}

async function adminAudit(db, actor, action, detail) {
  try {
    await db.exec(`CREATE TABLE IF NOT EXISTS admin_audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, detail TEXT,
      created_at TEXT DEFAULT (datetime('now')))`);
    await db.prepare('INSERT INTO admin_audit (actor,action,detail) VALUES (?,?,?)')
      .bind(actor || 'admin', action || '', detail || '').run();
  } catch (_) {}
}

async function resolveHotelBotConfig(db, env, hotelId) {
  const c = cfg(env);
  let guestToken = c.bot1 || '';
  let workToken = (c.bot2 || c.bot1) || '';
  let botUsername = (env.BOT1_USERNAME || '').replace(/^@/, '');
  let guestBotId = 'default';

  if (!db || !hotelId) {
    return { guest_token: guestToken, work_token: workToken, bot_username: botUsername, guest_bot_id: guestBotId, provisioned_by: 'manufacturer_cloud' };
  }

  const bind = await db.prepare('SELECT guest_bot_id, work_bot_id FROM hotel_bot_bindings WHERE hotel_id=?').bind(hotelId).first();
  if (bind?.guest_bot_id) {
    const gb = await db.prepare("SELECT bot_id, bot_token, bot_username FROM telegram_bots WHERE bot_id=? AND status='ACTIVE'").bind(bind.guest_bot_id).first();
    if (gb?.bot_token) {
      guestToken = gb.bot_token;
      botUsername = (gb.bot_username || botUsername).replace(/^@/, '');
      guestBotId = gb.bot_id;
    }
  }
  if (bind?.work_bot_id) {
    const wb = await db.prepare("SELECT bot_token FROM telegram_bots WHERE bot_id=? AND status='ACTIVE'").bind(bind.work_bot_id).first();
    if (wb?.bot_token) workToken = wb.bot_token;
  }
  return { guest_token: guestToken, work_token: workToken, bot_username: botUsername, guest_bot_id: guestBotId, provisioned_by: 'manufacturer_cloud' };
}

async function resolveBotById(db, env, botId) {
  if (!botId || botId === 'default') return { token: cfg(env).bot1, username: (env.BOT1_USERNAME || '').replace(/^@/, '') };
  const row = await db.prepare("SELECT bot_token, bot_username FROM telegram_bots WHERE bot_id=? AND status='ACTIVE'").bind(botId).first();
  if (row) return { token: row.bot_token, username: (row.bot_username || '').replace(/^@/, '') };
  return { token: cfg(env).bot1, username: (env.BOT1_USERNAME || '').replace(/^@/, '') };
}

async function createNotification(db, hotelId, type, payload) {
  const id = `NTF_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
  await db.prepare('INSERT INTO notifications (notify_id,hotel_id,notify_type,payload_json) VALUES (?,?,?,?)')
    .bind(id, hotelId, type, JSON.stringify(payload)).run();
  return id;
}

async function sendTG(bot, chat, text, btns) {
  if (!bot || !chat || !String(bot).trim()) return;
  const body = { chat_id: chat, text, parse_mode: 'HTML' };
  if (btns && btns.length) body.reply_markup = JSON.stringify({ inline_keyboard: btns });
  await fetch(`https://api.telegram.org/bot${bot}/sendMessage`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  });
}

function uid() { return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2,8)}`.toUpperCase(); }

// ═══════════════ API 路由 ═══════════════

async function apiHotelRegister(req, db) {
  const b = await req.json();
  const { hotel_id, hotel_name, machine_code, license_key, salesperson_id, region } = b;
  if (!hotel_id || !machine_code) return Response.json({ ok: false, error: '缺少参数' }, { status: 400 });

  let valid = true;
  if (license_key) {
    const lic = await db.prepare("SELECT * FROM license_keys WHERE license_key=? AND is_used=0 AND expire_date>=date('now')").bind(license_key).first();
    if (!lic) valid = false;
  }

  const exist = await db.prepare('SELECT hotel_id FROM hotels WHERE hotel_id=?').bind(hotel_id).first();
  if (exist) {
    await db.prepare("UPDATE hotels SET hotel_name=?,machine_code=?,license_key=?,salesperson_id=?,region=?,last_seen=datetime('now') WHERE hotel_id=?")
      .bind(hotel_name || '未命名酒店', machine_code, license_key || '', salesperson_id || '', region || '', hotel_id).run();
  } else {
    await db.prepare('INSERT INTO hotels (hotel_id,hotel_name,machine_code,license_key,salesperson_id,region) VALUES (?,?,?,?,?,?)')
      .bind(hotel_id, hotel_name || '未命名酒店', machine_code, license_key || '', salesperson_id || '', region || '').run();
  }
  if (license_key && valid) {
    await db.prepare('UPDATE license_keys SET is_used=1,hotel_id=? WHERE license_key=?').bind(hotel_id, license_key).run();
  }
  return Response.json({ ok: true, license_valid: valid, status: 'ACTIVE', kill_date: '2099-12-31' });
}

async function purgeStaleNotifications(db, hotelId) {
  if (!db || !hotelId) return;
  await db.prepare(
    `DELETE FROM notifications WHERE hotel_id=? AND acked=1 AND created_at < datetime('now', '-${NOTIF_TTL_ACKED_DAYS} days')`
  ).bind(hotelId).run();
  await db.prepare(
    `DELETE FROM notifications WHERE hotel_id=? AND acked=0 AND created_at < datetime('now', '-${NOTIF_TTL_PENDING_DAYS} days')`
  ).bind(hotelId).run();
}

async function apiHotelPoll(req, db, env) {
  const url = new URL(req.url);
  const hid = url.searchParams.get('hotel_id');
  const lite = url.searchParams.get('lite') === '1';
  if (!hid) return Response.json({ ok: false, error: '缺少 hotel_id' }, { status: 400 });

  const hotel = await db.prepare('SELECT status,kill_date FROM hotels WHERE hotel_id=?').bind(hid).first();
  if (!hotel) return Response.json({ ok: false, error: '酒店未注册' }, { status: 404 });

  let ks = null;
  if (hotel.status === 'SUSPENDED') ks = { kill_date: hotel.kill_date || '2020-01-01' };
  else if (hotel.kill_date && hotel.kill_date !== '2099-12-31' && new Date() >= new Date(hotel.kill_date)) {
    ks = { kill_date: hotel.kill_date };
  }

  const botConfig = await resolveHotelBotConfig(db, env, hid);
  botConfig.live_qr_base = `${url.origin}/r/`;

  if (lite) {
    return Response.json({
      ok: true,
      lite: true,
      hotel_status: hotel.status,
      kill_switch: ks,
      notifications: [],
      bot_config: botConfig,
    });
  }

  await db.prepare("UPDATE hotels SET last_seen=datetime('now') WHERE hotel_id=?").bind(hid).run();
  await purgeStaleNotifications(db, hid);

  const notifs = await db.prepare(
    'SELECT notify_id,notify_type,payload_json FROM notifications WHERE hotel_id=? AND acked=0 ORDER BY created_at LIMIT 20'
  ).bind(hid).all();

  return Response.json({
    ok: true,
    lite: false,
    hotel_status: hotel.status,
    kill_switch: ks,
    bot_config: botConfig,
    notifications: (notifs.results || []).map(n => ({
      notify_id: n.notify_id,
      notify_type: n.notify_type,
      payload_json: n.payload_json,
    })),
  });
}

async function apiAck(req, db) {
  const { notify_id } = await req.json();
  if (notify_id) await db.prepare('UPDATE notifications SET acked=1 WHERE notify_id=?').bind(notify_id).run();
  return Response.json({ ok: true });
}

async function apiGuestOrder(req, db, env, botCtx) {
  const c = botCtx || cfg(env);
  const body = await req.json();
  if (body.message || body.callback_query) { await handleTG(body, db, c); return Response.json({ ok: true }); }

  const { hotel_id, room_id, items, total, chat_id } = body;
  if (!hotel_id || !room_id) return Response.json({ ok: false, error: '缺少参数' }, { status: 400 });

  const oid = `ORD_${uid()}`;
  const itemsJson = JSON.stringify(items || []);
  const amt = total || 0;
  await db.prepare('INSERT INTO orders (order_id,hotel_id,room_id,items_json,total) VALUES (?,?,?,?,?)').bind(oid, hotel_id, room_id, itemsJson, amt).run();
  await createNotification(db, hotel_id, 'NEW_ORDER', { order_id: oid, room_id, total: amt, items: itemsJson });

  if (chat_id) await sendTG(c.bot1, chat_id, `✅ 下单成功！\n订单: ${oid.slice(-8)}\n房号: ${room_id}\n金额: ¥${amt.toFixed(2)}`);
  return Response.json({ ok: true, order_id: oid });
}

async function apiPayoutApprove(req, db) {
  const { payout_id, action } = await req.json();
  if (!payout_id || !action) return Response.json({ ok: false, error: '缺少参数' }, { status: 400 });
  const p = await db.prepare('SELECT * FROM payouts WHERE payout_id=?').bind(payout_id).first();
  if (!p) return Response.json({ ok: false, error: '审批单不存在' }, { status: 404 });

  const ns = action === 'approve' ? 'APPROVED' : 'REJECTED';
  await db.prepare('UPDATE payouts SET status=? WHERE payout_id=?').bind(ns, payout_id).run();
  await createNotification(db, p.hotel_id, 'PAYOUT_RESULT', { payout_id, status: ns, amount: p.amount, reason: p.reason });
  return Response.json({ ok: true, status: ns });
}

async function apiLicenseIssue(req, db, env) {
  const adm = await requireAdminRequest(req, env); if (!adm.ok) return adm.response;
  const { features, expire_days, salesperson_id } = adm.body;
  const lk = `SG-${uid()}-${uid().slice(0,4)}`;
  const ed = new Date(); ed.setDate(ed.getDate() + (expire_days || 365));
  await db.prepare('INSERT INTO license_keys (license_key,features_json,salesperson_id,expire_date) VALUES (?,?,?,?)')
    .bind(lk, JSON.stringify(features || { all: true }), salesperson_id || '', ed.toISOString().slice(0, 10)).run();
  await adminAudit(db, salesperson_id || 'admin', 'LICENSE_ISSUE', lk);
  return Response.json({ ok: true, license_key: lk, expire_date: ed.toISOString().slice(0, 10) });
}

async function apiAdPush(req, db, env) {
  const adm = await requireAdminRequest(req, env); if (!adm.ok) return adm.response;
  const { hotel_ids, ad_text, photo_url, region } = adm.body;
  if (!hotel_ids || !Array.isArray(hotel_ids) || !hotel_ids.length) return Response.json({ ok: false, error: '请选择目标酒店' }, { status: 400 });
  let c = 0;
  for (const hid of hotel_ids) { await createNotification(db, hid, 'AD_PUSH', { text: ad_text || '', photo_url: photo_url || '', region: region || '' }); c++; }
  await adminAudit(db, 'admin', 'AD_PUSH', `${c} hotels`);
  return Response.json({ ok: true, pushed: c });
}

async function apiSetAdSignature(req, db, env) {
  const adm = await requireAdminRequest(req, env); if (!adm.ok) return adm.response;
  // 向指定酒店下发被动广告签名（附加在每条Bot消息底部）
  const { hotel_ids, signature, enabled } = adm.body;
  if (!hotel_ids || !Array.isArray(hotel_ids) || !hotel_ids.length) return Response.json({ ok: false, error: '请选择目标酒店' }, { status: 400 });
  let c = 0;
  for (const hid of hotel_ids) { await createNotification(db, hid, 'SET_AD_SIGNATURE', { signature: signature || '', enabled: enabled ?? 1 }); c++; }
  await adminAudit(db, 'admin', 'SET_AD_SIGNATURE', `${c} hotels`);
  return Response.json({ ok: true, pushed: c });
}

async function apiHotelsList(req, db, env) {
  const url = new URL(req.url);
  const pwd = url.searchParams.get('pwd');
  if (pwd !== cfg(env).adminPwd) return Response.json({ ok: false, error: '密码错误' }, { status: 403 });
  const hotels = (await db.prepare('SELECT hotel_id,hotel_name,region,status,salesperson_id,last_seen,kill_date FROM hotels ORDER BY last_seen DESC').all()).results || [];
  return Response.json({
    ok: true,
    hotels: hotels.map(h => ({
      id: h.hotel_id, name: h.hotel_name, region: h.region || '',
      status: h.status, salesperson_id: h.salesperson_id || '',
      last_seen: h.last_seen, kill_date: h.kill_date
    }))
  });
}

async function apiHotelSuspend(req, db, env) {
  const adm = await requireAdminRequest(req, env); if (!adm.ok) return adm.response;
  const { hotel_id, action } = adm.body;
  if (!hotel_id || !action) return Response.json({ ok: false, error: '缺少参数' }, { status: 400 });
  const ns = action === 'suspend' ? 'SUSPENDED' : 'ACTIVE';
  await db.prepare('UPDATE hotels SET status=? WHERE hotel_id=?').bind(ns, hotel_id).run();
  if (action === 'suspend') await createNotification(db, hotel_id, 'KILL_SWITCH', { kill_date: '2020-01-01' });
  await adminAudit(db, 'admin', 'HOTEL_SUSPEND', `${hotel_id}:${ns}`);
  return Response.json({ ok: true, status: ns });
}

// ── 多 Bot 注册 / 酒店绑定 ──

async function apiBotUpsert(req, db, env) {
  const b = await req.json();
  if (!requireAdminPwd(b, env)) return Response.json({ ok: false, error: '密码错误' }, { status: 403 });
  const { bot_id, bot_token, bot_username, bot_role, label, status, max_guests } = b;
  if (!bot_id || !bot_token || !bot_username) return Response.json({ ok: false, error: '缺少 bot_id / token / username' }, { status: 400 });
  const uid = String(bot_id).trim();
  const user = String(bot_username).replace(/^@/, '').trim();
  await db.prepare(
    `INSERT INTO telegram_bots (bot_id, bot_token, bot_username, bot_role, label, status, max_guests, updated_at)
     VALUES (?,?,?,?,?,?,?,datetime('now'))
     ON CONFLICT(bot_id) DO UPDATE SET bot_token=excluded.bot_token, bot_username=excluded.bot_username,
       bot_role=excluded.bot_role, label=excluded.label, status=excluded.status, max_guests=excluded.max_guests, updated_at=datetime('now')`
  ).bind(uid, bot_token.trim(), user, bot_role || 'guest', label || '', status || 'ACTIVE', Number(max_guests) || 0).run();
  return Response.json({ ok: true, bot_id: uid, bot_username: user, webhook_path: `/api/tg-webhook/${uid}` });
}

async function apiBotsList(req, db, env) {
  const url = new URL(req.url);
  if (url.searchParams.get('pwd') !== cfg(env).adminPwd) return Response.json({ ok: false, error: '密码错误' }, { status: 403 });
  const bots = (await db.prepare("SELECT bot_id, bot_username, bot_role, label, status, created_at FROM telegram_bots ORDER BY bot_id").all()).results || [];
  const bindings = (await db.prepare('SELECT hotel_id, guest_bot_id, work_bot_id FROM hotel_bot_bindings').all()).results || [];
  return Response.json({ ok: true, bots, bindings });
}

async function apiHotelBotBind(req, db, env) {
  const b = await req.json();
  if (!requireAdminPwd(b, env)) return Response.json({ ok: false, error: '密码错误' }, { status: 403 });
  const { hotel_id, guest_bot_id, work_bot_id } = b;
  if (!hotel_id) return Response.json({ ok: false, error: '缺少 hotel_id' }, { status: 400 });
  await db.prepare(
    `INSERT INTO hotel_bot_bindings (hotel_id, guest_bot_id, work_bot_id, updated_at)
     VALUES (?,?,?,datetime('now'))
     ON CONFLICT(hotel_id) DO UPDATE SET guest_bot_id=excluded.guest_bot_id, work_bot_id=excluded.work_bot_id, updated_at=datetime('now')`
  ).bind(hotel_id, guest_bot_id || null, work_bot_id || null).run();
  return Response.json({ ok: true, hotel_id, guest_bot_id, work_bot_id });
}

// ── 活码：酒店同步 + 跳转 ──

async function apiLiveQrSync(req, db, env) {
  const b = await req.json();
  const hotelId = (b.hotel_id || '').trim();
  const rooms = b.rooms;
  if (!hotelId || !Array.isArray(rooms) || !rooms.length) {
    return Response.json({ ok: false, error: '缺少 hotel_id 或 rooms' }, { status: 400 });
  }
  const hotel = await db.prepare('SELECT hotel_id FROM hotels WHERE hotel_id=?').bind(hotelId).first();
  if (!hotel) return Response.json({ ok: false, error: '酒店未注册，请先完成云端注册' }, { status: 404 });

  const origin = new URL(req.url).origin;
  // 活码只存 hotel+room+token；Bot 在扫码时按 hotel_bot_bindings 实时解析，换 Bot 不重印贴纸
  const out = [];

  for (const r of rooms) {
    const roomId = String(r.room_id || '').trim();
    const token = String(r.token || '').trim();
    if (!roomId || !token) continue;

    let row = await db.prepare('SELECT code FROM live_qr_codes WHERE hotel_id=? AND room_id=?').bind(hotelId, roomId).first();
    let code = row?.code;
    if (!code) {
      for (let i = 0; i < 5; i++) {
        code = genLiveCode();
        const clash = await db.prepare('SELECT code FROM live_qr_codes WHERE code=?').bind(code).first();
        if (!clash) break;
      }
    }
    await db.prepare(
      `INSERT INTO live_qr_codes (code, hotel_id, room_id, token, status, updated_at)
       VALUES (?,?,?,?,'ACTIVE',datetime('now'))
       ON CONFLICT(hotel_id, room_id) DO UPDATE SET token=excluded.token,
         status='ACTIVE', updated_at=datetime('now')`
    ).bind(code, hotelId, roomId, token).run();

    out.push({ room_id: roomId, code, live_url: `${origin}/r/${code}`, token });
  }
  return Response.json({ ok: true, hotel_id: hotelId, rooms: out, live_qr_base: `${origin}/r/` });
}

async function apiLiveQrList(req, db, env) {
  const url = new URL(req.url);
  if (url.searchParams.get('pwd') !== cfg(env).adminPwd) {
    return Response.json({ ok: false, error: '密码错误' }, { status: 403 });
  }
  const hotelId = url.searchParams.get('hotel_id') || '';
  let q = `SELECT q.code, q.hotel_id, q.room_id, q.status, q.scan_count, q.last_scan_at, q.updated_at,
           h.hotel_name, b.bot_username
           FROM live_qr_codes q
           LEFT JOIN hotels h ON h.hotel_id = q.hotel_id
           LEFT JOIN hotel_bot_bindings hb ON hb.hotel_id = q.hotel_id
           LEFT JOIN telegram_bots b ON b.bot_id = hb.guest_bot_id`;
  const binds = [];
  if (hotelId) { q += ' WHERE q.hotel_id=?'; binds.push(hotelId); }
  q += ' ORDER BY q.hotel_id, q.room_id LIMIT 500';
  const rows = (await db.prepare(q).bind(...binds).all()).results || [];
  const origin = url.origin;
  return Response.json({
    ok: true,
    items: rows.map(r => ({
      ...r,
      live_url: `${origin}/r/${r.code}`,
    })),
  });
}

async function apiLiveQrResolve(req, db, env, code) {
  const row = await db.prepare(
    `SELECT q.*, hb.guest_bot_id AS bind_guest_bot_id
     FROM live_qr_codes q
     LEFT JOIN hotel_bot_bindings hb ON hb.hotel_id = q.hotel_id
     WHERE q.code=? AND q.status='ACTIVE'`
  ).bind(code).first();

  if (!row) {
    return new Response('<html><body style="font-family:sans-serif;text-align:center;padding:40px"><h2>活码无效</h2><p>请联系酒店前台更换二维码。</p></body></html>', {
      status: 404, headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  }

  // 贴纸上的 /r/{code} 永远不变；Bot 以当前酒店绑定为准（可随时在后台更换）
  const botId = row.bind_guest_bot_id || 'default';
  const bot = await resolveBotById(db, env, botId);
  const botUser = bot.username;
  if (!botUser) {
    return new Response('Bot 未配置用户名，请联系厂家在后台绑定 Bot。', { status: 503 });
  }

  await db.prepare("UPDATE live_qr_codes SET scan_count=scan_count+1, last_scan_at=datetime('now') WHERE code=?").bind(code).run();

  const startPayload = `${row.hotel_id}_${row.room_id}_${row.token}`;
  const tgUrl = `https://t.me/${botUser}?start=${encodeURIComponent(startPayload)}`;
  return Response.redirect(tgUrl, 302);
}

// ── Bot 轮盘：选取负载最低的客人 Bot ──
async function apiBotRoulette(req, db, env) {
  const b = await req.json();
  const hotelId = b.hotel_id || '';
  if (!hotelId) return Response.json({ ok: false, error: '缺少 hotel_id' }, { status: 400 });

  const bots = (await db.prepare(
    `SELECT b.bot_id, b.bot_token, b.bot_username, b.bot_role, b.max_guests,
            (SELECT COUNT(*) FROM guests g WHERE g.bot_id = b.bot_id AND g.status='ACTIVE') as guest_count
     FROM telegram_bots b
     WHERE b.bot_role='guest' AND b.status='ACTIVE'
     ORDER BY guest_count ASC, b.bot_id ASC`
  ).all()).results || [];

  const available = bots.filter(b => b.max_guests <= 0 || Number(b.guest_count) < Number(b.max_guests));

  if (available.length === 0) {
    if (bots.length === 0) return Response.json({ ok: false, error: '无可用客人 Bot' }, { status: 503 });
    const fallback = bots[0];
    return Response.json({
      ok: true, bot_id: fallback.bot_id, bot_username: fallback.bot_username,
      bot_token: fallback.bot_token, guest_count: Number(fallback.guest_count),
      max_guests: Number(fallback.max_guests), total_bots: bots.length, available_bots: 0,
      pool_full: true
    });
  }

  const picked = available[0];
  return Response.json({
    ok: true, bot_id: picked.bot_id, bot_username: picked.bot_username,
    bot_token: picked.bot_token, guest_count: Number(picked.guest_count),
    max_guests: Number(picked.max_guests), total_bots: bots.length, available_bots: available.length
  });
}

// ── 客人注册 / 更新（Webhook 自动记录）──
async function apiGuestUpsert(req, db) {
  const b = await req.json();
  const { hotel_id, chat_id, bot_id, tg_username, tg_first_name, room_id, status } = b;
  if (!hotel_id || !chat_id || !bot_id) return Response.json({ ok: false, error: '缺少 hotel_id/chat_id/bot_id' }, { status: 400 });

  const guestId = `GST_${hotel_id}_${chat_id}`;
  const exist = await db.prepare('SELECT guest_id FROM guests WHERE guest_id=?').bind(guestId).first();

  if (exist) {
    await db.prepare(
      `UPDATE guests SET tg_username=?, tg_first_name=?, room_id=?, status=?,
       last_active=datetime('now') WHERE guest_id=?`
    ).bind(tg_username || '', tg_first_name || '', room_id || '', status || 'ACTIVE', guestId).run();
  } else {
    await db.prepare(
      `INSERT INTO guests (guest_id, chat_id, tg_username, tg_first_name, hotel_id, bot_id, room_id, status)
       VALUES (?,?,?,?,?,?,?,?)`
    ).bind(guestId, String(chat_id), tg_username || '', tg_first_name || '', hotel_id, bot_id, room_id || '', status || 'ACTIVE').run();
  }
  return Response.json({ ok: true, guest_id: guestId });
}

// ── 厂家查客人列表 ──
async function apiGuestList(req, db, env) {
  const url = new URL(req.url);
  if (url.searchParams.get('pwd') !== cfg(env).adminPwd) return Response.json({ ok: false, error: '密码错误' }, { status: 403 });
  const hotelId = url.searchParams.get('hotel_id') || '';
  let q = `SELECT g.*, h.hotel_name, b.bot_username
           FROM guests g
           LEFT JOIN hotels h ON h.hotel_id = g.hotel_id
           LEFT JOIN telegram_bots b ON b.bot_id = g.bot_id`;
  const binds = [];
  if (hotelId) { q += ' WHERE g.hotel_id=?'; binds.push(hotelId); }
  q += ' ORDER BY g.last_active DESC LIMIT 500';
  const rows = (await db.prepare(q).bind(...binds).all()).results || [];
  const botStats = (await db.prepare(
    `SELECT b.bot_id, b.bot_username, b.bot_role, b.max_guests,
            (SELECT COUNT(*) FROM guests g WHERE g.bot_id=b.bot_id AND g.status='ACTIVE') as guest_count
     FROM telegram_bots b WHERE b.status='ACTIVE' ORDER BY b.bot_id`
  ).all()).results || [];
  return Response.json({
    ok: true,
    guests: rows.map(g => ({
      guest_id: g.guest_id, chat_id: g.chat_id, tg_username: g.tg_username,
      tg_first_name: g.tg_first_name, hotel_id: g.hotel_id, hotel_name: g.hotel_name,
      bot_id: g.bot_id, bot_username: g.bot_username, room_id: g.room_id,
      status: g.status, last_active: g.last_active, created_at: g.created_at
    })),
    total: rows.length, bot_stats: botStats
  });
}

// ── 厂家广播消息给客人 ──
async function apiGuestBroadcast(req, db, env) {
  const adm = await requireAdminRequest(req, env); if (!adm.ok) return adm.response;
  const { guest_ids, hotel_ids, message, photo_url } = adm.body;
  if (!message && !photo_url) return Response.json({ ok: false, error: '缺少消息内容' }, { status: 400 });

  let guests = [];
  if (guest_ids && guest_ids.length > 0) {
    const placeholders = guest_ids.map(() => '?').join(',');
    guests = (await db.prepare(
      `SELECT * FROM guests WHERE guest_id IN (${placeholders}) AND status='ACTIVE'`
    ).bind(...guest_ids).all()).results || [];
  } else if (hotel_ids && hotel_ids.length > 0) {
    const placeholders = hotel_ids.map(() => '?').join(',');
    guests = (await db.prepare(
      `SELECT * FROM guests WHERE hotel_id IN (${placeholders}) AND status='ACTIVE'`
    ).bind(...hotel_ids).all()).results || [];
  } else {
    guests = (await db.prepare("SELECT * FROM guests WHERE status='ACTIVE' LIMIT 500").all()).results || [];
  }

  let sent = 0, fails = 0;
  for (const g of guests) {
    try {
      const bot = await resolveBotById(db, env, g.bot_id);
      if (photo_url && bot.token) {
        await fetch(`https://api.telegram.org/bot${bot.token}/sendPhoto`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chat_id: g.chat_id, photo: photo_url, caption: message || '', parse_mode: 'HTML' })
        });
      } else if (bot.token) {
        await sendTG(bot.token, g.chat_id, message || '');
      }
      sent++;
    } catch (_) { fails++; }
  }

  await adminAudit(db, 'admin', 'GUEST_BROADCAST', `${sent}/${guests.length} ok fails=${fails}`);
  return Response.json({ ok: true, sent, total: guests.length, fails });
}

// ═══════════════ Telegram Webhook ═══════════════

async function handleTG(update, db, c) {
  const BOT1 = c.bot1, botId = c.bot_id || 'default';
  let hotelId = '', roomId = '';

  // ── 自动登记客人 ──
  const autoUpsert = async (hid, rid) => {
    if (!hid || !db) return;
    const msg = update.message || (update.callback_query && update.callback_query.message);
    const chat = msg && msg.chat;
    if (!chat) return;
    const chatId = String(chat.id);
    try {
      await db.prepare(
        `INSERT INTO guests (guest_id, chat_id, tg_username, tg_first_name, hotel_id, bot_id, room_id, status)
         VALUES (?,?,?,?,?,?,?,'ACTIVE')
         ON CONFLICT(guest_id) DO UPDATE SET
           tg_username=excluded.tg_username, tg_first_name=excluded.tg_first_name,
           room_id=CASE WHEN excluded.room_id!='' THEN excluded.room_id ELSE guests.room_id END,
           last_active=datetime('now')`
      ).bind(`GST_${hid}_${chatId}`, chatId,
        (chat.username || '').replace(/^@/, ''), chat.first_name || '',
        hid, botId, rid || '').run();
    } catch (_) {}
  };

  if (update.message) {
    const m = update.message, chat = m.chat.id.toString(), txt = (m.text || '').trim();
    if (txt.startsWith('/start')) {
      const p = txt.replace('/start', '').trim();
      if (p) {
        const parts = p.split('_');
        if (parts.length >= 2) {
          hotelId = parts[0]; roomId = parts.slice(1).join('_');
          await sendTG(BOT1, chat, `🏨 欢迎！${roomId} 房间\n请选择服务:`, [
            [{ text: '🛒 点购商品', callback_data: `shop:${hotelId}:${roomId}` }],
            [{ text: '🎯 连接WiFi', callback_data: `wifi:${hotelId}:${roomId}` }],
            [{ text: '📞 呼叫前台', callback_data: `call:${hotelId}:${roomId}` }],
            [{ text: '🧹 需要打扫', callback_data: `clean:${hotelId}:${roomId}` }],
          ]);
        }
      } else {
        await sendTG(BOT1, chat, '欢迎使用酒店服务助手！请扫描房间内的二维码获取服务。');
      }
    }
    if (hotelId) await autoUpsert(hotelId, roomId);
  }
  if (update.callback_query) {
    const cb = update.callback_query, chat = cb.message.chat.id.toString(), data = cb.data;
    await fetch(`https://api.telegram.org/bot${BOT1}/answerCallbackQuery`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ callback_query_id: cb.id })
    });
    const parts = data.split(':');
    if (parts.length >= 2) { hotelId = parts[1]; roomId = parts[2] || ''; }
    if (data.startsWith('shop:')) await sendTG(BOT1, chat, `🛒 商品菜单\n请回复需要的商品，如"矿泉水 2"\n或直接描述需求，前台会处理。`);
    else if (data.startsWith('wifi:')) await sendTG(BOT1, chat, '📶 WiFi: Hotel-WiFi / 密码: hotel2024');
    else if (data.startsWith('call:')) { await createNotification(db, hotelId, 'SERVICE_REQUEST', { room_id: roomId, type: 'CALL_FRONT' }); await sendTG(BOT1, chat, '📞 已呼叫前台'); }
    else if (data.startsWith('clean:')) { await createNotification(db, hotelId, 'SERVICE_REQUEST', { room_id: roomId, type: 'NEED_CLEAN' }); await sendTG(BOT1, chat, '🧹 已通知保洁'); }
    else if (data.startsWith('payout_approve:')) await apiPayoutApprove(new Request('about:blank', { method: 'POST', body: JSON.stringify({ payout_id: parts[1], action: 'approve' }) }), db);
    else if (data.startsWith('payout_reject:')) await apiPayoutApprove(new Request('about:blank', { method: 'POST', body: JSON.stringify({ payout_id: parts[1], action: 'reject' }) }), db);
    if (hotelId) await autoUpsert(hotelId, roomId);
  }
}

// ═══════════════ Admin 页面 ═══════════════

async function handleAdmin(req, db, env) {
  const url = new URL(req.url);
  const c = cfg(env);
  if (!c.adminPwd || url.searchParams.get('pwd') !== c.adminPwd) {
    return new Response(loginHTML(), { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  }
  if (!db) {
    return Response.json({ ok: false, error: 'D1 未绑定' }, { status: 503 });
  }
  const hotels = (await db.prepare('SELECT * FROM hotels ORDER BY last_seen DESC').all()).results || [];
  const hlist = hotels.map(h => ({ id: h.hotel_id, name: h.hotel_name, region: h.region || '-', status: h.status, kill_date: h.kill_date, salesperson: h.salesperson_id || '-', last_seen: h.last_seen }));
  const total = hlist.length, active = hlist.filter(h => h.status === 'ACTIVE').length;
  const orders = (await db.prepare('SELECT COUNT(*) as c FROM orders').first())?.c || 0;
  const payouts = (await db.prepare('SELECT COUNT(*) as c FROM payouts WHERE status="PENDING"').first())?.c || 0;
  const licenses = (await db.prepare('SELECT * FROM license_keys ORDER BY issued_at DESC LIMIT 50').all()).results || [];
  return new Response(dashHTML(hlist, { total, active, orders, payouts }, licenses, url.searchParams.get('pwd') || ''), { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

function loginHTML() {
  return '<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ShadowGuard</title><style>'
    + '*{margin:0;padding:0;box-sizing:border-box}body{font-family:"Microsoft YaHei",sans-serif;background:#0F172A;color:#E2E8F0;display:flex;justify-content:center;align-items:center;height:100vh}'
    + '.box{background:#1E293B;padding:40px;border-radius:16px;width:380px;text-align:center}h1{font-size:24px;margin-bottom:8px}h1 span{color:#EF4444}'
    + 'input{width:100%;padding:12px;margin:12px 0;border:1px solid #334155;border-radius:8px;background:#0F172A;color:#E2E8F0;font-size:16px}'
    + 'button{width:100%;padding:12px;background:#EF4444;color:#FFF;border:none;border-radius:8px;font-size:16px;font-weight:700;cursor:pointer}'
    + '.hint{color:#64748B;font-size:12px;margin-top:12px}</style></head><body><div class="box">'
    + '<h1>🛡️ Shadow<span>Guard</span></h1><p style="color:#94A3B8;margin-bottom:20px">厂家管理控制台</p>'
    + '<form method="get"><input type="password" name="pwd" placeholder="管理密码" autofocus><button>登录</button></form><p class="hint">请在 Cloudflare Secret 中配置 ADMIN_PASSWORD 后使用。</p></div></body></html>';
}

function dashHTML(hotels, stats, licenses, pwd) {
  const sc = s => s === 'ACTIVE' ? '#16A34A' : s === 'SUSPENDED' ? '#DC2626' : '#94A3B8';
  const st = s => s === 'ACTIVE' ? '运营中' : s === 'SUSPENDED' ? '已停用' : s;
  const hr = hotels.map(h => `<tr><td style="color:#93C5FD">${h.name}</td><td>${h.region}</td><td><span style="color:${sc(h.status)};font-weight:700">${st(h.status)}</span></td><td>${h.salesperson}</td><td>${(h.last_seen||'').slice(5,16)||'-'}</td><td><button class="btn-sm ${h.status==='ACTIVE'?'btn-danger':'btn-success'}" onclick="toggleHotel('${h.id}','${h.status==='ACTIVE'?'suspend':'resume'}')">${h.status==='ACTIVE'?'停用':'恢复'}</button></td></tr>`).join('');
  const lr = licenses.map(l => `<tr><td style="font-family:monospace;font-size:11px">${l.license_key}</td><td>${l.hotel_id||'未使用'}</td><td>${l.expire_date}</td><td>${l.salesperson_id||'-'}</td></tr>`).join('');
  return '<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ShadowGuard 厂商后台</title><style>'
    + '*{margin:0;padding:0;box-sizing:border-box}body{font-family:"Microsoft YaHei",sans-serif;background:#0F172A;color:#E2E8F0;min-height:100vh}'
    + '.header{background:#1E293B;padding:16px 24px;display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #EF4444}'
    + '.header h1{font-size:20px}.header h1 span{color:#EF4444}.header .stats{display:flex;gap:20px;font-size:13px;color:#94A3B8}'
    + '.container{max-width:1200px;margin:20px auto;padding:0 20px}.card{background:#1E293B;border-radius:12px;padding:20px;margin-bottom:20px}'
    + '.card h2{font-size:16px;margin-bottom:14px;padding-bottom:8px;border-bottom:1px solid #334155}'
    + 'table{width:100%;border-collapse:collapse}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #1E293B;font-size:13px}'
    + 'th{color:#64748B;font-weight:600;font-size:11px}tr:hover{background:#0F172A}'
    + 'button{padding:6px 14px;border:none;border-radius:6px;font-weight:700;cursor:pointer;font-size:12px}'
    + '.btn-danger{background:#DC2626;color:#FFF}.btn-success{background:#16A34A;color:#FFF}.btn-primary{background:#3B82F6;color:#FFF}.btn-sm{padding:4px 10px;font-size:11px}'
    + 'form input,form select{background:#0F172A;border:1px solid #334155;border-radius:6px;padding:8px 12px;color:#E2E8F0;font-size:13px;margin:0 6px}'
    + '.toast{position:fixed;top:20px;right:20px;background:#16A34A;color:#FFF;padding:12px 20px;border-radius:8px;font-weight:700;z-index:999;display:none}'
    + '</style></head><body><div class="header"><h1>🛡️ Shadow<span>Guard</span> 厂商控制台</h1>'
    + `<div class="stats"><span>酒店: <b>${stats.total}</b></span><span>运营: <b>${stats.active}</b></span><span>订单: <b>${stats.orders}</b></span><span>审批: <b>${stats.payouts}</b></span></div></div>`
    + '<div class="container"><div class="toast" id="toast"></div><div class="card"><h2>⚡ 快捷操作</h2><div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">'
    + '<div><b style="font-size:12px;color:#94A3B8">生成授权码</b><br><form onsubmit="issueLicense(event)" style="display:flex;gap:6px;margin-top:4px">'
    + '<input type="number" id="lic_days" value="365" style="width:70px"><input type="text" id="lic_sales" placeholder="业务员ID" style="width:100px"><button class="btn-primary" type="submit">生成</button></form></div>'
    + '<div><b style="font-size:12px;color:#94A3B8">推送广告（主动）</b><br><form onsubmit="pushAd(event)" style="display:flex;gap:6px;margin-top:4px">'
    + `<select id="ad_hotels" multiple style="width:150px;height:40px">${hotels.map(h => `<option value="${h.id}">${h.name}</option>`).join('')}</select>`
    + '<input type="text" id="ad_text" placeholder="广告文案（一次性推送）" style="width:200px"><button class="btn-primary" type="submit">推送</button></form></div>'
    + '<div><b style="font-size:12px;color:#94A3B8">设置签名（被动广告）</b><br><form onsubmit="setAdSig(event)" style="display:flex;gap:6px;margin-top:4px">'
    + `<select id="sig_hotels" multiple style="width:150px;height:40px">${hotels.map(h => `<option value="${h.id}">${h.name}</option>`).join('')}</select>`
    + '<input type="text" id="sig_text" placeholder="签名文案（留空=清除）" style="width:200px"><button class="btn-primary" type="submit">下发</button></form>'
    + '<p style="font-size:11px;color:#64748B;margin-top:4px">签名将附加在每条Bot消息底部，酒店无法修改</p></div></div></div>'
    + `<div class="card"><h2>🏨 酒店列表 (${hotels.length})</h2><table><thead><tr><th>名称</th><th>区域</th><th>状态</th><th>业务员</th><th>在线</th><th>操作</th></tr></thead><tbody>${hr||'<tr><td colspan="6" style="color:#64748B;text-align:center">暂无数据</td></tr>'}</tbody></table></div>`
    + `<div class="card"><h2>🔑 授权码</h2><table><thead><tr><th>授权码</th><th>绑定酒店</th><th>到期</th><th>业务员</th></tr></thead><tbody>${lr||'<tr><td colspan="4" style="color:#64748B;text-align:center">暂无</td></tr>'}</tbody></table></div></div>`
    + `<script>const ADMIN_PWD=${JSON.stringify(pwd)};async function api(p,d){d=d||{};d.pwd=ADMIN_PWD;const r=await fetch(p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});return r.json()}`
    + 'function toast(m){const t=document.getElementById("toast");t.textContent=m;t.style.display="block";setTimeout(()=>t.style.display="none",2000)}'
    + 'async function toggleHotel(id,a){await api("/api/hotel-suspend",{hotel_id:id,action:a});toast(a==="suspend"?"已停用":"已恢复");setTimeout(()=>location.reload(),500)}'
    + 'async function issueLicense(e){e.preventDefault();const d=parseInt(document.getElementById("lic_days").value)||365,s=document.getElementById("lic_sales").value,r=await api("/api/license-issue",{expire_days:d,salesperson_id:s,features:{all:true}});toast("授权码: "+r.license_key);setTimeout(()=>location.reload(),1000)}'
    + 'async function pushAd(e){e.preventDefault();const s=document.getElementById("ad_hotels"),ids=Array.from(s.selectedOptions).map(o=>o.value),t=document.getElementById("ad_text").value;if(!ids.length)return toast("请选择酒店");const r=await api("/api/ad-push",{hotel_ids:ids,ad_text:t});toast("已推送给"+r.pushed+"家酒店")}'
    + 'async function setAdSig(e){e.preventDefault();const s=document.getElementById("sig_hotels"),ids=Array.from(s.selectedOptions).map(o=>o.value),t=document.getElementById("sig_text").value;if(!ids.length)return toast("请选择酒店");const r=await api("/api/set-ad-signature",{hotel_ids:ids,signature:t});toast(t?"签名已下发给"+r.pushed+"家酒店":"已清除"+r.pushed+"家酒店的签名")}'
    + `</script><p style="margin-top:16px"><a href="/admin/bots?pwd=${encodeURIComponent(pwd)}" style="color:#93C5FD">→ Bot 注册与活码管理</a> | <a href="/admin/guests?pwd=${encodeURIComponent(pwd)}" style="color:#93C5FD">→ 客人管理与广播</a></p></body></html>`;
}

function adminBotsHTML(hotels, bots, bindings, liveItems, origin, pwd) {
  const botRows = (bots || []).map(b =>
    `<tr><td><code>${b.bot_id}</code></td><td>@${b.bot_username}</td><td>${b.bot_role}</td><td>${b.label || '-'}</td><td>${b.status}</td>`
    + `<td><code>/api/tg-webhook/${b.bot_id}</code></td></tr>`).join('');
  const bindOpts = (bots || []).map(b => `<option value="${b.bot_id}">@${b.bot_username} (${b.bot_id})</option>`).join('');
  const hotelOpts = (hotels || []).map(h => `<option value="${h.id}">${h.name} (${h.id})</option>`).join('');
  const bindRows = (bindings || []).map(b =>
    `<tr><td>${b.hotel_id}</td><td>${b.guest_bot_id || '-'}</td><td>${b.work_bot_id || '-'}</td></tr>`).join('');
  const qrRows = (liveItems || []).slice(0, 200).map(q =>
    `<tr><td><a href="${q.live_url}" target="_blank"><code>${q.code}</code></a></td><td>${q.hotel_id}</td><td>${q.room_id}</td>`
    + `<td>@${q.bot_username || '-'}</td><td>${q.scan_count || 0}</td><td>${(q.last_scan_at || '-').slice(0, 16)}</td></tr>`).join('');
  return '<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<title>Bot 与活码 — ShadowGuard</title><style>'
    + '*{margin:0;padding:0;box-sizing:border-box}body{font-family:"Microsoft YaHei",sans-serif;background:#0F172A;color:#E2E8F0;min-height:100vh}'
    + '.header{background:#1E293B;padding:16px 24px;border-bottom:2px solid #3B82F6;display:flex;justify-content:space-between;align-items:center}'
    + '.container{max-width:1200px;margin:20px auto;padding:0 20px}.card{background:#1E293B;border-radius:12px;padding:20px;margin-bottom:20px}'
    + '.card h2{font-size:16px;margin-bottom:12px;border-bottom:1px solid #334155;padding-bottom:8px}'
    + 'table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px 10px;border-bottom:1px solid #1E293B;text-align:left}'
    + 'th{color:#64748B}input,select{background:#0F172A;border:1px solid #334155;border-radius:6px;padding:8px;color:#E2E8F0;margin:4px}'
    + 'button{padding:8px 14px;border:none;border-radius:6px;font-weight:700;cursor:pointer;background:#3B82F6;color:#fff}'
    + '.hint{font-size:11px;color:#64748B;margin-top:8px;line-height:1.5}'
    + 'a{color:#93C5FD}</style></head><body>'
    + '<div class="header"><h1>🤖 Bot 注册 · 🔗 活码系统</h1><a href="/admin?pwd=' + encodeURIComponent(pwd) + '">← 返回总览</a></div>'
    + '<div class="container">'
    + '<div class="card"><h2>注册新 Bot</h2><form id="fBot" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">'
    + '<input name="bot_id" placeholder="bot_id 如 HTL_A_GUEST" style="width:140px">'
    + '<input name="bot_token" placeholder="Token" style="width:220px">'
    + '<input name="bot_username" placeholder="用户名不含@" style="width:120px">'
    + '<select name="bot_role"><option value="guest">guest</option><option value="work">work</option></select>'
    + '<input name="label" placeholder="备注" style="width:100px">'
    + '<button type="submit">保存 Bot</button></form>'
    + '<p class="hint">房间贴纸印 <code>' + origin + '/r/xxxx</code>（固定）。换 Bot 只改下方绑定，不重印。Webhook：<code>' + origin + '/api/tg-webhook/{bot_id}</code></p>'
    + '<table><thead><tr><th>ID</th><th>用户名</th><th>角色</th><th>备注</th><th>状态</th><th>Webhook</th></tr></thead><tbody>'
    + (botRows || '<tr><td colspan="6">暂无，可用默认 BOT1</td></tr>') + '</tbody></table></div>'
    + '<div class="card"><h2>酒店 ↔ Bot 绑定</h2><form id="fBind" style="display:flex;flex-wrap:wrap;gap:8px">'
    + '<select name="hotel_id" style="width:200px"><option value="">选酒店</option>' + hotelOpts + '</select>'
    + '<select name="guest_bot_id" style="width:180px"><option value="">客人 Bot</option>' + bindOpts + '</select>'
    + '<select name="work_bot_id" style="width:180px"><option value="">工作 Bot</option>' + bindOpts + '</select>'
    + '<button type="submit">绑定</button></form>'
    + '<table style="margin-top:12px"><thead><tr><th>酒店ID</th><th>客人Bot</th><th>工作Bot</th></tr></thead><tbody>'
    + (bindRows || '<tr><td colspan="3">暂无绑定，默认使用全局 BOT1/BOT2</td></tr>') + '</tbody></table></div>'
    + '<div class="card"><h2>活码列表（打印用固定链接 ' + origin + '/r/xxxx）</h2>'
    + '<p class="hint">活码短链固定；扫码时按「酒店↔Bot 绑定」实时跳转。换 Bot 只改绑定表，贴纸不用动。</p>'
    + '<table><thead><tr><th>活码</th><th>酒店</th><th>房间</th><th>Bot</th><th>扫码次数</th><th>最近扫码</th></tr></thead><tbody>'
    + (qrRows || '<tr><td colspan="6">暂无活码，请酒店端同步房间</td></tr>') + '</tbody></table></div></div>'
    + '<script>const PWD=' + JSON.stringify(pwd) + ';async function api(p,d){d.pwd=PWD;const r=await fetch(p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});return r.json()}'
    + 'document.getElementById("fBot").onsubmit=async e=>{e.preventDefault();const f=e.target,r=await api("/api/bot-upsert",{bot_id:f.bot_id.value,bot_token:f.bot_token.value,bot_username:f.bot_username.value,bot_role:f.bot_role.value,label:f.label.value});alert(r.ok?"已保存 "+r.bot_id:r.error);location.reload()}'
    + 'document.getElementById("fBind").onsubmit=async e=>{e.preventDefault();const f=e.target,r=await api("/api/hotel-bot-bind",{hotel_id:f.hotel_id.value,guest_bot_id:f.guest_bot_id.value,work_bot_id:f.work_bot_id.value});alert(r.ok?"已绑定":"失败:"+r.error);location.reload()}</script></body></html>';
}

async function handleAdminBots(req, db, env) {
  const url = new URL(req.url);
  const pwd = url.searchParams.get('pwd') || '';
  if (pwd !== cfg(env).adminPwd) {
    return new Response(loginHTML(), { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  }
  const hotels = (await db.prepare('SELECT hotel_id, hotel_name FROM hotels ORDER BY hotel_name').all()).results || [];
  const hlist = hotels.map(h => ({ id: h.hotel_id, name: h.hotel_name }));
  const bots = (await db.prepare("SELECT bot_id, bot_username, bot_role, label, status FROM telegram_bots ORDER BY bot_id").all()).results || [];
  const bindings = (await db.prepare('SELECT hotel_id, guest_bot_id, work_bot_id FROM hotel_bot_bindings').all()).results || [];
  const liveRes = await apiLiveQrList(new Request(url.origin + '/api/live-qr-list?pwd=' + encodeURIComponent(pwd)), db, env);
  const liveData = await liveRes.json();
  const origin = url.origin;
  return new Response(adminBotsHTML(hlist, bots, bindings, liveData.items || [], origin, pwd), {
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
}

// ── 客人管理页面 ──
async function handleAdminGuests(req, db, env) {
  const url = new URL(req.url);
  const pwd = url.searchParams.get('pwd') || '';
  if (pwd !== cfg(env).adminPwd) return new Response(loginHTML(), { headers: { 'Content-Type': 'text/html; charset=utf-8' } });

  const hotelFilter = url.searchParams.get('hotel_id') || '';
  const guestRes = await apiGuestList(new Request(url.origin + '/api/guest-list?pwd=' + encodeURIComponent(pwd) + (hotelFilter ? '&hotel_id=' + encodeURIComponent(hotelFilter) : '')), db, env);
  const guestData = await guestRes.json();

  const hotels = (await db.prepare('SELECT hotel_id, hotel_name FROM hotels ORDER BY hotel_name').all()).results || [];
  return new Response(adminGuestsHTML(guestData.guests || [], guestData.bot_stats || [], guestData.total || 0, hotels.map(h => ({ id: h.hotel_id, name: h.hotel_name })), pwd), {
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
}

function adminGuestsHTML(guests, botStats, total, hotels, pwd) {
  const hotelOpts = hotels.map(h => `<option value="${h.id}" ${new URL(location).searchParams.get('hotel_id')===h.id?'selected':''}>${h.name}</option>`).join('');
  const guestRows = guests.map(g =>
    `<tr><td>${g.hotel_name||'-'}</td><td>${g.tg_username||g.tg_first_name||'-'}</td><td><code>${g.chat_id}</code></td>`
    + `<td>@${g.bot_username||g.bot_id||'-'}</td><td>${g.room_id||'-'}</td><td>${(g.last_active||'').slice(0,16)}</td></tr>`).join('');

  const botStatRows = botStats.map(b =>
    `<tr><td><code>${b.bot_id}</code></td><td>@${b.bot_username}</td><td>${b.bot_role}</td>`
    + `<td>${b.guest_count||0}</td><td>${b.max_guests||'无限制'}</td>`
    + `<td>${b.max_guests>0 && b.guest_count>=b.max_guests?'<span style="color:#DC2626">满</span>':'<span style="color:#16A34A">可用</span>'}</td></tr>`).join('');

  const hotelFilterOpts = hotels.map(h => `<option value="${h.id}">${h.name}</option>`).join('');

  return '<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<title>客人管理 — ShadowGuard</title><style>'
    + '*{margin:0;padding:0;box-sizing:border-box}body{font-family:"Microsoft YaHei",sans-serif;background:#0F172A;color:#E2E8F0;min-height:100vh}'
    + '.header{background:#1E293B;padding:16px 24px;border-bottom:2px solid #10B981;display:flex;justify-content:space-between;align-items:center}'
    + '.container{max-width:1400px;margin:20px auto;padding:0 20px}.card{background:#1E293B;border-radius:12px;padding:20px;margin-bottom:20px}'
    + '.card h2{font-size:16px;margin-bottom:12px;border-bottom:1px solid #334155;padding-bottom:8px}'
    + 'table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px 10px;border-bottom:1px solid #1E293B;text-align:left}'
    + 'th{color:#64748B}input,select,textarea{background:#0F172A;border:1px solid #334155;border-radius:6px;padding:8px;color:#E2E8F0;margin:4px;font-family:inherit}'
    + 'textarea{width:100%;min-height:60px}button{padding:8px 14px;border:none;border-radius:6px;font-weight:700;cursor:pointer}'
    + '.btn-primary{background:#3B82F6;color:#fff}.btn-danger{background:#DC2626;color:#fff}.btn-success{background:#16A34A;color:#fff}'
    + '.btn-sm{padding:4px 10px;font-size:11px}'
    + '.toast{position:fixed;top:20px;right:20px;background:#16A34A;color:#FFF;padding:12px 20px;border-radius:8px;font-weight:700;z-index:999;display:none}'
    + '.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}'
    + '@media(max-width:900px){.grid2{grid-template-columns:1fr}}'
    + 'a{color:#93C5FD}'
    + '</style></head><body>'
    + '<div class="header"><h1>👥 客人管理</h1><div><a href="/admin?pwd=' + encodeURIComponent(pwd) + '">← 返回总览</a> | <a href="/admin/bots?pwd=' + encodeURIComponent(pwd) + '">Bot 管理</a></div></div>'
    + '<div class="container"><div class="toast" id="toast"></div>'
    + '<div class="grid2">'
    + '<div class="card"><h2>📢 厂家广播</h2><form id="fBroadcast">'
    + '<label style="font-size:11px;color:#94A3B8">目标酒店（留空=全部）</label>'
    + '<select id="bc_hotels" multiple style="width:100%;height:80px">' + hotelFilterOpts + '</select>'
    + '<textarea id="bc_msg" placeholder="广播消息内容（支持 HTML）"></textarea>'
    + '<button class="btn-primary" type="submit">发送广播</button>'
    + '<p style="font-size:11px;color:#64748B;margin-top:4px">消息将通过客人各自的 Bot 发送</p></form></div>'
    + '<div class="card"><h2>🤖 Bot 负载</h2><table><thead><tr><th>Bot ID</th><th>用户名</th><th>角色</th><th>客人数</th><th>上限</th><th>状态</th></tr></thead><tbody>'
    + (botStatRows || '<tr><td colspan="6">暂无 Bot</td></tr>')
    + '</tbody></table></div></div>'
    + '<div class="card"><h2>🛏️ 客人列表 (' + total + ' 位)</h2>'
    + '<form style="display:flex;gap:8px;align-items:center;margin-bottom:12px" onsubmit="filterHotel(event)">'
    + '<select id="filter_hotel" style="width:200px"><option value="">全部酒店</option>' + hotelOpts + '</select>'
    + '<button class="btn-sm btn-primary" type="submit">筛选</button></form>'
    + '<table><thead><tr><th>酒店</th><th>客人</th><th>Chat ID</th><th>Bot</th><th>房间</th><th>最近活跃</th></tr></thead><tbody>'
    + (guestRows || '<tr><td colspan="6">暂无客人数据</td></tr>')
    + '</tbody></table></div></div>'
    + '<script>'
    + 'const PWD=' + JSON.stringify(pwd) + ';'
    + 'async function api(p,d){d.pwd=PWD;const r=await fetch(p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});return r.json()}'
    + 'function toast(m){const t=document.getElementById("toast");t.textContent=m;t.style.display="block";setTimeout(()=>t.style.display="none",2000)}'
    + 'function filterHotel(e){e.preventDefault();const h=document.getElementById("filter_hotel").value;location.search=h?("?pwd="+encodeURIComponent(PWD)+"&hotel_id="+encodeURIComponent(h)):("?pwd="+encodeURIComponent(PWD))}'
    + 'document.getElementById("fBroadcast").onsubmit=async e=>{e.preventDefault();'
    + 'const s=document.getElementById("bc_hotels"),ids=Array.from(s.selectedOptions).map(o=>o.value),'
    + 'm=document.getElementById("bc_msg").value;'
    + 'if(!m)return toast("请输入消息内容");'
    + 'const r=await api("/api/guest-broadcast",{hotel_ids:ids.length?ids:undefined,message:m});'
    + 'toast("已发送: "+r.sent+"/"+r.total+(r.fails?" (失败"+r.fails+")":""))}'
    + '</script></body></html>';
}

// ═══════════════ 主入口 ═══════════════

export default {
  async fetch(request, env, ctx) {
    return handleRequest(request, env);
  },
};

async function handleRequest(req, env) {
  const url = new URL(req.url), path = url.pathname, method = req.method;

  if (path === '/api/health' || path === '/health') {
    return Response.json({
      ok: true,
      service: 'shadowguard-cloud',
      db: Boolean(env.DB),
    });
  }

  let db = env.DB || null;
  try {
    if (db) await initDB(db);

    if (path.startsWith('/r/') && method === 'GET') {
      const code = path.slice(3).split('/')[0];
      if (code) return apiLiveQrResolve(req, db, env, code);
    }
    if (path.startsWith('/api/tg-webhook/') && method === 'POST') {
      const botId = path.slice('/api/tg-webhook/'.length).split('/')[0];
      const bot = await resolveBotById(db, env, botId);
      const botCtx = { bot1: bot.token, bot2: bot.token, bot_id: botId };
      return apiGuestOrder(req, db, env, botCtx);
    }
    if (path === '/api/hotel-register' && method === 'POST') return apiHotelRegister(req, db);
    if (path === '/api/hotel-poll' && method === 'GET') return apiHotelPoll(req, db, env);
    if (path === '/api/ack' && method === 'POST') return apiAck(req, db);
    if (path === '/api/guest-order' && method === 'POST') return apiGuestOrder(req, db, env);
    if (path === '/api/payout-approve' && method === 'POST') return apiPayoutApprove(req, db);
    if (path === '/api/license-issue' && method === 'POST') return apiLicenseIssue(req, db, env);
    if (path === '/api/ad-push' && method === 'POST') return apiAdPush(req, db, env);
    if (path === '/api/set-ad-signature' && method === 'POST') return apiSetAdSignature(req, db, env);
    if (path === '/api/hotel-suspend' && method === 'POST') return apiHotelSuspend(req, db, env);
    if (path === '/api/hotels-list'   && method === 'GET')  return apiHotelsList(req, db, env);
    if (path === '/api/bot-upsert' && method === 'POST') return apiBotUpsert(req, db, env);
    if (path === '/api/bots-list' && method === 'GET') return apiBotsList(req, db, env);
    if (path === '/api/hotel-bot-bind' && method === 'POST') return apiHotelBotBind(req, db, env);
    if (path === '/api/live-qr-sync' && method === 'POST') return apiLiveQrSync(req, db, env);
    if (path === '/api/live-qr-list' && method === 'GET') return apiLiveQrList(req, db, env);
    if (path === '/api/bot-roulette' && method === 'POST') return apiBotRoulette(req, db, env);
    if (path === '/api/guest-upsert' && method === 'POST') return apiGuestUpsert(req, db);
    if (path === '/api/guest-list' && method === 'GET') return apiGuestList(req, db, env);
    if (path === '/api/guest-broadcast' && method === 'POST') return apiGuestBroadcast(req, db, env);
    if (path === '/admin/bots') return handleAdminBots(req, db, env);
    if (path === '/admin/guests') return handleAdminGuests(req, db, env);
    if (path === '/admin' || path === '/') return handleAdmin(req, db, env);
    return new Response('ShadowGuard Cloud v1.2 — Not Found', { status: 404 });
  } catch (err) {
    return Response.json({ ok: false, error: err.message }, { status: 500 });
  }
}
