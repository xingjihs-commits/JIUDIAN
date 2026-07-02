# 吃掉门锁系统 — 标准化作战手册

> 不管老板说"修门锁"、"改发卡"还是"适配新品牌"，先读这篇。
> 核心原则：**不碰 EXE，直调 DLL。DLL 不通就走底层 USB 写卡或寄生原厂软件。**

---

## 六步法（新品牌适配标准流程）

### Step 1: 摸底 (30 分钟)

到前台电脑做三件事：

```
1. 看桌面 / 开始菜单 → 在用哪个门锁软件
   → 找到 CardLock.exe / DoorLock.exe / 门锁管理系统.exe

2. 看程序目录有什么 DLL
   → V9RFL.dll / proRFL.dll / d12.dll / Mwic_32.dll → proUSB 体系
   → 其他 DLL → 不同体系

3. 看 System.ini / config.ini 有什么关键字段
   → LD / LDO / PCID / SN / dlsCoID → 授权体系
   → 不同字段名 → 不同品牌
```

**产出**：知道什么牌子、什么 DLL、几个文件。

---

### Step 2: 观察 (1 小时)

在前台正常发卡一次，记录每步：

```
打开软件 → 点"发卡" → 放卡 → 选卡型 → 点确定

观察：
├── 弹了什么窗口？（"注册到期"、"E0074"、"系统错误"）
├── 读卡器闪什么灯？（红=失败，绿=成功）
├── 软件目录有没有新文件生成？（log / temp / 备份）
└── 有没有远程软件在跑？（UltraViewer / TeamViewer）
```

**产出**：知道这个软件有没有被厂家锁着，弹什么错。

---

### Step 3: 解剖 DLL (3 小时) ← 核心

找到发卡相关的 DLL（通常叫 XXXRFL.dll / XXXLock.dll / XXXComm.dll）。

```
1. 看导出函数：
   dumpbin /exports XXX.dll
   找四个必然存在的函数：
   ├── Init/Open/Connect/USB  ← 初始化 USB 连接
   ├── Read/ReadCard          ← 读卡
   ├── Write/WriteCard        ← 写卡
   └── GuestCard/IssueCard    ← 发客人卡（最核心！）

2. 写一个小测试脚本：
   → 32位 Python 加载 DLL
   → 调 Init（参数先用 1 试试，不行再试 0）
   → 调 ReadCard（看能不能读出卡数据）
   → 调 GuestCard（看能不能发出客人卡）

3. 如果 DLL 直调通了的标志：
   → Init 返回 0（成功）
   → ReadCard 返回 16 字节 payload
   → GuestCard 写卡后读卡器闪绿灯
```

**产出**：确认这个 DLL 能不能直接用。**能 → 跳到 Step 6**。不能 → Step 4。

---

### Step 4: 找坑 (1 小时)

常见的 4 种坑和对应的解法：

```
坑 1：Init 有保护锁 → 调 d12=1 而非 d12=0（V9 经验）
坑 2：EXE 弹注册弹窗 → 精确补丁（找 E0074 判决，je→jmp，6字节）
坑 3：发卡器固件保护 → 绕过参数/换参数/底层裸写
坑 4：读卡器需要心跳 → 保留 PATH 2 或单独 timer 保活
```

**产出**：找到解坑方案。解完了再试 Step 3 的测试。

---

### Step 5: 系统卡处理 (2 小时)

如果 DLL **没有**对应的系统卡函数（MasterCard / BuildingCard 等）：

```
1. 拿一张原厂发的同类型卡，dump 出 16 字节 payload
2. 对照 V9 的 payload 结构推算格式（Magic + LockNo + Type + Body + CS）
3. 用 DirectWriteUSB 裸写绕过 DLL 发卡函数
4. 到真门锁上刷一下验证
```

**产出**：系统卡 payload 格式确认，写入 profile。

---

### Step 6: 固化 (30 分钟)

把成果写成 profile JSON，加到 `酒店系统/lock_adapters/profile/profiles/`：

```json
{
  "brand": "品牌名",
  "detect": {
    "files": ["XXX.dll", "XXX.exe"],
    "ini": "config.ini"
  },
  "dll": {
    "init": "initializeUSB",
    "init_params": [1],
    "read": "ReadCard",
    "write": "WriteCard",
    "guest": "GuestCard"
  },
  "payload": {
    "magic": "C92B20B7",
    "layout": "..."
  },
  "patch": {
    "error_code": "E0074",
    "patch_offset": "0x285D09"
  }
}
```

