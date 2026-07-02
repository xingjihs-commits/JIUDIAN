"""
card_sniffer.py — 旧系统发卡信号嗅探模块
==========================================
功能：
  监听串口，捕获旧系统发卡时的 APDU／串口指令，
  提取 Mifare 扇区密钥和房间号，写入新系统配置。

使用场景：
  旧系统发卡时，把读卡器串口信号同时接入本机，
  本模块实时捕获并解析，无需旧系统配合。

使用入口：设置 →「客户老系统一键整合台」→ 发卡嗅探；或设置 → 门锁迁移向导旁独立按钮

国内品牌：串口嗅探可选「品牌档案」（帧头、UID 偏移、帧间隔、推荐波特率），与品牌档案库
中的国内／东南亚品牌条目对齐扩展；具体帧格式因固件版本而异，现场需对照抓包微调。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

from PySide6.QtCore import QThread, Signal as QtSignal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTextEdit, QGroupBox, QFormLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtGui import QFont, QColor

from database import db
from i18n import i18n
from legacy_migration_guide import SniffGuideSession, cardlock_sniff_intro
from migration_guide_panel import MigrationGuidePanel
from design_tokens import _p
from ui_helpers import show_warning, show_info, show_error, style_dialog
from ui_surface import fd_apply_table_palette
from usb_driver_helper import offer_usb_driver_install, open_driver_install_dialog, list_serial_ports_detailed


# ─── 串口工具 ─────────────────────────────────────────────────────────────────

def _list_ports() -> List[str]:
    """列出系统可用串口"""
    ports = []
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            ports.append(p.device)
    except ImportError:
        pass
    if not ports:
        ports = [f"COM{i}" for i in range(1, 16)]
    return ports


# ─── 国内 / 常见门锁·发卡器串口档案（与品牌档案库品牌互补）────────────────
# 说明：帧格式随厂商固件变化；以下为现场常见启发式参数，非厂商官方规范。

SNIFFER_BRANDS: Dict[str, Dict[str, Any]] = {
    "prousb_cardlock": {
        "label": "proUSB 智能门锁",
        "frame_prefixes": [b"\x02\x02", b"\xaa\x55", b"\x5a\xa5", b"\xff\xd6"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "auto": {
        "label": "自动 / 通用启发式",
        "frame_prefixes": [],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": None,
    },
    "adel": {
        "label": "ADEL亚太 / 爱迪尔",
        "frame_prefixes": [b"\xaa\x55", b"\x02\x02", b"\xcc\xdd", b"\xff\xd6", b"\xff\xa4"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 55.0,
        "default_baud": 9600,
    },
    "anjubao": {
        "label": "安居宝",
        "frame_prefixes": [b"\x60\x00", b"\x02\x02", b"\xaa\x55"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 60.0,
        "default_baud": 9600,
    },
    "kaidisite": {
        "label": "凯迪仕 KDS",
        "frame_prefixes": [b"\xaa\x55", b"\xff\xd6", b"\x02\x02"],
        "uid_slice": (3, 7),
        "frame_gap_ms": 45.0,
        "default_baud": 19200,
    },
    "bitech": {
        "label": "必达 BETECH",
        "frame_prefixes": [b"\x02\x02", b"\xaa\x55", b"\x5a\xa5"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "hune": {
        "label": "科裕 HUNE",
        "frame_prefixes": [b"\x55\xaa", b"\x02\x02", b"\xff\xd6"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 55.0,
        "default_baud": 9600,
    },
    "ygs": {
        "label": "杨格 YGS",
        "frame_prefixes": [b"\xaa\x55", b"\x5a\x5a", b"\x02\x02"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "dinggu": {
        "label": "顶固 酒店发行",
        "frame_prefixes": [b"\x02\x02", b"\xaa\x55"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "tcl": {
        "label": "TCL 酒店门锁",
        "frame_prefixes": [b"\x02\x02", b"\xaa\x55", b"\xff\xd6"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "beian": {
        "label": "贝安",
        "frame_prefixes": [b"\x02\x02", b"\xaa\x55"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "dessmann": {
        "label": "德施曼",
        "frame_prefixes": [b"\x02\x02", b"\xaa\x55", b"\xff\xd6"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "orbita_cn": {
        "label": "欧比特 ORBITA",
        "frame_prefixes": [b"\xaa\x55", b"\x03\x03", b"\x02\x02"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "level": {
        "label": "力维",
        "frame_prefixes": [b"\x02\x03", b"\x02\x02", b"\xaa\x55"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 55.0,
        "default_baud": 9600,
    },
    "tengo": {
        "label": "天罡",
        "frame_prefixes": [b"\xaa\x66", b"\x02\x02", b"\xff\xd6"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 40.0,
        "default_baud": 57600,
    },
    "jweilink": {
        "label": "劲卫 / 创佳系",
        "frame_prefixes": [b"\x02\x02", b"\x55\xaa", b"\xaa\x55"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "dormakaba": {
        "label": "Dormakaba / Saflok",
        "frame_prefixes": [b"\xff\xd6", b"\xff\xa4", b"\x02\x02"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "vingcard": {
        "label": "VingCard",
        "frame_prefixes": [b"\xff\xd6", b"\xff\xa4", b"\x00\x84"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
    "onity": {
        "label": "Onity HT",
        "frame_prefixes": [b"\xff\xd6", b"\x02\x02", b"\xaa\x55"],
        "uid_slice": (2, 6),
        "frame_gap_ms": 50.0,
        "default_baud": 9600,
    },
}


def get_sniffer_profile(profile_id: str) -> Dict[str, Any]:
    """返回品牌档案副本（未知标识回退自动）。"""
    pid = (profile_id or "auto").strip() or "auto"
    if pid not in SNIFFER_BRANDS:
        pid = "auto"
    out = dict(SNIFFER_BRANDS[pid])
    out["id"] = pid
    return out


# ─── 信号解析器 ───────────────────────────────────────────────────────────────


class CardSignalParser:
    """
    解析串口原始字节流，提取门锁卡信息。
    支持通用 APDU + 国内常见品牌档案（帧头 / UID 切片 / 帧间隔由档案驱动）。
    """

    WRITE_PATTERNS_DEFAULT = [
        b"\x02\x02",
        b"\xFF\xD6",
        b"\xFF\xA4",
        b"\x60\x00",
        b"\xAA\x55",
    ]

    @staticmethod
    def _prefix_list(profile: Dict[str, Any]) -> List[bytes]:
        extra = profile.get("frame_prefixes") or []
        if not extra:
            return list(CardSignalParser.WRITE_PATTERNS_DEFAULT)
        merged: List[bytes] = []
        seen = set()
        for p in list(extra) + CardSignalParser.WRITE_PATTERNS_DEFAULT:
            if p and p not in seen:
                merged.append(p)
                seen.add(p)
        return merged

    @staticmethod
    def parse_frame(data: bytes, profile_id: str = "auto") -> Optional[Dict[str, Any]]:
        """
        尝试从原始字节中解析发卡信息。
        profile_id 对应嗅探品牌档案键。
        """
        if len(data) < 6:
            return None

        prof = get_sniffer_profile(profile_id)
        label = prof.get("label", profile_id)
        uid_a, uid_b = prof.get("uid_slice", (2, 6))
        uid_a, uid_b = int(uid_a), int(uid_b)
        if len(data) < uid_b:
            uid_a, uid_b = 2, min(6, len(data))

        result: Dict[str, Any] = {
            "raw_hex": data.hex().upper(),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "room_id": "",
            "key_a": "",
            "key_b": "",
            "sector": "",
            "card_uid": "",
            "protocol": "unknown",
            "brand_profile": label,
            "brand_id": prof.get("id", "auto"),
        }

        if len(data) >= uid_b:
            uid_candidate = data[uid_a:uid_b].hex().upper()
            if uid_candidate not in ("00000000", "FFFFFFFF", ""):
                result["card_uid"] = uid_candidate

        ascii_parts = re.findall(rb"[\x20-\x7E]{3,}", data)
        for part in ascii_parts:
            decoded = part.decode("ascii", errors="ignore").strip()
            room_match = re.search(r"\b(\d{3,4})\b", decoded)
            if room_match:
                result["room_id"] = room_match.group(1)
                break

        for i in range(len(data) - 6):
            chunk = data[i : i + 6]
            hex_chunk = chunk.hex().upper()
            if hex_chunk in ("000000000000", "FFFFFFFFFFFF", "A0A1A2A3A4A5"):
                continue
            if len(set(chunk)) <= 2:
                continue
            if not result["key_a"]:
                result["key_a"] = hex_chunk
            elif hex_chunk != result["key_a"]:
                result["key_b"] = hex_chunk
                break

        matched_hex = ""
        for pattern in CardSignalParser._prefix_list(prof):
            if data.startswith(pattern):
                matched_hex = pattern.hex().upper()
                break
        if matched_hex:
            result["protocol"] = f"{matched_hex}"
        else:
            result["protocol"] = f"heuristic:{prof.get('id', 'auto')}"

        if result["card_uid"] or result["key_a"] or result["room_id"]:
            return result
        return None


# ─── 嗅探线程 ─────────────────────────────────────────────────────────────────

class SnifferThread(QThread):
    """后台串口嗅探线程（按品牌档案调整帧间隔与解析）"""

    packet_captured = QtSignal(dict)
    status_changed = QtSignal(str)
    error_occurred = QtSignal(str)

    def __init__(self, port: str, baud: int = 9600, profile_id: str = "auto"):
        super().__init__()
        self.port = port
        self.baud = baud
        self.profile_id = (profile_id or "auto").strip() or "auto"
        self._running = False
        self._ser = None
        prof = get_sniffer_profile(self.profile_id)
        self._frame_gap_s = max(0.02, float(prof.get("frame_gap_ms", 50.0)) / 1000.0)

    def run(self):
        try:
            import serial
        except ImportError:
            self.error_occurred.emit("pyserial 未安装，请运行: pip install pyserial")
            return

        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=0.5)
            self._running = True
            plabel = get_sniffer_profile(self.profile_id).get("label", self.profile_id)
            self.status_changed.emit(
                i18n.t("card_sniffer.sniffing_status").format(port=self.port, baud=self.baud, label=plabel)
            )

            buf = b""
            last_activity = time.time()

            while self._running:
                try:
                    chunk = self._ser.read(64)
                    if chunk:
                        buf += chunk
                        last_activity = time.time()
                    elif buf and (time.time() - last_activity) > self._frame_gap_s:
                        result = CardSignalParser.parse_frame(buf, self.profile_id)
                        if result:
                            self.packet_captured.emit(result)
                        buf = b""
                except Exception:
                    buf = b""
                    time.sleep(0.1)

        except Exception as e:
            err = str(e)
            if any(k in err.lower() for k in ("could not open", "access is denied", "file not found", "找不到", "拒绝访问")):
                err += i18n.t("card_sniffer.serial_err_hint")
            self.error_occurred.emit(i18n.t("card_sniffer.serial_err_prefix").format(err=err))
        finally:
            if self._ser and self._ser.is_open:
                self._ser.close()
            self.status_changed.emit("⏹ 嗅探已停止")

    def stop(self):
        self._running = False
        if self._ser and self._ser.is_open:
            self._ser.close()


# ─── 嗅探对话框 UI ────────────────────────────────────────────────────────────

class CardSnifferDialog(QDialog):
    """发卡信号嗅探对话框"""

    def __init__(self, parent=None, *, initial_brand: str = ""):
        super().__init__(parent)
        self._initial_brand = initial_brand
        self.setWindowTitle(i18n.t("card_sniffer.window_title"))
        style_dialog(self, size="large")
        self._thread: Optional[SnifferThread] = None
        self._captured: List[Dict] = []
        self._guide = SniffGuideSession()
        self._build_ui()
        self.guide_panel.refresh()
        QTimer = __import__("PySide6.QtCore", fromlist=["QTimer"]).QTimer
        QTimer.singleShot(400, self._maybe_offer_drivers_on_open)

    def _maybe_offer_drivers_on_open(self) -> None:
        real = list_serial_ports_detailed()
        if not real:
            offer_usb_driver_install(self)

    def _refresh_port_combo(self) -> None:
        cur = self.port_combo.currentText() if hasattr(self, "port_combo") else ""
        self.port_combo.clear()
        ports = _list_ports()
        self.port_combo.addItems(ports)
        if cur:
            idx = self.port_combo.findText(cur)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
        if not list_serial_ports_detailed():
            self.lbl_status.setText(i18n.t("card_sniffer.no_com_port"))

    def _open_bundled_drivers(self) -> None:
        from usb_driver_helper import detect_serial_chip

        open_driver_install_dialog(self, chip_hint=detect_serial_chip(self.port_combo.currentText()))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 标题
        title = QLabel(i18n.t("card_sniffer.window_title"))
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        title.setStyleSheet(f"color:{_p('text')};")
        layout.addWidget(title)

        hint = QLabel(i18n.t("card_sniffer.hint_text"))
        hint.setWordWrap(True)
        hint.setObjectName("Small")
        # hint.setStyleSheet removed — uses ObjectName("Small") above
        layout.addWidget(hint)

        self.guide_panel = MigrationGuidePanel()
        self.guide_panel.bind_session(self._guide)
        self.guide_panel.set_sniff_start_callback(self._ensure_sniff_running)
        layout.addWidget(self.guide_panel)

        grp_brand = QGroupBox(i18n.t("card_sniffer.brand_group"))
        bf = QFormLayout(grp_brand)
        self.brand_combo = QComboBox()
        self._fill_brand_combo()
        self.brand_combo.currentIndexChanged.connect(self._sync_baud_hint)
        bf.addRow(i18n.t("card_sniffer.brand_label") + ":", self.brand_combo)
        self.lbl_brand_baud_hint = QLabel("")
        self.lbl_brand_baud_hint.setWordWrap(True)
        self.lbl_brand_baud_hint.setObjectName("Tiny")
        self.lbl_brand_baud_hint.setStyleSheet(f"color:{_p('text_muted')};")
        bf.addRow(self.lbl_brand_baud_hint)
        layout.addWidget(grp_brand)

        # 串口配置
        grp = QGroupBox(i18n.t("card_sniffer.serial_config"))
        form = QFormLayout(grp)

        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self._refresh_port_combo()
        port_row.addWidget(self.port_combo, 1)
        btn_ports = QPushButton(i18n.t("usb_drv_refresh_ports"))
        btn_ports.setObjectName("FdGhostBtn")
        btn_ports.clicked.connect(self._refresh_port_combo)
        port_row.addWidget(btn_ports)
        btn_drv = QPushButton(i18n.t("usb_drv_sniffer_btn"))
        btn_drv.setObjectName("FdGhostBtn")
        btn_drv.clicked.connect(self._open_bundled_drivers)
        port_row.addWidget(btn_drv)
        form.addRow(i18n.t("card_sniffer.serial_label") + ":", port_row)

        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "14400", "19200", "38400", "57600", "115200", "230400"])
        form.addRow(i18n.t("card_sniffer.baud_label") + ":", self.baud_combo)

        layout.addWidget(grp)

        # 控制按钮
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton(i18n.t("card_sniffer.btn_start_sniff"))
        self.btn_start.setObjectName("SolidPrimaryBtn")
        self.btn_start.clicked.connect(self._toggle_sniff)
        btn_row.addWidget(self.btn_start)

        self.lbl_status = QLabel(i18n.t("card_sniffer.status_stopped"))
        self.lbl_status.setObjectName("Small")
        btn_row.addWidget(self.lbl_status)
        btn_row.addStretch()

        btn_save = QPushButton(i18n.t("card_sniffer.btn_save"))
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.clicked.connect(self._save_selected)
        btn_row.addWidget(btn_save)

        btn_clear = QPushButton(i18n.t("card_sniffer.btn_clear"))
        btn_clear.setObjectName("FdGhostBtn")
        btn_clear.clicked.connect(self._clear)
        btn_row.addWidget(btn_clear)

        layout.addLayout(btn_row)

        # 捕获结果表格
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            i18n.t("card_sniffer.col_time"),
            i18n.t("card_sniffer.col_brand"),
            i18n.t("card_sniffer.col_room"),
            i18n.t("card_sniffer.col_uid"),
            i18n.t("card_sniffer.col_key_a"),
            i18n.t("card_sniffer.col_key_b"),
            i18n.t("card_sniffer.col_hex"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 64)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 56)
        self.table.setColumnWidth(3, 88)
        self.table.setColumnWidth(4, 120)
        self.table.setColumnWidth(5, 120)
        fd_apply_table_palette(self.table)
        layout.addWidget(self.table, 1)

        # 日志
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(56)
        self.log.setPlaceholderText(i18n.t("card_sniffer.log_placeholder"))
        layout.addWidget(self.log)

        # 关闭按钮
        btn_close = QPushButton(i18n.t("card_sniffer.btn_close"))
        btn_close.setObjectName("FdGhostBtn")
        # btn_close.setStyleSheet removed — uses ObjectName("FdGhostBtn") above
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close, alignment=0)

    def _fill_brand_combo(self) -> None:
        self.brand_combo.blockSignals(True)
        self.brand_combo.clear()
        order = [
            "auto", "prousb_cardlock", "adel", "anjubao", "kaidisite", "bitech", "hune", "ygs", "dinggu",
            "tcl", "beian", "dessmann", "orbita_cn", "level", "tengo", "jweilink",
            "dormakaba", "vingcard", "onity",
        ]
        for bid in order:
            if bid in SNIFFER_BRANDS:
                self.brand_combo.addItem(SNIFFER_BRANDS[bid]["label"], bid)
        self.brand_combo.blockSignals(False)
        if self._initial_brand:
            idx = self.brand_combo.findData(self._initial_brand)
            if idx >= 0:
                self.brand_combo.setCurrentIndex(idx)
        self._sync_baud_hint()

    def _sync_baud_hint(self) -> None:
        bid = self.brand_combo.currentData()
        meta = get_sniffer_profile(str(bid or "auto"))
        baud = meta.get("default_baud")
        gap = meta.get("frame_gap_ms", 50)
        if baud:
            self.lbl_brand_baud_hint.setText(
                i18n.t("card_sniffer.baud_hint_fixed").format(baud=baud, gap=gap)
            )
        else:
            self.lbl_brand_baud_hint.setText(
                i18n.t("card_sniffer.baud_hint_auto").format(gap=gap)
            )

    def _ensure_sniff_running(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        port = self.port_combo.currentText()
        if not port:
            show_warning(self, i18n.t("card_sniffer.serial_label"), i18n.t("card_sniffer.warn_select_port"))
            return
        baud = int(self.baud_combo.currentText())
        pid = str(self.brand_combo.currentData() or "auto")
        self._thread = SnifferThread(port, baud, pid)
        self._thread.packet_captured.connect(self._on_packet)
        self._thread.status_changed.connect(self.lbl_status.setText)
        self._thread.error_occurred.connect(self._on_error)
        self._thread.start()
        self.btn_start.setText("⏹ 停止嗅探")
        self.btn_start.setStyleSheet(
            f"background:{_p('danger')}; color:white; border-radius:8px; padding:8px 20px; font-weight:700;"
        )
        self.brand_combo.setEnabled(False)
        self.port_combo.setEnabled(False)
        self.baud_combo.setEnabled(False)
        self.lbl_status.setText(i18n.t("card_sniffer.status_listening"))
        self.log.append(i18n.t("card_sniffer.log_listening"))

    def _toggle_sniff(self):
        if self._thread and self._thread.isRunning():
            self._thread.stop()
            self._thread.wait(2000)
            self.btn_start.setText(i18n.t("card_sniffer.btn_start_sniff"))
            self.btn_start.setStyleSheet(
                f"background:{_p('amount_positive')}; color:white; border-radius:8px; padding:8px 20px; font-weight:700;"
            )
            self.brand_combo.setEnabled(True)
            self.port_combo.setEnabled(True)
            self.baud_combo.setEnabled(True)
        else:
            self._ensure_sniff_running()

    def _on_packet(self, pkt: Dict):
        self._captured.append(pkt)
        row = self.table.rowCount()
        self.table.insertRow(row)
        vals = [
            pkt.get("timestamp", ""),
            pkt.get("brand_profile", "-"),
            pkt.get("room_id", "-"),
            pkt.get("card_uid", "-"),
            pkt.get("key_a", "-"),
            pkt.get("key_b", "-"),
            pkt.get("raw_hex", "")[:36],
        ]
        for c, v in enumerate(vals):
            item = QTableWidgetItem(v)
            if pkt.get("key_a") and c in (4, 5):
                item.setBackground(QColor(_p('amount_positive')).lighter(180))
            self.table.setItem(row, c, item)
        self.table.scrollToBottom()
        self.log.append(
            i18n.t("card_sniffer.log_packet").format(
                ts=pkt['timestamp'], profile=pkt.get('brand_profile',''),
                room=pkt.get('room_id','-'), uid=pkt.get('card_uid','-'),
                key_a=pkt.get('key_a','-')
            )
        )
        if pkt.get("key_a"):
            self._guide.on_packet_captured(True)
            self.log.append(i18n.t("card_sniffer.log_card_read"))
            self.guide_panel.refresh()

    def _on_error(self, msg: str):
        self.log.append(i18n.t("card_sniffer.log_error_prefix").format(msg=msg))
        self.lbl_status.setText(i18n.t("card_sniffer.log_error_prefix").format(msg=msg))
        if any(k in msg for k in (i18n.t("card_sniffer.serial_err"), "could not open", "拒绝访问", "找不到", "COM")):
            offer_usb_driver_install(self, reason=msg)

    def _save_selected(self):
        rows = set(i.row() for i in self.table.selectedItems())
        if not rows:
            show_warning(self, i18n.t("card_sniffer.tip"), i18n.t("card_sniffer.warn_select_row"))
            return

        saved = 0
        keys_to_save = {}
        for r in rows:
            pkt = self._captured[r] if r < len(self._captured) else {}
            room_id = pkt.get("room_id", "")
            key_a = pkt.get("key_a", "")
            key_b = pkt.get("key_b", "")
            card_uid = pkt.get("card_uid", "")

            if key_a:
                keys_to_save[f"room_{room_id}_key_a"] = key_a
            if key_b:
                keys_to_save[f"room_{room_id}_key_b"] = key_b

            # 写入卡片记录
            try:
                db.execute(
                    "INSERT OR IGNORE INTO card_records "
                    "(card_id, room_id, card_type, status, source_system, registry_kind) "
                    "VALUES (?, ?, ?, ?, ?, 'guest')",
                    (card_uid or f"SNIFF_{r}", room_id, "MIFARE Classic", "sniffed", "card_sniffer")
                )
                saved += 1
            except Exception:
                pass

        # 写入密钥配置与品牌元数据
        existing: Dict[str, Any] = {}
        try:
            existing = json.loads(db.get_config("legacy_lock_keys") or "{}")
        except Exception:
            pass
        bid = str(self.brand_combo.currentData() or "auto")
        plabel = get_sniffer_profile(bid).get("label", bid)
        if keys_to_save:
            existing.update(keys_to_save)
        existing["sniffer_last_profile_id"] = bid
        existing["sniffer_last_profile_label"] = plabel
        db.set_config("legacy_lock_keys", json.dumps(existing, ensure_ascii=False))
        db.set_config("card_system", "generic_mifare")
        db.set_config("card_sniffer_profile", bid)
        db.set_config("lock_brand", bid)
        db.set_config("lock_brand_name", plabel)
        for pkt in self._captured:
            try:
                from power_controller_config import sync_power_from_sniffer
                sync_power_from_sniffer(pkt)
            except Exception:
                pass

        show_info(
            self, i18n.t("card_sniffer.save_title"),
            i18n.t("card_sniffer.save_body").format(saved=saved, keys=len(keys_to_save)),
        )
        self.accept()  # 通知调用方嗅探完成

    def _clear(self):
        self.table.setRowCount(0)
        self._captured.clear()
        self.log.clear()

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            self._thread.stop()
            self._thread.wait(1000)
        super().closeEvent(event)


# ─── 入口函数 ─────────────────────────────────────────────────────────────────

def open_card_sniffer(parent=None, *, show_intro: bool = True, initial_brand: str = ""):
    """打开发卡信号嗅探对话框"""
    from vendor_gate import require_vendor_or_block

    if not require_vendor_or_block(parent):
        return None
    # 确保 card_records 表存在
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS card_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id TEXT, room_id TEXT,
                issue_time TEXT, expire_time TEXT,
                card_type TEXT DEFAULT 'MIFARE Classic',
                status TEXT DEFAULT 'active',
                source_system TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    except Exception:
        pass
    if show_intro:
        show_info(
            parent,
            i18n.t("card_sniffer.intro_title"),
            cardlock_sniff_intro()
            + "\n\n"
            + i18n.t("card_sniffer.intro_body"),
        )
    dlg = CardSnifferDialog(parent, initial_brand=initial_brand)
    return dlg.exec()
