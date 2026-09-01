# ECAT Luna 开发执行计划

最后更新：2026-08-26

## 使用方式

本文件记录已完成的软件批次以及仍受外部条件限制的后续工作。Luna 每次只领取一个批次，先阅读
`PROJECT_REVIEW_AND_ROADMAP.md`、`DEVELOPMENT_STATE.md`、本文件和当前 Git diff，完成
代码、测试、文档和交接记录后停止，不跨批次顺手重构。

职责划分：

- Luna：源码、模拟测试、协议测试、静态检查、构建脚本、发布产物自动检查和文档更新。
- Copilot：批次开始前审查范围，批次结束后复核安全边界、差异和验证结果，处理 Luna
  无法稳定完成的跨模块缺陷。
- 用户：真实驱动、I/O、焊机、急停、STO、安全门、域控安装和现场动作验收。

共同约束：

- 保留当前未提交改动，不回滚、不提交、不建分支。
- 先形成一个可证伪假设，再做最小改动并立即运行最窄测试。
- 不连接或驱动真实 EtherCAT 设备；测试必须使用 mock/fake。
- 自动化通过只能标记“软件验证完成”，不得替代实机或安全链路验收。
- 每批完成后更新本文件状态及 `PROJECT_REVIEW_AND_ROADMAP.md`，记录实际命令和结果。

## 执行顺序

### L1：Runtime 故障与退出测试

状态：`完成（软件验证）`

目标：补齐阶段一安全逻辑的线程和失败路径证据，不改变公开控制协议。

范围：

- 为 Runtime 单周期/线程路径增加 WKC 丢失、部分 WKC、CiA 402 Fault、异常退出测试。
- 验证安全输出传输失败时进入可诊断状态，且原始异常不会造成测试线程泄漏。
- 覆盖驱动、纯焊机、纯 I/O 三种循环；HAUTO 部分 WKC 例外必须单独测试。

禁止：真实网卡访问、自动恢复运动、重构 PDO profile、修改 Electron。

验收：

```powershell
python -m pytest -q tests/test_runtime_interface.py tests/test_welding.py
python -m pytest -q
```

历史批次结果（2026-08-25）：聚焦测试 `43 passed`，全量测试 `73 passed`。仅使用
mock/fake 主站和从站；真实网卡、驱动、I/O、焊机、急停、STO、断网和强制退出后的
物理停止仍待用户现场验收。

### L2：WebSocket 单控制客户端所有权

状态：`完成（软件验证）`

目标：观察客户端不能发控制命令，断开观察客户端不能停止控制客户端的动作。

范围：

- 定义一个控制客户端，其余客户端只接收 state/log/adapters。
- 增加显式获取、释放或拒绝控制权协议；控制客户端断开时执行停止。
- shutdown 仍只接受 Electron 随机令牌，不纳入普通控制权。
- 增加双客户端连接、拒绝、释放、断开和广播测试。

禁止：使用客户端 IP 推断身份、允许静默抢占、删除断开安全停止。

验收：

```powershell
python -m pytest -q tests/test_websocket_hmi.py
python -m pytest -q
```

本批结果（2026-08-26）：`python -m pytest -q tests/test_websocket_hmi.py` 为
`16 passed`，并通过 `node --check src/dm3c_ecat/web/app.js`。控制者断开会停止
Runtime，观察者断开不会停止控制者；多浏览器连接和现场停止响应仍待用户验收。

### L3：统一或移除旧 HTTP 控制入口

状态：`完成（软件验证）`

目标：消除 HTTP 与 WebSocket 在类型、字段、单位和安全行为上的双实现。

推荐方案：若 README/package entry point 没有明确兼容承诺，移除 HTTP 控制入口，仅保留
Electron WebSocket；若必须保留，则抽取共享命令 schema 和 Runtime 调用层。

范围：

- 先搜索 `ecat-hmi` 的入口、文档和调用者，记录兼容性结论。
- 统一 object、严格 bool/int、有限浮点、范围、未知字段和请求体大小限制。
- 增加 HTTP 与 WebSocket 同命令同结果测试，或删除入口后的 CLI/文档回归测试。