同时在 `memory/sessions/changelog.md` 追加一条记录。

**至此这个品牌的门锁被"吃掉"了。**

---

## 已吃掉的品牌

| 品牌 | 状态 | 说明 |
|:-----|:----:|:------|
| V9 proUSB（华尔顿） | 完全吃掉 | DLL 直调 + DirectWriteUSB + 精确补丁 v12 |

## 有 profile、适配器已就绪、差最后 DLL 验证的品牌

| 品牌 | profile 路径 | 适配器 |
|:-----|:-------------|:-------|
| 爱迪尔 9200 | `profiles/aidier_9200.json` | `aidier_9200.py` |
| 爱迪尔 3200 | `profiles/aidier_3200.json` | `aidier_3200.py` |
| 爱迪尔 MainDll | `profiles/aidier_maindll.json` | `aidier_maindll.py` |
| 必达 IB | `profiles/bida_ib.json` | `bida_ib.py` |
| 力维 | `profiles/level_lock.json` | `level_lock.py` |
| 西容 | `profiles/syron.json` | `syron.py` |
| 雅迪顿 | `profiles/yadidun.json` | `yadidun.py` |
| 同创新佳 | `profiles/tongchuang.json` | `tongchuang.py` |
| 宝迅达 | `profiles/baoxunda.json` | `baoxunda.py` |

## 已知坑（给新 AI）

- `_cards_all_types.py`（`tools/dev/_v9_crypto/`）是研发工具，**不是生产代码**。生产用 `BrandPayloadFactory`
- GuestCard 校验和 `sum(payload[:14]) & 0xFF` **已知错误**，但门锁硬件不校验
- 应急卡（EmergencyCard）**连真实样本都没有**，payload 格式是盲猜的
- 部署时 **`rfl_bridge_32.exe` 不能丢**，没有它发卡器连不上

## 参考资料

- 破解成果总览：`knowledge/综合成果.md`
- 事件时间线：`故事线.md`
- 品牌适配器注册：`酒店系统/lock_adapters/__init__.py`
- 通用适配器引擎：`酒店系统/lock_adapters/generic_adapter.py`
- 自动 DLL 探针：`酒店系统/lock_deploy/dll_probe.py` — 黑盒端自动探测
- 诊断包导入器：`酒店系统/lock_deploy/profile_merger.py` — 厂家端导入
- 桥泛化协议：`酒店系统/lock_adapters/rfl_bridge_32.py` — `bind_from_profile()`

---

## 附录：自动 DLL 探针（dll_probe）使用说明

> 现场遇到未知品牌时，无需手分析，让 probe 自动扫描。

### 现场端（酒店电脑）

```bash
cd 酒店系统
python lock_deploy/dll_probe.py "D:\智能门锁管理系统"
```

输出示例：
```
✅ 探测成功: 疑似 爱迪尔 Lock9200
   DLL: D:\智能门锁\Lock9200.dll
   导出函数: 23 个
   匹配合集: 5 组
   置信度: 85%
   能否发卡: 能 ✨
```

然后运行"生成诊断包"功能，`candidate_profile.json` 会自动打包进 `diagnostic.zip`。

### 厂家端（办公室）

```bash
python lock_deploy/profile_merger.py solid_lock_diag_未知品牌_20260608.zip
```

输出：
```
✅ 找到 candidate profile: 疑似 爱迪尔 Lock9200
   ✅ 导入成功
```

导入后 profile 出现在 `lock_adapters/profile/profiles/auto_*.json`，下次 PMS 更新即自动识别。

### 已有品牌 + 桥泛化验证

如果现场已有 rfl_bridge_32.exe，还可以：

```python
from lock_adapters.bridge_client import RflBridge

bridge = RflBridge()
bridge.start()
bridge.load_dll(r"D:\智能门锁\Lock9200.dll")
resp = bridge.bind_from_profile({"dll": {"init": "init", "read": "readcard", ...}})
# resp = {"bound": ["init", "readcard"], "missing": []}
init_resp = bridge.generic_initialize(init_fn_name="init", param_list=[0, 1])
# init_resp = {"ret": 0, "out": {"working_param": 1}}
```

> 最后更新：2026-06-08 | V9 proUSB 已完全可控 | 自动探针闭环完成
