# ECAT Test 开发状态

最后更新：2026-08-22

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

- ESI：`ESI/KF_EC2SS3V1.23.xml`
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

- ESI：`ESI/HAUTO_AX58100_DIO_IO_MAP_FIX.xml`
- 实际设备名：`HAUTO_DIO_16`
- Vendor/Product/Revision：`0x00000001/0x00010200/0x00000001`
- 固定 RxPDO `0x1600`、TxPDO `0x1A00`，实际过程镜像为输出 2 字节、输入 2 字节
- Runtime 识别为 `digital_io`，直接请求 OP，周期读取 16 路 DI 并控制 16 路 DO；不执行 CiA 402 使能、Jog 或运动模式
- WebSocket 命令为 `set_output(channel, enabled)`；输出初始为零，停止、客户端断开和 Runtime 退出时清零
- 真实网卡验证：从站可进入 OP，AL 状态为 `0x0000`；连续过程数据 WKC 观测为 `3,1,1` 重复，运行时保留正 WKC 的 I/O 状态并显示实际/期望 WKC，`WKC <= 0` 才进入错误并清零输出

## DECOWELL 模块化远程 I/O

- ESI：`ESI/DECOWELL_EX-1100_V1.9.8.xml` 及 Digital_BOOL/UINT/USINT 变体
- 实际设备：DECOWELL EX-1100；Vendor/Product/Revision：`0x00444543/0x00000001/0x00010001`
- 现场模块组合：EX-203S 32DI，模块 ID `0x7C`；EX-313S 32DO，模块 ID `0x7F`，两种模块各安装两组
- 固定 RxPDO `0x1601`、TxPDO `0x1A00`；实际过程镜像为 Rx 8 bytes/64 bits、Tx 16 bytes/128 bits，Runtime 按检测到的模块组动态提供 64DI/64DO
- 由于 EX-1100 槽位 PDO 是动态组合，映射前必须读取 `0xF050` 校验模块 ID，然后写入 `0x8000:01 = 7c 00`、`0x8010:01 = 7f 00`、`0x8020:01 = 7c 00`、`0x8030:01 = 7f 00`；这里按 ESI `<Data>` 原始字节发送，不能按小端整数发送成 `00 7c`/`00 7f`
- Runtime 初始化顺序为 PRE-OP -> 模块 SDO 初始化 -> `config_map()` -> SAFE-OP -> 一次零输出过程数据交换 -> OP；直接从 PRE-OP 请求 OP 会失败
- `ecat-probe --initialize-modular-io --cycle-once` 已验证四个槽位初始化、SAFE-OP、AL=`0x0000` 和零输出 WKC=`3`
- 正式 Runtime 和 WebSocket HMI 已现场验证：状态 `OPERATIONAL`，snapshot 为 64DI/64DO、WKC=`3/3`，输入输出掩码均为 `0x0000000000000000`；HMI 按 snapshot 动态生成 64 个 DI 与 64 个 DO 控件
- 当前只验证零输出启动和关闭清零，尚未连接现场负载逐路验证 DO 动作；模块 ID 组合不匹配时 Runtime 会拒绝启动

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
过程或运行过程 WKC 不匹配时立即停止并显示错误。

## HMI 安全行为

- 启动时自动枚举并过滤 WAN、Wi-Fi、蓝牙、VPN、虚拟和回环接口；唯一物理网卡自动选择，多物理网卡等待 HMI 选择
- `ECAT_INTERFACE` 或 `--interface` 显式指定接口时优先于自动探测；未选网卡不会启动实时线程
- 网卡切换只允许在驱动未使能且没有任何运动命令时进行，切换时停止旧线程并重建主站
- 网卡和模式切换都会检查 PV/PP/HM/CSP 的 pending/active 状态，任何运动期间都拒绝切换
- Jog 只在按住按钮并持续收到浏览器心跳时发送非零速度
- 松开、`pointercancel`、`pointerleave`、页面失焦和页面卸载都会停止
- 心跳超过 `0.35 s` 没有更新时自动发送零速度
- 程序退出时发送零速度并禁能
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
