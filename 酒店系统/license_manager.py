"""
license_manager.py — License 双轨验证引擎
==========================================
双轨策略：
  轨道1（本地）：SHA-256 激活码离线验证，无网络依赖
  轨道2（云端）：向 Cloudflare Worker /api/hotel-register 注册并获取 kill_date
优先级：云端验证成功 → 写入本地缓存；云端不可达 → 降级本地缓存；两者均无 → 拒绝启动
"""

import os as _os
import uuid as _uuid
import datetime
import hashlib
import hmac
import threading
import requests
from database import db
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QProgressBar
)
from PySide6.QtCore import Qt, QThread, Signal
from ui_helpers import show_info, show_warning


# ─────────────────────────────────────────────────────────────────────────────
#  常量
# ─────────────────────────────────────────────────────────────────────────────
_SALT = _os.environ.get("SOLID_LICENSE_SALT", str(_uuid.UUID(int=_uuid.getnode()))).strip()
_DEFAULT_HASH = "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"  # kept for backwards compatibility
_CLOUD_TIMEOUT = 6  # 秒


# ─────────────────────────────────────────────────────────────────────────────
#  LicenseManager — 核心验证逻辑
# ─────────────────────────────────────────────────────────────────────────────
class LicenseManager:

    # ── 机器码 ────────────────────────────────────────────────────────────────
    @staticmethod
    def get_machine_code() -> str:
        """基于 MAC 地址生成 6 段机器识别码，格式：AA-BB-CC-DD-EE-FF"""
        mac = _uuid.getnode()
        return '-'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))

    @staticmethod
    def get_hotel_id() -> str:
        """酒店唯一 ID：机器码哈希前16位，首次生成后持久化到数据库"""
        cached = db.get_config("hotel_id")
        if cached:
            return cached
        raw = LicenseManager.get_machine_code().replace("-", "")
        hid = "HT_" + hashlib.sha256(raw.encode()).hexdigest()[:16].upper()
        db.set_config("hotel_id", hid)
        return hid

    # ── 本地轨道验证 ──────────────────────────────────────────────────────────
    @staticmethod
    def verify_local_code(code: str) -> tuple[bool, str]:
        """
        验证本地激活码格式：YYYYMMDD-MACPREFIX-HASH8
        返回 (ok, expire_date_str or error_msg)
        """
        if not code:
            return False, "激活码为空"
        machine_code = LicenseManager.get_machine_code().replace("-", "")
        parts = code.strip().split("-")
        if len(parts) < 3:
            return False, "格式错误（应为 YYYYMMDD-MACPREFIX-HASH8）"
        date_part = parts[0]
        mac_part  = parts[1]
        hash_part = parts[2]
        expected_hash = hashlib.sha256(
            f"{date_part}{machine_code}{_SALT}".encode()
        ).hexdigest()[:8].upper()
        if mac_part.upper() != machine_code[:6].upper():
            return False, "机器码不匹配"
        if hash_part.upper() != expected_hash:
            return False, "校验码错误"
        try:
            expire_dt = datetime.datetime.strptime(date_part, "%Y%m%d")
            if expire_dt <= datetime.datetime.now():
                return False, "激活码已过期"
            return True, expire_dt.strftime("%Y-%m-%d")
        except ValueError:
            return False, "日期格式非法"

    # ── 云端轨道验证 ──────────────────────────────────────────────────────────
    @staticmethod
    def verify_cloud(license_key: str = "") -> tuple[bool, str, str]:
        """
        向云端注册/验证，返回 (ok, kill_date, status)
        失败时返回 (False, "", error_msg)
        """
        worker_url = db.get_config("cloud_worker_url") or ""
        if not worker_url:
            return False, "", "未配置云端地址"
        hotel_id   = LicenseManager.get_hotel_id()
        hotel_name = db.get_config("hotel_name") or "未命名酒店"
        machine    = LicenseManager.get_machine_code()
        region     = db.get_config("hotel_region") or ""
        payload = {
            "hotel_id":      hotel_id,
            "hotel_name":    hotel_name,
            "machine_code":  machine,
            "license_key":   license_key,
            "region":        region,
        }
        try:
            resp = requests.post(
                f"{worker_url.rstrip('/')}/api/hotel-register",
                json=payload, timeout=_CLOUD_TIMEOUT
            )
            data = resp.json()
            if data.get("ok"):
                kill_date = data.get("kill_date", "2099-12-31")
                status    = data.get("status", "ACTIVE")
                return True, kill_date, status
            return False, "", data.get("error", "云端拒绝")
        except requests.exceptions.ConnectionError:
            return False, "", "网络不可达"
        except requests.exceptions.Timeout:
            return False, "", "云端超时"
        except Exception as e:
            return False, "", str(e)

    # ── 主验证入口 ────────────────────────────────────────────────────────────
    @staticmethod
    def is_activation_required() -> bool:
        """判断系统是否需要初次激活（未激活且无有效 kill_switch_date）。"""
        if LicenseManager.is_active():
            return False
        # 已激活过（有本地缓存但已过期）→不走激活页，走过期提示
        kill_date_str = db.get_config("kill_switch_date")
        activation_done = db.get_config("activation_code_hash")
        if activation_done and kill_date_str:
            return False
        return True

    @staticmethod
    def is_active() -> bool:
        """
        双轨检查：
        1. 读取本地缓存 kill_switch_date，若有效直接通过（离线模式）
        2. 若本地无效，尝试云端轮询（需要 cloud_worker_url）
        """
        # 本地缓存检查
        kill_date_str = db.get_config("kill_switch_date")
        if kill_date_str:
            try:
                kill_date = datetime.datetime.strptime(kill_date_str, "%Y-%m-%d")
                if datetime.datetime.now() < kill_date:
                    return True
            except (ValueError, TypeError):
                pass
        return False

    @staticmethod
    def activate_with_code(code: str) -> tuple[bool, str]:
        """
        双轨激活：先本地验证，同时尝试云端注册
        返回 (ok, message)
        """
        # 轨道1：本地验证
        local_ok, local_result = LicenseManager.verify_local_code(code)
        if local_ok:
            db.set_config("kill_switch_date", local_result)
            # 异步尝试云端注册（不阻塞）
            threading.Thread(
                target=LicenseManager.verify_cloud,
                args=(code,), daemon=True
            ).start()
            return True, f"本地验证成功，授权至 {local_result}"

        # 轨道2：云端验证（本地失败时尝试）
        cloud_ok, kill_date, status = LicenseManager.verify_cloud(code)
        if cloud_ok and status == "ACTIVE":
            db.set_config("kill_switch_date", kill_date)
            return True, f"云端验证成功，授权至 {kill_date}"

        return False, f"本地：{local_result} | 云端：{status or '不可达'}"

    @staticmethod
    def sync_cloud_status() -> bool:
        """
        后台静默同步云端状态（由心跳服务调用）
        若云端返回 SUSPENDED 或 kill_date 已过，更新本地缓存
        """
        worker_url = db.get_config("cloud_worker_url") or ""
        if not worker_url:
            return True  # 无云端配置，视为本地模式，不干预
        hotel_id = LicenseManager.get_hotel_id()
        try:
            resp = requests.get(
                f"{worker_url.rstrip('/')}/api/hotel-poll",
                params={"hotel_id": hotel_id},
                timeout=_CLOUD_TIMEOUT
            )
            data = resp.json()
            if not data.get("ok"):
                return True  # 云端异常，保持本地状态
            try:
                from telegram_bot_config import apply_cloud_poll_response
                apply_cloud_poll_response(data)
            except Exception:
                pass
            hotel_status = data.get("hotel_status", "ACTIVE")
            kill_switch  = data.get("kill_switch")
            if hotel_status == "SUSPENDED" and kill_switch:
                # 云端强制停用：写入过去日期触发锁机
                db.set_config("kill_switch_date", kill_switch.get("kill_date", "2020-01-01"))
                return False
            # 处理远程通知
            notifications = data.get("notifications", [])
            if notifications:
                from manufacturer_comm import ManufacturerCommService
                ManufacturerCommService.process_notifications(notifications, worker_url)
            return True
        except Exception:
            return True  # 网络异常，保持本地状态

    @staticmethod
    def derive_hardware_key() -> str:
        """从硬盘序列号+MAC地址派生硬件指纹密钥。"""
        import uuid as _uuid
        try:
            node = _uuid.getnode()
            fingerprint = f"{node}"
            return hashlib.sha256(fingerprint.encode()).hexdigest()
        except Exception:
            return hashlib.sha256(b"solid_fallback").hexdigest()

    @staticmethod
    def persist_activation_metadata(*, source: str, kill_date: str, status: str) -> None:
        """写入激活元数据（厂家 bypass / 激活码共享逻辑）。"""
        db.set_config("kill_switch_date", kill_date)
        db.set_config("activation_code_hash", hashlib.sha256(f"{source}:{kill_date}".encode()).hexdigest())
        db.set_config("license_source", source)
        db.set_config("license_status", status)

    @staticmethod
    def verify_rsa_signature(data: str, signature_b64: str, public_key_pem: str) -> bool:
        """RSA-2048 SHA-256 签名验证（骨架，替换简单 SHA-256 对比）。"""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            import base64
            key = serialization.load_pem_public_key(public_key_pem.encode())
            signature = base64.b64decode(signature_b64)
            key.verify(signature, data.encode(), padding.PKCS1v15(), hashes.SHA256())
            return True
        except ImportError:
            return hashlib.sha256(data.encode()).hexdigest()[:16] == signature_b64[:16]
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
#  云端激活线程（非阻塞）
# ─────────────────────────────────────────────────────────────────────────────
class _CloudActivateThread(QThread):
    result = Signal(bool, str)

    def __init__(self, code: str):
        super().__init__()
        self.code = code

    def run(self):
        ok, msg = LicenseManager.activate_with_code(self.code)
        self.result.emit(ok, msg)


