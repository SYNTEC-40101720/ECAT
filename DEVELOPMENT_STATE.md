## L6 CSP 软件保护（2026-08-26）

- CSP 提交前验证目标位置、有限且 `0.01..60s` 的持续时间，以及由位移/持续时间推导
	的速度和加速度；默认上限分别为 `10000` 和 `100000` 驱动单位。
- CSP 运行中读取现有 `0x6064` 实际位置反馈，跟随误差上限为 `1000` 位置单位，观测
	周期超过 `0.020s` 即锁定 `ERROR`；锁定会清除运动命令并发送 `0x0006` 安全输出。
- 该保护只作用于已经通过 profile 和 `0x6502` 能力交集的 CSP；未验证 profile 不会因
	本批代码自动开放。实现不声明 Windows/Python 软件循环的实时保证。
- 模拟验证：CSP 窄测试 `15 passed`，使用虚拟时钟覆盖边界、超限、反向、非法时长、
	跟随误差和周期超限；真实驱动、同步周期、单位和安全链路仍待现场验收。

## L7 混合总线切换互锁（2026-08-26）

- Runtime 模式切换和网卡切换前统一检查驱动使能/运动命令与反馈、焊机命令与焊接
	反馈、数字输出掩码；任一活动或非零状态都会拒绝切换。
- 安全帧清零驱动控制输出、数字输出和焊机完整命令，并严格要求安全交换的 WKC 等于
	期望值；WKC 失败锁定 `ERROR`，不会进入 PRE-OP 或执行 PDO 重映射。HAUTO 运行期
	允许的部分 WKC 不适用于该安全帧。
- 模拟验证覆盖 drive+I/O、drive+welding、drive+I/O+welding 和 WKC 失败关闭，L7
	窄测 `10 passed`。真实总线拓扑、从站实际状态、物理输出清零和安全链路仍待验收。

## L8 SYNTEC 域控发布链路（2026-08-26）

- 默认发布参数待用户复核：产品名 `SYNTEC-ECAT-Test`、版本 `1.0.0.0`、目录
	`D:\Release\SYNTEC-ECAT-Test`、发布年 `2026`、目标 `win-x64`，Electron GUI 无控制台。
- 新增 PyInstaller one-dir spec、SYNTEC 中性版本资源（`000004B0`、Translation
	`[0, 1200]`、`Copyright © SYNTEC 2026`）以及后端/发布/产物验证脚本。后端构建已生成
	自包含 `SYNTEC-ECAT-Test-Backend.exe`，不依赖目标机 Python。
- Electron packaged 模式从 `process.resourcesPath\backend` 启动后端，开发态仍使用
	`py -m dm3c_ecat.websocket_hmi`；必要 ESI 由 `extraResources` 携带。Node 路径测试已通过。
- Electron 安装产物尝试构建至目标目录，但下载 Electron `38.8.6` 时网络请求超时，故
	安装包和 packaged GUI 烟雾测试未完成。域控最终安装、签名/白名单和真实设备验收仍待用户。
# ECAT Test 开发状态

最后更新：2026-09-01

## 最终收尾状态（2026-08-26）

- L2-L4、L6、L7 软件实现完成并通过模拟/协议测试；L5 软件校验完成，但部分 PDO 映射、Revision/固件证据和全部硬件仍待资料或实机确认。
- L8 发布配置、PyInstaller 后端构建和后端产物验证完成；Electron 安装包因下载 Electron `38.8.6` 在约 15% 后超时未完成，packaged GUI 烟雾测试未完成。
- 默认产品名 `SYNTEC-ECAT-Test`、版本 `1.0.0.0`、输出目录 `D:\Release\SYNTEC-ECAT-Test` 和发布元数据仍需用户复核；域控安装/签名/白名单验收未完成。
- 最终验证基线：Python 全量 `98 passed`，Node `6 passed`；历史阶段计数保留在对应记录中。

## WebSocket 控制权（2026-08-26）

