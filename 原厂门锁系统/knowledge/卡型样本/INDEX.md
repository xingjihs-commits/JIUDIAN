# 卡型样本 — 13种卡真实Payload数据

> 所有样本来源：383份 MDB 备份（6.2GB），3000+条卡记录。

---

## 卡型代码（第9字节高4bits）

| 代码 | 卡型 | 样本数 | 样本文件 |
|:----:|:-----|:------:|:---------|
| 0 | 授权卡/工程卡 | 13 | [所有授权卡](samples/all_auth_cards.txt) |
| 1 | 记录卡 | 2 | [所有记录卡](samples/all_record_cards.txt) |
| 2 | 房号设置卡 | 10 | [所有房号设置卡](samples/all_roomset_cards.txt) |
| 3 | 时钟设置卡 | 5 | [所有时钟设置卡](samples/all_timeset_cards.txt) |
| 4 | 挂失卡 | 1 | [所有挂失卡](samples/all_lock_cards.txt) |
| 6 | 客人卡 | 1808+ | [所有客人卡](samples/all_guest_cards.txt) |
| 7 | 退房卡 | 5 | [所有退房卡](samples/all_checkout_cards.txt) |
| 8 | 区域卡 | 2 | [所有区域卡](samples/all_group_cards.txt) |
| A | 应急卡 | 0 | ⚠️ 无样本 |
| B | 总卡 | 14 | [所有总卡](samples/all_master_cards.txt) |
| C | 楼栋卡 | 1 | [所有楼栋卡](samples/all_building_cards.txt) |
| D | 楼层卡 | 1 | [所有楼层卡](samples/all_floor_cards.txt) |
| F | 空白卡 | — | Solid PMS自产 |

## Payload 通用结构 (16字节)

```
偏移  长度  字段       说明
0      4    Magic      固定 C92B20B7
4      4    LockNo     锁号（客人卡加密，其它明文/通配符）
8      1    Salt       盐值
9      1    TypeSeq    高4bits=卡类型, 低4bits=序号
10     2    CardNo     卡号 (Little-Endian)
12     4    Timestamp  BCD 编码时间戳
14     2    Checksum   校验和
```

## 注意事项

- **授权卡指纹**: `C92B20B7DF48809C`（13张dump一致，dlsCoID=2826423）
- **总卡指纹**: `C92B20B7FF00...`（新版）/ `C92B20B7AA00...`（旧版）
- **应急卡无样本**：需原厂软件生成一次

> 原始样本保留在 `knowledge/卡型样本/samples/`