禁止：保留两套独立字段转换、扩大监听地址、默认绑定非 loopback。

验收：

```powershell
python -m pytest -q
python -m compileall -q src tests start_ecat_test.py
```

本批结果（2026-08-26）：移除 `ecat-hmi` HTTP/SSE 控制脚本及 Handler，保留
Electron WebSocket 控制入口和 `ecat-probe` 诊断入口；L3 窄回归 `59 passed`。

### L4：Electron 后端生命周期测试

状态：`完成（软件验证）`

目标：后端未就绪、异常退出和窗口关闭都可观测且不可误进入控制状态。

范围：

- 抽取可测试的后端启动/关闭逻辑，避免测试真正启动 Electron UI。
- 增加启动握手、端口占用、Python 启动失败、意外退出、优雅关闭和超时强杀测试。
- 后端未 ready 前禁用控制 UI，并显示明确错误状态。

禁止：把固定延时当作唯一 ready 判据、在日志中输出 shutdown token。

验收：

```powershell
npm test
node --check electron/main.cjs
node --check src/dm3c_ecat/web/app.js
python -m pytest -q
```

如项目尚无 JavaScript 测试框架，Luna 可选择 Node 内建 `node:test`，避免引入大型依赖。

历史批次结果（2026-08-26）：抽取 `electron/backend_lifecycle.cjs`，覆盖重复启动、
WebSocket ready 握手、优雅 shutdown 和超时 kill；`npm test` 为 `4 passed`。

### L5：设备 Revision 与 PDO 映射可信化

状态：`部分完成（软件验证，仍需设备事实）`

目标：未知 Revision 不自动套用已知 profile，PDO 映射不符时拒绝进入 OP。

范围：

- profile 增加明确 Revision 适用条件和未知版本错误。
- 使用结构化 SDO 读取逐项校验 assignment、index、subindex、bit length 和顺序。
- 为 DM3C、KaiFull、HAUTO、DECOWELL、麦格米特建立 mock readback 测试。
- ESI 与现场记录冲突只记录为版本差异，不擅自覆盖已验证事实。

禁止：仅校验总字节数、未知 Revision 回退到首个 profile、修改现场设备配置做试探。

验收：PDO 任一项不匹配的测试必须失败关闭；全部模拟测试和静态检查通过。

本批结果（2026-08-26）：DM3C/KaiFull profile 绑定已记录 Revision `0x0001`，
驱动配置后回读 `0x1C12:01`、`0x1C13:01` assignment；未知/不匹配 Revision 和
assignment mismatch 有测试覆盖。ESI 条目位宽/顺序及 HAUTO、DECOWELL、麦格米特
现场 Revision 尚缺足够一致证据，保留为待实机/协议资料验收，未猜测映射。

### L6：CSP 软件保护

状态：`完成（软件验证，实机启用前必须用户验收）`

目标：CSP 在软件侧具备速度、加速度、跟随误差和周期异常保护。

范围：

- 将限制参数集中配置并在提交轨迹前验证。
- 运行中检查实际位置、跟随误差和周期超限，异常进入锁定错误并安全停止。
- 使用确定性虚拟时钟测试边界、超限、反向和零持续时间。
- 默认保持未验证 CSP profile 不可用，不因完成代码自动开放 HMI。

禁止：声称 Windows/Python 10 ms 循环具有实时保证、绕过 `0x6502` 能力检查。

本批结果（2026-08-26）：Runtime 在 CSP 轨迹提交前校验位置、持续时间、推导速度和
推导加速度；默认限制为速度 `10000`、加速度 `100000`、跟随误差 `1000` 位置单位、
最大观测周期 `0.020s`。运行中使用实际位置反馈检查跟随误差和周期超限，异常锁定
`ERROR`、清除命令并发送禁能安全帧。新增确定性边界/反向/非法时长/超限测试；未改变
`0x6502` 能力与 profile 交集规则，未验证 CSP 的驱动仍不可用。窄测为 `15 passed`。

### L7：混合总线切换互锁

状态：`完成（软件验证，仍需实机验收）`

目标：重映射或模式切换前，所有设备命令和反馈都处于安全状态。

范围：

