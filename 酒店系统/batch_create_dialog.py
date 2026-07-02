"""批量创建房间对话框。

锁号是物理真实值。它们可能来自老系统的门锁数据库、手动输入，或暂时为空。
本模块不会猜测或自动递增锁号。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from database import db
from event_bus import bus
from i18n import i18n
from ui_helpers import build_dialog_header, show_info, show_warning, style_dialog
from ui_surface import fd_apply_table_palette
from lock_adapters.prousb_v9 import ProUsbV9Adapter


def _clean_hex(raw: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", str(raw or "")).upper()


def _valid_lock_or_empty(raw: str) -> tuple[bool, str]:
    text = str(raw or "").strip()
    if not text:
        return True, ""
    cleaned = _clean_hex(text)
    return len(cleaned) == 8 and cleaned == text.upper(), cleaned


def _room_types() -> list[tuple[str, str]]:
    try:
        rows = db.execute("SELECT type_id, type_name FROM room_type_templates").fetchall()
    except Exception:
        rows = []
    if rows:
        return [(str(tname or tid), str(tid)) for tid, tname in rows]
    return [
        (i18n.t("batch_create.room_type_king"), "大床房"),
        (i18n.t("batch_create.room_type_twin"), "双床房"),
        (i18n.t("batch_create.room_type_suite"), "套房"),
        (i18n.t("batch_create.room_type_standard"), "标准间"),
    ]


def _fill_type_combo(combo: QComboBox) -> None:
    for label, value in _room_types():
        combo.addItem(label, value)


def _text_item(text: str = "", editable: bool = True) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text or ""))
    if not editable:
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _room_exists(room_id: str) -> bool:
    try:
        return db.execute("SELECT 1 FROM rooms WHERE room_id=?", (room_id,)).fetchone() is not None
    except Exception:
        return False


def _lock_owner(lock_no: str, exclude_room: str = "") -> str:
    if not lock_no:
        return ""
    try:
        row = db.execute(
            "SELECT room_id FROM rooms WHERE lock_no=? AND room_id<>?",
            (lock_no, exclude_room),
        ).fetchone()
    except Exception:
        return ""
    return str(row[0]) if row else ""


def _candidate_mdb_paths() -> list[Path]:
    try:
        from lock_deploy.importer import load_takeover_config

        cfg = load_takeover_config() or {}
    except Exception:
        cfg = {}
    out: list[Path] = []
    for key in ("lock_takeover_live_mdb_path", "lock_takeover_mdb_path"):
        raw = cfg.get(key) or ""
        if raw:
            out.append(Path(raw))
    install = cfg.get("lock_takeover_install_dir") or ""
    if install:
        out.append(Path(install) / "CardLock.mdb")
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        s = str(p)
        if s not in seen:
            seen.add(s)
            uniq.append(p)
    return uniq


def _fetch_roominfo_rows(mdb_path: Path) -> list[dict[str, Any]]:
    from mdb_import_backend import open_mdb_via_sqlite_cache

    conn, _msg = open_mdb_via_sqlite_cache(str(mdb_path))
    if conn is None:
        return []
    try:
        rows, cols = conn.fetch_table("RoomInfo")
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(cols, row))
        room_no = str(item.get("RoomNo") or "").strip()
        if not room_no:
            continue
        item["LockNo"] = ProUsbV9Adapter.lock_no_from_roominfo_row(item)
        out.append(item)
    return sorted(out, key=lambda r: str(r.get("RoomNo") or ""))


def _import_ckcard_registry(mdb_path: Path) -> int:
    from mdb_import_backend import open_mdb_via_sqlite_cache

    conn, _msg = open_mdb_via_sqlite_cache(str(mdb_path))
    if conn is None:
        return 0
    try:
        rows, cols = conn.fetch_table("CKCard")
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass
    count = 0
    for raw in rows:
        item = dict(zip(cols, raw))
        uid = str(item.get("CardUID") or "").strip().upper()
        data = str(item.get("CardData") or "").strip().upper()
        if not uid:
            continue
        try:
            db.execute(
                "INSERT OR REPLACE INTO blank_card_registry (card_uid, card_data, source, note) VALUES (?, ?, 'legacy_ckcard', ?)",
                (uid, data, str(mdb_path)),
            )
        except Exception:
            pass
        count += 1
    return count


class ImportRoomsFromMdbDialog(QDialog):
    """将老系统房间信息导入到新系统房间。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("batch_create.import_win_title"))
        style_dialog(self, size="large")
        self._mdb_path: Path | None = None
        self._rows: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(
            build_dialog_header(
                i18n.t("batch_create.import_header_title"),
                i18n.t("batch_create.import_header_sub"),
            )
        )

        top = QHBoxLayout()
        self.lbl_source = QLabel(i18n.t("batch_create.import_source_label"))
        self.lbl_source.setWordWrap(True)
        top.addWidget(self.lbl_source, 1)
        btn_reload = QPushButton(i18n.t("batch_create.import_btn_reload"))
        btn_reload.setObjectName("FdGhostBtn")
        btn_reload.clicked.connect(self._load_rows)
        top.addWidget(btn_reload)
        layout.addLayout(top)

        opt = QHBoxLayout()
        self.type_combo = QComboBox()
        _fill_type_combo(self.type_combo)
        opt.addWidget(QLabel(i18n.t("batch_create.import_default_type_label")))
        opt.addWidget(self.type_combo)
        self.chk_overwrite = QCheckBox(i18n.t("batch_create.import_chk_overwrite"))
        self.chk_overwrite.setChecked(True)
        self.chk_overwrite.stateChanged.connect(self._refresh_statuses)
        opt.addWidget(self.chk_overwrite)
        opt.addStretch()
        layout.addLayout(opt)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            i18n.t("batch_create.import_col_import"),
            i18n.t("batch_create.import_col_room"),
            i18n.t("batch_create.import_col_building"),
            i18n.t("batch_create.import_col_floor"),
            i18n.t("batch_create.import_col_romid"),
            i18n.t("batch_create.import_col_lock"),
            i18n.t("batch_create.import_col_status"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        fd_apply_table_palette(self.table)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        btn_all = QPushButton(i18n.t("batch_create.import_btn_all"))
        btn_all.setObjectName("FdGhostBtn")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none = QPushButton(i18n.t("batch_create.import_btn_none"))
        btn_none.setObjectName("FdGhostBtn")
        btn_none.clicked.connect(lambda: self._set_all(False))
        buttons.addWidget(btn_all)
        buttons.addWidget(btn_none)
        buttons.addStretch()
        btn_cancel = QPushButton(i18n.t("batch_create.import_btn_cancel"))
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)
        btn_import = QPushButton(i18n.t("batch_create.import_btn_import"))
        btn_import.setObjectName("SolidPrimaryBtn")
        btn_import.clicked.connect(self._import_selected)
        buttons.addWidget(btn_import)
        layout.addLayout(buttons)

        self._load_rows()

    def _load_rows(self) -> None:
        paths = [p for p in _candidate_mdb_paths() if p.is_file()]
        if not paths:
            self.lbl_source.setText(i18n.t("batch_create.import_no_mdb"))
            self.table.setRowCount(0)
            return
        self._mdb_path = paths[0]
        self.lbl_source.setText(i18n.t("batch_create.import_source_fmt").format(path=self._mdb_path))
        try:
            self._rows = _fetch_roominfo_rows(self._mdb_path)
        except Exception as exc:
            self._rows = []
            show_warning(self, i18n.t("batch_create.import_read_fail_title"), i18n.t("batch_create.import_read_fail_body").format(exc=exc))
        self._populate()

    def _populate(self) -> None:
        self.table.setRowCount(0)
        for row in self._rows:
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked)
            self.table.setItem(idx, 0, check)
            self.table.setItem(idx, 1, _text_item(row.get("RoomNo", ""), False))
            self.table.setItem(idx, 2, _text_item(row.get("BldNo", ""), False))
            self.table.setItem(idx, 3, _text_item(row.get("FlrNo", ""), False))
            self.table.setItem(idx, 4, _text_item(row.get("RomID", ""), False))
            self.table.setItem(idx, 5, _text_item(row.get("LockNo", ""), False))
            self.table.setItem(idx, 6, _text_item("", False))
        self._refresh_statuses()

    def _refresh_statuses(self) -> None:
        overwrite = self.chk_overwrite.isChecked()
        for r in range(self.table.rowCount()):
            room_id = self.table.item(r, 1).text().strip()
            exists = _room_exists(room_id)
            status = i18n.t("batch_create.import_status_new")
            if exists:
                status = i18n.t("batch_create.import_status_overwrite") if overwrite else i18n.t("batch_create.import_status_skip")
            self.table.item(r, 6).setText(status)
            self.table.item(r, 0).setCheckState(Qt.Checked if (not exists or overwrite) else Qt.Unchecked)

    def _set_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(state)

    def _selected_rows(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).checkState() != Qt.Checked:
                continue
            # 收集老系统额外列，保存为扩展属性
            extra = {}
            if r < len(self._rows):
                raw = self._rows[r]
                mapped_cols = {"RoomNo", "BldNo", "FlrNo", "RomID", "LockNo", "Dai", "Price", "MaxCards"}
                for col, val in raw.items():
                    col_s = str(col).strip()
                    if col_s in mapped_cols or not val or str(val).strip() == "":
                        continue
                    extra[col_s] = str(val).strip()
            rows.append({
                "room_id": self.table.item(r, 1).text().strip(),
                "building": self.table.item(r, 2).text().strip(),
                "floor": self.table.item(r, 3).text().strip(),
                "rom_id": self.table.item(r, 4).text().strip(),
                "lock_no": self.table.item(r, 5).text().strip(),
                "dai": str(self._rows[r].get("Dai") or "0") if r < len(self._rows) else "0",
                "price": str(self._rows[r].get("Price") or "") if r < len(self._rows) else "",
                "max_cards": str(self._rows[r].get("MaxCards") or "100") if r < len(self._rows) else "100",
                "_extra_props": json.dumps(extra),
            })
        return rows

    def _import_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            show_warning(self, i18n.t("batch_create.import_no_selection_title"), i18n.t("batch_create.import_no_selection_body"))
            return
        seen_locks: set[str] = set()
        for row in rows:
            lock_no = row["lock_no"]
            if lock_no in seen_locks:
                show_warning(self, i18n.t("batch_create.import_lock_dup_title"), i18n.t("batch_create.import_lock_dup_body").format(lock=lock_no))
                return
            owner = _lock_owner(lock_no, row["room_id"])
            if owner:
                show_warning(self, i18n.t("batch_create.import_lock_conflict_title"), i18n.t("batch_create.import_lock_conflict_body").format(lock=lock_no, room=owner))
                return
            seen_locks.add(lock_no)
        room_type = str(self.type_combo.currentData() or self.type_combo.currentText()).strip()
        overwrite = self.chk_overwrite.isChecked()

        def work(conn):
            for row in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO buildings (building_id, bld_no, name, sort_order) VALUES (?, ?, ?, ?)",
                    (row["building"] or "1", int(row["building"] or 1), f"{int(row['building'] or 1):02d}", int(row["building"] or 1)),
                )
                exists = conn.execute("SELECT 1 FROM rooms WHERE room_id=?", (row["room_id"],)).fetchone()
                if exists:
                    if not overwrite:
                        skipped += 1
                        continue
                    conn.execute(
                        "UPDATE rooms SET building=?, floor=?, lock_no=?, bld_no=?, flr_no=?, rom_id=?, dai=?, max_cards=?, rate_override=?, extra_props=? WHERE room_id=?",
                        (
                            row["building"], row["floor"], row["lock_no"],
                            int(row["building"] or 1), int(row["floor"] or 0), int(row["rom_id"] or 0),
                            int(float(row["dai"] or 0)), int(float(row["max_cards"] or 100)),
                            float(row["price"]) if row["price"] else None,
                            row.get("_extra_props", "{}"),
                            row["room_id"],
                        ),
                    )
                    updated += 1
                    continue
                conn.execute(
                    "INSERT INTO rooms (room_id, floor, room_type, status, building, lock_no, bld_no, flr_no, rom_id, dai, max_cards, rate_override, extra_props) "
                    "VALUES (?, ?, ?, 'VC', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["room_id"], row["floor"], room_type, row["building"], row["lock_no"],
                        int(row["building"] or 1), int(row["floor"] or 0), int(row["rom_id"] or 0),
                        int(float(row["dai"] or 0)), int(float(row["max_cards"] or 100)),
                        float(row["price"]) if row["price"] else None,
                        row.get("_extra_props", "{}"),
                    ),
                )
                created += 1
            return created, updated, skipped

        try:
            created, updated, skipped = db.run_transaction(work)
            ck_count = _import_ckcard_registry(self._mdb_path) if self._mdb_path else 0
        except Exception as exc:
            show_warning(self, i18n.t("batch_create.import_fail_title"), str(exc))
            return
        show_info(self, i18n.t("batch_create.import_success_title"), i18n.t("batch_create.import_success_body").format(created=created, updated=updated, skipped=skipped, ck_count=ck_count))
        bus.room_status_changed.emit("__import__", "READY")
        self.accept()


