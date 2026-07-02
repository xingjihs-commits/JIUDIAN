# Solid PMS · 酒店小型超市商品图库 v1.1

## 概述

本目录为 Solid 酒店 PMS 前台超市 + Telegram「🛒 超市下单」共用的商品总库数据源。

- **132 SKU**，6 大分类
- **54 条有图**（P0: 19 + P1: 35），**78 条仅 Emoji**（P2）
- **18 个默认上架**（`default_listed: 1`）
- 货币：USD（柬埔寨酒店场景）

## 目录结构

```
assets/shop/
├── README.md           # 本文件
├── manifest.json       # 唯一数据源（132 条）
├── acceptance.csv      # 验收打分表
├── categories/         # 6 张分类图标 64×64
└── items/              # 商品图 PNG 256 + JPG 128
```

## 图片风格

- 白底商品摄影风（`#FFFFFF`）
- 无品牌 Logo、无包装文字、无条码
- AI 生成，非爬图
- PMS 用 `items/{SKU}.png`（256×256）
- Telegram 用 `items/{SKU}_tg.jpg`（128×128，≤30KB）

## 版权说明

所有商品图为 AI 生成的无品牌示意商品图，仅供 Solid PMS 内部使用。不含任何第三方商标或版权素材。

## 新增 SKU 规则

1. 在 `manifest.json` 的 `items` 数组追加一条，字段与现有条目一致
2. `sku` 全大写英文+下划线，不可与已有重复
3. `telegram_label` ≤12 个中文字
4. `image_priority`：P0（必出图精修）/ P1（出图）/ P2（仅 Emoji，`image: null`）
5. 有图 SKU：生成 512 源图 → 缩放到 256 PNG + 128 JPG
6. 更新 `acceptance.csv` 追加一行
7. 重新打包 zip

## 默认上架（18 个）

矿泉水、可乐、啤酒、功能饮料、果汁、冰红茶、纯牛奶、方便面、薯片、巧克力、安全套、牙刷、牙膏、香皂、毛巾、拖鞋、抽纸、湿巾

## 分类

| id | 名称 | 数量 |
|:---|:---|---:|
| drink | 饮料酒水 | 28 |
| food | 方便食品 | 22 |
| snack | 休闲零食 | 24 |
| daily | 日用百货 | 32 |
| care | 个人护理 | 18 |
| tobacco | 烟酒 | 8 |

## 接线说明（PMS Agent 后续）

- 从 `manifest.json` 导入种子数据到 `shop_items` 表
- `default_listed` → 期初盘点默认勾选
- `listed` 字段由商家在前台勾选控制
- Telegram 按钮用 `telegram_label`，发图用 `image_tg`
