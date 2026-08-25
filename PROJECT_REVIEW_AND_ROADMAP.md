# ECAT 项目审查与后续开发路线图

最后更新：2026-08-25

## 文档用途

本文件用于跨对话保存项目审查结论、已确认问题、风险边界、修复顺序和验收标准。
开始新的 Copilot 对话时，应先阅读本文件、`DEVELOPMENT_STATE.md` 和当前 Git 差异，
再继续开发。本文记录的是审查时点的事实；代码修复或实机验证完成后必须同步更新状态。

## 当前项目状态

- 架构：Python/`pysoem` EtherCAT Runtime + WebSocket + Electron HMI。
- Python：要求 3.12 及以上；`pysoem==1.1.13`、`websockets>=14,<16`。
- 前端：Electron 38，静态页面位于 `src/dm3c_ecat/web/`。
- 支持设备：Leadshine DM3C、KaiFull SSD60N、HAUTO DIO、DECOWELL 模块化 I/O、
  麦格米特 EtherCAT 焊机。
- 审查初始基线：`python -m pytest -q` 为 `48 passed`；本轮修复后为 `73 passed`。
  Python compileall、
  `node --check src/dm3c_ecat/web/app.js`、`python -m pip check` 均通过。
- `npm run build` 失败：当前没有 `build` 脚本，也没有完整的 Python 后端随包方案。
- 当前工作区包含用户未提交的源码、测试、文档和焊机功能改动。后续不得擅自回滚。

## 已确认 Bug

### P0：驱动可能假使能

位置：`src/dm3c_ecat/hmi.py` 的 `Runtime.enable_drive()`。

现象：`0x0006 -> 0x0007 -> 0x000F` 使能序列只检查 WKC，没有按 CiA 402
状态字确认 Ready to switch on、Switched on 和 Operation enabled，也没有统一检查 Fault 位。

已复现：桩环境中反馈状态字保持 `0x0000`、WKC 正常时，`enabled` 仍会变为 `True`。

影响：HMI 可能显示已使能并允许提交运动命令，但驱动并未进入 Operation enabled；
驱动运行中进入 Fault 时也可能只因 WKC 正常而未立即锁定错误。

修复方向：

1. 为每个控制字定义状态字 mask/value 和超时。
2. 每个使能阶段循环发送安全零目标，并确认目标状态后再进入下一阶段。
3. 每周期检查 Fault、Switch on disabled 和模式反馈；异常时执行统一安全停止。

### P0：焊机起焊互锁不完整

位置：`src/dm3c_ecat/hmi.py` 的 `set_welding_command()`、`start_welding()`，以及
`src/dm3c_ecat/web/app.js` 的开始焊接处理。

现象：设置带起焊位的命令时没有同时检查通信就绪、电源故障和故障码；前端会先发送
`set_welding_command(startWelding=true)`，再发送 `start_welding`。

已复现：`welding_power_fault=True` 且 `welding_communication_ready=False` 时，调用
`set_welding_command(start_welding=True, robot_ready=True, ...)` 后仍得到
`welding_command_active=True`、`welding_start_welding=True`。

影响：第二条命令即使被拒绝，第一条命令已经可能把起焊位置入过程数据。

修复方向：

1. 参数准备与实际起焊分离，参数命令永远不携带起焊位。
2. 起焊使用单一原子操作，并要求通信就绪、机器人准备、无电源故障且故障码为零。
3. 运行中出现故障或通信未就绪时，立即清零完整 37 字节 RxPDO。

### P0：停止和退出不保证物理输出立即清零

位置：`Runtime.stop_motion()`、`disable()`、`stop()`、`close()`，以及
`electron/main.cjs` 的后端退出处理。

现象：停止函数主要清除内存中的命令和 I/O mask，实际从站输出需要等待后续周期；
Electron 关闭时直接调用 `backend.kill()`，可能跳过 Python `finally` 中的安全周期。

已复现：调用 `stop_motion()` 后 `io_output_mask` 为零，但模拟从站的 output 仍保持
旧值 `01 00`。

影响：线程已停止、进程被强杀或周期异常时，不能证明驱动禁能、I/O 和焊机输出已经
送达从站。软件停止不能替代 STO、急停和安全门。

