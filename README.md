# ioslocgo

iOS 模拟定位命令行工具，通过 Apple 官方调试通道（DVT）下发坐标。**无需越狱**，在 Windows / macOS / Linux 上均可使用。

相比直接使用 [pymobiledevice3](https://github.com/doronz88/pymobiledevice3)，本工具补齐了三件实际使用中最费时间的事：

- **国内坐标系自动转换**。从高德、腾讯、百度地图复制的坐标直接下发会偏移 500 米以上，足以让打卡、围栏判定失败。工具内置 GCJ-02 / BD-09 到 WGS-84 的转换。
- **环境自检**。把「Apple 驱动服务是否运行、设备是否配对、开发者模式是否开启」逐项检查，失败时给出中文修复指引，而不是抛出一屏 Python 异常栈。
- **坐标保持**。模拟定位依赖 DVT 通道持续存在，下发完就退出等于没改。工具明确以前台进程持有通道，并说明退出后果。

在 iPhone 15 / iOS 27.0 / Windows 11 上实测通过。

## 工作原理与固有限制

模拟定位使用的是 Xcode 调试 iOS 设备的同一条通道：电脑作为调试主机，通过 USB 建立 RemoteXPC 隧道，再经 DVT 的 LocationSimulation 通道下发坐标。

这决定了几条无法绕开的限制：

- **必须连着数据线**。隧道断开（拔线、关闭终端、设备重启）后定位立即恢复真实位置。
- **必须开启开发者模式**。iOS 16 起的强制要求。
- **iOS 端做不出独立 App**。沙盒内既访问不到 usbmuxd，也无权修改系统定位状态。App Store 上声称能改定位的应用，实际都改不了系统级定位。
- **iOS 17.4 以下需要管理员权限**。更高版本可用免提权的用户态隧道，本工具默认走这条路径。

## 安装

需要 Python 3.10 以上。

```bash
pip install -U pymobiledevice3
pip install ioslocgo
```

或从源码安装：

```bash
git clone https://github.com/wang33550/ioslocgo
cd ioslocgo
pip install -e .
```

Windows 还需要 Apple 的 USB 驱动，安装 [Apple Devices](https://apps.microsoft.com/detail/9np83lwlpz9k)（Microsoft Store）或 iTunes 即可，无需运行它们。

> **Windows 安装提示**：pymobiledevice3 的 `lzfse` 与 `pyimg4` 依赖没有 Windows 预编译包，缺少 C 编译器时会安装失败。这两个只用于固件镜像解析，与模拟定位无关。可用 `pip install --no-deps pymobiledevice3` 跳过，或先装 [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)。

## 使用

### 第一步，环境自检

```bash
ioslocgo doctor
```

```
环境自检
------------------------------------------------------------
[  OK  ] Apple 驱动服务：运行中
[  OK  ] 设备连接：iPhone / iPhone15,4 / iOS 27.0
[  OK  ] 开发者模式：已开启
------------------------------------------------------------
```

任何一项失败都会附带具体修复步骤。

### 第二步，下发坐标

```bash
ioslocgo set 30.36165 119.973495 --source gcj02
```

```
坐标系转换：GCJ02 30.361650, 119.973495  ->  WGS-84 30.364185, 119.968880（偏移 525 米）

已下发模拟定位：30.364185, 119.968880
按 Ctrl+C 停止。保持本进程运行，退出后设备将恢复真实定位。
```

隧道在进程内自动建立，**无需管理员权限**，也不必手动复制 RSD 地址。工具使用
pymobiledevice3 的纯 Python 用户态网络栈，不创建系统级虚拟网卡。

`--source` 的取值：

| 值 | 坐标来源 |
|---|---|
| `gcj02` | 高德、腾讯、Apple 地图中国区（默认） |
| `bd09` | 百度地图 |
| `wgs84` | Google Maps 国际版、GPX 文件、GNSS 设备 |

不确定来源时，先用 `gcj02` 下发，在手机地图上看落点。若位置偏向东北方数百米，说明来源其实是 WGS-84，改用 `--source wgs84`。

### iOS 17.0 - 17.3 或自动隧道失败时

这些版本缺少 CoreDeviceProxy，无法使用用户态隧道，需要手动建立提权隧道。
Windows 下在 PowerShell 执行：

```powershell
Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-Command','python -m pymobiledevice3 lockdown start-tunnel'
```

macOS / Linux：

```bash
sudo python -m pymobiledevice3 lockdown start-tunnel
```

窗口中会输出 `RSD Address` 与 `RSD Port`，**保持该窗口不要关闭**，然后：

```bash
ioslocgo set 30.36165 119.973495 --source gcj02 --rsd "fd13:d8fb:3cd1::1 61051"
```

### 其他命令

```bash
# 仅转换坐标，不连接设备
ioslocgo convert 30.36165 119.973495 --source gcj02

# 按 GPX 轨迹连续移动
ioslocgo play route.gpx

# 清除模拟定位
ioslocgo clear

# 尝试启用开发者模式
ioslocgo enable-devmode
```

## 开发者模式与锁屏密码

`enable-devmode` 在设备设有锁屏密码时会失败，报 `Cannot enable developer-mode when passcode is set`。这是 Apple 的限制，两种处理方式：

1. **手机上手动开启**（推荐）。先执行一次 `ioslocgo enable-devmode` 让菜单项显示出来，然后在手机上：设置 → 隐私与安全性 → 拉到底部 → 开发者模式 → 打开 → 重启 → 解锁后再确认一次。

2. **临时关闭锁屏密码**后执行 `ioslocgo enable-devmode`，成功后再设回密码。开发者模式一旦开启，重设密码不会关闭它。

   注意：关闭锁屏密码会**移除 Apple Pay 中的全部银行卡**，需要重新添加。关闭期间设备处于无保护状态。

## 常见问题

**定位没变，或者变了又立刻回去了**

`ioslocgo set` 进程退出了。该进程必须保持在前台运行，坐标才持续生效。

**位置偏了几百米**

`--source` 选错了。参见上文的坐标来源对照表。

**`无法连接 usbmuxd`**

Windows 上是 Apple Mobile Device Service 未运行。以管理员身份执行：

```powershell
net start "Apple Mobile Device Service"
```

若服务是在插线之后才启动的，需要重新插拔数据线让它重新枚举设备。

**`未检测到设备`**

重新插拔数据线，保持手机解锁，在弹出的「信任此电脑」上点信任并输入密码。

**微信小程序里定位没生效**

`wx.getLocation` 读取的是系统 CoreLocation，原理上应当拿到模拟值。但部分应用会额外采集 WiFi SSID、基站信息做交叉校验 —— GPS 坐标变了而周围 WiFi 环境没变，这种比对可以发现异常。这属于对方服务端策略，工具层面无法处理。

## 使用边界

本工具面向的是开发测试、隐私保护、地理围栏功能验证等场景。

请注意：用于考勤打卡造假可能违反劳动合同并造成用人单位损失；用于游戏可能违反服务协议导致封号。**向不特定人群销售此类工具与自己使用的法律定性完全不同**，涉嫌《刑法》第 285 条第三款等罪名，本项目不鼓励也不支持任何形式的转售。使用后果由使用者自行承担。

## 许可证

GPL-3.0-or-later。

本项目依赖 [pymobiledevice3](https://github.com/doronz88/pymobiledevice3)（GPL-3.0-or-later），协议要求衍生作品保持相同许可证。**如果你打包分发本项目（含 PyInstaller 打包），必须一并提供完整源码。**

## 致谢

全部底层协议实现来自 [doronz88/pymobiledevice3](https://github.com/doronz88/pymobiledevice3)。
