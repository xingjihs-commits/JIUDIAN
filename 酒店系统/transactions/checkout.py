"""transactions.checkout -- 退房事务。

支持三种模式:
  - bycard:   有卡退房 (前台放卡 -> 读 -> 擦 -> 退)
  - nocard:   无卡退房 (不碰卡 -> 退 -> 制作退房卡给保洁刷门锁)
  - team:     团体退房 (批量多间房一次性退)

[sub-a] 财务闭环增强：
  - 退房结算时生成 bill_headers 记录，把 folio_items 关联到 bill（修复账单无头问题）
  - ledger 写入带 exchange_rate（多币种对账基础）
  - 对 folio_items 中带 sku 的迷你吧商品调用 reserve_shop_stock 扣减库存
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from database import db
from lock_legacy_bridge import LEGACY_ACTIVE_CARD_STATUSES

logger = logging.getLogger(__name__)


def _placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def _get_default_room_status() -> str:
    """Old CardLock: FlagCheckOut=0 -> 空净房(READY), 1 -> 脏房(DIRTY)."""
    try:
        flag = str(db.get_config("lock_takeover_flag_checkout") or "").strip()
    except Exception:
        flag = ""
    if flag == "0":
        return "READY"
    if flag == "1":
        return "DIRTY"
    return "DIRTY"


@dataclass
class CheckoutResult:
    ok: bool
    guest_name: str = ""
    deposit_refund: float = 0.0
    cancelled_cards: int = 0
    room_status: str = ""
    checkout_card_hex: str = ""
    next_action: str = ""
    bill_no: str = ""  # [sub-a] 退房生成的账单号，便于 UI 跳转/打印
    error: str = ""


class SingleRoomCheckout:
    """单间房退房逻辑 (bycard / nocard)，内部事务。"""

    def __init__(
        self,
        room_id: str,
        operator: str,
        *,
        mode: str = "nocard",
        target_room_status: str = "",
        card_id: str = "",
    ):
        self.room_id = str(room_id or "").strip()
        self.operator = str(operator or "").strip()
        self.mode = mode
        self.target_room_status = target_room_status or _get_default_room_status()
        self.card_id = str(card_id or "").strip()
        if not self.room_id:
            raise ValueError("room_id 不能为空")
        if not self.operator:
            raise ValueError("operator 不能为空")
        if mode not in ("bycard", "nocard"):
            raise ValueError(f"mode must be 'bycard' or 'nocard', got {mode!r}")

    def execute(self) -> CheckoutResult:
        try:
            with db.transaction() as conn:
                guest_row = conn.execute(
                    "SELECT id, name, checkin_time FROM guests "
                    "WHERE room_id=? AND status='INHOUSE' ORDER BY id DESC LIMIT 1",
                    (self.room_id,),
                ).fetchone()
                if not guest_row:
                    raise RuntimeError("未找到当前在住客人")
                guest_id, guest_name, checkin_time = guest_row

                dep_row = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM ledger "
                    "WHERE room_id=? AND tx_type IN ('DEPOSIT_IN','DEPOSIT_OUT') "
                    "AND is_deposit=1 AND created_at >= COALESCE(?, '1970-01-01')",
                    (self.room_id, checkin_time),
                ).fetchone()
                deposit_amt = float(dep_row[0] or 0) if dep_row else 0.0

                cur = conn.execute(
                    "UPDATE guests SET status='OUT', flag='CheckOut', checkout_time=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='INHOUSE'",
                    (guest_id,),
                )
                if (cur.rowcount or 0) != 1:
                    raise RuntimeError("在住记录已变化")

                # [sub-a] 取本位币与当时汇率：多币种对账与汇兑损益计算的基础
                # 若 services.exchange_rate 不可用（如单元测试或导入时）回退 1.0
                exchange_rate_v: float = 1.0
                try:
                    from services.exchange_rate import get_rate as _get_rate
                    rate_dec = _get_rate("USD")  # 多数场景同币种，返回 1.0
                    exchange_rate_v = float(rate_dec)
                except Exception as _e:
                    logger.debug("[checkout] 取汇率失败，回退 1.0: %s", _e)

                if deposit_amt > 0:
                    db.append_ledger_conn(
                        conn, "DEPOSIT_OUT", -deposit_amt, "CASH", 1, self.room_id,
                        "退房押金退还", is_deposit=1,
                        exchange_rate=exchange_rate_v,
                    )

                # [sub-a] 财务闭环：生成 bill_headers + 关联 folio_items + 扣减迷你吧库存
                bill_no = self._generate_bill_header(
                    conn, guest_id=guest_id, checkin_time=checkin_time,
                    deposit_refund=deposit_amt, exchange_rate=exchange_rate_v,
                )

                # 有卡退房: 擦卡
                cancelled_cards = 0
                if self.mode == "bycard" and self.card_id:
                    try:
                        from lock_issue_service import cancel_card_via_adapter
                        res = cancel_card_via_adapter()
                        if res.success:
                            conn.execute(
                                "UPDATE card_records SET status=? WHERE card_id=?",
                                ("ERASED", self.card_id),
                            )
                            cancelled_cards = 1
                    except Exception:
                        pass

                active_count_row = conn.execute(
                    f"SELECT COUNT(*) FROM card_records WHERE room_id=? AND status IN ({_placeholders(LEGACY_ACTIVE_CARD_STATUSES)})",
                    (self.room_id, *LEGACY_ACTIVE_CARD_STATUSES),
                ).fetchone()

                rs = self.target_room_status
                room_cur = conn.execute(
                    "UPDATE rooms SET status=? WHERE room_id=? AND status='INHOUSE'",
                    (rs, self.room_id),
                )
                if (room_cur.rowcount or 0) != 1:
                    raise RuntimeError("房态已变化")

                db.log_action(self.operator, "CHECKOUT",
                              f"room={self.room_id} guest={guest_name} mode={self.mode} bill={bill_no}")

            # 无卡退房: 事务外制作退房卡（可能涉及发卡器 I/O，不能放事务里）
            checkout_card_hex = ""
            next_action = ""
            if self.mode == "nocard":
                try:
                    from lock_issue_service import _adapter
                    from lock_adapters.prousb_v9 import format_date
                    ad = _adapter()
                    if ad is not None:
                        if not ad.is_open:
                            ad.initialize()
                        res = ad.issue_check_out_card(
                            b_date=format_date(datetime.now()),
                        )
                        if getattr(res, "success", False) and res.card_hex:
                            checkout_card_hex = res.card_hex
                            next_action = (
                                f"已将退房卡数据写入发卡器。请把退房卡从发卡器取下，"
                                f"交给服务员/保洁拿到 {self.room_id} 房门刷一次，"
                                f"门锁即废除所有客人卡。"
                            )
                except Exception:
                    next_action = (
                        "请到「门锁诊断」手动制作一张退房卡，交给服务员去房门刷一次。"
                    )

            from event_bus import bus
            try:
                bus.toast_requested.emit(
                    f"\U0001f6aa {self.room_id} 退房成功 · {guest_name}"
                )
            except Exception:
                pass

            return CheckoutResult(
                ok=True,
                guest_name=str(guest_name or ""),
                deposit_refund=deposit_amt,
                cancelled_cards=cancelled_cards,
                room_status=self.target_room_status,
                checkout_card_hex=checkout_card_hex,
                next_action=next_action,
                bill_no=bill_no,
            )
        except Exception as exc:
            return CheckoutResult(ok=False, error=str(exc))

    # ── [sub-a] 账单头生成 + folio_items 关联 + 迷你吧库存扣减 ─────────
    def _generate_bill_header(
        self,
        conn,
        *,
        guest_id: int,
        checkin_time: str | None,
        deposit_refund: float,
        exchange_rate: float,
    ) -> str:
        """退房时生成 bill_headers，把该房间未关账的 folio_items 关联到新账单。

        解决的业务缺口：原系统 folio_items 无头，无法打印整张账单。
        本函数：
          1. 汇总该房间自入住以来的 folio_items 金额（含损坏赔偿 / 迷你吧消费）
          2. 加上退房时的押金退还（如有）作为账单总额
          3. 写入 bill_headers，bill_no 唯一
          4. UPDATE folio_items.bill_id = 新账单号（仅未关账的行）
          5. 对 folio_items 中带 sku 的迷你吧商品调用 reserve_shop_stock 扣减库存
             （这些是入住期间记账但未即时扣库的商品）

        失败容错：任何子步骤失败都仅日志告警，不阻断退房主流程
        （退房成功是核心目标；账单/库存可后补）。
        """
        try:
            from money_utils import quantize_money, base_currency
            currency = base_currency()
        except Exception:
            currency = "USD"
            quantize_money = lambda v: float(v)  # type: ignore  # noqa: E731

        bill_no = f"BILL{int(datetime.now().timestamp() * 1000)}_{uuid.uuid4().hex[:6]}"

        # 汇总 folio_items（自入住以来该房间的所有未关账行）
        try:
            folio_total_row = conn.execute(
                "SELECT COALESCE(SUM(total), 0) FROM folio_items "
                "WHERE room_id=? AND (bill_id IS NULL OR bill_id='') "
                "AND created_at >= COALESCE(?, '1970-01-01')",
                (self.room_id, checkin_time),
            ).fetchone()
            folio_total = float(folio_total_row[0] or 0) if folio_total_row else 0.0
        except Exception as _e:
            logger.warning("[checkout] 汇总 folio_items 失败: %s", _e)
            folio_total = 0.0

        total_amount = folio_total + max(0.0, float(deposit_refund or 0))
        try:
            total_amount = float(quantize_money(total_amount))
        except Exception:
            pass  # quantize_money 失败时保留原始 float

        # 写 bill_headers
        try:
            conn.execute(
                "INSERT INTO bill_headers "
                "(bill_no, guest_id, checkin_id, total_amount, currency, exchange_rate, status, operator_id, note) "
                "VALUES (?,?,?,?,?,?, 'OPEN', ?, ?)",
                (bill_no, guest_id, None, total_amount, currency, float(exchange_rate or 1.0),
                 self.operator, f"退房结算 {self.room_id}"),
            )
        except Exception as _e:
            logger.warning("[checkout] 写 bill_headers 失败: %s", _e)
            return bill_no  # 仍返回 bill_no 便于追踪，folio_items 关联可能失败但退房继续

        # 迷你吧商品扣库存：先取快照（UPDATE bill_id 之前），再扣库存
        # 损坏赔偿行 sku 是中文标签（如"床品损坏"），reserve_shop_stock 会因找不到 SKU 安全返回 False
        sku_rows: list = []
        try:
            sku_rows = conn.execute(
                "SELECT sku, COALESCE(qty,1), note FROM folio_items "
                "WHERE room_id=? AND (bill_id IS NULL OR bill_id='') "
                "AND sku IS NOT NULL AND sku<>'' "
                "AND created_at >= COALESCE(?, '1970-01-01')",
                (self.room_id, checkin_time),
            ).fetchall()
        except Exception as _e:
            logger.warning("[checkout] 读取 folio_items sku 行失败: %s", _e)

        # 关联 folio_items 到新账单（UPDATE 之后 sku_rows 快照仍可用）
        try:
            conn.execute(
                "UPDATE folio_items SET bill_id=? "
                "WHERE room_id=? AND (bill_id IS NULL OR bill_id='') "
                "AND created_at >= COALESCE(?, '1970-01-01')",
                (bill_no, self.room_id, checkin_time),
            )
        except Exception as _e:
            logger.warning("[checkout] 关联 folio_items 到账单 %s 失败: %s", bill_no, _e)

        # 用预先取的快照扣库存；reserve_shop_stock 是基于当前 stock 的原子 UPDATE，
        # 即使本房间还有其他在途扣减也能安全处理
        for sku, qty, name in sku_rows:
            if not sku or not str(sku).strip():
                continue
            try:
                ok = db.reserve_shop_stock(str(sku), int(qty or 1))
                if not ok:
                    logger.info(
                        "[checkout] 迷你吧扣库存失败/库存不足: sku=%s name=%s qty=%s（账单 %s 已生成，库存需后补）",
                        sku, name, qty, bill_no,
                    )
            except Exception as _e:
                logger.debug("[checkout] 迷你吧扣库存异常 sku=%s: %s", sku, _e)

        return bill_no


class CheckoutTransaction:
    """退房事务入口。自动判断有卡/无卡。"""

    def __init__(self, room_id: str, operator: str):
        self.room_id = str(room_id or "").strip()
        self.operator = str(operator or "").strip()
        if not self.room_id:
            raise ValueError("room_id 不能为空")
        if not self.operator:
            raise ValueError("operator 不能为空")

    def execute_nocard(
        self,
        target_room_status: str = "",
    ) -> CheckoutResult:
        """无卡退房: 数据库退房 + 制作退房卡给保洁刷锁。"""
        return SingleRoomCheckout(
            self.room_id, self.operator,
            mode="nocard", target_room_status=target_room_status,
        ).execute()

    def execute_bycard(
        self,
        card_id: str,
        target_room_status: str = "",
    ) -> CheckoutResult:
        """有卡退房: 读卡 -> 擦卡 -> 退房。"""
        return SingleRoomCheckout(
            self.room_id, self.operator,
            mode="bycard", target_room_status=target_room_status,
            card_id=card_id,
        ).execute()


class TeamCheckoutTransaction:
    """团体退房: 一次性退多间在住房。"""

    def __init__(self, room_ids: list[str], operator: str):
        self.room_ids = [str(r or "").strip() for r in room_ids if r and str(r).strip()]
        self.operator = str(operator or "").strip()
        if not self.room_ids:
            raise ValueError("room_ids 不能为空")
        if not self.operator:
            raise ValueError("operator 不能为空")

    def execute(self, target_room_status: str = "") -> list[CheckoutResult]:
        """批量退房，每间独立事务。失败的不影响其他。"""
        results: list[CheckoutResult] = []
        for rid in self.room_ids:
            r = SingleRoomCheckout(
                rid, self.operator,
                mode="nocard", target_room_status=target_room_status,
            ).execute()
            results.append(r)
        return results