修复方向：实现统一 `safe_stop()`，在主站仍可用时写零输出/禁能帧，执行有限次数过程
数据交换并记录 WKC；Electron 先请求优雅退出并等待，超时后才强制结束。

## 高风险问题

### P1：焊机部分 WKC 被当作可继续运行

`run_welding_loop()` 和焊机 SAFE-OP 检查只在 `WKC <= 0` 时失败。混合总线中部分
从站掉线仍可能得到正 WKC，因此焊机可能继续发送活动命令。

要求：驱动和焊机使用严格期望 WKC；HAUTO 若必须容忍部分 WKC，应作为明确的设备级
例外，同时读取从站状态和 AL 状态，不得把该策略泛化到焊机。

### P1：WebSocket/HTTP 输入校验可被类型强转绕过

位置：`src/dm3c_ecat/websocket_hmi.py`、`src/dm3c_ecat/hmi.py` 的 HTTP Handler。

已复现：JSON 字符串 `"false"` 经 `bool()` 转换后为 `True`；JSON 数组进入
`handle_command()` 会触发未捕获的 `AttributeError`。旧 HTTP PP 接口还把加减速时间
转为整数，与 WebSocket 的浮点秒数不一致。

要求：先验证消息必须是 JSON object，再进行严格 bool、整数、有限浮点数和范围校验；
统一 HTTP/WebSocket 字段与单位，并限制请求体大小。控制服务默认只绑定 loopback。

当前状态：WebSocket 命令入口已完成 JSON object、字段集合、字符串、严格 bool、整数和
有限浮点数校验；对应非法形状、字符串 bool、NaN 和未知字段测试已加入。HTTP 入口仍待
统一，不能将本项整体标记为完成。

### P1：CLI Jog 使用错误的反馈方向和过程数据 API

位置：`src/dm3c_ecat/jog.py`。

已复现：`statusword_from_overlap_output()` 从 `slave.output` 读取状态字；模拟输入中写入
`0x0021` 时仍返回零。代码调用 `config_overlap_map()` 后却使用
`send_processdata()`，而不是 `send_overlap_processdata()`。CLI 也未实现 Runtime 的
`100000` 速度上限。

要求：从 `slave.input` 解码状态字；统一 overlap API；增加速度上限、Fault 检查、
每周期严格 WKC 和对应单元测试。

当前状态：CLI Jog 已从 `slave.input` 解码状态字，等待/初始化/运行/退出路径统一使用
`send_overlap_processdata()`，并增加速度上限、WKC 和 Fault 检查；对应测试已加入。

### P1：Electron 发布链路不完整

位置：`package.json`、`electron-builder.yml`、`electron/main.cjs`。

现状：只有 `start/dev` 脚本；builder 配置仅包含 Electron 和 Web 文件；启动依赖目标机
存在 `py`、项目源码及 Python 包。当前配置不能形成可独立运行的安装包。

要求：确定 Python 后端交付方式后再补打包。推荐将后端打成独立、可审计的可执行文件，
放入 `extraResources`，主进程按 `process.resourcesPath` 启动；加入启动握手、异常退出
提示和优雅关闭。域控环境发布时遵循 SYNTEC 打包规范。

## 其他设计隐患

### PDO 与设备版本

- profile 当前只按 Vendor/Product 匹配，没有绑定 Revision/固件版本。
- Runtime 主要验证过程镜像长度，没有逐项回读 PDO 对象、顺序和位宽。
- 本地 `ESI/KF_EC2SS3V1.23.xml` 中 `0x1600` CSP 映射为 9 字节；项目状态文档记录
  KaiFull 实机映射为 13 字节并包含 `0x60FF`。这可能是现场版本差异，不能直接判定
  哪一方错误，但必须按 revision/固件版本建 profile 并做映射 readback。

### CSP 运动保护

- 当前只限制目标位置和持续时间，没有按位移/时间限制目标速度和加速度。
- 没有跟随误差、周期抖动、DC 同步或超差停机保护。
- 10 ms Windows/Python 软件循环不等价于确定性的同步运动周期。

在完成速度/加速度限制、跟随误差保护和真实设备验证前，CSP 不应作为生产运动能力。

### 混合总线互锁

