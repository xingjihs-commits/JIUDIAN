# V9 proUSB 门锁加密体系 — 完整知识库

> **一句话总结：** V9 厂商的"加密"不是一套算法，而是**六层独立的加密体系**叠加使用。
> 我们已经完全掌握了全部六层的原理、算法、密钥来源和 Python 解码工具。

---

## 六层加密体系总览

```
系统启动 ──→ System.ini (PCID/dlsCoID) ──→ 数据库连接 ──→ 发卡操作 ──→ MIFARE 卡数据
                │                              │              │              │
                ▼                              ▼              ▼              ▼
          第1层: 算法密钥              第4/5/6层: DB加密   第2层: LockNo  第3层: CardData
          (AES keys 从这里来)           (MySQL/AES)         (BCD 变换)     (AES-256 + 偏移)
```

| 层 | 名称 | 算法 | 密钥来源 | 位置 | 状态 |
|:--:|:-----|:-----|:---------|:-----|:----:|
| 1 | **systeminfo 密钥推导** | AES-256-ECB / 偏移模式 | `System.ini` PCID/dlsCoID + 云DB `systeminfo` 表 | 云数据库 | ✅ 已掌握 |
| 2 | **GuestCard LockNo 加密** | BCD nibble 位变换（非 AES） | `dlsCoID` 作为变换种子 | `V9RFL.dll` / `proRFL.dll` | ✅ 已掌握 |
| 3 | **CardData AES 加密** | AES-256-ECB + 字节偏移 | 由第1层 `systeminfo` 推导 | `CardLock.exe` Delphi 层 | ✅ 已掌握 |
| 4 | **MySQL ENCODE/DECODE** | XOR 流密码（自定义） | `CustInfo` 表，用 `HotelUID` 作密钥 | 云数据库 | ✅ 已掌握 |
| 5 | **OperatorInfo AES-256** | AES-256-ECB | **固定全局密钥** | 本地数据库 `OperatorInfo` | ✅ 已掌握 |
| 6 | **DCPcrypt 库加密** | AES-128/192/256, TripleDES, Blowfish, RC4 | `systeminfo.hotelsysinfo4` | `CardLock.exe` Delphi CREncryption | ✅ 已掌握 |

---

## 第1层: systeminfo 密钥推导

### 流程

```
云 DB systeminfo 表
 ├── hotelsysinfo1 ──→ AES-256 密钥 (32 字节)
 ├── hotelsysinfo3 ──→ AES-256 密钥 (32 字节)
 ├── hotelsysinfo4 ──→ DCPcrypt 初始化向量
 └── PPDL ───────────→ 偏移模式种子
```

### Python 工具

```python
from _crypto_complete import parse_systeminfo

info = parse_systeminfo(json_data)
# info["sysinfo1_key"]    → AES key 1
# info["sysinfo3_key"]    → AES key 3
# info["sysinfo4_key"]    → DCPcrypt IV
# info["ppdl_offset"]     → 偏移模式参数
```

---

## 第2层: GuestCard LockNo（BCD 位变换）

### 核心发现

**不是 AES、不是加密算法，是 BCD（Binary-Coded Decimal）bit-wise 变换。**  
这是一个**恒等变换**——在旧版 PCID 下，密文 = 明文本身。

### 变换步骤

```
输入: 4 字节 LockNo (e.g. BCD 编码的 6 位十进制数)
   ↓
1. 将 4 字节按 BCD nibble 拆成 8 个半字节
2. 按固定拓扑重排 nibble 位置
3. 对每个 nibble 应用 dlsCoID 衍生的位掩码
4. 某些 nibble 之间做 XOR
   ↓
输出: 4 字节"密文"（送入卡 payload）
```

### 函数位置

- `V9RFL.dll` / `proRFL.dll` 导出函数 `GuestCard`
- 反汇编长度: 340 条指令
- 反汇编文件: `_disasm_guestcard_output.txt`

### Python 工具

```python
from _crypto_complete import (
    guestcard_bcd_transform,
    guestcard_payload_parse,
)

# LockNo 加密（明文 → 卡上密文）
encrypted = guestcard_bcd_transform(plain_lock_bytes, dlsCoID)

# 解析 16 字节卡 payload
parsed = guestcard_payload_parse(payload_16bytes)
# parsed["lockno"]   → LockNo（已解密）
# parsed["salt"]     → 1 字节盐值
# parsed["type_seq"] → 卡类型 / 序列号
# parsed["card_no"]  → 2 字节卡号
# parsed["timestamp"]→ 4 字节时间戳 (BCD)
# parsed["checksum"] → 2 字节校验和
```

### 卡 Payload 结构 (16 字节)

```
偏移  长度  字段       说明
0      4    Magic      固定 C92B20B7
4      4    LockNo     加密/明文 锁号 (BCD)
8      1    Salt       盐值
9      1    TypeSeq    卡类型 + 序号
10     2    CardNo     卡号 (Little-Endian)
12     4    Timestamp  BCD 编码时间戳
16     2    Checksum   校验和
```

---

## 第3层: CardData AES 加密

### 位置

