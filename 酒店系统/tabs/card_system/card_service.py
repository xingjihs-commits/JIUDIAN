"""
业务逻辑层 — CardService
制卡/补卡/注销/查询/过期检查 / 管理卡登记
"""
from __future__ import annotations

import uuid as _uuid
import logging
from datetime import datetime, timedelta

from database import db
from .card_driver import get_driver
from ._shared import REGISTRY_CARD_KINDS
from i18n import i18n

logger = logging.getLogger(__name__)


def _agent_debug_log(tag: str, msg: str, data: dict):
    logger.debug("[%s] %s: %s", tag, msg, data)


class CardService:
    """门卡业务逻辑（制卡/补卡/注销/查询）"""

    @staticmethod
    def _effective_operator(operator_id: str) -> str:
        oid = (operator_id or "").strip()
        if oid and oid not in ("FRONTDESK", "SETUP_WIZARD"):
            return oid
        try:
            from permission_system import PermissionManager
            u = PermissionManager.current_user()
            if u:
                return str(u.get("username") or oid or "FRONTDESK")
        except Exception:
            pass
        return oid or "FRONTDESK"

    @staticmethod
    def issue_card(room_id: str, guest_name: str,
                   expire_dt: datetime, operator_id: str = "",
                   card_no: int = 0) -> tuple[bool, str]:
        takeover_active = False
        try:
            from lock_issue_service import takeover_configured
            takeover_active = takeover_configured()
        except Exception as e:
            _agent_debug_log("H1", "Check takeover failed", {"room_id": room_id, "error": str(e)})
        _agent_debug_log("H1", "Frontdesk issue card entry", {"room_id": room_id, "takeover_active": takeover_active})

        try:
            row = db.execute(
                "SELECT COALESCE(last_card_no, 0), COALESCE(last_seq, 0) FROM rooms WHERE room_id=?",
                (room_id,),
            ).fetchone()
            next_card_no = card_no if card_no > 0 else ((int(row[0]) if row else 0) + 1)
            last_seq = int(row[1]) if row and len(row) > 1 else 0
            next_seq = (last_seq + 1) & 0x0F
        except Exception:
            next_card_no = max(card_no, 1)
            next_seq = 0

        expire_ts = int(expire_dt.timestamp())
        issue_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        expire_time_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S")
        op_use = CardService._effective_operator(operator_id)

        temp_card_id = f"PENDING-{_uuid.uuid4().hex[:8].upper()}"
        try:
            db.execute(
                """INSERT INTO card_records
                   (card_id, room_id, guest_name, issue_time, expire_time, status, operator_id, registry_kind, sequence)
                   VALUES (?, ?, ?, ?, ?, 'PENDING', ?, 'guest', ?)""",
                (temp_card_id, room_id, guest_name, issue_time_str, expire_time_str, op_use, next_card_no)
            )
        except Exception as e:
            return False, f"数据库预写入失败：{e}"

        if takeover_active:
            try:
                from lock_issue_service import issue_guest_for_frontdesk
                res = issue_guest_for_frontdesk(room_id, expire_dt, card_no=next_card_no, seq=next_seq)
                ok = res.success
                card_id = (res.card_hex or "")[:32] or f"CARD-{room_id}-{datetime.now().strftime('%H%M%S')}"
                if not ok:
                    card_id = res.error or i18n.t("card_takeover_issue_failed")
                _agent_debug_log("H4", "Frontdesk takeover issue card",
                                 {"room_id": room_id, "ok": ok, "card_id": card_id, "error": res.error})
            except Exception as e:
                ok, card_id = False, i18n.t("card_takeover_exception").format(e=e)
                _agent_debug_log("H4", "Frontdesk takeover exception", {"room_id": room_id, "error": str(e)})
        else:
            driver = get_driver()
            if not driver.is_connected():
                ok, msg = driver.connect()
                if not ok:
                    try:
                        db.execute("DELETE FROM card_records WHERE card_id=?", (temp_card_id,))
                    except Exception:
                        pass
                    return False, msg
            ok, card_id = driver.write_card(room_id, expire_ts)
        if not ok:
            try:
                db.execute("DELETE FROM card_records WHERE card_id=?", (temp_card_id,))
            except Exception:
                pass
            return False, f"写卡失败：{card_id}"

        try:
            db.execute(
                "UPDATE card_records SET card_id=?, status='ACTIVE' WHERE card_id=?",
                (card_id, temp_card_id)
            )
            db.execute(
                "UPDATE rooms SET last_card_no=?, last_seq=? WHERE room_id=?",
                (next_card_no, next_seq, room_id),
            )
        except Exception as e:
            db.log_action(op_use, "CARD_ISSUE_DB_ERROR",
                          f"Card written but DB update failed: {card_id} room:{room_id} err:{e}")
            return False, f"卡已写入但数据库更新失败，请联系管理员：{e}"

        db.log_action(op_use, "CARD_ISSUE",
                      f"Room {room_id} guest {guest_name} card {card_id} card_no={next_card_no}")
        try:
            db.log_door_open_event(room_id, str(card_id), "issue_write", op_use, 1, guest_name)
        except Exception:
            pass
        return True, card_id

    @staticmethod
    def register_registry_card(
        card_id: str, registry_kind: str, label: str, operator_id: str = "SETUP_WIZARD"
    ) -> tuple[bool, str]:
        raw = (card_id or "").strip().upper().replace(" ", "").replace(":", "")
        if len(raw) < 4:
            return False, "卡号过短或无效"
        if registry_kind not in REGISTRY_CARD_KINDS:
            return False, f"类型须为 {' | '.join(REGISTRY_CARD_KINDS)}"
        default_labels = {
            "master": "总卡", "auth": "授权卡", "housekeeping": "保洁总卡",
            "floor": "楼层卡", "building": "楼栋卡",
        }
        label_use = (label or "").strip() or default_labels.get(registry_kind, registry_kind)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        far = (datetime.now() + timedelta(days=3650)).strftime("%Y-%m-%d %H:%M:%S")
        op_reg = CardService._effective_operator(operator_id)
        row = db.execute("SELECT card_id FROM card_records WHERE card_id=?", (raw,)).fetchone()
        if row:
            db.execute(
                "UPDATE card_records SET registry_kind=?, guest_name=?, room_id=?, status='ACTIVE', "
                "issue_time=?, expire_time=?, operator_id=?, source_system=COALESCE(source_system, ?) WHERE card_id=?",
                (registry_kind, label_use, "__REGISTRY__", now, far, op_reg, "registry_ui", raw),
            )
        else:
            db.execute(
                """INSERT INTO card_records
                   (card_id, room_id, guest_name, issue_time, expire_time, card_type, status, operator_id, registry_kind, source_system)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (raw, "__REGISTRY__", label_use, now, far, "MIFARE Classic", "ACTIVE", op_reg, registry_kind, "registry_ui"),
            )
        db.log_action(op_reg, "CARD_REGISTRY", f"{registry_kind}:{raw}:{label_use}")
        try:
            db.log_door_open_event("__REGISTRY__", raw, f"registry_{registry_kind}", op_reg, 1, label_use)
        except Exception:
            pass
        return True, raw

    @staticmethod
    def cancel_card(card_id: str, operator_id: str = "FRONTDESK") -> tuple[bool, str]:
        op_use = CardService._effective_operator(operator_id)
        driver = get_driver()
        if not driver.is_connected():
            ok, msg = driver.connect()
            if not ok:
                return False, msg
        ok, msg = driver.cancel_card(card_id)
        if not ok:
            return False, msg
        try:
            db.execute("UPDATE card_records SET status='CANCELLED' WHERE card_id=?", (card_id,))
        except Exception as e:
            return False, f"数据库更新失败：{e}"
        db.log_action(op_use, "CARD_CANCEL", f"注销卡号{card_id}")
        try:
            row = db.execute("SELECT room_id FROM card_records WHERE card_id=?", (card_id,)).fetchone()
            rid = str(row[0]) if row and row[0] is not None else ""
            db.log_door_open_event(rid, card_id, "cancel_lock", op_use, 1, "")
        except Exception:
            pass
        return True, "注销成功"

    @staticmethod
    def reissue_card(old_card_id: str, room_id: str, guest_name: str,
                     expire_dt: datetime, operator_id: str = "FRONTDESK") -> tuple[bool, str]:
        op_use = CardService._effective_operator(operator_id)
        CardService.cancel_card(old_card_id, op_use)
        ok, result = CardService.issue_card(room_id, guest_name, expire_dt, op_use)
        if ok:
            db.log_action(op_use, "CARD_REISSUE",
                          f"房间{room_id} 旧卡{old_card_id} 新卡{result}")
            try:
                db.log_door_open_event(room_id, str(result), "reissue_write", op_use, 1, f"old={old_card_id}")
            except Exception:
                pass
        return ok, result

    @staticmethod
    def get_room_cards(room_id: str) -> list[dict]:
        rows = db.execute(
            """SELECT card_id, guest_name, issue_time, expire_time, status, operator_id,
                      COALESCE(registry_kind, 'guest') AS rk
               FROM card_records WHERE room_id=? ORDER BY issue_time DESC""",
            (room_id,)
        ).fetchall()
        return [
            {"card_id": r[0], "guest_name": r[1], "issue_time": r[2],
             "expire_time": r[3], "status": r[4], "operator_id": r[5], "registry_kind": r[6]}
            for r in rows
        ]

    @staticmethod
    def get_all_cards(status_filter: str = "ALL") -> list[dict]:
        if status_filter == "ALL":
            rows = db.execute(
                """SELECT card_id, room_id, guest_name, issue_time, expire_time, status, operator_id,
                          COALESCE(registry_kind, 'guest') AS rk
                   FROM card_records ORDER BY issue_time DESC LIMIT 200"""
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT card_id, room_id, guest_name, issue_time, expire_time, status, operator_id,
                          COALESCE(registry_kind, 'guest') AS rk
                   FROM card_records WHERE status=? ORDER BY issue_time DESC LIMIT 200""",
                (status_filter,)
            ).fetchall()
        return [
            {"card_id": r[0], "room_id": r[1], "guest_name": r[2],
             "issue_time": r[3], "expire_time": r[4],
             "status": r[5], "operator_id": r[6], "registry_kind": r[7]}
            for r in rows
        ]

    @staticmethod
    def expire_overdue_cards():
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "UPDATE card_records SET status='EXPIRED' WHERE status='ACTIVE' AND expire_time < ?",
            (now,)
        )