class BatchCreateRoomDialog(QDialog):
    """批量创建房间。锁号为可选手动输入值。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("batch_create.create_win_title"))
        style_dialog(self, size="large")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(
            build_dialog_header(
                i18n.t("batch_create.create_header_title"),
                i18n.t("batch_create.create_header_sub"),
            )
        )

        form = QFormLayout()
        self.building_input = QLineEdit()
        self.building_input.setPlaceholderText(i18n.t("batch_create.building_ph"))
        self.floor_input = QLineEdit()
        self.floor_input.setPlaceholderText(i18n.t("batch_create.floor_ph"))
        self.type_combo = QComboBox()
        _fill_type_combo(self.type_combo)
        form.addRow(i18n.t("batch_create.building_label"), self.building_input)
        form.addRow(i18n.t("batch_create.floor_label"), self.floor_input)
        form.addRow(i18n.t("batch_create.type_label"), self.type_combo)
        layout.addLayout(form)

        gen = QHBoxLayout()
        self.start_input = QLineEdit()
        self.start_input.setPlaceholderText(i18n.t("batch_create.start_ph"))
        self.end_input = QLineEdit()
        self.end_input.setPlaceholderText(i18n.t("batch_create.end_ph"))
        btn_fill = QPushButton(i18n.t("batch_create.btn_generate"))
        btn_fill.setObjectName("FdGhostBtn")
        btn_fill.clicked.connect(self._fill_rooms)
        gen.addWidget(QLabel(i18n.t("batch_create.range_label")))
        gen.addWidget(self.start_input)
        gen.addWidget(self.end_input)
        gen.addWidget(btn_fill)
        layout.addLayout(gen)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([i18n.t("batch_create.col_room"), i18n.t("batch_create.col_lock")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        fd_apply_table_palette(self.table)
        self.table.setAlternatingRowColors(False)
        layout.addWidget(self.table, 1)

        row_buttons = QHBoxLayout()
        btn_add = QPushButton(i18n.t("batch_create.btn_add_row"))
        btn_add.setObjectName("FdGhostBtn")
        btn_add.clicked.connect(lambda: self._add_row("", ""))
        btn_del = QPushButton(i18n.t("batch_create.btn_delete_row"))
        # 删除是危险操作，不设样式名称
        btn_del.clicked.connect(self._delete_selected)
        row_buttons.addWidget(btn_add)
        row_buttons.addWidget(btn_del)
        row_buttons.addStretch()
        layout.addLayout(row_buttons)

        buttons = QHBoxLayout()
        buttons.addStretch()
        btn_cancel = QPushButton(i18n.t("batch_create.btn_cancel"))
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)
        btn_save = QPushButton(i18n.t("batch_create.btn_save_all"))
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.clicked.connect(self._save)
        buttons.addWidget(btn_save)
        layout.addLayout(buttons)

    def _add_row(self, room_id: str, lock_no: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, _text_item(room_id))
        self.table.setItem(r, 1, _text_item(lock_no))

    def _fill_rooms(self) -> None:
        start = self.start_input.text().strip()
        end = self.end_input.text().strip()
        if not start.isdigit() or not end.isdigit():
            show_warning(self, i18n.t("batch_create.err_format_title"), i18n.t("batch_create.err_format_body"))
            return
        s, e = int(start), int(end)
        if s > e:
            show_warning(self, i18n.t("batch_create.err_range_title"), i18n.t("batch_create.err_range_body"))
            return
        width = max(len(start), len(end))
        self.table.setRowCount(0)
        for n in range(s, e + 1):
            self._add_row(str(n).zfill(width), "")

    def _delete_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _collect_rows(self) -> list[tuple[str, str]] | None:
        rows: list[tuple[str, str]] = []
        seen_rooms: set[str] = set()
        seen_locks: set[str] = set()
        for r in range(self.table.rowCount()):
            room_id = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            lock_raw = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            ok, lock_no = _valid_lock_or_empty(lock_raw)
            if not room_id:
                show_warning(self, i18n.t("batch_create.err_room_empty_title"), i18n.t("batch_create.err_room_empty_body").format(row=r + 1))
                return None
            if room_id in seen_rooms:
                show_warning(self, i18n.t("batch_create.err_room_dup_title"), i18n.t("batch_create.err_room_dup_body").format(room=room_id))
                return None
            if not ok:
                show_warning(self, i18n.t("batch_create.err_lock_invalid_title"), i18n.t("batch_create.err_lock_invalid_body").format(room=room_id))
                return None
            if lock_no:
                if lock_no in seen_locks:
                    show_warning(self, i18n.t("batch_create.err_lock_dup_title"), i18n.t("batch_create.err_lock_dup_body").format(lock=lock_no))
                    return None
                owner = _lock_owner(lock_no)
                if owner:
                    show_warning(self, i18n.t("batch_create.err_lock_conflict_title"), i18n.t("batch_create.err_lock_conflict_body").format(lock=lock_no, room=owner))
                    return None
                seen_locks.add(lock_no)
            if _room_exists(room_id):
                show_warning(self, i18n.t("batch_create.err_room_exists_title"), i18n.t("batch_create.err_room_exists_body").format(room=room_id))
                return None
            seen_rooms.add(room_id)
            rows.append((room_id, lock_no))
        if not rows:
            show_warning(self, i18n.t("batch_create.err_no_rooms_title"), i18n.t("batch_create.err_no_rooms_body"))
            return None
        return rows

    def _save(self) -> None:
        rows = self._collect_rows()
        if rows is None:
            return
        building = self.building_input.text().strip() or "A"
        floor = self.floor_input.text().strip() or ""
        room_type = str(self.type_combo.currentData() or self.type_combo.currentText()).strip()

        def work(conn):
            for room_id, lock_no in rows:
                conn.execute(
                    "INSERT INTO rooms (room_id, floor, room_type, status, building, lock_no) "
                    "VALUES (?, ?, ?, 'READY', ?, ?)",
                    (room_id, floor, room_type, building, lock_no),
                )
            return len(rows)

        try:
            count = db.run_transaction(work)
        except Exception as exc:
            show_warning(self, i18n.t("batch_create.save_fail_title"), str(exc))
            return
        show_info(self, i18n.t("batch_create.save_success_title"), i18n.t("batch_create.save_success_body").format(count=count))
        bus.room_status_changed.emit("__batch__", "READY")
        self.accept()


class CreateSingleRoomDialog(QDialog):
    """创建单个房间，可手动输入锁号。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("batch_create.single_win_title"))
        style_dialog(self, size="small")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(
            build_dialog_header(
                i18n.t("batch_create.single_header_title"),
                i18n.t("batch_create.single_header_sub"),
            )
        )

        form = QFormLayout()
        self.room_input = QLineEdit()
        self.room_input.setPlaceholderText(i18n.t("batch_create.room_ph"))
        self.building_input = QLineEdit()
        self.building_input.setPlaceholderText(i18n.t("batch_create.building_ph"))
        self.floor_input = QLineEdit()
        self.floor_input.setPlaceholderText(i18n.t("batch_create.floor_ph"))
        self.type_combo = QComboBox()
        _fill_type_combo(self.type_combo)
        self.lock_input = QLineEdit()
        self.lock_input.setPlaceholderText(i18n.t("batch_create.lock_ph"))
        self.lock_input.setMaxLength(8)
        form.addRow(i18n.t("batch_create.room_label"), self.room_input)
        form.addRow(i18n.t("batch_create.building_label"), self.building_input)
        form.addRow(i18n.t("batch_create.floor_label"), self.floor_input)
        form.addRow(i18n.t("batch_create.type_label"), self.type_combo)
        form.addRow(i18n.t("batch_create.lock_label"), self.lock_input)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch()
        btn_cancel = QPushButton(i18n.t("batch_create.btn_cancel"))
        btn_cancel.setObjectName("FdGhostBtn")
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)
        btn_save = QPushButton(i18n.t("batch_create.btn_save"))
        btn_save.setObjectName("SolidPrimaryBtn")
        btn_save.clicked.connect(self._save)
        buttons.addWidget(btn_save)
        layout.addLayout(buttons)

    def _save(self) -> None:
        room_id = self.room_input.text().strip()
        if not room_id:
            show_warning(self, i18n.t("batch_create.err_incomplete_title"), i18n.t("batch_create.err_incomplete_body"))
            return
        if _room_exists(room_id):
            show_warning(self, i18n.t("batch_create.err_dup_title"), i18n.t("batch_create.err_dup_body").format(room=room_id))
            return
        ok, lock_no = _valid_lock_or_empty(self.lock_input.text())
        if not ok:
            show_warning(self, i18n.t("batch_create.err_lock_title"), i18n.t("batch_create.err_lock_body"))
            return
        if lock_no:
            owner = _lock_owner(lock_no)
            if owner:
                show_warning(self, i18n.t("batch_create.err_lock_conflict_title"), i18n.t("batch_create.err_lock_conflict_body").format(lock=lock_no, room=owner))
                return
        building = self.building_input.text().strip() or "A"
        floor = self.floor_input.text().strip() or ""
        room_type = str(self.type_combo.currentData() or self.type_combo.currentText()).strip()
        try:
            db.execute(
                "INSERT INTO rooms (room_id, floor, room_type, status, building, lock_no) "
                "VALUES (?, ?, ?, 'READY', ?, ?)",
                (room_id, floor, room_type, building, lock_no),
            )
        except Exception as exc:
            show_warning(self, i18n.t("batch_create.single_save_fail_title"), str(exc))
            return
        show_info(self, i18n.t("batch_create.single_save_success_title"), i18n.t("batch_create.single_save_success_body").format(room=room_id))
        bus.room_status_changed.emit("__single__", "READY")
        self.accept()
