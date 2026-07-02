# SolidCollector — U 盘万能采集工具

## 用途

在不依赖 Solid PMS 的情况下，学习未知品牌门锁系统的卡片数据格式。
只需一张空白卡 + 一张已发卡（客人卡），工具就能自动分析出数据规律并生成 Profile，
**输出 `.solidhandover` 握手包**，供 PMS 导入后直接发卡。

## 核心流程（9 步操作教练）

```
步骤 1 开始扫描 → 2 选客人卡 → 3 读空白 → 4 原厂写卡
→ 5 读已写 → 6 添加样本 → 7 开始分析 → 8 核对读数 → 9 生成握手包
```

界面顶部 **StepCoach 教练条** 实时指示当前步骤；底部固定 **开始分析** 按钮（步骤 7 起可见）。

## 使用方法

### 打包版（推荐）

```bash
1. 确保 bridge32.exe 与 SolidCollector.exe 在同一目录
2. 双击 SolidCollector.exe
3. 按顶部 9 步教练操作（仅第 4 步去原厂软件写卡）：
   - 选择原厂门锁目录 → 「开始扫描」
   - 读空白卡（采样本）→ 原厂发客人卡 → 读已写卡（采样本）→ 添加样本
   - 底栏「开始分析」→ 「核对读数（毕业验证）」
   - 毕业 6/6 → 生成 `.solidhandover` 握手包
```

详细一页纸：`memory/plans/collector_step_coach_field_card.md`

### 源码运行

```bash
python collector_main.py
```

## 文件说明

| 文件 | 说明 |
|:---|:---|
| `collector_main.py` | 主入口 |
| `collector_ui.py` | 9 步教练 UI + 六维毕业面板 |
| `step_coach.py` | 9 步状态机与 copy |
| `collector_bridge.py` | 32 位桥接 RPC 客户端 |
| `bridge32.exe` | 32 位桥接子进程（读卡写卡） |
| `bridgecore/` | 独立分析引擎（差分分析/校验和/Profile/.solidhandover 打包） |
| `SolidCollector.spec` | PyInstaller 打包配置 |

## 产出物

`learned_profiles/*.solidhandover` — 自包含握手包，内含：
- Profile JSON（卡协议配置）
- 原厂 DLL 副本（dll_direct 模式）
- bridge32.exe
- MANIFEST.json（版本/mode/校验和）

## 打包

```bash
python -m PyInstaller SolidCollector.spec --noconfirm
# 输出：dist/SolidCollector.exe
# bridge32.exe 需在打包前放入本项目目录
```

或双击项目根目录 **采集器_一键打包.bat**。

## 桥接文件

32 位桥接程序 `bridge32.exe` 与 `SolidCollector.exe` 同目录即可。
Collector 依赖它是 32 位的（与 PMS 的 rfl_bridge_32.exe 同源可复用）。