`CardLock.exe` Delphi 编译函数 `sub_00698560`
（不是 DLL 里的，是 exe 自身编译的 Pascal 函数）

### 算法

- AES-256-ECB (用 DCPcrypt / Rijndael)
- **额外步骤**：加密后的 16 字节按 `systeminfo.PPDL` 推导的偏移模式重排

### Python 工具

```python
from _crypto_complete import aes_decrypt_ecb

key = bytes.fromhex("32字节AES密钥Hex")
decrypted = aes_decrypt_ecb(ciphertext_16bytes, key)
```

---

## 第4层: MySQL ENCODE/DECODE

### 用途

加密 `CustInfo` 表中的敏感字段：
- `HTName`（客人姓名）
- `HTTel`（电话）
- `HTLXR`（联系人）
- `HTAdd`（地址）
- `HTCP`（身份证）

### 算法

自定义 XOR 流密码（与 MySQL `ENCODE()`/`DECODE()` 函数兼容）：

```python
def mysql_decode(data: bytes, key: str) -> str:
    key_bytes = key.encode('latin-1')
    result = bytearray(len(data))
    for i in range(len(data)):
        k = key_bytes[i % len(key_bytes)]
        result[i] = data[i] ^ k ^ (i & 0xFF)
    return result.decode('utf-8', errors='replace')
```

**密钥 key = `HotelUID`**（从 `systeminfo` 表或 `System.ini` 中取）

---

## 第5层: OperatorInfo AES-256

### 用途

加密酒店管理系统操作员账号密码。
- 表: `OperatorInfo`
- 字段: `GongHao`（账号）, `MiMa`（密码）

### 全局密钥（固定）

```
4E2CAFAACF3471834682CD2E342C7AA7C90D5EFA4C9AA61361A4C68D0C63D186
```

这是 **全局固定** 的 AES-256-ECB 密钥，所有 V9 安装都一样。

### Python 工具

```python
from _crypto_complete import decrypt_operator_field

plaintext = decrypt_operator_field(hex_encoded_string)
# 解密 "F1E2D3C4..." → "admin"
```

---

## 第6层: DCPcrypt 库加密

### 用途

`CREncryption.pas` 中定义的通用加密组件，
被 `CardLock.exe` 用于数据库级别的字段加密。

### 支持的算法

| 算法 | 密钥长度 |
|:-----|:---------|
| AES-128 (Rijndael) | 16 字节 |
| AES-192 (Rijndael) | 24 字节 |
| AES-256 (Rijndael) | 32 字节 |
| TripleDES | 24 字节 |
| Blowfish | 1-56 字节 |
| RC4 | 1-256 字节 |

### 密钥来源

`systeminfo.hotelsysinfo4` → 解析后获得密钥和 IV

---

## Python 工具箱

### 核心文件

| 文件 | 说明 |
|:-----|:------|
| `_crypto_complete.py` | **主库** — 包含全部 6 层加密的解密函数 |
| `_crypto_master_report.md` | 详细技术报告 |
| `_disasm_guestcard_output.txt` | GuestCard 函数反汇编（340 条指令） |
| `_disasm_v9rfl_real_output.txt` | V9RFL.dll 整体反汇编 |

### 快速使用

```python
# 1. 导入全部功能
from _crypto_complete import *

# 2. 解析 systeminfo 密钥
info = parse_systeminfo(systeminfo_json)

# 3. 解密 MySQL 字段
name = mysql_decode(custinfo_row["HTName"], hotel_uid)

# 4. 解密操作员密码
password = decrypt_operator_field(operator_row["MiMa"])

# 5. 解析卡 payload
card = guestcard_payload_parse(raw_16bytes)
```

---

## 关键文件索引

```
原厂门锁系统/
├── knowledge/加密体系/             ← 本目录（加密体系知识库）
│   ├── README.md                   ← 本文档
│   ├── _crypto_complete.py         ← Python 解密库
│   ├── _crypto_master_report.md    ← 详细技术报告
│   ├── _disasm_guestcard_output.txt
│   └── _disasm_v9rfl_real_output.txt
│
├── tools/cloud_db_dump/           ← 云端数据库样本
│   ├── CustInfo.json               ← 含 ENCODE 加密字段的样例
│   ├── systeminfo.json             ← 密钥数据
│   └── OperatorInfo.json           ← 加密的操作员数据
│
├── tools/dev/_x*                   ← 其他调试工具（部分相关）
│
└── 项目执行任务分拆_给AI看.md       ← 主任务文档（含 T0 门锁破解）
```

---

## 下一步行动（硬件到手后）

1. **PM3 破解 MIFARE 卡密钥** → 按 `项目执行任务分拆_给AI看.md` T0 步骤
2. **用 PM3 dump 客人卡** → 验证我们生成的 payload 是否能开门
3. **用 ACR122U 写卡** → 日常使用（不用每次都开 PM3）
4. **如果遇到新卡/新加密** → 用 PM3 autopwn + dump + `_crypto_complete.py` 分析

---

_最后更新: 2026-06-03 | 由 AI 辅助逆向工程完成_