- WebSocket 网关维护单一控制客户端；连接必须显式发送 `acquire_control`，已有控制者
	时其它连接只能观察，控制命令会被拒绝。
- `release_control` 仅控制者可用，并先调用 Runtime 安全停止；控制者断开时清除所有权
	并停止 Runtime，观察者断开不会影响控制者。
- 控制权状态通过独立 `control` 消息广播；Electron HMI 连接后自动申请，未持有控制权
	时禁用运动、I/O 和焊机控制。
- 软件模拟验证：`tests/test_websocket_hmi.py` 为 `16 passed`；尚未进行多浏览器真实
	连接和现场停止链路验收。

## L1 Runtime 故障测试（2026-08-25）

- 驱动、纯焊机和纯数字 I/O 循环对 WKC 失配都会锁定 `ERROR`、清除命令并结束循环；
  HAUTO 仅在其 profile 明确允许时接受正的部分 WKC。
- 驱动循环检测 CiA 402 Fault；Runtime 线程异常会设置 `running=False`，记录异常类型和
  原始消息，并执行最终安全输出清理，避免异常线程继续存活。
- 安全输出交换异常或安全帧 WKC 失配会进入可诊断错误状态；不会声称输出已被现场设备
  接收。
- 历史验证：`python -m pytest -q tests/test_runtime_interface.py tests/test_welding.py` 为
  `43 passed`；`python -m pytest -q` 为 `73 passed`；Pylance 语法检查和文件诊断无错误。
- 本批只完成软件模拟验证；真实主站断线恢复、过程帧送达、物理输出清零和安全链路仍待
  实机确认。
## 当前实现

项目 ECAT Test 使用 Python/`pysoem` 包提供 EtherCAT 后端，Electron + WebSocket 提供
桌面 UI。`ecat-probe` 用于扫描和 SAFE-OP 检查；`ecat-jog` 是受限命令行
备用入口。项目不再使用 PySide6，源码位于 `src/dm3c_ecat/`。

### CiA 402 模式目录

- 标准模式值已集中定义在 `src/dm3c_ecat/motion_modes.py`：PP=1、VM=2、PV=3、PT=4、HM=6、IP=7、CSP=8、CSV=9、CST=10
- 当前两份本地 ESI 明确提供的 RxPDO 为 PV `0x1602`、PP `0x1601`、Homing `0x1603`、CSP `0x1600`；KaiFull 实机读取到 CSP RxPDO 为 13 bytes，实际映射含 `0x60FF`
- Runtime 启动和模式切换前读取 `0x6502`，只有同时存在 profile PDO 且被固件能力字声明的模式才会进入 HMI `availableModes`；读取失败会停止配置
- KaiFull 实机 `0x6502 = 0x00A5`，声明 PP、PV、IP、CSV；当前本地 profile 只为 PV/PP 提供可确认 PDO，因此 HM/CSP 不会被切换或发送
- Runtime 和 HMI 已实现 PV、PP、Homing、CSP 的模式选择/过程数据分支；HM/CSP 代码仍需在具备对应 `0x6502` 能力的真实设备上验收
- VM、PT、IP、CSV、CST 会在 HMI 模式目录中显示，但因本地 ESI 没有完整可确认映射而被禁用；不能仅凭 `0x6060` 数值复用其它 PDO

## 已验证硬件事实

- 驱动：Leadshine DM3C-EC556
- Vendor：`0x4321`
- Product：`0x8600`
- Revision：`0x0001`
- Npcap 接口：由运行时自动探测；历史验证接口名称仅作日志参考，不作为默认值
- RxPDO：`0x1C12 = 0x1602`，15 bytes
- TxPDO：`0x1C13 = 0x1A00`，19 bytes
- 配置方式：`config_overlap_map()` 可进入 SAFE-OP
- 预期 WKC：`3`
- Profile Velocity：`0x6060 = 0x03`，反馈 `0x6061 = 0x03`
- 故障复位：写 `0x6040 = 0x0080` 已验证

