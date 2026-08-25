# ECAT Test

本项目是通用 EtherCAT 测试工具，使用 Python/`pysoem` 实现测试后端，并以
Electron + Python WebSocket 作为桌面 HMI 架构。

## 安装

```powershell
py -m pip install --editable .
```

确认 Npcap 已安装，并确保没有其他 EtherCAT 主站进程占用同一网卡。

桌面 UI 统一使用下面的 Electron 入口，不再安装或使用 PySide6。

程序日志默认写入 `logs/ecat-test.log`，同时输出到控制台。日志包含网卡打开、
PDO/OP 配置、使能序列、Jog、心跳看门狗、WKC 和异常堆栈。

## Electron + Python + WebSocket 桌面 HMI

安装 Python 包和 Electron 依赖：

```powershell
py -m pip install --editable .
npm install
```

启动 Electron 桌面 HMI：

```powershell
py start_ecat_test.py
```

启动时会枚举 Npcap 网卡并过滤 WAN Miniport、Wi-Fi、蓝牙、VPN、虚拟和回环接口。
只有一个物理网卡时自动使用它；存在多个物理网卡时，Electron HMI 会等待在网卡面板中选择。
未选定网卡不会启动 EtherCAT 实时线程。网卡只能在驱动未使能且没有运动命令时切换。

也可以直接使用 `npm start` 启动。若需要显式指定 EtherCAT 网卡：

```powershell
py start_ecat_test.py --interface "\Device\NPF_{网卡 GUID}"
```

`--interface` 和环境变量 `ECAT_INTERFACE` 都会覆盖自动探测；显式接口无效时会在运行状态中报告错误。

`start_ecat_test.py` 和 `npm start` 都会进入同一条 Electron 启动流程。Electron
主进程会自动完成以下流程：

1. 启动 Python 模块 `dm3c_ecat.websocket_hmi`。
2. Python 在 `ws://127.0.0.1:8765` 提供 EtherCAT 状态和控制命令。
3. Electron 加载 `src/dm3c_ecat/web/index.html`。
4. Electron 窗口关闭时终止 Python 后端，WebSocket 客户端断开时停止运动。

因此不需要再单独执行 `main.py`、启动浏览器或单独启动 Python WebSocket。

## 命令行工具

只读扫描和状态检查：

```powershell
ecat-probe
ecat-probe --cycle-once
```

DECOWELL 模块化 I/O 需要先写入 ESI 定义的槽位模块 ID，再执行 SAFE-OP 零输出检查：

```powershell
ecat-probe --initialize-modular-io --cycle-once
```

该选项会写入模块配置 SDO，但不会请求 OP、使能输出或发送运动命令。

配置速度 PDO 并进入 SAFE-OP，不执行运动：

```powershell
ecat-probe --configure-velocity-pdo --cycle-once
```

受限命令行 Jog 备用入口：

```powershell
ecat-jog <velocity-in-drive-units> <seconds> --confirm-jog
```

命令行工具在只有一个物理网卡时自动选择；多网卡或没有物理网卡时请把接口名作为位置参数传入。
命令行 Jog 最长 10 秒，退出或中断时发送零速度并禁能。

## 当前测试配置

- Leadshine DM3C-EC556：Vendor/Product `0x4321/0x8600`，Rx/Tx `15/19` bytes
- KaiFull EC2SS3 / SSD60N：Vendor/Product `0x024B/0x0215`，Rx/Tx `15/23` bytes
- 两种驱动都使用 RxPDO `0x1C12 = 0x1602`、TxPDO `0x1C13 = 0x1A00`
- CiA 402 mode: Profile Velocity，`0x6060 = 0x03`
- 两种驱动都使用 `config_overlap_map()` 进入 SAFE-OP；新凯福驱动已验证零输出 WKC 为 `3`
- 已验证故障复位控制字：`0x6040 = 0x0080`
- 运行时读取 CiA 402 `0x6502` Supported Drive Modes，并将固件能力与本地已确认 PDO 映射取交集；读取失败会停止配置
- KaiFull 实机 `0x6502 = 0x00A5`，声明 PP、PV、IP、CSV；当前 profile 只有 PV/PP 的可用 PDO，因此 HM/CSP 会被拒绝

### HAUTO 远程 I/O

- HAUTO DIO 16DI/16DO：Vendor/Product `0x00000001/0x00010200`
- 固定 RxPDO `0x1600` / TxPDO `0x1A00`，过程镜像为输出 2 字节、输入 2 字节
- Runtime 直接请求 OP，不执行 CiA 402 使能或运动模式；Web HMI 显示 16 路输入并提供 16 路输出开关
- 输出初始为零；WebSocket 断开、程序退出或点击停止后输出清零
- 当前硬件连续周期实测 WKC 会在 `3/3` 与 `1/3` 间变化，但从站保持 OP 且 AL 状态为零；运行时将非正 WKC 视为断链，正 WKC 显示为部分响应并继续刷新 I/O

### DECOWELL 模块化远程 I/O

- DECOWELL EX-1100：Vendor/Product `0x00444543/0x00000001`；现场组合为 EX-203S（模块 ID `0x7C`）+ EX-313S（模块 ID `0x7F`）重复两组
- 固定 RxPDO `0x1601` / TxPDO `0x1A00`，过程镜像为输出 8 字节、输入 16 字节；HMI 显示并控制 64 路 DI/64 路 DO
- 映射前读取 `0xF050` 校验模块组合，并按 ESI 原始字节写入 `0x8000:01 = 7c 00`、`0x8010:01 = 7f 00`、`0x8020:01 = 7c 00`、`0x8030:01 = 7f 00`；写入后才执行 PDO 映射
- Runtime 会根据重复模块组动态扩展过程数据和通道数；模块组合不匹配时拒绝启动
- 现场零输出验证：SAFE-OP/OP 状态正常、AL=`0x0000`、过程数据 WKC=`3`；尚未接入负载逐路验证实际 DO 动作

