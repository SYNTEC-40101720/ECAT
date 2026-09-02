# ESI 目录说明

本目录分为两个子目录，用于隔离"汇入的原始 ESI"和"程序实际使用的 ESI"：

```
ESI/
├── incoming/    # 汇入区：放入新获取的 ESI 文件，不影响程序运行
├── active/      # 使用区：仅包含当前程序代码依赖的 ESI 文件
└── README.md    # 本说明
```

## incoming/ — 汇入区

将新获取或待评估的 ESI 文件放在这里。此目录中的文件：

- **不会**被 PyInstaller 打包（`backend.spec` 只引用 `ESI/active`）
- **不会**被 electron-builder 打包（`electron-builder.yml` 只引用 `ESI/active`）
- **不会**影响程序运行或测试

## active/ — 使用区

仅包含当前程序实际依赖的 ESI 文件。当需要在程序中支持新设备时：

1. 将 ESI 文件从 `incoming/` 复制到 `active/`（可保持子目录结构）
2. 在 `src/dm3c_ecat/device_profiles.py` 中添加对应的 profile
3. 在 `tests/` 中添加匹配验证测试
4. 更新 `DEVELOPMENT_STATE.md` 中该设备的 ESI 路径

## 当前 active ESI 文件清单

| 厂商目录 | 文件 | 设备 | Vendor/Product |
|---|---|---|---|
| `凯福/` | `KF_EC2SS3V1.23.xml` | 凯福 SSD60N 驱动 | `0x024B`/`0x0215` |
| `雷赛/` | `DM3C-EC系列XML文件.XML` | 雷赛 DM3C-EC 驱动 | `0x4321`/`0x8600` |
| `HAUTO/` | `HAUTO_AX58100_DIO_IO_MAP_FIX.xml` | HAU TO AX58100 DIO | `0x00000001`/`0x00010200` |
| `实点-Solidot/` | `EcatTerminal-EC4_V4.04_BOOL.xml` | Solidot EC4-1616A | `0x00884443`/`0x00000004` |
| `DECOWELL/` | `DECOWELL_EX-1100_V1.9.8.xml` | DECOWELL EX-1100 | `0x00444543`/`0x00000001` |
| `麦格米特/` | `MegmeetESI260416.xml` | 麦格米特焊机 | `0xE000001B`/`0x00000036` |
| `麦格米特/` | `Megmeet_ENIP_protocol.md` | 麦格米特 ENIP 协议表 | — |