## 新增凯福驱动扫描结果

- ESI：`ESI/active/凯福/KF_EC2SS3V1.23.xml`
- 实际设备名：`SSD60N`
- Vendor/Product/Revision：`0x024B/0x0215/0x0001`
- 默认映射：Rx 104 bit、Tx 184 bit；切换到速度映射 `0x1602/0x1A00` 后为 Rx 120 bit、Tx 184 bit
- Supported Drive Modes：`0x6502 = 0x00A5`，固件声明 PP、PV、IP、CSV；与本地 PDO profile 取交集后 HMI 可用模式为 PV、PP
- 通过 `ecat-probe --configure-velocity-pdo --cycle-once` 进入 SAFE-OP，零输出 WKC 为 `3`
- PV 低速正反 Jog 和使能已由实机确认
- PP 模式运行时切换、19/23 bytes 过程镜像、零输出 SAFE-OP、回 OP 和使能已验证；尚未提交真实 PP 目标位置
- 当前位置反馈来自 TxPDO `0x6064`；使用 `config_overlap_map()` 时周期必须调用 `send_overlap_processdata()`，否则 WKC 仍可能为 3 但 TxPDO 输入全 0
- PV/PP 动态切换时会重新执行 `config_init()` 重建 FMMU/Sync Manager，确保 `0x6064` 反馈在切换后继续刷新

## HAUTO 远程 I/O

- ESI：`ESI/active/HAUTO/HAUTO_AX58100_DIO_IO_MAP_FIX.xml`
- 实际设备名：`HAUTO_DIO_16`
- Vendor/Product/Revision：`0x00000001/0x00010200/0x00000001`
- 固定 RxPDO `0x1600`、TxPDO `0x1A00`，实际过程镜像为输出 2 字节、输入 2 字节
- Runtime 识别为 `digital_io`，直接请求 OP，周期读取 16 路 DI 并控制 16 路 DO；不执行 CiA 402 使能、Jog 或运动模式
- WebSocket 命令为 `set_output(channel, enabled)`；输出初始为零，停止、客户端断开和 Runtime 退出时清零
- 真实网卡验证：从站可进入 OP，AL 状态为 `0x0000`；连续过程数据 WKC 观测为 `3,1,1` 重复，运行时保留正 WKC 的 I/O 状态并显示实际/期望 WKC，`WKC <= 0` 才进入错误并清零输出

## 实点 Solidot EC4-1616A 远程 I/O（2026-09-01）

- ESI：`ESI/active/实点-Solidot/EcatTerminal-EC4_V4.04_BOOL.xml`；随附说明要求同一工程只能选择一个 EC4 XML 变体，不得同时使用 BOOL、UINT、USINT 文件
- 实际身份：设备名 `EC4-1616A`，Vendor/Product/Revision 为 `0x00884443/0x00000004/0x00000001`
- 固定 PDO：RxPDO `0x1600`、TxPDO `0x1A00`；每个 PDO 包含 16 个 `BOOL`，过程镜像为 Rx 2 bytes/16 bits、Tx 2 bytes/16 bits
- 设备不提供 CoE `0x1C00` SyncManager Communication Type 对象。pysoem `config_map()`/`config_overlap_map()` 会先记录 `0x1C00:00` 的 SDO abort `0x06020000`，随后已完成 SII 固定 PDO 映射；Runtime 和 `ecat-probe` 只在从站位置、索引、子索引和 abort code 全部精确匹配时忽略该错误，并继续校验实际缓冲区和总映射长度
- 现场验收：`py -m dm3c_ecat.probe --cycle-once` 已通过；SAFE-OP=`0x0004`、AL=`0x0000`、期望/实际 WKC=`3/3`、输出 `0000`、输入 `0000`，未请求 OP、未发送运动命令
- Runtime/HMI 已按 16DI/16DO profile 接入；当前仍需在安全条件下逐路接入负载验证 DO、电气输入极性和断链/退出后的物理清零

