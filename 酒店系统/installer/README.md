# Solid 品牌图标资产

| 文件 | 说明 |
|:---|:---|
| `assets/brand/icon_master.png` | 用户交付母版（透明底玻璃图标）；存在时 build 优先使用 |
| `assets/brand/solid_emblem.svg` | 母版：仅金色图形（透明底） |
| `assets/brand/solid_icon.svg` | 母版：完整 App 图标 |
| `assets/app_icon.png` | 512px 窗口/任务栏（build 生成） |
| `assets/app_icon.ico` | 桌面/EXE/安装包多尺寸 ICO |
| `assets/mark.png` | 侧栏/闪屏/登录 192px |
| `assets/mark_sm.png` | 顶栏 28px 显示用 @2x |
| `assets/logo_full.png` | 横排 Mark+字标 |

## 重新生成

```bash
cd 酒店系统
python tools/build_app_icon.py
```

脚本会同步复制到 `采集器/assets/`。

## 打包引用

- `Solid_onefile.spec` / `Solid.spec` → `icon='assets/app_icon.ico'`
- `installer/SolidInstaller.iss` → `SetupIconFile=..\assets\app_icon.ico`
- 应用内统一经 `brand_assets.py` 加载 `mark.png` / `mark_sm.png`