- 模式和网卡切换检查 `welding_command_active`，但没有完整检查焊机反馈仍在焊接的
  `welding_active`。
- 模式切换没有要求数字输出 mask 为零。
- 进入 PRE-OP 并重建 FMMU/Sync Manager 会影响同总线其他设备，因此切换前必须确认
  所有设备已经进入安全状态。

### probe 与旧 HTTP HMI

- `ecat-probe` 名称和模块说明称为只读，但模块初始化和 PDO 配置选项会写 SDO。
- 模块化 I/O 未指定初始化时，probe 只给警告后继续映射，应在过程数据测试前失败关闭。
- `ecat-hmi` HTTP/SSE 入口与 Electron WebSocket 入口已经出现字段和单位差异。应删除
  废弃入口，或抽取共享命令 schema，避免维护两套控制协议。

## 本轮已完成记录（2026-08-25）

- WebSocket：命令必须为 JSON object；字段集合严格匹配；布尔值、字符串、整数和有限
  浮点数按类型校验；非法消息会返回可处理的 `ValueError`，不会让网关崩溃。
- CLI Jog：反馈状态字改读 TxPDO 输入；overlap 映射改用 overlap 发送；等待状态检查
  WKC/Fault；CLI 速度限制为 `±100000`。
- CiA 402：使能按 Ready to switch on、Switched on、Operation enabled 逐阶段等待；
  状态超时或 Fault 拒绝使能，运行中 Fault 清除使能请求并锁定 `ERROR`。
- 安全停止：过程数据交换串行化；停止、禁用和关闭会立即发送零运动、零 I/O、零焊机
  输出，禁用/关闭使用控制字 `0x0006`。
- 焊机：参数设置禁止携带起焊位；唯一 `start_welding` 路径要求通信就绪、机器人准备、
  无电源故障且故障码为零；运行中互锁丢失会清零完整 RxPDO。
- WKC：驱动和焊机严格匹配期望 WKC；部分 WKC 仅由 HAUTO profile 显式允许。
- Electron：使用随机令牌的本机 WebSocket shutdown 请求优雅退出 Python 后端，2.5 秒
  超时后才强制终止。
- Runtime L1：驱动、纯焊机和纯数字 I/O 循环新增 WKC 失配、部分 WKC、CiA 402 Fault、
  安全输出失败和线程异常退出测试；HAUTO 部分 WKC 继续限定为 profile 级例外。
- Runtime 异常会停止线程、锁定 `ERROR` 并记录原始异常；最终安全输出传输失败会进入
  可诊断状态。
- 验证结果：聚焦命令 `python -m pytest -q tests/test_runtime_interface.py tests/test_welding.py`
  为 `43 passed`；`python -m pytest -q` 为 `73 passed`；本批修改文件诊断无错误。
- 阶段二仍未完成：HTTP 入口统一、真实主站断线恢复、Electron 启动关闭测试和多客户端
  控制权仍待处理。

## 测试覆盖缺口

现有测试主要覆盖纯逻辑和模拟从站；Runtime 线程故障路径已加入 fake 主站测试。尚缺：

- Runtime 真实主站断线恢复、实机安全帧送达和过程输出停止响应。
- 焊机运行中通信丢失的完整循环测试和真实设备反馈验证。
- 严格 JSON 类型、非法消息形状、NaN/Infinity、越界值和超大消息。
- 多 WebSocket 客户端连接/断开时的控制权和停止策略。
- CLI Jog 的输入反馈和 overlap 过程数据调用。
- Electron 启动握手、后端异常退出、窗口关闭和打包产物烟雾测试。
- PDO 映射 readback、设备 Revision 选择和混合总线安全切换。
- 真实急停、STO、限位、安全门和进程强制退出后的停止响应。

## 分阶段开发规划

具体软件批次、Luna 提示词、交接格式和用户实机验收队列见
`LUNA_DEVELOPMENT_PLAN.md`。默认由 Luna 按 L1 至 L8 顺序执行，每次只处理一个批次；
Copilot 负责批次审查，用户负责真实设备和域控环境验收。

### 阶段一：安全闭环

目标：先修复所有 P0，不新增运动模式或焊机功能。

任务：