## 实点 Solidot XB6S-EC2002 插片式模块化远程 I/O（2026-09-02）

- ESI：`ESI/active/实点-XB6S/EcatTerminal-XB6S_V2.1.8_ENUM.xml`
- 实际身份：设备名 `XB6S-EC2002`，Vendor/Product/Revision 为 `0x00884443`/`0x00005601`/`0x00000001`（ESI 中 ProductCode 与实际设备一致）
- 架构：ETG.5001 Modular Device Profile，最多 32 个插片模块
- 耦 合器自身固定 PDO：RxPDO `0x16FF`（2 bytes CouplerCtrl `0xF200`）、TxPDO `0x1AFF`（2 bytes CouplerState `0xF100`）
- 模块 PDO 按槽位递增（`DependOnSlot`），第一个 DO 模块使用 `0x1601`，第一个 DI 模块使用 `0x1A00`
- 模块检测：读 `0xF050` Detected Module Ident List
- 模块配置：写 `0xF030` Configured Module Ident List（先写 SI0=0 清空，逐个写入模块 ID，最后写 SI0=count）
- 现场模块组合：
  - Slot 1: `XB6S-3200` ModuleIdent `0x0000E10C`（32DI，TxPDO `0x1A00` 映射 `0x6000:01..20`，4 bytes）
  - Slot 2: `XB6S-0032A` ModuleIdent `0x0000E104`（32DO，RxPDO `0x1601` 映射 `0x7010:01..20`，4 bytes）
- 模块 ID 来自 XB6S V2.1.8 ESI；XB6S 系列模块 ID 范围 `0xE1xx`（DI）和 `0xE10x`/`0xE00x`（DO），与老款 XB6 的 `0x06xx` 不同
- 过程镜像：Rx 6 bytes（4 DO + 2 耦 合器）、Tx 6 bytes（4 DI + 2 耦 合器）
- 无 DC 同步配置
- `ecat-probe --initialize-modular-io --cycle-once` 已验证：模块初始化成功、SAFE-OP=`0x0004`、AL=`0x0000`、WKC=`3/3`、零输出/零输入
- Runtime 模块配置协议 `f030_array`：检测模块组合后计算总 DI/DO 通道数和 Rx/Tx 字节数（含耦 合器头部），通过 `0xF030` 数组写入模块 ID
- `_write_io_output` 和 `read_io_inputs` 自动跳过 `coupler_rx_bytes`/`coupler_tx_bytes` 头部偏移
- 尚需在安全条件下逐路接入负载验证 DO 输出和 DI 读取

## DECOWELL 模块化远程 I/O

- ESI：`ESI/active/DECOWELL/DECOWELL_EX-1100_V1.9.8.xml`（Digital_BOOL/UINT/USINT 变体仅在 `ESI/incoming/` 备查）
- 实际设备：DECOWELL EX-1100；Vendor/Product/Revision：`0x00444543/0x00000001/0x00010001`
- 现场模块组合：EX-203S 32DI，模块 ID `0x7C`；EX-313S 32DO，模块 ID `0x7F`，两种模块各安装两组
- 固定 RxPDO `0x1601`、TxPDO `0x1A00`；实际过程镜像为 Rx 8 bytes/64 bits、Tx 16 bytes/128 bits，Runtime 按检测到的模块组动态提供 64DI/64DO
- 由于 EX-1100 槽位 PDO 是动态组合，映射前必须读取 `0xF050` 校验模块 ID，然后写入 `0x8000:01 = 7c 00`、`0x8010:01 = 7f 00`、`0x8020:01 = 7c 00`、`0x8030:01 = 7f 00`；这里按 ESI `<Data>` 原始字节发送，不能按小端整数发送成 `00 7c`/`00 7f`
- Runtime 初始化顺序为 PRE-OP -> 模块 SDO 初始化 -> `config_map()` -> SAFE-OP -> 一次零输出过程数据交换 -> OP；直接从 PRE-OP 请求 OP 会失败
- `ecat-probe --initialize-modular-io --cycle-once` 已验证四个槽位初始化、SAFE-OP、AL=`0x0000` 和零输出 WKC=`3`
- 正式 Runtime 和 WebSocket HMI 已现场验证：状态 `OPERATIONAL`，snapshot 为 64DI/64DO、WKC=`3/3`，输入输出掩码均为 `0x0000000000000000`；HMI 按 snapshot 动态生成 64 个 DI 与 64 个 DO 控件
- 当前只验证零输出启动和关闭清零，尚未连接现场负载逐路验证 DO 动作；模块 ID 组合不匹配时 Runtime 会拒绝启动

