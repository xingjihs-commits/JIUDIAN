"""
legacy_migration_guide.py — 老系统或门锁迁移全套现场话术

门锁一步到五步、整合台、USB 迁移、一键接管、分步向导、发卡嗅探 共用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


# ─── 嗅探（放什么卡/老系统点什么/已读取/换下一张）────────────────────────

@dataclass(frozen=True)
class SniffCardRound:
    key: str
    title: str
    card_what: str
    place_hint: str
    old_system_steps: str
    after_read_hint: str
    optional: bool = False


DEFAULT_SNIFF_ROUNDS: List[SniffCardRound] = [
    SniffCardRound(
        key="guest",
        title="第 1 张 · 客人房卡（必做）",
        card_what="空白 M1 白卡（IC 卡），或酒店日常发给客人的那种房卡",
        place_hint=(
            "① 请拿一张【空白白卡】，芯片朝上，平放在读卡器发卡座上。\n"
            "   （不要用总卡、不要用已开过门的旧房卡。）"
        ),
        old_system_steps=(
            "② 请切换到【旧系统门锁系统】发卡界面：\n"
            "   · 房号填：888（测试房，没有就填任意一间空房）\n"
            "   · 有效期按平时习惯即可\n"
            "   · 点击【写卡】或【发卡】或【制卡】（您店里按钮叫什么就点哪个）\n"
            "   · 听到读卡器「滴」一声表示旧系统已写卡"
        ),
        after_read_hint="这张是【客人房卡】密钥，系统以后给客人入住写卡、取电器会用到。",
        optional=False,
    ),
    SniffCardRound(
        key="master",
        title="第 2 张 · 总卡 / 大楼卡（建议做）",
        card_what="酒店的总卡、万能卡，或厂家提供的「总卡」样品（通常只有 1～2 张）",
        place_hint=(
            "① 请取下上一张测试卡。\n"
            "② 换一张【总卡】放在读卡器上（若酒店没有总卡可点「跳过本张」）。"
        ),
        old_system_steps=(
            "② 在旧系统里选择【总卡 / 大楼卡 / 管理卡】发卡：\n"
            "   · 仍点【写卡】或【发卡】\n"
            "   · 不要选房号，或选「总卡」类型（按旧系统界面为准）"
        ),
        after_read_hint="总卡用于开门调试、厂家授权；系统会单独登记，不会当客人卡发出。",
        optional=True,
    ),
    SniffCardRound(
        key="auth",
        title="第 3 张 · 授权卡 / 楼层卡（可选）",
        card_what="授权卡、楼层卡、清洁卡等（按酒店实际有的选做，没有可跳过）",
        place_hint=(
            "① 取下上一张卡。\n"
            "② 换【授权卡】或【楼层卡】放上读卡器（没有此类卡可跳过）。"
        ),
        old_system_steps=(
            "② 旧系统里按平时发这类卡的方式操作：\n"
            "   · 选对应卡类型（授权 / 楼层 / 清洁）\n"
            "   · 点【写卡】或【发卡】"
        ),
        after_read_hint="用于员工通道；若酒店不用可跳过，不影响客人入住。",
        optional=True,
    ),
]


PHASE_PLACE = "place"
PHASE_OLD_SYSTEM = "old_system"
PHASE_LISTEN = "listen"
PHASE_READ_OK = "read_ok"
PHASE_DONE_ALL = "done_all"


class SniffGuideSession:
    """发卡嗅探：一张一张操作。"""

    def __init__(self, rounds: Optional[List[SniffCardRound]] = None):
        self.rounds = list(rounds or DEFAULT_SNIFF_ROUNDS)
        self.round_index = 0
        self.phase = PHASE_PLACE
        self._captured_this_round = False

    @property
    def current_round(self) -> SniffCardRound:
        i = min(self.round_index, len(self.rounds) - 1)
        return self.rounds[i]

    def progress_label(self) -> str:
        r = self.current_round
        opt = "（可跳过）" if r.optional else "（必做）"
        return f"嗅探进度：{self.round_index + 1} / {len(self.rounds)}  ·  {r.title} {opt}"

    def banner_text(self) -> str:
        if self.phase == PHASE_DONE_ALL:
            return (
                "✅ 全部卡已指引完成。\n"
                "请在下方表格勾选捕获记录 →【保存选中密钥到系统】→ 回「前台门锁对接」点 ⑤ 验收。"
            )
        r = self.current_round
        if self.phase == PHASE_PLACE:
            return (
                f"👉 {r.title}\n\n"
                f"【放什么卡】{r.card_what}\n\n"
                f"{r.place_hint}\n\n"
                "卡放好后，点：【卡已放好 → 我去老系统写卡】"
            )
        if self.phase == PHASE_OLD_SYSTEM:
            return (
                f"👉 {r.title} — 请在老系统操作\n\n"
                f"{r.old_system_steps}\n\n"
                "老系统写卡完成后，点：【我已在老系统点完写卡 → 开始监听】"
            )
        if self.phase == PHASE_LISTEN:
            return (
                f"👉 {r.title} — 正在监听\n\n"
                "③ 读卡器仍连着本机；刚才那张卡先不要拿走。\n"
                "④ 下方表格出现绿色「密钥A」= 【已读取】→ 点【已读取，我取下这张卡】"
            )
        if self.phase == PHASE_READ_OK:
            return (
                f"✅ 已读取 — {r.title}\n\n"
                f"{r.after_read_hint}\n\n"
                "请【取下这张卡】，再点：【已取下 → 换下一张卡】"
            )
        return ""

    def primary_button_label(self) -> str:
        if self.phase == PHASE_DONE_ALL:
            return ""
        if self.phase == PHASE_PLACE:
            return "卡已放好 → 我去老系统写卡"
        if self.phase == PHASE_OLD_SYSTEM:
            return "我已在老系统点完写卡 → 开始监听"
        if self.phase == PHASE_LISTEN:
            return "已读取，我取下这张卡"
        if self.phase == PHASE_READ_OK:
            if self.round_index + 1 < len(self.rounds):
                return "已取下 → 换下一张卡"
            return "已取下 → 全部完成"
        return "下一步"

    def secondary_button_label(self) -> Optional[str]:
        r = self.current_round
        if self.phase in (PHASE_PLACE, PHASE_OLD_SYSTEM, PHASE_LISTEN) and r.optional:
            return "跳过本张（酒店没有这种卡）"
        if self.phase == PHASE_LISTEN:
            return "老系统没反应，重新写卡"
        return None

    def advance_primary(self) -> Tuple[str, bool]:
        start_sniff = False
        if self.phase == PHASE_PLACE:
            self.phase = PHASE_OLD_SYSTEM
        elif self.phase == PHASE_OLD_SYSTEM:
            self.phase = PHASE_LISTEN
            self._captured_this_round = False
            start_sniff = True
        elif self.phase == PHASE_LISTEN:
            if not self._captured_this_round:
                return "请等待下方表格出现绿色密钥行，或确认老系统已点写卡。", False
            self.phase = PHASE_READ_OK
        elif self.phase == PHASE_READ_OK:
            self.round_index += 1
            if self.round_index >= len(self.rounds):
                self.phase = PHASE_DONE_ALL
            else:
                self.phase = PHASE_PLACE
                self._captured_this_round = False
        return "", start_sniff

    def skip_optional_round(self) -> None:
        r = self.current_round
        if not r.optional:
            return
        self.round_index += 1
        if self.round_index >= len(self.rounds):
            self.phase = PHASE_DONE_ALL
        else:
            self.phase = PHASE_PLACE
            self._captured_this_round = False

    def retry_listen(self) -> bool:
        if self.phase == PHASE_LISTEN:
            self.phase = PHASE_OLD_SYSTEM
            self._captured_this_round = False
            return True
        return False

    def on_packet_captured(self, has_key: bool) -> None:
        if self.phase == PHASE_LISTEN and has_key:
            self._captured_this_round = True

    def listen_button_enabled(self) -> bool:
        if self.phase == PHASE_LISTEN:
            return self._captured_this_round
        return self.phase != PHASE_DONE_ALL


def cardlock_sniff_intro() -> str:
    return (
        "④ 发卡嗅探 — 按窗口黄框一步一步：\n"
        "   放哪张卡 → 老系统点写卡 → 已读取 → 换下一张。"
    )


# ─── 通用分步指引（非嗅探窗）──────────────────────────────────────────────────

class GuideAction:
    NONE = ""
    EXECUTE_PREFLIGHT = "execute_preflight"
    EXECUTE_IMPORT = "execute_import"
    OPEN_USB = "open_usb"
    OPEN_SNIFF = "open_sniff"
    EXECUTE_VERIFY = "execute_verify"
    OPEN_FRONTDESK = "open_frontdesk"
    START_ONECLICK = "start_oneclick"
    BROWSE_DIR = "browse_dir"
    START_USB_SCAN = "start_usb_scan"
    RETRY = "retry"
    IMPORT_CSV = "import_csv"
    EXPORT_SGBAK = "export_sgbak"
    IMPORT_SGBAK = "import_sgbak"
    EXPORT_SGKEY = "export_sgkey"
    IMPORT_SGKEY = "import_sgkey"
    OPEN_LEGACY_WIZARD = "open_legacy_wizard"


@dataclass
class _Phase:
    banner: str
    primary: str
    secondary: Optional[str] = None
    action: str = GuideAction.NONE


class PhaseGuideSession:
    """多段文字 + 最后一步触发动作。"""

    def __init__(self, progress_title: str, phases: List[_Phase]):
        self._progress_title = progress_title
        self._phases = phases
        self._index = 0

    def progress_label(self) -> str:
        n = len(self._phases)
        return f"{self._progress_title} — 小步 {min(self._index + 1, n)} / {n}"

    def banner_text(self) -> str:
        if self._index >= len(self._phases):
            return "本步骤指引已完成。"
        return self._phases[self._index].banner

    def primary_button_label(self) -> str:
        if self._index >= len(self._phases):
            return ""
        return self._phases[self._index].primary

    def secondary_button_label(self) -> Optional[str]:
        if self._index >= len(self._phases):
            return None
        return self._phases[self._index].secondary

    def advance_primary(self) -> Tuple[str, str]:
        if self._index >= len(self._phases):
            return "", GuideAction.NONE
        ph = self._phases[self._index]
        action = ph.action or GuideAction.NONE
        self._index += 1
        return "", action

    def reset(self) -> None:
        self._index = 0


def _preflight_session() -> PhaseGuideSession:
    return PhaseGuideSession(
        "① 预检环境",
        [
            _Phase(
                "👉 ① 预检 — 确认旧库路径\n\n"
                "【要做什么】看本窗上方「门锁数据库路径」：\n"
                "  · 一般是旧门锁软件安装目录里的 CardLock.mdb\n"
                "  · 路径不对就点浏览重新选\n"
                "  · 只读打开，不会改旧文件",
                "路径已确认 → 下一步",
            ),
            _Phase(
                "👉 ① 预检 — 数据库组件\n\n"
                "【要做什么】若从没装过 Access 引擎：\n"
                "  · 先点下面灰色按钮启用系统内置组件\n"
                "  · 仍失败再用微软官网备用\n"
                "  · 必须用公司发的完整部署文件夹运行系统",
                "组件已就绪 / 不用装 → 下一步",
            ),
            _Phase(
                "👉 ① 预检 — 开始检测\n\n"
                "【要做什么】点橙色按钮，系统自动检查：\n"
                "  · 能否打开 MDB\n"
                "  · 本机组件是否齐全\n"
                "通过后会自动进入 ② 导入。",
                "开始执行 ① 预检",
                action=GuideAction.EXECUTE_PREFLIGHT,
            ),
        ],
    )


def _import_session() -> PhaseGuideSession:
    return PhaseGuideSession(
        "② 导入旧库",
        [
            _Phase(
                "👉 ② 导入 — 最后确认\n\n"
                "【要做什么】确认门锁数据库路径正确。\n"
                "【注意】导入期间不要关本窗口；旧系统可照常营业。",
                "路径无误 → 下一步",
            ),
            _Phase(
                "👉 ② 导入 — 将读取哪些数据\n\n"
                "系统会从旧库【只读】导入：\n"
                "  · 房间列表\n"
                "  · 在住客人\n"
                "  · 发卡历史（若有）\n"
                "  · 能从库里读到的门锁线索",
                "我明白了 → 开始导入",
                action=GuideAction.EXECUTE_IMPORT,
            ),
        ],
    )


def _usb_cardlock_session() -> PhaseGuideSession:
    return PhaseGuideSession(
        "③ USB 门锁迁移",
        [
            _Phase(
                "👉 ③ USB — 准备 U 盘\n\n"
                "【要插什么】发卡器配套 USB 加密狗或配置 U 盘\n"
                "  · 插在【做对接的这台前台电脑】上\n"
                "  · 等电脑「叮咚」识别后再下一步",
                "U 盘已插好 → 下一步",
            ),
            _Phase(
                "👉 ③ USB — 打开迁移窗\n\n"
                "【要做什么】下一步会弹出门锁迁移小窗；\n"
                "  在里面按黄框：点开始扫描 → 表格里点立即迁移。\n"
                "  · 不要拔 U 盘直到提示成功",
                "打开 USB 迁移窗口",
                action=GuideAction.OPEN_USB,
            ),
        ],
    )


def _sniff_cardlock_session() -> PhaseGuideSession:
    return PhaseGuideSession(
        "④ 发卡嗅探",
        [
            _Phase(
                "👉 ④ 嗅探 — 什么时候需要\n\n"
                "【适用】③ USB 做完仍无法在系统写卡；\n"
                "  或没有 U 盘、已点稍后补。\n"
                "【准备】读卡器串口已分线到本机（现场布线）。",
                "需要嗅探 → 下一步",
            ),
            _Phase(
                "👉 ④ 嗅探 — 打开专用窗口\n\n"
                "【要做什么】下一步打开发卡嗅探窗；\n"
                "  按窗内黄框：放哪种卡 → 老系统点写卡 → 已读取 → 换下一张。\n"
                "  做完记得保存选中密钥再回本窗点 ⑤。",
                "打开发卡嗅探窗口",
                action=GuideAction.OPEN_SNIFF,
            ),
        ],
    )


def _verify_session() -> PhaseGuideSession:
    return PhaseGuideSession(
        "⑤ 验收",
        [
            _Phase(
                "👉 ⑤ 验收 — 核对清单\n\n"
                "【应看到】下方日志里：\n"
                "  · 房间数大于 0\n"
                "  · 门锁密钥或 USB 迁移记录至少一项\n"
                "【通过后】回前台：选房 → 入住 → 收款 → 发卡。",
                "开始刷新验收",
                action=GuideAction.EXECUTE_VERIFY,
            ),
        ],
    )


def get_cardlock_step_session(step_key: str, *, step_done: bool = False) -> object:
    if step_done:
        return _done_step_session(step_key)
    factories = {
        "preflight": _preflight_session,
        "import": _import_session,
        "usb": _usb_cardlock_session,
        "sniff": _sniff_cardlock_session,
        "verify": _verify_session,
    }
    fn = factories.get(step_key)
    if fn:
        return fn()
    return PhaseGuideSession("迁移", [_Phase("请按左侧橙色步骤按钮操作。", "知道了")])


def _done_step_session(step_key: str) -> PhaseGuideSession:
    titles = {
        "preflight": "① 预检",
        "import": "② 导入",
        "usb": "③ USB",
        "sniff": "④ 嗅探",
        "verify": "⑤ 验收",
    }
    t = titles.get(step_key, step_key)
    s = PhaseGuideSession(t, [_Phase(f"✅ {t} 已完成。\n👉 请点左侧下一步（橙色高亮）。", "")])
    s._index = 1
    return s


def cardlock_step_banner(step_key: str, *, step_done: bool = False) -> str:
    return get_cardlock_step_session(step_key, step_done=step_done).banner_text()


# ─── 整合台：先走哪条路 ─────────────────────────────────────────────────────

def hub_path_session(*, is_cardlock: bool, frontdesk_opened: bool) -> PhaseGuideSession:
    if is_cardlock:
        return PhaseGuideSession(
            "整合台 · 智能门锁换系统",
            [
                _Phase(
                    "👉 本店是门锁换系统\n\n"
                    "【请勿】先点下面一键接管、USB、嗅探等小按钮。\n"
                    "【请只】点绿色大按钮【前台门锁对接】。",
                    "我去前台对接窗",
                    action=GuideAction.OPEN_FRONTDESK,
                ),
            ],
        )
    return PhaseGuideSession(
        "整合台 · 其它旧 PMS",
        [
            _Phase(
                "👉 其它旧前台软件（非门锁）\n\n"
                "【推荐】点【一键接管旧系统】：选旧软件安装文件夹即可。\n"
                "【复杂库】再用「分步导入」；补密钥用 USB / 嗅探。",
                "下一步",
            ),
            _Phase(
                "【要做什么】点下面【一键接管旧系统】，\n"
                "按弹出窗黄框选文件夹 → 开始迁移。",
                "打开一键接管",
                action=GuideAction.START_ONECLICK,
            ),
        ],
    )


# ─── USB 迁移窗 ─────────────────────────────────────────────────────────────

def usb_migrate_session() -> PhaseGuideSession:
    return PhaseGuideSession(
        "USB 门锁迁移",
        [
            _Phase(
                "👉 第 1 步 — 插入 U 盘\n\n"
                "【插什么】门锁厂家 USB 加密狗或配置盘\n"
                "【插哪里】本台对接电脑 USB 口，等识别成功",
                "已插入 → 下一步",
            ),
            _Phase(
                "👉 第 2 步 — 扫描\n\n"
                "【要做什么】点下面蓝色开始扫描 USB 驱动器\n"
                "  · 扫不到：换 USB 口、确认 U 盘灯亮\n"
                "  · 仍没有：看下方「已知品牌库」是否含本品牌",
                "开始扫描 USB",
                action=GuideAction.START_USB_SCAN,
            ),
            _Phase(
                "👉 第 3 步 — 迁移\n\n"
                "【要做什么】扫描结果表格最右列\n"
                "  · 点对应行的立即迁移\n"
                "  · 弹出确认点是\n"
                "  · 看到绿色已迁移即可关闭",
                "我已点完迁移",
            ),
        ],
    )


# ─── 一键接管 ─────────────────────────────────────────────────────────────────

def one_click_session(
    folder_path: str = "",
    db_count: int = 0,
    detected_brand: str = "",
) -> PhaseGuideSession:
    """一键接管会话，支持动态注入已选文件夹信息和扫描结果。"""
    folder_info = ""
    if folder_path:
        import os as _os
        folder_short = _os.path.basename(folder_path.rstrip("\\/")) or folder_path
        parts = []
        parts.append(f"📂 已选文件夹：{folder_short}")
        if db_count > 0:
            parts.append(f"发现 {db_count} 个数据库文件")
        if detected_brand:
            parts.append(f"识别为：{detected_brand}")
        folder_info = "\n".join(parts)

    step2_prefix = f"\n\n（{folder_path}）" if folder_path else ""

    return PhaseGuideSession(
        "一键接管",
        [
            _Phase(
                "👉 第 1 步 — 选择旧系统所在文件夹\n\n"
                "【选什么】旧酒店软件整个安装文件夹\n"
                "  · 点下方浏览文件夹按钮\n"
                "  · 系统会自动扫描识别里面有什么\n\n"
                + (folder_info if folder_info else "  ⏳ 请先点击浏览选择文件夹"),
                "已选好 → 看一下扫描结果",
                action=GuideAction.BROWSE_DIR,
            ),
            _Phase(
                "👉 第 2 步 — 确认要迁移的内容\n\n"
                "勾选下方需要导入的数据类型（建议全勾）。\n"
                "不修改旧系统任何文件，请放心。" + step2_prefix,
                "选项已确认 → 开始迁移",
            ),
            _Phase(
                "👉 第 3 步 — 开始执行迁移\n\n"
                "点窗口底部的红色开始迁移按钮。\n"
                "  · 过程中观察右下日志，不要关闭窗口\n"
                "  · 完成后去房态核对房间数和在住姓名\n"
                + (f"  · 来源：{folder_path}" if folder_path else ""),
                "开始迁移",
                action=GuideAction.START_ONECLICK,
            ),
        ],
    )


# ─── 分步向导（四页）──────────────────────────────────────────────────────────

# ─── 数据导入中心（CSV/整机/密钥/老系统）────────────────────────────────

def data_import_tab_session(tab_index: int) -> PhaseGuideSession:
    """tab 顺序：0 CSV · 1 整机 · 2 密钥 · 3 老系统"""
    if tab_index == 0:
        return PhaseGuideSession(
            "CSV 导入房间",
            [
                _Phase(
                    "👉 CSV 导入 — 准备文件\n\n"
                    "【要什么文件】旧系统导出的 CSV\n"
                    "  · 表头必须有：room_id、floor、room_type\n"
                    "  · 用 Excel 另存为 CSV 格式即可",
                    "文件已准备好 → 下一步",
                ),
                _Phase(
                    "👉 CSV 导入 — 选择并导入\n\n"
                    "【要做什么】点下面选择 CSV 文件并导入\n"
                    "  · 已存在的房号会自动跳过\n"
                    "  · 完成后到房态看房间数",
                    "打开选择 CSV",
                    action=GuideAction.IMPORT_CSV,
                ),
            ],
        )
    if tab_index == 1:
        return PhaseGuideSession(
            "整机数据迁移",
            [
                _Phase(
                    "👉 整机迁移 — 什么时候用\n\n"
                    "【适用】换电脑、重装系统、备份还原\n"
                    "【不适用】从竞争对手旧 PMS 接管（请用老系统迁移或整合台）",
                    "明白了 → 下一步",
                ),
                _Phase(
                    "👉 第 1 步 — 在旧电脑导出\n\n"
                    "【要做什么】点下面导出整机数据\n"
                    "  · 把备份文件拷到 U 盘或网盘\n"
                    "  · 门锁密钥请另做「密钥迁移」标签页",
                    "开始导出 .sgbak",
                    action=GuideAction.EXPORT_SGBAK,
                ),
                _Phase(
                    "👉 第 2 步 — 在新电脑导入\n\n"
                    "【要做什么】勾选要恢复的内容\n"
                    "  · 一般全勾房间、在住客人和系统配置\n"
                    "  · 再点选择备份文件并导入",
                    "选择 .sgbak 并导入",
                    action=GuideAction.IMPORT_SGBAK,
                ),
            ],
        )
    if tab_index == 2:
        return PhaseGuideSession(
            "密钥迁移",
            [
                _Phase(
                    "👉 密钥 — 旧电脑先导出\n\n"
                    "【要做什么】在还能开系统的旧电脑上：\n"
                    "  · 设一个导出密码（务必记住）\n"
                    "  · 点导出密钥文件\n"
                    "  · 把密钥文件拷到新电脑",
                    "旧电脑已导出 / 我在这台导出",
                    action=GuideAction.EXPORT_SGKEY,
                ),
                _Phase(
                    "👉 密钥 — 新电脑导入\n\n"
                    "【要做什么】在新电脑上：\n"
                    "  · 输入与导出时相同密码\n"
                    "  · 点选择密钥文件并导入\n"
                    "  · 导入后门锁写卡应恢复正常",
                    "选择 .sgkey 并导入",
                    action=GuideAction.IMPORT_SGKEY,
                ),
            ],
        )
    return PhaseGuideSession(
        "老系统分步迁移",
        [
            _Phase(
                "👉 复杂旧库 — 分步向导\n\n"
                "【适用】Access/DBF 等需人工核对字段的库\n"
                "【CardLock 换系统】请用整合台「前台门锁对接」，不要从这里进",
                "下一步",
            ),
            _Phase(
                "【要做什么】点下面红色按钮启动老系统迁移向导\n"
                "  · 向导每页顶部也有黄框指引\n"
                "  · 按向导 1→2→3→4 页完成",
                "启动迁移向导",
                action=GuideAction.OPEN_LEGACY_WIZARD,
            ),
        ],
    )


def legacy_wizard_page_session(page_id: int) -> PhaseGuideSession:
    pages = {
        0: (
            "向导 · 第 1 步 找数据文件",
            [
                _Phase(
                    "👉 找旧软件里的数据库\n\n"
                    "【要做什么】选旧软件安装目录 → 点开始扫描\n"
                    "  · 列表里选体积最大的数据库那一项\n"
                    "  · 不确定就问旧软件售后要「数据文件路径」",
                    "扫描完成，已选中文件 → 点向导【下一步】",
                ),
            ],
        ),
        1: (
            "向导 · 第 2 步 打开库",
            [
                _Phase(
                    "👉 打开旧数据库\n\n"
                    "【要做什么】点【尝试打开】\n"
                    "  · 有密码：在弹出框输入旧系统数据库密码\n"
                    "  · 打开成功后看表列表是否像房间/客人",
                    "已打开 → 点向导【下一步】",
                ),
            ],
        ),
        2: (
            "向导 · 第 3 步 核对字段",
            [
                _Phase(
                    "👉 核对字段对应\n\n"
                    "【要做什么】每张表检查：房号、姓名等是否对得上\n"
                    "  · 下拉框可改映射\n"
                    "  · 看不懂就保持自动映射",
                    "核对完 → 点向导【下一步】",
                ),
            ],
        ),
        3: (
            "向导 · 第 4 步 导入",
            [
                _Phase(
                    "👉 写入 Solid\n\n"
                    "【要做什么】勾选要导入的表 → 点【开始导入】\n"
                    "  · 完成后到房态看房间数是否正常",
                    "开始导入（在本页按钮）",
                ),
            ],
        ),
    }
    title, phases = pages.get(page_id, ("向导", [_Phase("按页面提示操作。", "知道了")]))
    return PhaseGuideSession(title, phases)
