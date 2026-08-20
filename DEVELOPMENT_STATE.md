# ECAT Test 开发状态

最后更新：2026-08-20

## 当前实现

项目 ECAT Test 使用 Python/`pysoem` 包提供 EtherCAT 后端，Electron + WebSocket 提供
桌面 UI。`ecat-probe` 用于扫描和 SAFE-OP 检查；`ecat-jog` 是受限命令行
备用入口。项目不再使用 PySide6，源码位于 `src/dm3c_ecat/`。

## 已验证硬件事实

- 驱动：Leadshine DM3C-EC556
- Vendor：`0x4321`
- Product：`0x8600`
- Revision：`0x0001`
- Npcap 接口：`\Device\NPF_{635786B8-C648-4999-89CA-87850E677316}`
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
- 通过 `ecat-probe --configure-velocity-pdo --cycle-once` 进入 SAFE-OP，零输出 WKC 为 `3`
- PV 低速正反 Jog 和使能已由实机确认
- PP 模式运行时切换、19/23 bytes 过程镜像、零输出 SAFE-OP、回 OP 和使能已验证；尚未提交真实 PP 目标位置

## Jog 修复

旧桌面 HMI 曾在 Jog 时直接发送 `0x000F`，导致驱动未完成 CiA 402 使能而不
运动。现在 Electron HMI 必须先打开使能，周期线程执行
`0x0006 -> 0x0007 -> 0x000F` 并显示 `ENABLED`；随后才允许 Jog。松开按钮
只停止运动并保持 `0x000F` 零速使能，关闭滑动开关后回到 `0x0006`。使能
过程或运行过程 WKC 不匹配时立即停止并显示错误。

## HMI 安全行为

- Jog 只在按住按钮并持续收到浏览器心跳时发送非零速度
- 松开、`pointercancel`、`pointerleave`、页面失焦和页面卸载都会停止
- 心跳超过 `0.35 s` 没有更新时自动发送零速度
- 程序退出时发送零速度并禁能
- 速度上限为 `100000` 驱动单位
- EtherCAT WKC、状态字、故障码和模式会显示在页面上
- PP 目标位置支持绝对/相对选择；模式切换只允许在未使能时进行
- PP 定位期间使用浏览器心跳；页面失焦、隐藏、卸载或连接断开都会停止定位

## 待实机确认

1. 桌面 HMI 的 PP 绝对位置低速短时定位
2. PP 相对位移、目标到位状态和重复定位
3. 速度/位置单位和低速方向
4. 窗口失焦、桌面程序关闭、通信异常和急停后的停止行为
5. STO、限位和机械安全链路

真实运动前必须确认外接急停、STO、正反限位和机械安全；首次只使用低速、
短时间测试。任何 WKC、故障码或状态异常都应立即停止，不自动重试。