## 麦格米特 EtherCAT 焊机

- ESI：`ESI/active/麦格米特/MegmeetESI260416.xml`
- Vendor/Product：`0xE000001B/0x00000036`
- 原始 RxPDO `0x1600`、TxPDO `0x1A00`；输出/输入过程镜像均为 37 bytes
- `src/dm3c_ecat/welding.py` 集中维护 5 种工作模式、Rx 前 8 字节命令打包和 Tx 前 14 字节反馈解码；其余过程镜像字节保留为零或忽略
- Runtime 已支持焊机独占总线、焊机+数字 I/O，以及焊机与伺服共用 overlap 过程数据；焊机不走 CiA 402 使能和运动状态机
- Electron HMI 已增加焊机导航、五种模式、机器人准备/起焊/气检/送丝/寻位命令、JOB 与模拟量输入、TxPDO 反馈显示和 120 ms 浏览器心跳
- 停止、禁能、客户端断开、页面失焦/隐藏/卸载、Runtime 关闭和 `0.35 s` 心跳超时都会清零完整焊机 Rx 过程镜像并立即交换一次过程数据
- 参数设置不允许携带起焊位；起焊只允许通过独立操作执行，并要求通信就绪、机器人准备、无电源故障且故障码为零；运行中互锁丢失会清零命令
- `ecat-probe` 已显示焊机身份、PDO 索引、37/37 字节过程镜像，并支持 `--cycle-once` 的 SAFE-OP 零输出检查
- 当前验证范围：profile、协议字节、Runtime 模拟过程数据、WebSocket 参数转发、HMI JavaScript/DOM 静态检查；真实焊机尚未接入，以下项目仍未验收：
	- 真实焊机 SAFE-OP/OP、通信就绪和连续 WKC
	- 五种工作模式的 Rx 命令位、JOB/模拟量单位和 Tx 状态位现场对应关系
	- 低电流、短时起弧、送丝/气检/反抽/寻位动作以及电源故障处理
	- 外部急停、STO、安全门、焊机使能链路和页面/进程退出后的实际停止响应

## 伺服与远程 I/O 共存

- Runtime 不再要求总线上只有一个从站，会逐个识别支持的 profile；当前限制为最多一个伺服和一个已支持远程 I/O
- `drive_slave` 与 `io_slave` 分开保存，`self.slave` 仅作为单设备和旧测试接口的兼容别名
- 伺服存在时统一使用 `config_overlap_map()`，一个周期同时写伺服输出和 HAUTO DO，再读取伺服反馈与 HAUTO DI
- snapshot 返回 `deviceType = mixed`、`hasDrive`、`hasDigitalIo`、`driveDevice`、`ioDevice` 以及 `ioInputChannels`/`ioOutputChannels`；Electron HMI 在混合状态同时显示伺服和 I/O 页面，并按通道数生成 I/O 控件
- 伺服进入 `ENABLED/JOGGING/PP_MOVING/HOMING/CSP_MOVING` 时仍允许 `set_output`；停止命令会清零 HAUTO 输出
- 当前混合配置已通过双从站模拟测试；尚未在同一真实总线上同时连接伺服和 HAUTO，现场仍需确认总 WKC、拓扑顺序和安全链路

## Jog 修复

