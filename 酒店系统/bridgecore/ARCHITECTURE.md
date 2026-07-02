# BridgeCore 五层架构 — 接管任何门锁系统的完整设计

> 目标：给安装目录 / DLL 文件 / USB 设备 → 自动探测 → 自动观察 → 自动生成 profile → 自动接管。
> 全程不需要人写一行品牌特定代码。

---

## 总体架构

```
                    ┌─────────────────────────────────────┐
                    │        TakeoverWizard (CLI)          │ ← 已实现 [v1.0]
                    │    Step 1~6 一站式接管向导          │
                    └──────────┬──────────────────────────┘
                               │
                    ┌──────────▼──────────────────────────┐
                    │       GenericLockAdapter             │ ← 已有（消费 profile）
                    │  (按 profile 驱动的通用适配器)        │
                    └──────────┬──────────────────────────┘
                               │
                    ┌──────────▼──────────────────────────┐
                    │     ProfileGenerator                 │ ← 已实现
                    │  (学习结果 + DLL 探测 → profile.json) │
                    └──────────┬──────────────────────────┘
                               │
              ┌────────────────▼──────────────────────────────┐
              │         ProtocolLearner                        │ ← 已实现
              │  (校验和匹配 + 字段偏移推断 + 卡型分类)        │
              └────────────────┬──────────────────────────────┘
                               │
              ┌────────────────▼──────────────────────────────┐
              │         PhysicalChannel (抽象层)               │ ← 已实现
              │  DllChannel | SerialChannel | UsbHidChannel    │
              │  (自动检测 VID/PID/DLL → 选最优通道)           │
              └───────────────────────────────────────────────┘
                               │
              ┌────────────────▼──────────────────────────────┐
              │         Orchestrator                           │ ← 已有（录制/回放）
              │  (Observer + Injector + FaultManager 编排)     │
              └───────────────────────────────────────────────┘
                               │
              ┌────────────────▼──────────────────────────────┐
              │         PanicRecovery                          │ ← 已实现 [v1.0]
              │   Level 1: 软复位 (restart bridge)            │
              │   Level 2: 强制断电复位 (USB Power Cycle)      │
              │   反馈闭环: 恢复后立即 Initialize              │
              └───────────────────────────────────────────────┘
```

**当前版本已实现的完整路径**：

```
ChannelDetector.detect() → probe_dll() → record_session() → ProtocolLearner.learn() → generate_profile() → save_profile() → replay()
```

---

## 模块详解

### 1. PhysicalChannel 抽象层 (`physical_channel.py`)

统一三种物理通信通道，上层不需要关心"当前读卡器是插 USB、走串口还是加载 DLL"。

| 通道类型 | 类名 | 依赖 | 说明 |
|---------|------|------|------|
| DLL | `DllChannel` | `RflBridge` | 封装现有桥接架构 |
| 串口 | `SerialChannel` | pyserial | 直连 RS232/COM 口 |
| USB HID | `UsbHidChannel` | pywinusb | 直连 USB 读卡器 |

**ChannelDetector** 自动扫描：
1. USB HID 设备（复用 `hardware_support.detect_card_readers()`）
2. DLL 文件（复用 `dll_probe.probe()`）
3. COM 端口（复用 pyserial）

### 2. DLL 探测器 (`dll_prober.py`)

包装 `lock_deploy/dll_probe.py` 的能力为结构化 `ProbeResult`。

- `probe_dll(install_dir)` — 根据安装目录探测
- `probe_dll_by_path(dll_path)` — 直接探测指定 DLL

返回 `ProbeResult` 包含：品牌猜测、导出函数列表、分类映射、置信度。

### 3. 协议学习器 (`protocol_learner.py`)

利用 Observer 录制的发卡流量，自动推导三件事：

1. **校验和算法** — 遍历 OPERATOR_REGISTRY 所有 16 种算法逐一比对
2. **Payload 字段偏移** — 差分分析多次发卡的变化字节推断锁号/站点码位置
3. **卡型分类** — 按 fn_name + type_byte 自动聚类

支持从 `RecordingSession` 或 JSONL 文件学习。

### 4. Profile 生成器 (`profile_generator.py`)

将 `ProtocolLearnResult` + `ProbeResult` → 标准 profile JSON。

生成的 profile 字段：
- `brand` / `adapter_id` — 自动命名
- `checksum` — 算法名 + offset + length
- `layout` — 字段偏移推断
- `card_types` — 卡型定义
- `physical_channel` — 通道类型
- `confidence` — 置信度

### 5. 接管向导 (`takeover_wizard.py`)

CLI 向导，串联所有接管步骤：

