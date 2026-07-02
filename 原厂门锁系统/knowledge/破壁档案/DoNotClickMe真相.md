# DoNotClickMe.exe 真相报告（IDR 反编译证据闭环）

- **生成时间**：2026-05-31 13:30
- **证据来源**：`D:\IDR\Projects\DoNotClickMe\` 完整反编译产物
- **结论性质**：硬结论，已多重交叉验证

---

## TL;DR（一句话）

**`DoNotClickMe.exe` 是厂家网络版软件自动更新器，对发卡器锁完全无效**。最初交接文档里把它列为「红线 / 不要点」是**正确**的，之前误判它为"工程工具"——现已证伪。

---

## 一、证据链

### 1. 工程入口（DoNotClickMe.dpr）

```
program DoNotClickMe;
uses SysUtils, Classes;
```

- 启动逻辑：必须有 1 个命令行参数，且第一个字符 = `'$'`，否则直接退出
- 主窗口类：`TForm1`（来自 Unit1.pas）

### 2. 窗体（Form1.dfm）

- **标题**：`#36719#20214#27880#20876` → 解码 = **"软件注册"**
- **状态文本**："操作完成，请重新进入注册界面，点申请注册，进行注册信息确认！"
- **进度文本**（IdHTTP1Work）："软件注册中，请耐心等待，注册数据传输:"
- **可见控件**：1 个 ProgressBar + 2 个 Label
- **不可见组件**：`TIdHTTP`、`TMySQLUniProvider`、6 个 `TUniQuery`

→ 这是一个**纯软件层的下载/更新工具**，连按钮都没有。

### 3. Unit1.pas 核心逻辑

#### FormCreate（启动时执行）

```pascal
// 写死的腾讯云 MySQL 凭据（明文！）
UniConnection1.ProviderName = 'MySQL'
UniConnection1.Username     = 'root'
UniConnection1.Server       = '55becaa57a900.sh.cdb.myqcloud.com'
UniConnection1.Password     = 'yan123!!!'
UniConnection1.Database     = 'CardLock'
UniConnection1.Port         = 6238   // 0x185E

// SQL 查询
SELECT * FROM AutoUpdate WHERE DaiLi = :a   // :a = dlsCoID（命令行带过来）

// 取 UpdateFile 字段（'|' 分隔的文件名列表），启动 Timer
```

异常文本：`服务器连接失败`

#### Timer1Timer（轮询每个待更新文件）

```pascal
for each file in UpdateFile.split('|'):
    download(http://www.pradlockreg.club/<dlsCoID>/<file>)
    保存为 <basename>_New.<ext>
    再调 kernel32.CopyFileA 覆盖本地原文件
```

文件扩展名修正：如果是 `.mdb`，下载名仍带 `.mdb`。

### 4. 全文件搜索：零 V9RFL 调用

| 关键字 | 命中文件 |
|---|---|
| `V9RFL` / `proRFL` | **0** |
| `initializeUSB` | **0** |
| `SetReaderMode` | **0** |
| `proTest` / `LockCard` | **0** |
| `DirectWriteUSB` / `DirectReadUSB` | **0** |
| `Buzzer` / `CardErase` / `WriteCard` / `GuestCard` / `IniCard` / `ReadCard` | **0** |
| `SetPassword`（误中） | CREncryption / DBAccess / Unit1 各几处，**全部是 `TUniConnection.SetPassword`**（数据库连接密码），与发卡器 `V9RFL.SetPassword` 无关 |

**DoNotClickMe.exe 不调用 V9RFL.dll 的任何函数，也不做任何 USB 操作。**

---

## 二、对发卡器锁的影响

**完全没有**。它做的事：

- ✗ 不复位发卡器
- ✗ 不解锁两红一蓝
- ✗ 不调 SetReaderMode / proTest / LockCard
- ✗ 不发任何 USB 命令
- ✓ 只能下载新版 CardLock.exe 覆盖旧版

→ 即使运行成功，发卡器仍然是"半死"状态。

---

## 三、意外发现（仅信息记录，不作为行动建议）

IDR 反编译暴露了厂家服务器明文凭据：

| 项 | 值 | DNS 状态（2026-05-31 探测） |
|---|---|---|
| 更新文件源 | `http://www.pradlockreg.club/` | **在线**，A 记录 `193.112.38.194` (TTL 600) |
| MySQL 主机 | `55becaa57a900.sh.cdb.myqcloud.com:6238` | **在线**，CNAME → `sh-cdb-075evfvs.sql.tencentcdb.com` → `43.145.109.180` |
| MySQL 用户 | `root` | — |
| MySQL 密码 | `yan123!!!` | — |
| 数据库名 | `CardLock` | — |
| 关键表 | `AutoUpdate(DaiLi, UpdateFile)` | — |

### ⚠️ 法律 / 合规警告

- 即使凭据明文写在客户端 EXE 里，**未授权连接厂家数据库仍构成未授权访问**（中国《刑法》第 285 条 / 美国 CFAA / 通用计算机犯罪法）。
- root 账号操作会进数据库审计日志，IP 可追溯。
- 厂家可能对这台域名/IP 有蜜罐告警。

**此条目仅作为反编译证据记录，不构成连接建议**。任何外部网络访问决定权完全归用户。

---

## 四、对项目交接文档的修正

`项目交接_v2.1_发卡器现状.md` 里关于 `DoNotClickMe.exe` 的红线判断**应保持红色**，并增补一句话：

> 经 IDR 完整反编译（D:\IDR\Projects\DoNotClickMe\）证实：**DoNotClickMe.exe 是网络版软件自动更新器，对发卡器固件锁完全无效，且会向厂家服务器外发本机 dlsCoID。不要运行。**

---

## 五、剩下还能走的路（按可执行性排序）

### A. 继续 CardLock.exe 静态分析，找 F2 工程模式入口（推荐）

- IDR 已经定位到 `sub_00741190(challenge, 0x176)` 是酒店名框 F2 触发的挑战函数
- 需要做：用 IDR 反编译 `D:\AI\智能门锁管理系统新2021网络版\CardLock.exe`，把生成的 `_Unit*.pas` 放到 `D:\IDR\Projects01\`
- 目标：复刻 challenge-response 算法，本地算出工程模式密码

### B. 直接 ctypes 暴力遍历 `SetReaderMode` 参数空间（低风险）

- `V9RFL.dll` 的 `SetReaderMode(m1, m2)` 参数未知
- 在断电安全的前提下，遍历 `m1∈[0..7], m2∈[0..255]`，配合 Buzzer 听声、用空白卡观察 LED
- 风险：未知是否会触发更深层锁；建议先封装"超时熔断 + 状态机回滚"

### C. 物理 / 商务路径

- 找原酒店要"授权卡 / 工程卡"（IniCard 类型，非客人卡）
- 联系厂家提供 PCID + dlsCoID = 2826423 申请远程解锁

### ❌ 不要做的事

- 不要再跑 `_probe_v9_*` 或矩阵式写擦脚本（就是触发锁的元凶）
- 不要点 DoNotClickMe.exe（已证实无效，还会上报本机信息）
- 不要改 System.ini 的 SN / LD / PCID（会触发软件自校验）

---

## 六、附录：图像基址注解

`_Unit1.pas`（884 字节，image base `0x5F400000`）= 旁路加载的某个 DLL 占位（很可能是 Devart / Indy 系列），不是 DoNotClickMe.exe 本体。本体的 image base 是 `0x00400000`，主 Unit1.pas 是 22376 字节那个。