旧桌面 HMI 曾在 Jog 时直接发送 `0x000F`，导致驱动未完成 CiA 402 使能而不
运动。现在 Electron HMI 必须先打开使能，周期线程执行
`0x0006 -> 0x0007 -> 0x000F` 并显示 `ENABLED`；随后才允许 Jog。松开按钮
只停止运动并保持 `0x000F` 零速使能，关闭滑动开关后回到 `0x0006`。使能
每个控制字阶段必须等待对应 CiA 402 状态；状态超时、Fault 或严格 WKC 不匹配时
不会置为已使能。运行中 Fault 会清除使能请求并锁定错误状态。

## HMI 安全行为

- 启动时自动枚举并过滤 WAN、Wi-Fi、蓝牙、VPN、虚拟和回环接口；唯一物理网卡自动选择，多物理网卡等待 HMI 选择
- `ECAT_INTERFACE` 或 `--interface` 显式指定接口时优先于自动探测；未选网卡不会启动实时线程
- WebSocket HMI 先监听再启动 EtherCAT Runtime；即使从站扫描或 PDO 配置失败，网卡面板仍可显示和选择物理网卡
- 从站断开时，`pysoem.find_adapters()` 仍应返回物理网卡；HMI 应报告“未检测到从站”或接口错误，不能显示为“未发现 Npcap 网卡”
- 网卡切换只允许在驱动未使能且没有任何运动命令时进行，切换时停止旧线程并重建主站
- 网卡和模式切换都会检查 PV/PP/HM/CSP 的 pending/active 状态，任何运动期间都拒绝切换
- Jog 只在按住按钮并持续收到浏览器心跳时发送非零速度
- 松开、`pointercancel`、`pointerleave`、页面失焦和页面卸载都会停止
- 心跳超过 `0.35 s` 没有更新时自动发送零速度
- 停止、禁用和程序退出时会串行发送一次零运动、零数字输出和零焊机命令；Electron
	使用带随机令牌的本机关闭请求等待后端清理，2.5 秒超时后才强制终止
- 驱动和焊机要求实际 WKC 等于期望 WKC；HAUTO 现场观测到的部分 WKC 兼容仅在其
	profile 中显式启用，DECOWELL 与混合总线不继承该例外
- 速度上限为 `100000` 驱动单位
- EtherCAT WKC、状态字、故障码和模式会显示在页面上
- PP 目标位置支持绝对/相对选择；模式切换只允许在未使能时进行
- PP 定位期间使用浏览器心跳；页面失焦、隐藏、卸载或连接断开都会停止定位
- HM/CSP 期间使用对应浏览器心跳；页面失焦、隐藏、卸载或连接断开都会停止运动

## 待实机确认

1. 桌面 HMI 的 PP 绝对位置低速短时定位
2. PP 相对位移、目标到位状态和重复定位
3. 速度/位置单位和低速方向
4. 窗口失焦、桌面程序关闭、通信异常和急停后的停止行为
5. STO、限位和机械安全链路
6. 在声明 HM 能力的驱动上确认回零方法、原点/限位行为、偏置和回零完成/错误状态；当前 KaiFull `0x6502` 不声明 HM
7. 在声明 CSP 能力的驱动上确认 10 ms 软件周期下的跟随误差、停止行为和目标轨迹；当前 KaiFull `0x6502` 不声明 CSP

## 尚未具备映射的模式

VM、PT、IP、CSV、CST 目前只有 CiA 402 标准模式值和 HMI 目录入口，尚未纳入任一
本地驱动 profile。要启用这些模式，必须先取得对应驱动手册/真实 ESI、`0x6502`
能力值、RxPDO/TxPDO 映射、过程数据长度和单位，再分别实现 packet、状态机、看门狗
和低速实机验收；不能仅修改 `0x6060` 后复用 PV、PP 或 CSP 的过程镜像。

真实运动前必须确认外接急停、STO、正反限位和机械安全；首次只使用低速、
短时间测试。任何 WKC、故障码或状态异常都应立即停止，不自动重试。