```
Step 1: ChannelDetector.detect()   — 检测物理通道
Step 2: probe_dll()                — DLL 功能探测
Step 3: record_session()           — 录制（用户用原厂软件发卡）
Step 4: ProtocolLearner.learn()    — 协议学习
Step 5: generate_profile()         — 生成 Profile
Step 6: replay_last()              — 回放验证
```

```bash
# CLI 使用
python -m bridgecore.takeover_wizard "D:\智能门锁管理系统"
```

参数：
- `install_dir` — 门锁系统安装目录
- `--auto` — 自动模式（无需人工确认）
- `--recording-dir` — 录制保存目录
- `--profiles-dir` — Profile 输出目录

---

## 现有资产复用

| 模块 | 复用 |
|------|------|
| `hardware_support.py` | USB 读卡器 VID/PID 检测 |
| `dll_probe.py` | DLL 扫描 + 导出枚举 + 模式匹配 |
| `operator_lib.py` | 16 种校验和算法池 |
| `observer.py` | 录制引擎 |
| `injector.py` | 回放验证引擎 |
| `orchestrator.py` | 录制/回放生命周期管理 |

---

## 不修改的文件

- `generic_adapter.py` — 只消费 profile
- `bridge_client.py` — `_call` 钩子已完备
- `rfl_bridge_32.py` — 专注 V9 DLL
- `payload_factory.py` — 专注已知品牌 payload 构造
- `prousb_v9.py` — 专有适配器
- `app_main.py` — 不涉及
- 10 个 profile JSON 文件 — 不动

---

## 6. PanicRecovery 恐慌恢复 (`panic_recovery.py`)

双重冗余恢复机制，当 Injector 回放或运行时发生连续通信失败时自动执行。

### 恢复流程

```
连续 3 次硬件/超时失败 (或 FaultManager 熔断触发)
    |
    +-- Level 1: 软复位 (Soft Reset) -- 解决 90% 僵死
    |   +-- bridge.stop()     -- 优雅关闭子进程 + USB
    |   +-- bridge.start()    -- 重新启动 32 位桥
    |   +-- load_dll()        -- 重新加载 DLL
    |   +-- initializeUSB()   -- 重新初始化设备
    |
    +-- 如果 Level 1 失败:
    |   +-- Level 2: 强制断电复位 (Power Cycle)
    |       +-- PowerController.cycle_port() -- 物理断电重连
    |       |   +-- WinUsbPowerController  -- Windows SetupAPI 禁用/启用
    |       |   +-- SerialRelayPowerController -- 串口继电器模块
    |       +-- bridge.start() -> load_dll -> initializeUSB
    |       +-- 等待 2s 让 USB 重新枚举
    |
    +-- 反馈闭环:
        +-- _send_initialize() -> 尝试 d12=1/0/2 直到成功
```

### 自动触发

注册到 FaultManager 和 Injector，当连续 `soft_reset_threshold` 次硬件/超时错误时自动执行。

### PowerController 抽象

```python
class PowerController(ABC):
    @abstractmethod
    def cycle_port(self, vid: str = "", pid: str = "") -> bool: ...

class WinUsbPowerController(PowerController):
    # 通过 Windows SetupAPI + devcon.exe 禁用/启用 USB 设备

class SerialRelayPowerController(PowerController):
    # 通过串口 USB 继电器模块控制电源
```

### 配置参数 (PanicConfig)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `soft_reset_threshold` | 3 | 连续失败次数触发 Level 1 |
| `max_soft_resets` | 2 | Level 1 最大尝试次数 |
| `max_power_cycles` | 1 | Level 2 最大尝试次数 |
| `relay_port` | "" | 串口继电器 COM 口 |
| `relay_on_cmd` | "" | 继电器上电指令 |
| `relay_off_cmd` | "" | 继电器断电指令 |
| `power_off_duration` | 1.5s | 断电持续时间 |

---

## 完整生存闭环

```
TakeoverWizard                          GenericLockAdapter
    |                                         |
    +-- Step 1: ChannelDetector               +-- 正常发卡 - Observer 录制
    +-- Step 2: DllProber                     +-- Injector 回放 - FaultManager
    +-- Step 3: record_session                 |
    +-- Step 4: ProtocolLearner                +-- 熔断触发 -> PanicRecovery
    +-- Step 5: ProfileGenerator               |   +-- Level 1: 软复位
    +-- Step 6: replay() -> 验证                |   +-- Level 2: 断电复位
    |                                         |   +-- 恢复 -> Initialize
    +-- profile.json -------- 存入 -------->   |
                                               +-- 全失败 -> 透传模式 -> 人工
```

**从"首次接入"到"长期稳定运行"的完整链路**：通道检测 -> DLL 探测 -> 录制学习 -> profile 生成 -> 回放验证 -> 接管运行 -> 熔断保护 -> 自动恢复 -> 持续运行。
