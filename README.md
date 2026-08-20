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

也可以直接使用 `npm start` 启动。若需要指定 EtherCAT 网卡：

```powershell
py start_ecat_test.py --interface "\Device\NPF_{网卡 GUID}"
```

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

配置速度 PDO 并进入 SAFE-OP，不执行运动：

```powershell
ecat-probe --configure-velocity-pdo --cycle-once
```

受限命令行 Jog 备用入口：

```powershell
ecat-jog <velocity-in-drive-units> <seconds> --confirm-jog
```

命令行 Jog 最长 10 秒，退出或中断时发送零速度并禁能。

## 当前测试配置

- Leadshine DM3C-EC556：Vendor/Product `0x4321/0x8600`，Rx/Tx `15/19` bytes
- KaiFull EC2SS3 / SSD60N：Vendor/Product `0x024B/0x0215`，Rx/Tx `15/23` bytes
- 两种驱动都使用 RxPDO `0x1C12 = 0x1602`、TxPDO `0x1C13 = 0x1A00`
- CiA 402 mode: Profile Velocity，`0x6060 = 0x03`
- 两种驱动都使用 `config_overlap_map()` 进入 SAFE-OP；新凯福驱动已验证零输出 WKC 为 `3`
- 已验证故障复位控制字：`0x6040 = 0x0080`

### HMI 运动模式

- `PV 点动`：保持现有正转/反转按住 Jog，使用 `0x1602` 和 `0x6060 = 0x03`
- `PP 点位`：切换到未使能状态后选择模式，使用 `0x1601`、`0x6060 = 0x01`，填写目标位置、轮廓速度和加减速后执行一次定位
- PP 目标支持绝对位置和相对位移：绝对模式填写坐标，方向由当前位置与目标坐标比较决定；相对模式下 `+` 为正向位移、`-` 为反向位移
- 执行期间页面心跳中断会停止运动，停止按钮发送 PP Halt
- 模式切换必须在驱动未使能且没有运动命令时进行；运行时会经过 PRE-OP 重新配置 PDO，再回到 OP
- PP 过程镜像实测为 Rx 19 bytes、Tx 23 bytes、IO map 42 bytes

ESI 文件用于描述设备，不会被 `pysoem` 自动从 `ESI/` 目录加载；运行时支持列表和实际过程镜像要求位于
`src/dm3c_ecat/device_profiles.py`。网卡可通过环境变量覆盖：

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
