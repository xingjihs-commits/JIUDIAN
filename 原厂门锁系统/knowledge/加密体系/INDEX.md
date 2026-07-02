# 加密体系 — 六层加密知识库

> 六层加密知识（已从 `tools/dev/_v9_crypto/` 整合至此）
> 全部6层加密已掌握，Python实现见 `_crypto_complete.py`。

---

## 文件列表

| 文件 | 内容 |
|:-----|:------|
| [README.md](README.md) | 六层加密全景总览（AES/BCD/MySQL/DCPcrypt） |
| [客卡BCD变换.md](客卡BCD变换.md) | GuestCard LockNo的BCD nibble位变换算法 |
| [SLE4442安全芯片.md](SLE4442安全芯片.md) | 发卡器内硬件安全芯片（PSC/EC机制） |
| [授权机制.md](授权机制.md) | System.ini LD/LDO/PCID/SN 授权体系 |
| [dll分析.md](dll分析.md) | V9RFL.dll 77个导出函数分析 |

---

## 六层一览

| 层 | 名称 | 算法 | 密钥来源 | 状态 |
|:-:|:-----|:----:|:---------|:----:|
| 1 | systeminfo密钥推导 | AES-256-ECB | PCID/dlsCoID + 云DB | ✅ |
| 2 | GuestCard LockNo | BCD nibble变换 | dlsCoID | ✅ |
| 3 | CardData AES | AES-256-ECB+偏移 | systeminfo推导 | ✅ |
| 4 | MySQL ENCODE/DECODE | XOR流密码 | HotelUID | ✅ |
| 5 | OperatorInfo AES | AES-256-ECB | 全局固定密钥 | ✅ |
| 6 | DCPcrypt库加密 | AES/DES/Blowfish/RC4 | systeminfo | ✅ |

> 关键全局密钥（第5层）: `4E2CAFAACF3471834682CD2E342C7AA7C90D5EFA4C9AA61361A4C68D0C63D186`