1. [x] 实现 CiA 402 状态转换等待与周期 Fault 监测。
2. [x] 统一安全停止路径，覆盖驱动、数字输出和焊机。
3. [x] 重构焊机参数准备/起焊，增加通信就绪和故障互锁。
4. [x] 焊机与驱动使用严格 WKC；HAUTO 部分 WKC 策略单独配置。
5. [x] Electron 改为优雅退出，超时后再强制终止。

代码与模拟测试已完成；以下验收标准仍需真实驱动、I/O、焊机和安全回路验证。

验收标准：

- 状态字未达到目标状态时绝不设置 `enabled=True`。
- 焊机未通信就绪、故障码非零或电源故障时，起焊位始终为零。
- 任一停止操作后，下一个可执行过程周期中驱动禁能、I/O 和焊机输出归零。
- WKC、状态字或反馈故障会进入锁定错误态，不能自动恢复运动。
- 窗口关闭日志能够证明安全帧发送和后端正常退出。

### 阶段二：协议与自动化测试

任务：

1. [x] 引入 WebSocket 命令的严格类型校验；HTTP 入口仍待统一。
2. [x] 修复 CLI Jog。
3. [x] 增加 Runtime 线程、断线、部分 WKC、故障和退出测试。
4. 明确单客户端控制权，观察客户端不能因断开而停止另一个控制客户端的命令。
5. 清理或统一旧 HTTP HMI。

验收标准：

- 非 object JSON、字符串布尔值、非有限数值和越界值全部被拒绝且服务保持运行。
- CLI Jog 从输入过程镜像读取状态字，并使用正确的 overlap API。
- 每个安全控制路径至少有一个失败测试和一个成功测试。
- `pytest`、Python compileall、JavaScript 语法检查全部通过。

### 阶段三：设备配置可信化

任务：

1. profile 增加 Revision/固件适用条件。
2. 配置后回读并逐项核对 PDO index/subindex/bit length/order。
3. CSP 加入速度、加速度、跟随误差和周期异常保护。
4. 混合总线切换前确认所有设备反馈安全、输出为零。

验收标准：

- PDO 任一对象、顺序、位宽或总长度不符即拒绝 SAFE-OP/OP。
- 未知 Revision 不自动套用已知 profile。
- CSP 超速、跟随误差或周期超限时立即执行安全停止。

### 阶段四：交付与实机验收

任务：

1. 完成 Python 后端随包、Electron builder、版本信息和日志归档。
2. 在干净 Windows/域控环境验证安装、启动、升级和卸载。
3. 按低速、短时原则验证 PP、混合总线、焊机和异常停止。
4. 验证外部急停、STO、限位、安全门和断电/断网场景。

验收标准：

- 安装包不依赖系统 Python 或开发目录。
- 后端启动失败会阻止控制界面进入可操作状态，并显示明确错误。
- 异常关闭、通信中断和硬件故障均有日志与实机停止结果。
- 真实安全链路验收完成前，不把软件停止描述为安全功能。

## 每次修改后的最低验证

```powershell
python -m pytest -q
python -m compileall -q src tests start_ecat_test.py
node --check src/dm3c_ecat/web/app.js
python -m pip check
git diff --check
```

涉及 Electron 发布时额外执行构建和安装产物烟雾测试；涉及 EtherCAT 行为时，自动化
测试不能替代真实从站、急停、STO、限位和机械安全验收。

## 新对话接续说明

新对话可直接使用以下请求：

> 先阅读 `PROJECT_REVIEW_AND_ROADMAP.md`、`DEVELOPMENT_STATE.md`、
> `LUNA_DEVELOPMENT_PLAN.md` 和当前 Git diff，从 L1 开始，每次只执行一个 Luna 批次。
> 坚持最小改动、修改后立即运行最窄测试，不回滚现有用户改动；完成后更新规划状态、
> 验证命令和实机边界，不自行开始下一批。

## 更新规则

- 已修复的问题移入“已完成记录”，保留修复文件、测试和验收结果。
- 真实硬件结果优先于推断；记录设备身份、Revision、PDO readback、WKC 和测试条件。
- 自动化通过只能证明软件路径，不能将未完成的实机项标记为完成。
- 新发现必须注明“已复现”“代码风险”或“待实机确认”，避免混淆证据等级。