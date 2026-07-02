"""
Solid PMS — 账本核心服务

从 database.ShadowDatabase 中抽离的纯账本逻辑：
- 哈希链计算（SHA-256 防篡改）
- 交易记录写入
- 交班/日结汇总查询

依赖：database.ShadowDatabase（只用于执行 SQL）
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 计入「营业额 / P&L 经营收入」— 不含押金
LEDGER_REVENUE_TX_TYPES = ("ROOM_IN", "SHOP", "TIP", "LEGACY_IMPORT")
# 押金收取与退还
LEDGER_DEPOSIT_TX_TYPES = ("DEPOSIT_IN", "DEPOSIT_OUT")
# 历史兼容名
LEDGER_INCOME_TX_TYPES = LEDGER_REVENUE_TX_TYPES
# 计入「资金池 / 交班应有现金」的净现金流类型
LEDGER_CASH_NET_TX_TYPES = (
    "ROOM_IN", "DEPOSIT_IN", "DEPOSIT_OUT", "SHOP",
    "CASH_IN", "PAYOUT", "EXPENSE", "TIP",
)


def _sql_in_types(types: tuple[str, ...]) -> str:
    return ",".join(f"'{t}'" for t in types)


def _resolve_operator() -> str:
    """获取当前登录操作员。"""
    try:
        from permission_system import PermissionManager
        u = PermissionManager.current_user()
        if u:
            return str(u.get("username") or u.get("id") or "unknown")
        return PermissionManager.current_role() or "guest"
    except Exception:
        return "unknown"


class LedgerHashChain:
    """SHA-256 哈希链计算（纯函数，无副作用）"""

    @staticmethod
    def compute_prev_hash(conn: Any) -> str:
        """从数据库读取上一个哈希。"""
        last = conn.execute(
            "SELECT current_hash FROM ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return last[0] if last else "GENESIS"

    @staticmethod
    def compute_current(
        prev_hash: str,
        tx_id: str,
        tx_type: str,
        amount: float,
        currency: str,
        pay_method: str,
        is_deposit: int,
        operator_id: str,
        room_id: Optional[str],
        exchange_rate: float,
    ) -> str:
        """计算当前交易的 SHA-256 哈希。"""
        data = (
            f"{tx_id}{tx_type}{amount}{currency}"
            f"{pay_method}{is_deposit}{operator_id}"
            f"{room_id or ''}{exchange_rate}{prev_hash}"
        )
        return hashlib.sha256(data.encode()).hexdigest()

    @staticmethod
    def verify_chain(db: Any) -> tuple[bool, str]:
        """验证整条哈希链完整性。"""
        rows = db.execute(
            "SELECT tx_id, tx_type, amount, currency, pay_method, "
            "is_deposit, operator_id, room_id, exchange_rate, "
            "prev_hash, current_hash FROM ledger ORDER BY id"
        ).fetchall()
        prev = "GENESIS"
        for row in rows:
            (tx_id, tx_type, amount, currency, pay_method,
             is_deposit, operator_id, room_id, rate,
             stored_prev, stored_curr) = row
            expected_curr = LedgerHashChain.compute_current(
                prev, tx_id, tx_type, float(amount or 0), currency or "USD",
                pay_method or "CASH", int(is_deposit or 0),
                operator_id or "", room_id, float(rate or 1.0),
            )
            if stored_curr != expected_curr:
                return False, (
                    f"哈希链断裂于 tx_id={tx_id}: "
                    f"期望={expected_curr[:16]}... 实际={stored_curr[:16]}..."
                )
            prev = stored_curr
        return True, "哈希链完整"


class LedgerService:
    """账本服务 — 统一账本操作入口。

    设计原则：
    - 不持有数据库连接（由调用方注入 db: ShadowDatabase）
    - 所有方法纯函数，不含 Qt/UI 逻辑
    - 可被 services/checkout_service.py 等编排层组合使用
    """

    def __init__(self, db: Any):
        self._db = db

    def append(
        self,
        tx_type: str,
        amount: float,
        currency: str = "USD",
        *,
        operator_id: Optional[str] = None,
        room_id: Optional[str] = None,
        note: str = "",
        pay_method: str = "CASH",
        is_deposit: int = 0,
        tx_id_override: Optional[str] = None,
        emit_event: bool = True,
        checkin_id: Optional[str] = None,
        reference_no: Optional[str] = None,
        order_id: Optional[str] = None,
        exchange_rate: Optional[float] = None,
        write_payment_record: bool = False,
    ) -> Optional[str]:
        """写入账本流水（原子操作，哈希链防篡改）。

        委托给 database.ShadowDatabase.append_ledger。
        """
        return self._db.append_ledger(
            tx_type=tx_type,
            amount=amount,
            currency=currency,
            operator_id=operator_id,
            room_id=room_id,
            note=note,
            pay_method=pay_method,
            is_deposit=is_deposit,
            tx_id_override=tx_id_override,
            emit_event=emit_event,
            checkin_id=checkin_id,
            reference_no=reference_no,
            order_id=order_id,
            exchange_rate=exchange_rate,
            write_payment_record=write_payment_record,
        )

    def append_in_transaction(
        self,
        conn: Any,
        tx_type: str,
        amount: float,
        currency: str = "USD",
        *,
        operator_id: Optional[str] = None,
        room_id: Optional[str] = None,
        note: str = "",
        pay_method: str = "CASH",
        is_deposit: int = 0,
        tx_id_override: Optional[str] = None,
        checkin_id: Optional[str] = None,
        reference_no: Optional[str] = None,
        order_id: Optional[str] = None,
        exchange_rate: Optional[float] = None,
    ) -> Optional[str]:
        """事务内写账本（调用方负责 commit/rollback）。"""
        return self._db.append_ledger_conn(
            conn=conn,
            tx_type=tx_type,
            amount=amount,
            currency=currency,
            operator_id=operator_id,
            room_id=room_id,
            note=note,
            pay_method=pay_method,
            is_deposit=is_deposit,
            tx_id_override=tx_id_override,
            checkin_id=checkin_id,
            reference_no=reference_no,
            order_id=order_id,
            exchange_rate=exchange_rate,
        )

    def get_cashier_summary(self, since: str = "") -> dict:
        """交班汇总（按类型/支付方式分组）。"""
        return self._db.build_cashier_shift_summary(since)

    def close_business_day(
        self, business_date: str = "", operator_id: str = "night_audit"
    ) -> tuple[bool, str]:
        """日结锁定。"""
        return self._db.close_business_day(business_date, operator_id)

    def verify_integrity(self) -> tuple[bool, str]:
        """验证账本哈希链完整性。"""
        return LedgerHashChain.verify_chain(self._db)

    def get_ledger_rows(
        self,
        room_id: Optional[str] = None,
        tx_type: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 500,
    ) -> list:
        """查询账本流水（分页）。"""
        conditions = []
        params = []
        if room_id:
            conditions.append("room_id=?")
            params.append(room_id)
        if tx_type:
            conditions.append("tx_type=?")
            params.append(tx_type)
        if since:
            conditions.append("created_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = (
            f"SELECT * FROM ledger {where} "
            f"ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)
        return self._db.execute(sql, tuple(params)).fetchall()

    def get_today_stats(self) -> dict:
        """今日收支快照。"""
        sql = (
            "SELECT tx_type, COALESCE(SUM(amount), 0), COUNT(*) "
            "FROM ledger WHERE date(created_at)=date('now','localtime') "
            "GROUP BY tx_type"
        )
        rows = self._db.execute(sql).fetchall()
        stats = {}
        for tx_type, amount, count in rows:
            stats[tx_type] = {"amount": float(amount or 0), "count": int(count)}
        return stats