### 麦格米特 EtherCAT 焊机

- ESI：`ESI/麦格米特/MegmeetESI260416.xml`；Vendor/Product `0xE000001B/0x00000036`
- 原始 RxPDO `0x1600` / TxPDO `0x1A00`，过程镜像为输出 37 字节、输入 37 字节；Runtime 保留完整镜像，只使用前 8 个命令字节和前 14 个状态字节
- 工作模式值：`0` 直流一元化、`1` 脉冲一元化、`2` JOB、`3` 近控、`4` 分别模式
- Rx 命令包括开始焊接、机器人准备、气体检测、点动送丝、反抽送丝、寻位使能、JOB、焊接电流/送丝速度和焊接电压/电压强度；Tx 状态包括起弧成功、焊接状态、电源故障、通信就绪、故障码、寻位成功和实际量
- 焊机作为原始 PDO 设备处理，不执行 CiA 402 的 `0x6502`、`0x6040` 或 `0x6060` 逻辑；可单独接入，也可与伺服/数字 I/O 共用 overlap 过程数据
- Electron HMI 的“焊机”页面提供命令参数、反馈状态和 `welding_keepalive` 心跳；开始焊接前必须启用“机器人准备”，反馈电源故障时禁止起焊
- 参数命令、起焊、停止和心跳通过 WebSocket 转发；页面失焦、隐藏、卸载、客户端断开、Runtime 停止或心跳超时都会清零焊机输出，非活动帧为完整 37 字节零
- 当前只完成 ESI/profile、协议字节、模拟 Runtime、WebSocket 和 HMI 静态验证；尚未连接真实麦格米特焊机，也未确认低速送丝、起弧、焊接状态和故障码的现场行为

### 伺服与远程 I/O 共存

- Runtime 会扫描同一 EtherCAT 总线上的所有从站，最多绑定一个已支持伺服和一个已支持远程 I/O；不是用 I/O 替换伺服
- 只有伺服时显示 CiA 402 手动控制，只有远程 I/O 时显示数字 I/O；两者同时存在时 Electron HMI 同时显示两块面板
- 混合总线使用同一个过程数据周期：伺服发送运动 PDO，HAUTO 同时发送 DO 并读取 DI；`set_output` 不依赖伺服是否处于 `ENABLED/JOGGING`
- 目前实机分别验证过伺服和 HAUTO；伺服+HAUTO 同总线的过程镜像已由双从站模拟测试覆盖，首次现场组合接线仍需按安全门槛低速验证

### HMI 运动模式

- `HM 回零`：仅在 `0x6502` 声明 HM 且 profile 提供 `0x1603` 时开放；使用 ESI 的 `0x1603`，回零方法必须按驱动器手册确认
- `CSP 周期同步位置`：仅在 `0x6502` 声明 CSP 且 profile 提供对应 PDO 时开放；使用驱动实际 `0x1600` 映射，主站按 10 ms 周期生成目标位置轨迹；DM3C 当前 profile 为 8 bytes，KaiFull/SSD60N 实测为 13 bytes（含 `0x60FF` 目标速度字段）
- 模式目录同时列出 CiA 402 的 `VM`、`PT`、`IP`、`CSV`、`CST` 标准值；当前两份本地 ESI 没有为这些模式提供可安全复用的完整 PDO 映射，因此 HMI 会显示但禁止切换和发送命令
- HM/CSP 执行期间页面心跳中断会停止运动；Homing 状态字到位/错误位会更新页面状态
- 模式切换必须在驱动未使能且没有运动命令时进行；运行时会经过 PRE-OP 重新配置 PDO，再回到 OP

ESI 文件用于描述设备，不会被 `pysoem` 自动从 `ESI/` 目录加载；运行时支持列表和实际过程镜像要求位于
`src/dm3c_ecat/device_profiles.py`，CiA 402 模式值和 packet 元数据位于
`src/dm3c_ecat/motion_modes.py`。Homing/CSP 首次接入真实设备前仍需低速、短时验证。
如需覆盖自动探测，可设置环境变量：

```powershell
$env:ECAT_INTERFACE = '\Device\NPF_{网卡 GUID}'
npm start
```

## 文件说明

- `src/dm3c_ecat/logging_setup.py`：日志配置和滚动文件处理
- `src/dm3c_ecat/hmi.py`：浏览器 HMI 和实时周期主站
- `src/dm3c_ecat/websocket_hmi.py`：Electron 使用的 Python WebSocket 网关
- `electron/main.cjs`：Electron 主进程和 Python 后端生命周期管理
- `src/dm3c_ecat/probe.py`：扫描、PDO 检查和 SAFE-OP 验证
- `src/dm3c_ecat/jog.py`：受限命令行 Jog 备用工具
- `pyproject.toml`：标准 Python 包配置和命令入口
- `requirements-pysoem.txt`：固定 `pysoem` 版本
- `ESI/`：设备 ESI 文件
- `DEVELOPMENT_STATE.md`：实机验证记录和安全门槛

SOEM/`pysoem` 的许可和再发布要求以其发行包及官方许可文本为准。