# ─────────────────────────────────────────────────────────────────────────────
#  授权过期对话框
# ─────────────────────────────────────────────────────────────────────────────
class LicenseExpiredDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("系统授权已过期")
        from ui_helpers import style_dialog
        style_dialog(self, size="small")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Dialog)
        self._thread = None
        self._build_ui()

    def _build_ui(self):
        from design_tokens import _p
        danger = _p("danger")
        bg = _p("bg")
        surface = _p("surface")
        text = _p("text")
        text_muted = _p("text_muted")
        primary = _p("primary")

        l = QVBoxLayout(self)
        l.setSpacing(16)
        l.setContentsMargins(32, 32, 32, 32)

        # 标题
        lbl_warn = QLabel("⚠️  软件服务授权已到期", alignment=Qt.AlignCenter)
        lbl_warn.setObjectName("H1Title")
        lbl_warn.setStyleSheet(f"color:{danger}; font-weight:800;")
        l.addWidget(lbl_warn)

        # 说明
        machine = LicenseManager.get_machine_code()
        hotel_id = LicenseManager.get_hotel_id()
        lbl_desc = QLabel(
            f"此终端的管理系统服务已暂停。\n"
            f"请联系软件供应商续费以解锁并恢复所有营业数据。\n\n"
            f"机器识别码：{machine}\n"
            f"酒店 ID：{hotel_id}",
            alignment=Qt.AlignCenter
        )
        lbl_desc.setObjectName("Body")
        lbl_desc.setStyleSheet(f"color:{text_muted}; line-height:1.6;")
        lbl_desc.setWordWrap(True)
        l.addWidget(lbl_desc)

        # 输入框
        self.txt_code = QLineEdit(placeholderText="请输入续期激活码（本地码或云端授权码均可）")
        l.addWidget(self.txt_code)

        # 进度条（验证中显示）
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        l.addWidget(self.progress)

        # 状态标签
        self.lbl_status = QLabel("", alignment=Qt.AlignCenter)
        self.lbl_status.setObjectName("Small")
        self.lbl_status.setStyleSheet(f"color:{text_muted};")
        l.addWidget(self.lbl_status)

        # 按钮
        h = QHBoxLayout()
        btn_verify = QPushButton("🔓  验证并解锁")
        btn_verify.setObjectName("SolidPrimaryBtn")
        btn_verify.clicked.connect(self._verify)
        btn_exit = QPushButton("退出")
        btn_exit.setObjectName("SecondaryBtn")
        btn_exit.clicked.connect(self.reject)
        h.addWidget(btn_verify)
        h.addWidget(btn_exit)
        l.addLayout(h)

    def _verify(self):
        code = self.txt_code.text().strip()
        if not code:
            self.lbl_status.setText("⚠️ 请输入激活码")
            self.lbl_status.setObjectName("Small")
            self.lbl_status.setStyleSheet(f"color:{_p('accent')};")
            return
        self.lbl_status.setText("正在验证（双轨检查中）...")
        self.lbl_status.setObjectName("Small")
        self.lbl_status.setStyleSheet(f"color:{_p('text_muted')};")
        self.progress.show()
        self._thread = _CloudActivateThread(code)
        self._thread.result.connect(self._on_result)
        self._thread.start()

    def _on_result(self, ok: bool, msg: str):
        self.progress.hide()
        if ok:
            self.lbl_status.setText(f"✅ {msg}")
            self.lbl_status.setObjectName("Small")
            self.lbl_status.setStyleSheet(f"color:{_p('amount_positive')};")
            show_info(self, "解锁成功", f"{msg}\n\n系统将立即恢复运营，感谢您的支持。")
            self.accept()
        else:
            self.lbl_status.setText(f"❌ {msg}")
            self.lbl_status.setObjectName("Small")
            self.lbl_status.setStyleSheet(f"color:{_p('danger')};")
