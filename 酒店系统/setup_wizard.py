"""
setup_wizard.py — 系统初始化向导
首次启动时自动弹出，引导用户完成基础配置。
完成后写入 system_config.setup_done = 1，下次不再弹出。

步骤（先开业、后通知）：
  第一步 — 酒店基本信息（名称/地址/电话/币种/WiFi，必填）
  Step 2 — 房间录入（批量建房，可稍后补）
  Step 3 — 商品库存与开业盘点（可跳过）
  Step 4 — 总卡/授权卡登记（可跳过）
  Step 5 — Telegram 机器人（可选，建议网管）
  第六步 — 角色与通知（各岗位 Chat ID，可跳过）
  完成页 — 写入 setup_done=1
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QFormLayout, QWidget, QStackedWidget, QFrame,
    QDoubleSpinBox, QComboBox, QSpinBox, QProgressBar,
    QScrollArea, QGroupBox, QTextEdit, QFileDialog, QTableWidget, QTableWidgetItem, QHeaderView,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from design_tokens import _p
from database import db
from brand_config_v4 import APP_NAME_FULL
from ui_helpers import (
    show_info,
    show_warning,
    ask_confirm,
    style_dialog,
    build_dialog_header,
    make_dialog_scroll_area,
)
from ui_surface import fd_apply_table_palette
import logging
logger = logging.getLogger(__name__)


# ── 主题感知色彩辅助（所有 setStyleSheet 使用这些，不硬编码）──
_WIZ_TEXT = lambda: _p("text")
_WIZ_MUTED = lambda: _p("text_muted")
_WIZ_BODY = lambda: _p("text")
_WIZ_CARD_BG = lambda: _p("surface")
_WIZ_CARD_BORDER = lambda: _p("border")
_WIZ_BG = lambda: _p("bg_root")
_WIZ_SUCCESS = lambda: _p("amount_positive")
_WIZ_DANGER = lambda: _p("danger")
_WIZ_PRIMARY = lambda: _p("primary")
_WIZ_HEADER_BG = lambda: _p("sidebar")
_WIZ_HEADER_TEXT = lambda: _p("surface")
_WIZ_PROGRESS_BG = lambda: _p("border")
_WIZ_PROGRESS_CHUNK = lambda: _p("primary")


# 向导内容区跟随主题，不再硬编码白底



# ─────────────────────────────────────────────
# 欢迎页
# ─────────────────────────────────────────────
class _WelcomePage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(20, 16, 20, 16)

        from brand_assets import make_brand_mark_label

        icon = make_brand_mark_label(48, object_name="WizardBrandMark")
        l.addWidget(icon)

        title = QLabel("欢迎使用 Solid 酒店管理系统")
        title.setObjectName("H2Title")
        title.setStyleSheet(f"font-weight:bold;color:{_WIZ_TEXT()};")
        title.setAlignment(Qt.AlignCenter)
        l.addWidget(title)

        sub = QLabel("先能接待客人，再配通知 · 除 ① 外均可「稍后补全」")
        sub.setObjectName("Small")
        sub.setStyleSheet(f"color:{_WIZ_MUTED()};")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        l.addWidget(sub)

        desc = QLabel(
            "① 酒店信息（必填）→ ② 房间 → ②b 旧门锁（可跳过）→ "
            "③ 商品盘点 → ④ 门卡登记 → ⑤ Telegram → ⑥ 角色通知\n\n"
            "点底部【开始配置】进入；看不清可在此区域内上下滚动。"
        )
        desc.setStyleSheet(
            f"color:{_WIZ_BODY()};line-height:1.5;"
            f"background:{_WIZ_BG()};border:1px solid {_WIZ_CARD_BORDER()};border-radius:8px;padding:10px;"
        )
        desc.setAlignment(Qt.AlignLeft)
        desc.setWordWrap(True)
        l.addWidget(desc)


# ─────────────────────────────────────────────
# 第1页：酒店基本信息
# ─────────────────────────────────────────────
class _HotelInfoPage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(24, 16, 24, 16)
        l.addWidget(build_dialog_header("① 酒店基本信息", "店名、币种、押金、税率、WiFi；本步须完成，不可跳过"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        f = QFormLayout(inner)
        f.setSpacing(10)

        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("如：阳光商务酒店")
        self.txt_name.setText(db.get_config("hotel_name") or "")

        self.txt_address = QLineEdit()
        self.txt_address.setPlaceholderText("如：泰国曼谷素坤逸路 123 号")
        self.txt_address.setText(db.get_config("hotel_address") or "")

        self.txt_phone = QLineEdit()
        self.txt_phone.setPlaceholderText("如：+66-2-123-4567")
        self.txt_phone.setText(db.get_config("hotel_phone") or "")

        self.cmb_currency = QComboBox()
        currencies = [
            ("฿ 泰铢 (THB)", "฿", "THB"),
            ("$ 美元 (USD)", "$", "USD"),
            ("¥ 人民币 (CNY)", "¥", "CNY"),
            ("₫ 越南盾 (VND)", "₫", "VND"),
            ("៛ 瑞尔 (KHR)", "៛", "KHR"),
            ("K 缅元 (MMK)", "K", "MMK"),
            ("€ 欧元 (EUR)", "€", "EUR"),
        ]
        for label, sym, code in currencies:
            self.cmb_currency.addItem(label, (sym, code))
        # 恢复已保存的币种
        saved_code = db.get_config("currency_code") or "THB"
        for i in range(self.cmb_currency.count()):
            if self.cmb_currency.itemData(i)[1] == saved_code:
                self.cmb_currency.setCurrentIndex(i)
                break

        self.spn_tax = QDoubleSpinBox()
        self.spn_tax.setRange(0, 0.5)
        self.spn_tax.setSingleStep(0.01)
        self.spn_tax.setDecimals(2)
        self.spn_tax.setSuffix("  （如 0.07 = 7%）")
        self.spn_tax.setValue(float(db.get_config("tax_rate") or 0.07))

        self.spn_default_deposit = QDoubleSpinBox()
        self.spn_default_deposit.setRange(0, 999999)
        self.spn_default_deposit.setDecimals(0)
        self.spn_default_deposit.setValue(db.get_config_float("default_deposit", 50.0))

        self.txt_wifi_name = QLineEdit()
        from brand_config_v4 import APP_NAME
        self.txt_wifi_name.setPlaceholderText(f"如：{APP_NAME}_Hotel_5G")
        self.txt_wifi_name.setText(db.get_config("wifi_name") or "")

        self.txt_wifi_pwd = QLineEdit()
        self.txt_wifi_pwd.setPlaceholderText("WiFi 密码（客人机器人一键查看）")
        self.txt_wifi_pwd.setText(db.get_config("wifi_password") or "")

        f.addRow("酒店名称 *:", self.txt_name)
        f.addRow("酒店地址:", self.txt_address)
        f.addRow("联系电话:", self.txt_phone)
        f.addRow("货币单位:", self.cmb_currency)
        f.addRow("税率:", self.spn_tax)
        f.addRow("默认押金（全店）:", self.spn_default_deposit)
        f.addRow("WiFi 名称:", self.txt_wifi_name)
        f.addRow("WiFi 密码:", self.txt_wifi_pwd)

        scroll.setWidget(inner)
        l.addWidget(scroll, 1)

        tip = QLabel("无线网络密码将显示在客人机器人的无线网络一键查看功能中。")
        tip.setObjectName("Tiny")
        tip.setStyleSheet(f"color:{_WIZ_MUTED()};background:{_WIZ_BG()};border:1px solid {_WIZ_CARD_BORDER()};border-radius:6px;padding:8px;")
        tip.setWordWrap(True)
        l.addWidget(tip)

    def save(self):
        name = self.txt_name.text().strip()
        if not name:
            show_warning(self, "信息不完整", "酒店名称不能为空。")
            return False
        db.set_config("hotel_name", name)
        db.set_config("hotel_address", self.txt_address.text().strip())
        db.set_config("hotel_phone", self.txt_phone.text().strip())
        db.set_config("tax_rate", str(self.spn_tax.value()))
        db.set_config("default_deposit", str(self.spn_default_deposit.value()))
        sym, code = self.cmb_currency.currentData()
        db.set_config("currency_symbol", sym)
        db.set_config("currency_code", code)
        db.set_config("wifi_name", self.txt_wifi_name.text().strip())
        db.set_config("wifi_password", self.txt_wifi_pwd.text().strip())
        return True


# ─────────────────────────────────────────────
        # 第2页：Telegram 机器人配置
# ─────────────────────────────────────────────
class _TelegramPage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(24, 16, 24, 16)
        l.addWidget(build_dialog_header(
            "④ Telegram 通知（可选）",
            "机器人由厂家统一配置；此处只填本店老板 Chat ID。可点「稍后补全」"
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setSpacing(12)
        il.setContentsMargins(0, 0, 8, 0)

        from telegram_bot_config import status_label

        grp_status = QGroupBox("🤖 厂家机器人（只读）")
        grp_status.setStyleSheet(f"QGroupBox {{ font-weight: bold; color: {_WIZ_TEXT()}; }}")
        sf = QVBoxLayout(grp_status)
        self.lbl_bot_status = QLabel(status_label())
        self.lbl_bot_status.setObjectName("Small")
        self.lbl_bot_status.setWordWrap(True)
        self.lbl_bot_status.setStyleSheet(
            "color:{_WIZ_SUCCESS()}; background:{_WIZ_BG()}; border:1px solid {_WIZ_CARD_BORDER()}; "
            "border-radius:6px; padding:8px;"
        )
        sf.addWidget(self.lbl_bot_status)
        il.addWidget(grp_status)

        grp_hotel = QGroupBox("本店接收人")
        grp_hotel.setStyleSheet(f"QGroupBox {{ font-weight: bold; color: {_WIZ_TEXT()}; }}")
        hf = QFormLayout(grp_hotel)

        self.txt_boss_chat = QLineEdit()
        self.txt_boss_chat.setPlaceholderText("老板 Chat ID（向 @userinfobot 发消息获取）")
        self.txt_boss_chat.setText(db.get_config("telegram_chat_id") or "")

        hf.addRow("老板 Chat ID:", self.txt_boss_chat)
        il.addWidget(grp_hotel)

        # 测试按钮
        self.btn_test = QPushButton("📡 发送测试消息到老板")
        self.btn_test.setObjectName("FdGhostBtn")
        fd_apply_action_btn(self.btn_test)
        self.btn_test.clicked.connect(self._test_send)
        il.addWidget(self.btn_test)

        self.lbl_test_result = QLabel("")
        self.lbl_test_result.setWordWrap(True)
        il.addWidget(self.lbl_test_result)

        tip = QLabel(
            "机器人 Token 由厂家在云端配置，酒店无需填写。\n"
            "   获取聊天ID：向 @userinfobot 发消息即可。\n"
            "   不配置不影响入住；开通推送后厂家会同步机器人到本机。"
        )
        tip.setObjectName("Tiny")
        tip.setStyleSheet(f"color:{_WIZ_MUTED()};background:{_WIZ_BG()};border:1px solid {_WIZ_CARD_BORDER()};border-radius:6px;padding:8px;")
        tip.setWordWrap(True)
        il.addWidget(tip)
        il.addStretch()

        scroll.setWidget(inner)
        l.addWidget(scroll, 1)

    def _test_send(self):
        from telegram_bot_config import get_work_bot_token, status_label
        self.lbl_bot_status.setText(status_label())
        token = get_work_bot_token()
        chat_id = self.txt_boss_chat.text().strip()
        if not token:
            self.lbl_test_result.setText("❌ 厂家机器人尚未同步到本机，请联系厂家或检查云端地址")
            self.lbl_test_result.setStyleSheet(f"color:{_WIZ_DANGER()};")
            return
        if not chat_id:
            self.lbl_test_result.setText("❌ 请填写老板 Chat ID")
            self.lbl_test_result.setStyleSheet(f"color:{_WIZ_DANGER()};")
            return
        self.btn_test.setEnabled(False)
        self.btn_test.setText("发送中...")
        try:
            import requests
            hotel_name = db.get_config("hotel_name") or "酒店"
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id,
                      "text": f"✅ <b>{hotel_name}</b> — {APP_NAME_FULL} 初始化向导测试消息\n\n机器人配置成功！系统已就绪。",
                      "parse_mode": "HTML"},
                timeout=8
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                self.lbl_test_result.setText("✅ 测试消息发送成功！")
                self.lbl_test_result.setStyleSheet(f"color:{_WIZ_SUCCESS()};font-weight:bold;")
            else:
                err = resp.json().get("description", "未知错误")
                self.lbl_test_result.setText(f"❌ 发送失败：{err}")
                self.lbl_test_result.setStyleSheet(f"color:{_WIZ_DANGER()};")
        except Exception as e:
            self.lbl_test_result.setText(f"❌ 网络错误：{e}")
            self.lbl_test_result.setStyleSheet(f"color:{_WIZ_DANGER()};")
        finally:
            self.btn_test.setEnabled(True)
            self.btn_test.setText("📡 发送测试消息到老板")

    def save(self):
        boss_chat = self.txt_boss_chat.text().strip()
        if boss_chat:
            db.set_config("telegram_chat_id", boss_chat)
        return True


# ─────────────────────────────────────────────
# 第3页：角色与通知
# ─────────────────────────────────────────────
class _RolesPage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(8)
        l.setContentsMargins(24, 16, 24, 16)
        l.addWidget(build_dialog_header(
            "⑤ 角色与通知",
            "各岗位聊天号码；机器人由厂家配置，此处只填员工身份号"
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        fl = QVBoxLayout(inner)
        fl.setSpacing(10)

        def _make_role_group(title, key_chat, key_group, placeholder_chat, placeholder_group):
            grp = QGroupBox(title)
            grp.setStyleSheet(f"QGroupBox {{ font-weight: bold; color: {_WIZ_TEXT()}; font-size: 12px; }}")
            gf = QFormLayout(grp)
            gf.setSpacing(6)
            chat_edit = QLineEdit()
            chat_edit.setPlaceholderText(placeholder_chat)
            chat_edit.setText(db.get_config(key_chat) or "")
            group_edit = QLineEdit()
            group_edit.setPlaceholderText(placeholder_group)
            group_edit.setText(db.get_config(key_group) or "")
            gf.addRow("个人 Chat ID:", chat_edit)
            gf.addRow("群组 ID（可选）:", group_edit)
            return grp, chat_edit, group_edit

        grp_boss, self.boss_chat, self.boss_group = _make_role_group(
            "👔 老板", "telegram_chat_id", "boss_group_id",
            "老板个人 Chat ID", "老板群组 ID（可选）"
        )
        grp_front, self.front_chat, self.front_group = _make_role_group(
            "🏨 前台", "front_desk_chat_id", "front_desk_group_id",
            "前台个人 Chat ID（多个用逗号分隔）", "前台群组 ID（推荐）"
        )
        grp_hk, self.hk_chat, self.hk_group = _make_role_group(
            "保洁", "housekeeping_chat_id", "housekeeping_group_id",
            "保洁个人 Chat ID", "保洁群组 ID（推荐）"
        )
        grp_eng, self.eng_chat, self.eng_group = _make_role_group(
            "🔧 工程/维修", "engineering_chat_id", "engineering_group_id",
            "工程师 Chat ID", "工程群组 ID（可选）"
        )
        grp_food, self.food_chat, self.food_group = _make_role_group(
            "餐饮/超市配送", "food_chat_id", "food_group_id",
            "餐饮负责人 Chat ID", "餐饮群组 ID（可选）"
        )

        for grp in [grp_boss, grp_front, grp_hk, grp_eng, grp_food]:
            fl.addWidget(grp)

        fl.addStretch()
        scroll.setWidget(inner)
        l.addWidget(scroll, 1)

        tip = QLabel("群组 ID 通常为负数（如 -1001234567890）。将机器人加入群组后，向群发消息即可获取群组 ID。")
        tip.setObjectName("Tiny")
        tip.setStyleSheet(f"color:{_WIZ_MUTED()};background:{_WIZ_BG()};border:1px solid {_WIZ_CARD_BORDER()};border-radius:6px;padding:8px;")
        tip.setWordWrap(True)
        l.addWidget(tip)

    def save(self):
        def _save(key, widget):
            v = widget.text().strip()
            if v:
                db.set_config(key, v)

        _save("telegram_chat_id", self.boss_chat)
        _save("boss_group_id", self.boss_group)
        _save("front_desk_chat_id", self.front_chat)
        _save("front_desk_group_id", self.front_group)
        _save("housekeeping_chat_id", self.hk_chat)
        _save("housekeeping_group_id", self.hk_group)
        _save("engineering_chat_id", self.eng_chat)
        _save("engineering_group_id", self.eng_group)
        _save("food_chat_id", self.food_chat)
        _save("food_group_id", self.food_group)
        return True


# ─────────────────────────────────────────────
# 第4页：商品库存录入（可跳过）
# ─────────────────────────────────────────────
class _ShopPage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(24, 16, 24, 16)
        l.addWidget(build_dialog_header("③ 商品库存与开业盘点", "可添加 SKU；下方「实盘」在点「下一步」时写入库存并记 INIT_OPENING 流水"))

        # 手动添加区
        grp = QGroupBox("手动添加商品")
        grp.setStyleSheet(f"QGroupBox {{ font-weight: bold; color: {_WIZ_TEXT()}; }}")
        gf = QFormLayout(grp)

        self.txt_sku = QLineEdit()
        self.txt_sku.setPlaceholderText("如：WATER_500ML")

        self.txt_emoji = QLineEdit()
        self.txt_emoji.setPlaceholderText("如：💧")
        self.txt_emoji.setMaxLength(32)

        self.txt_item_name = QLineEdit()
        self.txt_item_name.setPlaceholderText("如：矿泉水 500ml")

        self.spn_price = QDoubleSpinBox()
        self.spn_price.setRange(0, 99999)
        self.spn_price.setDecimals(2)
        self.spn_price.setSingleStep(1)

        self.spn_stock = QSpinBox()
        self.spn_stock.setRange(0, 9999)
        self.spn_stock.setValue(10)

        gf.addRow("商品编号:", self.txt_sku)
        gf.addRow("表情图标:", self.txt_emoji)
        gf.addRow("商品名称:", self.txt_item_name)
        gf.addRow("单价:", self.spn_price)
        gf.addRow("初始库存:", self.spn_stock)

        btn_add = QPushButton("添加商品")
        btn_add.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(btn_add, primary=True)
        btn_add.clicked.connect(self._add_item)
        gf.addRow("", btn_add)

        self.lbl_add_result = QLabel("")
        gf.addRow("", self.lbl_add_result)

        l.addWidget(grp)

        # 已添加商品列表
        self.lbl_count = QLabel("已添加：0 件商品")
        self.lbl_count.setObjectName("Small")
        self.lbl_count.setStyleSheet(f"color:{_WIZ_MUTED()};")
        l.addWidget(self.lbl_count)
        self._refresh_count()

        tip = QLabel("商品信息可在「设置 → 商品管理」中随时修改。盘点表在添加商品后会自动刷新；点底栏「稍后补全」可跳过本页。")
        tip.setObjectName("Tiny")
        tip.setStyleSheet(f"color:{_WIZ_MUTED()};background:{_WIZ_BG()};border:1px solid {_WIZ_CARD_BORDER()};border-radius:6px;padding:8px;")
        tip.setWordWrap(True)
        l.addWidget(tip)

        st = QGroupBox("开业盘点（实盘 → 系统库存）")
        st.setStyleSheet(f"QGroupBox {{ font-weight: bold; color: {_WIZ_TEXT()}; }}")
        st_l = QVBoxLayout(st)
        self.tbl_count = QTableWidget(0, 4)
        self.tbl_count.setHorizontalHeaderLabels(["SKU", "名称", "系统库存", "实盘数量"])
        self.tbl_count.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_count.setMaximumHeight(220)
        fd_apply_table_palette(self.tbl_count)
        self.tbl_count.setAlternatingRowColors(False)
        st_l.addWidget(self.tbl_count)
        l.addWidget(st)
        self._refresh_stocktake_table()

        l.addStretch()

    def _refresh_stocktake_table(self):
        self.tbl_count.setRowCount(0)
        try:
            rows = db.execute("SELECT sku, name, COALESCE(stock,0) FROM shop_items ORDER BY name").fetchall()
        except Exception:
            rows = []
        for sku, name, stock in rows:
            r = self.tbl_count.rowCount()
            self.tbl_count.insertRow(r)
            self.tbl_count.setItem(r, 0, QTableWidgetItem(str(sku)))
            self.tbl_count.setItem(r, 1, QTableWidgetItem(str(name or "")))
            it_s = QTableWidgetItem(str(int(stock)))
            it_s.setTextAlignment(Qt.AlignCenter)
            self.tbl_count.setItem(r, 2, it_s)
            sp = QSpinBox()
            sp.setRange(0, 99999)
            sp.setValue(int(stock))
            self.tbl_count.setCellWidget(r, 3, sp)

    def _refresh_count(self):
        try:
            cnt = db.execute("SELECT COUNT(*) FROM shop_items").fetchone()[0]
            self.lbl_count.setText(f"已添加：{cnt} 件商品")
        except Exception:
            pass

    def _add_item(self):
        sku = self.txt_sku.text().strip()
        name = self.txt_item_name.text().strip()
        if not sku or not name:
            self.lbl_add_result.setText("❌ 商品编号和名称不能为空")
            self.lbl_add_result.setStyleSheet(f"color:{_WIZ_DANGER()};")
            return
        emoji = self.txt_emoji.text().strip() or "📦"
        price = self.spn_price.value()
        stock = self.spn_stock.value()
        try:
            # 检查 shop_items 是否有 emoji 列
            try:
                db.execute("ALTER TABLE shop_items ADD COLUMN emoji TEXT DEFAULT '📦'")
            except Exception:
                pass
            try:
                db.execute("ALTER TABLE shop_items ADD COLUMN category TEXT DEFAULT ''")
            except Exception:
                pass
            db.execute(
                "INSERT OR REPLACE INTO shop_items (sku, category, emoji, name, price, stock) VALUES (?,?,?,?,?,?)",
                (sku, "", emoji, name, price, stock)
            )
            self.lbl_add_result.setText(f"✅ 已添加：{emoji} {name}")
            self.lbl_add_result.setStyleSheet(f"color:{_WIZ_SUCCESS()};")
            self.txt_sku.clear()
            self.txt_emoji.clear()
            self.txt_item_name.clear()
            self.spn_price.setValue(0)
            self.spn_stock.setValue(10)
            self._refresh_count()
            self._refresh_stocktake_table()
        except Exception as e:
            self.lbl_add_result.setText(f"❌ 添加失败：{e}")
            self.lbl_add_result.setStyleSheet(f"color:{_WIZ_DANGER()};")

    def save(self):
        mapping = {}
        for r in range(self.tbl_count.rowCount()):
            it0 = self.tbl_count.item(r, 0)
            w = self.tbl_count.cellWidget(r, 3)
            if not it0 or not isinstance(w, QSpinBox):
                continue
            sku = it0.text().strip()
            if sku:
                mapping[sku] = w.value()
        try:
            db.apply_opening_stocktake(mapping, "SETUP_WIZARD")
        except Exception:
            pass
        return True  # 商品录入可选


# ─────────────────────────────────────────────
# 老板账号设置（厂家驻店时为酒店老板设密码）
# ─────────────────────────────────────────────
class _BossAccountPage(QWidget):
    """厂家在此为酒店老板设账号密码；其它岗位（前台/保洁等）由老板登录后自行添加。"""

    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(24, 16, 24, 16)
        l.addWidget(build_dialog_header(
            "老板账号",
            "厂家在此为酒店老板设置登录账号与密码。完成后老板用这套账号登录主界面，"
            "再在「员工管理」里添加前台、保洁、经理等账号。"
        ))

        default_user = "老板"
        default_disp = "老板"
        try:
            row = db.execute(
                "SELECT username, display_name FROM staff_accounts "
                "WHERE role='boss' AND is_active=1 ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                default_user = row[0] or default_user
                default_disp = row[1] or default_disp
        except Exception:
            pass

        f = QFormLayout()
        f.setSpacing(10)
        self.txt_user = QLineEdit(default_user)
        self.txt_user.setPlaceholderText("登录账号名（建议留中文「老板」或店主拼音）")
        f.addRow("登录账号:", self.txt_user)

        self.txt_disp = QLineEdit(default_disp)
        self.txt_disp.setPlaceholderText("显示用姓名")
        f.addRow("显示姓名:", self.txt_disp)

        self.txt_pwd = QLineEdit()
        self.txt_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_pwd.setPlaceholderText("至少 4 位")
        f.addRow("登录密码:", self.txt_pwd)

        self.txt_pwd2 = QLineEdit()
        self.txt_pwd2.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_pwd2.setPlaceholderText("再输入一次")
        f.addRow("确认密码:", self.txt_pwd2)

        l.addLayout(f)

        tip = QLabel(
            "完成本步后：\n"
            "  • 老板账号生效，登录面板只列真实启用的账号（演示号 1234 一并停用）\n"
            "  • 老板登录主界面后，可在「员工管理」里加前台、保洁、经理等账号\n"
            "  • 新账号默认权限按角色固定，与现在一致"
        )
        tip.setObjectName("Small")
        tip.setStyleSheet(
            f"color:{_WIZ_BODY()};background:{_WIZ_BG()};"
            f"border:1px solid {_WIZ_CARD_BORDER()};border-radius:6px;padding:10px;"
        )
        tip.setWordWrap(True)
        l.addWidget(tip)
        l.addStretch()

    def save(self) -> bool:
        u = self.txt_user.text().strip()
        d = self.txt_disp.text().strip() or u
        p1 = self.txt_pwd.text()
        p2 = self.txt_pwd2.text()

        already_seeded = bool(db.get_config("vendor_seeded_boss_at"))

        if not p1 and not p2:
            if already_seeded:
                return True
            show_warning(
                self, "未设置密码",
                "首次配置必须为老板账号设置密码。若要稍后再设，请点「稍后补全」跳过本步。"
            )
            return False
        if p1 != p2:
            show_warning(self, "密码不一致", "两次密码不一致，请重新输入。")
            return False
        if len(p1) < 4:
            show_warning(self, "密码过短", "密码至少 4 位。")
            return False
        if not u:
            show_warning(self, "账号必填", "请填写老板登录账号。")
            return False

        try:
            from permission_system import finalize_vendor_boss_account
            finalize_vendor_boss_account(username=u, password=p1, display_name=d)
        except Exception as e:
            show_warning(self, "保存失败", f"创建老板账号失败：\n{e}")
            return False
        return True


# ─────────────────────────────────────────────
# 接管门锁：非厂家账号占位页
# ─────────────────────────────────────────────
class _VendorGatePlaceholder(QWidget):
    """非 debug_panel 账号看到的占位页，提示联系厂家完成接管。"""

    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setContentsMargins(24, 24, 24, 24)
        l.setSpacing(16)

        icon = QLabel("")
        icon.setStyleSheet("font-size:48px;")
        icon.setAlignment(Qt.AlignCenter)
        l.addWidget(icon)

        title = QLabel("此步骤需厂家工程师完成")
        title.setObjectName("H3Title")
        title.setStyleSheet(f"font-weight:bold;color:{_WIZ_TEXT()};")
        title.setAlignment(Qt.AlignCenter)
        l.addWidget(title)

        body = QLabel(
            "接管门锁系统、扫描发卡器、导入注册数据属于厂家驻店初始化工作。\n\n"
            "✋ 本步须由厂家人员操作。\n"
            "请用厂家账号登录后重新打开本向导，或联系 Solid 厂家工程师到场完成。\n"
            "完成后厂家退出账号，酒店前台即可正常使用。"
        )
        body.setWordWrap(True)
        body.setObjectName("Body")
        body.setStyleSheet(f"color:{_WIZ_BODY()};")
        body.setAlignment(Qt.AlignCenter)
        l.addWidget(body)

        l.addStretch()

    def save(self) -> bool:
        return True


# ─────────────────────────────────────────────
# 第2b页：旧门锁系统对接（换系统酒店，可跳过）
# ─────────────────────────────────────────────
class _LegacyLockPage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(24, 16, 24, 16)
        l.addWidget(build_dialog_header(
            "②b 接上旧门锁软件",
            "换系统且仍用原智能门锁发卡器的，在本步完成对接（发卡器须插在本电脑）",
        ))

        hint = QLabel(
            "若您正在从旧前台系统换到本系统，且门锁还是原来那套：\n"
            "请点下面按钮；弹出窗顶部【黄框】会告诉您每一步：放什么、点什么、已读取、换下一张。\n"
            "按 ①→⑤ 顺序点橙色按钮即可。\n\n"
            "全新酒店、没有旧门锁软件：点「稍后补全」跳过；\n"
            "以后可在「设置 → 接上旧软件」里补做。"
        )
        hint.setObjectName("Small")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{_WIZ_BODY()};background:{_WIZ_BG()}; "
            f"border:1px solid {_WIZ_CARD_BORDER()}; border-radius:8px; padding:10px;"
        )
        l.addWidget(hint)

        btn = QPushButton("★ 打开前台门锁对接（按 ①→⑤ 操作）")
        btn.setObjectName("SolidPrimaryBtn")
        btn.clicked.connect(self._open_frontdesk)
        l.addWidget(btn)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setObjectName("Tiny")
        self.lbl_status.setStyleSheet(f"color:{_WIZ_MUTED()};")
        l.addWidget(self.lbl_status)
        l.addStretch()
        self._refresh_status()

    def _refresh_status(self) -> None:
        ok_at = db.get_config("takeover_last_ok_at") or ""
        brand = db.get_config("lock_brand_name") or ""
        if ok_at:
            self.lbl_status.setText(f"已记录对接: {ok_at}  品牌: {brand or '—'}")
        else:
            self.lbl_status.setText("尚未对接旧门锁系统。")

    def _open_frontdesk(self) -> None:
        try:
            from cardlock_frontdesk import open_cardlock_frontdesk

            open_cardlock_frontdesk(self)
            self._refresh_status()
        except Exception as e:
            show_warning(self, "无法打开", str(e))

    def save(self) -> bool:
        return True


# ─────────────────────────────────────────────
# 第5页：批量建房
# ─────────────────────────────────────────────
class _BatchRoomPage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(24, 16, 24, 16)
        l.addWidget(build_dialog_header(
            "② 房间录入",
            "先有房间主界面才能选房态；可跳过，之后在「房态」或设置中手动添加"
        ))

        try:
            existing = int(db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] or 0)
        except Exception:
            existing = 0
        if existing > 0:
            tip = QLabel(
                f"当前已有 {existing} 间房（来自老系统接管或手工录入）。\n"
                "若没有更多房间要新增，直接点「下一步 →」即可。"
            )
            tip.setObjectName("Small")
            tip.setWordWrap(True)
            tip.setStyleSheet(
                f"color:{_WIZ_PRIMARY()};background:{_WIZ_BG()}; "
                f"border:1px solid {_WIZ_CARD_BORDER()}; border-radius:6px; padding:8px;"
            )
            tip.setWordWrap(True)
            l.addWidget(tip)

        f = QFormLayout()
        f.setSpacing(10)

        self.spn_floors = QSpinBox()
        self.spn_floors.setRange(1, 50)
        self.spn_floors.setValue(3)
        self.spn_floors.setSuffix(" 层")

        self.spn_per_floor = QSpinBox()
        self.spn_per_floor.setRange(1, 50)
        self.spn_per_floor.setValue(10)
        self.spn_per_floor.setSuffix(" 间/层")

        self.cmb_type = QComboBox()
        try:
            rts = db.execute("SELECT type_name FROM room_type_templates ORDER BY type_name").fetchall()
            for (rtn,) in rts:
                self.cmb_type.addItem(rtn)
        except Exception:
            pass
        if self.cmb_type.count() == 0:
            self.cmb_type.addItems(["标准间", "豪华间", "套房"])

        self.spn_start = QSpinBox()
        self.spn_start.setRange(1, 999)
        self.spn_start.setValue(1)
        self.spn_start.setSuffix(" 号起")

        from PySide6.QtWidgets import QCheckBox
        self.chk_skip_four = QCheckBox("跳过含数字「4」的房号（如 104、204、14）")
        self.chk_skip_four.setChecked(True)
        self.txt_skip_digits = QLineEdit()
        self.txt_skip_digits.setPlaceholderText("额外跳过数字，逗号分隔，如：4,13,14")
        self.txt_skip_digits.setEnabled(False)
        self.chk_skip_four.toggled.connect(lambda on: self.txt_skip_digits.setEnabled(bool(on)))
        self.chk_skip_four.toggled.connect(self._update_preview)
        self.txt_skip_digits.textChanged.connect(self._update_preview)

        f.addRow("楼层数:", self.spn_floors)
        f.addRow("每层房间数:", self.spn_per_floor)
        f.addRow("默认房型:", self.cmb_type)
        f.addRow("起始房号:", self.spn_start)
        f.addRow("", self.chk_skip_four)
        f.addRow("去号段:", self.txt_skip_digits)

        l.addLayout(f)

        self.lbl_preview = QLabel()
        self.lbl_preview.setObjectName("Tiny")
        self.lbl_preview.setStyleSheet(f"color:{_WIZ_BODY()};background:{_WIZ_BG()};border:1px solid {_WIZ_CARD_BORDER()};border-radius:6px;padding:8px;")
        self.lbl_preview.setWordWrap(True)
        l.addWidget(self.lbl_preview)

        self.btn_create = QPushButton("立即建房")
        self.btn_create.setObjectName("SolidPrimaryBtn")
        self.btn_create.clicked.connect(self._do_create)
        l.addWidget(self.btn_create)

        self.btn_import_mdb = QPushButton("从老系统导入房间 / 锁号")
        self.btn_import_mdb.setObjectName("FdActSecondary")
        self.btn_import_mdb.clicked.connect(self._import_from_mdb)
        l.addWidget(self.btn_import_mdb)

        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        l.addWidget(self.lbl_result)

        self.spn_floors.valueChanged.connect(self._update_preview)
        self.spn_per_floor.valueChanged.connect(self._update_preview)
        self.spn_start.valueChanged.connect(self._update_preview)
        self._update_preview()

        l.addStretch()
        self._created = False

    def _skip_digits_set(self) -> set:
        digits: set = set()
        if self.chk_skip_four.isChecked():
            digits.add("4")
        raw = (self.txt_skip_digits.text() or "").replace("，", ",")
        for part in raw.split(","):
            p = part.strip()
            if p:
                digits.add(p)
        return digits

    def _room_id_allowed(self, room_id: str, skip_digits: set) -> bool:
        if not skip_digits:
            return True
        return not any(ch in room_id for ch in skip_digits)

    def _iter_room_ids(self, floors: int, per: int, start: int, skip_digits: set):
        for floor in range(1, floors + 1):
            for room_num in range(start, start + per):
                room_id = f"{floor}{room_num:02d}"
                if self._room_id_allowed(room_id, skip_digits):
                    yield room_id

    def _update_preview(self):
        floors = self.spn_floors.value()
        per = self.spn_per_floor.value()
        start = self.spn_start.value()
        skip_digits = self._skip_digits_set()
        examples = []
        total = 0
        for room_id in self._iter_room_ids(floors, per, start, skip_digits):
            total += 1
            if len(examples) < 8:
                examples.append(room_id)
        preview_str = "、".join(examples) if examples else "（无符合去号段规则的房号，请调整条件）"
        if total > len(examples):
            preview_str += f"... 共 {total} 间"
        skip_hint = f"（已去号：{','.join(sorted(skip_digits))}）" if skip_digits else ""
        self.lbl_preview.setText(f"📋 预览：{preview_str}{skip_hint}")

    def _do_create(self):
        floors = self.spn_floors.value()
        per = self.spn_per_floor.value()
        start = self.spn_start.value()
        room_type = self.cmb_type.currentText()
        skip_digits = self._skip_digits_set()
        created = 0
        skipped = 0
        for room_id in self._iter_room_ids(floors, per, start, skip_digits):
            existing = db.execute("SELECT room_id FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            if existing:
                skipped += 1
                continue
            floor = room_id[0] if room_id else "1"
            db.execute(
                "INSERT INTO rooms (room_id, floor, room_type, status) VALUES (?,?,?,?)",
                (room_id, str(floor), room_type, "READY"),
            )
            created += 1
        from event_bus import bus
        bus.room_status_changed.emit("__batch__", "READY")
        self.lbl_result.setText(f"✅ 建房完成：新建 {created} 间，跳过已存在 {skipped} 间")
        self.lbl_result.setStyleSheet(f"color:{_WIZ_SUCCESS()};font-weight:bold;")
        self.btn_create.setText("✅ 已建房")
        self.btn_create.setEnabled(False)
        self._created = True

    def _import_from_mdb(self):
        try:
            from batch_create_dialog import ImportRoomsFromMdbDialog
            dlg = ImportRoomsFromMdbDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.lbl_result.setText("✅ 已从老系统导入房间和锁号。")
                self.lbl_result.setStyleSheet(f"color:{_WIZ_SUCCESS()};font-weight:bold;")
                self._created = True
        except Exception as exc:
            show_warning(self, "从老系统导入房间", str(exc))

    def save(self):
        return True


# ─────────────────────────────────────────────
# 门卡登记页：总卡 / 授权卡（仅写入备查，不模拟写物理卡）
# ─────────────────────────────────────────────
class _RegistryCardsPage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setSpacing(12)
        l.setContentsMargins(24, 16, 24, 16)
        l.addWidget(build_dialog_header(
            "④ 总卡 / 授权卡登记",
            "录入卡面 UID（或读卡器读出的卡号），用于备查与审计；可与「门卡」标签页中登记互通。可跳过。"
        ))
        f = QFormLayout()
        self.txt_master = QLineEdit()
        self.txt_master.setPlaceholderText("如：A1B2C3D4（总卡 UID）")
        self.txt_master_lbl = QLineEdit()
        self.txt_master_lbl.setPlaceholderText("备注，如：工程总卡")
        self.txt_auth = QLineEdit()
        self.txt_auth.setPlaceholderText("如：授权卡 UID（可选）")
        self.txt_auth_lbl = QLineEdit()
        self.txt_auth_lbl.setPlaceholderText("备注，如：前台授权卡")
        f.addRow("总卡卡号:", self.txt_master)
        f.addRow("总卡备注:", self.txt_master_lbl)
        f.addRow("授权卡卡号:", self.txt_auth)
        f.addRow("授权卡备注:", self.txt_auth_lbl)
        l.addLayout(f)
        hint = QLabel("若暂无实体卡可跳过；以后可在工作台「门卡」→「登记总卡/授权卡」补录。")
        hint.setObjectName("Tiny")
        hint.setStyleSheet(f"color:{_WIZ_MUTED()};background:{_WIZ_BG()};border:1px solid {_WIZ_CARD_BORDER()};border-radius:6px;padding:8px;")
        hint.setWordWrap(True)
        l.addWidget(hint)
        l.addStretch()

    def save(self):
        from card_system import CardService

        op = "SETUP_WIZARD"
        try:
            from permission_system import PermissionManager
            u = PermissionManager.current_user()
            if u:
                op = u.get("username") or op
        except Exception:
            pass

        mid = self.txt_master.text().strip()
        if mid:
            ok, msg = CardService.register_registry_card(
                mid, "master", self.txt_master_lbl.text().strip(), op
            )
            if not ok:
                show_warning(self, "门卡登记", msg)
                return False
        aid = self.txt_auth.text().strip()
        if aid:
            ok, msg = CardService.register_registry_card(
                aid, "auth", self.txt_auth_lbl.text().strip(), op
            )
            if not ok:
                show_warning(self, "门卡登记", msg)
                return False
        return True


# ─────────────────────────────────────────────
# 完成页
# ─────────────────────────────────────────────
class _DonePage(QWidget):
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        l.setAlignment(Qt.AlignCenter)
        l.setSpacing(16)
        l.setContentsMargins(32, 32, 32, 32)

        icon = QLabel("🎉")
        icon.setStyleSheet("font-size:64px;")
        icon.setAlignment(Qt.AlignCenter)
        l.addWidget(icon)

        title = QLabel("初始化完成！")
        title.setObjectName("H1Title")
        title.setStyleSheet(f"font-weight:bold;color:{_WIZ_SUCCESS()};")
        title.setAlignment(Qt.AlignCenter)
        l.addWidget(title)

        hotel_name = db.get_config("hotel_name") or "您的酒店"
        sub = QLabel(f"「{hotel_name}」已成功配置 {APP_NAME_FULL}")
        sub.setObjectName("Body")
        sub.setStyleSheet(f"color:{_WIZ_MUTED()};")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        l.addWidget(sub)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{_WIZ_CARD_BORDER()};")
        l.addWidget(sep)

        tips = QLabel(
            "接下来您可以：\n\n"
            "  • 在主界面点击房间卡片办理入住与收银\n"
            "  • 若已配置 Telegram：客人可扫房间码使用机器人；未配置可在「设置」补全\n"
            "  • 在「设置」中调整主题、机器人、各岗位通知、商品与房价\n"
            "  • 在「价格」标签页中设置房型价格\n"
            "  • 在「会员」标签页中管理会员积分\n\n"
            "如需帮助，请联系系统技术支持。"
        )
        tips.setObjectName("Small")
        tips.setStyleSheet(f"color:{_WIZ_BODY()};")
        tips.setAlignment(Qt.AlignLeft)
        tips.setWordWrap(True)
        l.addWidget(tips)
        l.addStretch()


# ─────────────────────────────────────────────
# 主向导对话框
# ─────────────────────────────────────────────
class SetupWizard(QDialog):  # v7 视觉升级
    """多步骤初始化向导"""

    @classmethod
    def _resolve_lock_takeover_page(cls):
        """优先 LockTakeoverPage；非厂家账号返回占位页；不可用时回落 _LegacyLockPage。"""
        try:
            from vendor_gate import current_is_vendor
            if not current_is_vendor():
                return _VendorGatePlaceholder
        except Exception as e:
            logger.warning("[setup_wizard] vendor_gate check failed: %s", e)

        try:
            from lock_deploy.wizard_page import LockTakeoverPage
            return LockTakeoverPage
        except Exception as e:
            logger.warning("[setup_wizard] fallback to legacy lock page: %s", e)
            return _LegacyLockPage

    STEPS = [
        ("欢迎", None),
        ("酒店信息", _HotelInfoPage),
        ("老板账号", _BossAccountPage),
        # 接管门锁要放在房间录入之前 —— 接管成功后会用老库 RoomInfo / CardInfo
        # 反向回填房间和锁号；先建房再接管会产生空 lock_no 的脏数据。
        ("接管门锁", None),  # 运行时解析为 LockTakeoverPage 或 _LegacyLockPage
        ("商品录入", _ShopPage),
        ("房间录入", _BatchRoomPage),
        ("门卡登记", _RegistryCardsPage),
        ("Telegram", _TelegramPage),
        ("角色通知", _RolesPage),
        ("完成", _DonePage),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🏨 Solid — 初始化向导")
        style_dialog(self, size="medium")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._current = 0
        self._pages = []

        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)

        # ── 顶部进度条 ──
        header = QFrame()
        header.setStyleSheet(f"background:{_WIZ_HEADER_BG()};")
        header.setMinimumHeight(56)
        header.setMaximumHeight(90)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(24, 12, 24, 12)
        hl.setSpacing(6)

        self.lbl_step = QLabel("欢迎")
        self.lbl_step.setObjectName("H2Title")
        self.lbl_step.setStyleSheet(f"color:{_WIZ_HEADER_TEXT()};font-weight:bold;")
        hl.addWidget(self.lbl_step)

        self.progress = QProgressBar()
        self.progress.setRange(0, len(self.STEPS) - 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setStyleSheet(f"""
            QProgressBar {{ background:{_WIZ_PROGRESS_BG()}; border-radius:3px; }}
            QProgressBar::chunk {{ background:{_WIZ_PROGRESS_CHUNK()}; border-radius:3px; }}
        """)
        hl.addWidget(self.progress)
        l.addWidget(header)

        # ── 内容区域 ──
        self.stack = QStackedWidget()
        self.stack.setObjectName("SetupWizardStack")

        # 欢迎页
        self.stack.addWidget(_WelcomePage())
        self._pages.append(None)

        # 各步骤页
        for label, cls in self.STEPS[1:-1]:
            if cls is None:
                # 接管门锁那一格运行时才决定用 LockTakeoverPage 还是老的 _LegacyLockPage
                if label == "接管门锁":
                    cls = self._resolve_lock_takeover_page()
                else:
                    cls = _LegacyLockPage  # 兜底，理论上不会发生
            page = cls()
            self.stack.addWidget(page)
            self._pages.append(page)

        # 完成页
        self._done_page = _DonePage()
        self.stack.addWidget(self._done_page)
        self._pages.append(None)

        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            if w:
                w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        # 内容区可滚动：避免 QStackedWidget 按最高页撑破屏幕、挡住底部按钮
        self._content_scroll = make_dialog_scroll_area(self.stack)
        l.addWidget(self._content_scroll, 1)

        # ── 底部按钮（始终贴在窗口底，不随内容滚走）──
        footer = QFrame()
        footer.setObjectName("SetupWizardFooter")
        footer.setStyleSheet(
            f"#SetupWizardFooter {{ background:{_WIZ_BG()}; border-top:1px solid {_WIZ_CARD_BORDER()}; }}"
        )
        footer.setMinimumHeight(44)
        footer.setMaximumHeight(70)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(24, 12, 24, 12)

        self.btn_skip = QPushButton("稍后补全")
        self.btn_skip.setObjectName("FdGhostBtn")
        self.btn_skip.setToolTip("跳过当前步骤：本页未保存的填写项可稍后在「设置」中补全。")
        self.btn_skip.clicked.connect(self._skip)
        fl.addWidget(self.btn_skip)
        fl.addStretch()

        # 步骤点阵指示器
        self._step_dots = QWidget()
        self._step_dots.setObjectName("StepDots")
        sd_lay = QHBoxLayout(self._step_dots)
        sd_lay.setContentsMargins(0, 0, 0, 0)
        sd_lay.setSpacing(6)
        self._step_dot_labels = []
        for i in range(len(self.STEPS)):
            dot = QLabel("●")
            dot.setObjectName("StepDot")
            dot.setFixedSize(12, 16)
            dot.setAlignment(Qt.AlignCenter)
            dot.setCursor(Qt.PointingHandCursor)
            dot.mousePressEvent = lambda _e, idx=i: self._jump_to(idx)
            sd_lay.addWidget(dot)
            self._step_dot_labels.append(dot)
        self._step_label = QLabel("")
        self._step_label.setObjectName("StepCountLabel")
        self._step_label.setObjectName("Tiny")
        self._step_label.setStyleSheet(f"color:{_WIZ_MUTED()};")
        sd_lay.addWidget(self._step_label)
        fl.addWidget(self._step_dots)

        self.btn_back = QPushButton("← 上一步")
        self.btn_back.setObjectName("FdGhostBtn")
        self.btn_back.setEnabled(False)
        fd_apply_action_btn(self.btn_back)
        self.btn_back.clicked.connect(self._go_back)

        self.btn_next = QPushButton("下一步 →")
        self.btn_next.setObjectName("SolidPrimaryBtn")
        fd_apply_action_btn(self.btn_next, primary=True)
        self.btn_next.clicked.connect(self._go_next)

        fl.addWidget(self.btn_back)
        fl.addWidget(self.btn_next)
        l.addWidget(footer)

        self._update_ui()

    def _update_ui(self):
        idx = self._current
        total = len(self.STEPS)
        label = self.STEPS[idx][0]

        self.lbl_step.setText(
            f"步骤 {idx}/{total - 1}：{label}" if idx > 0 else f"欢迎使用 {APP_NAME_FULL}"
        )
        self.progress.setValue(idx)
        # 步骤点阵更新
        for i, dot in enumerate(self._step_dot_labels):
            if i < idx:
                dot.setObjectName("Tiny")
                dot.setStyleSheet(f"color:{_WIZ_SUCCESS()};font-weight:bold;")
                dot.setToolTip(f"{self.STEPS[i][0]} ✓")
            elif i == idx:
                dot.setObjectName("Small")
                dot.setStyleSheet(f"color:{_WIZ_PRIMARY()};font-weight:bold;")
                dot.setToolTip(f"← 当前: {self.STEPS[i][0]}")
            elif i == total - 1:
                dot.setObjectName("Tiny")
                dot.setStyleSheet(f"color:{_WIZ_MUTED()};")
                dot.setToolTip("完成")
            else:
                dot.setObjectName("Tiny")
                dot.setStyleSheet(f"color:{_WIZ_CARD_BORDER()};")
                dot.setToolTip(self.STEPS[i][0])
        step_total = total - 1
        self._step_label.setText(f"{idx}/{step_total}" if idx > 0 else "")

        self.stack.setCurrentIndex(idx)
        self._content_scroll.verticalScrollBar().setValue(0)
        self.btn_back.setEnabled(idx > 0)
        # 第 ① 步酒店信息必填，不提供跳过
        can_skip = 0 < idx < total - 1 and idx != 1
        self.btn_skip.setVisible(can_skip)

        if idx == total - 1:
            self.btn_next.setText("✅ 开始使用")
            self.btn_skip.setVisible(False)
        elif idx == 0:
            self.btn_next.setText("开始配置 →")
        else:
            self.btn_next.setText("下一步 →")

    def _go_next(self):
        idx = self._current
        total = len(self.STEPS)

        if idx == total - 1:
            db.set_config("setup_done", "1")
            self.accept()
            return

        page = self._pages[idx]
        if page and hasattr(page, "save"):
            if not page.save():
                return

        self._current += 1

        # 保存成功 toast（仅非最后一步）
        if self._current < total - 1:
            try:
                from ui.components.toast import ToastManager, ToastType
                ToastManager.instance().show("已保存", ToastType.SUCCESS)
            except ImportError:
                pass

        if self._current == total - 1:
            self.stack.removeWidget(self._done_page)
            self._done_page = _DonePage()
            self.stack.insertWidget(total - 1, self._done_page)

        self._update_ui()

    def _go_back(self):
        if self._current > 0:
            self._current -= 1
            self._update_ui()

    def _skip(self):
        total = len(self.STEPS)
        if self._current < total - 1:
            self._current += 1
            self._update_ui()

    def _jump_to(self, idx: int) -> None:
        """点步骤点阵跳转（仅允许跳到已完成的步骤或欢迎页）。"""
        if idx == 0 or idx <= self._current:
            self._current = idx
            self._update_ui()

    def closeEvent(self, event):
        total = len(self.STEPS)
        if self._current < total - 1:
            if ask_confirm(
                self, "结束初始化向导",
                "尚未完成全部步骤。确定要关闭向导并进入主界面吗？\n"
                "（当前页未点「下一步」的修改不会保存；之后可在「设置」中补全。）",
            ):
                db.set_config("setup_done", "1")
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