- 检查驱动未使能/未运动、焊机命令与反馈均未焊接、数字输出为零。
- 切换前发送安全输出并验证严格 WKC；失败时拒绝进入 PRE-OP。
- 增加 drive+I/O、drive+welding、drive+I/O+welding 的模拟测试。

本批结果（2026-08-26）：模式和网卡切换共用安全互锁；活动驱动、运动命令/反馈、
焊机命令/焊接反馈或非零数字输出会被拒绝。安全帧清零驱动、I/O 和焊机输出，且
严格要求 `WKC == expected WKC`；WKC 失败会锁定 `ERROR`，不进入 PRE-OP 或重映射。
HAUTO 运行期部分 WKC 例外未用于切换安全帧。L7 窄测 `10 passed`，未连接真实设备。

禁止：在活动焊接、非零输出或运动反馈存在时重建映射。

### L8：SYNTEC 域控发布链路

状态：`部分完成（配置和后端产物完成，Electron 安装包受网络阻塞）`

目标：生成不依赖系统 Python/开发目录的 Electron + Python 后端安装产物。

范围：

- Python 后端采用 PyInstaller one-dir、windowed、`--noupx`，从纯英文无空格路径构建。
- 最终 exe 名称以 `SYNTEC` 开头；CompanyName/LegalCopyright 含 `SYNTEC`。
- 版本使用四段数字；2026 发布版权为 `Copyright © SYNTEC 2026`。
- Python version resource 使用 `000004B0` 和 Translation `[0, 1200]`。
- Electron builder 通过 `extraResources` 携带后端、Python 依赖和必要 ESI。
- 主进程开发态与 packaged 态分别解析后端路径，增加启动握手和错误提示。
- 自动检查文件、版本信息和后端 `_internal` 依赖；安装包内容和 packaged GUI 烟雾测试须在安装包生成后执行。

禁止：UPX、直接依赖目标机 `py`、从中文/空格路径执行 PyInstaller、引入 `ctypes`
Windows API 绕过策略。

本批默认参数（待用户复核）：产品名 `SYNTEC-ECAT-Test`，版本 `1.0.0.0`（package.json
SemVer 为 `1.0.0`），发布目录 `D:\Release\SYNTEC-ECAT-Test`，发布年 `2026`，目标
`win-x64`，Electron GUI 无控制台。Python 后端 one-dir 构建已完成，Electron 安装产物
因下载 Electron `38.8.6` 网络请求超时未完成；域控机器上的签名、白名单、安装、升级、
卸载和现场启动仍待用户验收。

## Luna 每批提示词

将 `{批次}` 替换为 `L1` 至 `L8`：

> 阅读 `PROJECT_REVIEW_AND_ROADMAP.md`、`DEVELOPMENT_STATE.md`、
> `LUNA_DEVELOPMENT_PLAN.md` 和当前 Git diff，只执行 `{批次}`。保留所有现有未提交
> 改动，不连接真实 EtherCAT 设备，不跨批次重构。先从对应代码和现有测试形成一个
> 可证伪假设，做最小修改并立即运行最窄测试。完成后运行该批验收命令，更新两份规划
> 文档，并报告修改文件、测试结果、未验证的实机边界和建议的下一批次。不要提交 Git。

## 交接格式

Luna 每批结束必须留下：

1. 状态：`完成`、`部分完成` 或 `阻塞`。
2. 修改文件及每个文件的行为变化。
3. 实际执行的验证命令和完整通过数量。
4. 尚未验证的硬件、域控或安全边界。
5. 新发现问题及证据等级：已复现、代码风险或待实机确认。
6. 下一批建议；不得自行开始下一批。

## 用户验收队列

以下工作不交给 Luna 自动执行：

1. 阶段一安全闭环的真实驱动、I/O、焊机验证。
2. PP 低速绝对/相对运动、方向、单位和重复定位。
3. 窗口关闭、断网、断电、进程强杀后的实际输出与停止响应。
4. 急停、STO、限位、安全门及机械风险评估。
5. CSP/Homing 在声明能力的真实驱动上的低速验收。
6. SYNTEC 域控机器上的安装、启动、升级、卸载和白名单验证。
