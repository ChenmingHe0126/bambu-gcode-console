# bambu-gcode-console

给 Bambu Lab A1(及其他 Bambu 打印机)**逐行发送 G-code** 的极简命令行控制台,用于课堂演示 —— 类似 Printrun/Pronterface 里"一句一句执行 G-code"的体验。

A minimal Pronterface-style line-by-line G-code console for Bambu Lab printers over LAN MQTT, made for classroom demos.

## 背景

Bambu A1 没有 USB Type-B 串口,不能像 Marlin 打印机那样用 Pronterface 直连。但它在**局域网模式**下开放了一个 MQTT 接口,支持 `gcode_line` 指令 —— 每条消息执行一行(或几行)G-code。本工具就是包了一层交互式终端的 MQTT 客户端。

## 打印机端设置(必做)

固件 01.05.00.00(2025 年 6 月)起,Bambu 加入了 Authorization Control,不开开发者模式就会拒绝一切本地控制指令。步骤:

1. 打印机屏幕:**设置 → 网络(WLAN)→ 开启「仅局域网模式 (LAN-only Mode)」**,首次开启后重启打印机
2. 回到同一菜单,开启 **「开发者模式 (Developer Mode)」**
3. 记下 LAN-only 页面显示的**访问码 (Access Code)**(如果是全 0,把 LAN-only 关了再开一次)
4. 序列号在 **设置 → 设备信息** 或机身贴纸上;IP 在网络设置里能看到

代价(上课用基本无所谓,但要知道):

- 打印机与 Bambu 云断开,**Handy App 和远程监控不可用**
- 固件升级要用 SD 卡/U 盘离线升,或临时切回云模式
- Bambu 官方对开发者模式不提供售后支持
- 这两个开关会跨固件升级保留,不用每次重设

## 安装与使用

```bash
pip install "paho-mqtt>=2.0"
```

**最省事的方式:双击 `start_console.bat`** —— 浏览器会自动打开控制台页面,访问码/IP 填一次就记住(存在 `~/.bambu-gcode-console.json`),之后每次只要点一下 Connect。关掉黑色命令行窗口即停止服务。界面为英文(英文授课用)。

> 为什么不能直接双击 html?浏览器没法直接说打印机的 MQTT 协议,中间必须有个本地 Python 桥,`.bat` 就是一键把桥拉起来再开浏览器。

两个入口都支持**自动发现**:`--ip`/`--serial` 可以不填,程序按「显式参数(先探测可达)→ 上次成功的缓存地址(`~/.bambu-gcode-console.json`)→ SSDP 广播监听」的顺序自己找打印机。首次使用或换网络时建议手动传一次 `--ip`,之后就会记住。注意:校园/企业 WiFi 常过滤广播,Windows 在 Public 网络下也拦入站 UDP——SSDP 收不到时去打印机屏幕 设置→网络 查 IP 即可;Bambu Studio/OrcaSlicer 开着会占用发现端口。

**方式一:命令行控制台**(最像 Pronterface 的串口终端)

```bash
python bambu_console.py --code 12345678
```

进入 `gcode>` 提示符后逐行输入即可。内置命令:`status`(看温度/状态)、`help`、`exit`。

**方式二:网页控制台**(课堂投影推荐,带 Jog 按钮)

```bash
python bambu_web.py --code 12345678
```

然后浏览器打开 <http://127.0.0.1:8347>:

- **Jog 面板**:X/Y/Z 方向键 + 归零,步进 0.1/1/10/50 mm 可选
- **温度卡片**:喷嘴/热床实时温度 + 一键预设(150/200/220 等)
- **G-code 控制台**:逐行输入,每条显示 `ok`/`FAILED` 回执,↑↓ 翻历史
- **Print now(一键打印)**:拖入 .gcode → 自动注入 Bambu Studio 真品骨架 `a1_skeleton.gcode.3mf`(Studio CLI 切的 10 mm 立方体,保留其 HEADER/CONFIG 注释块,EXECUTABLE 块整个换成你的 gcode,重算 md5)→ FTPS 上传 → MQTT `project_file` 启动。拖入 Studio 导出的 `.gcode.3mf` 则原样上传后启动。已在 A1 固件 01.08.01.00 上实测 IDLE → PREPARE → RUNNING
- **Send to SD**(默认拖入即自动发送):把文件**原封不动**传进 SD 卡根目录,然后在打印机屏幕上选中它开始打印。裸 `.gcode` 屏幕能启动、但远程 `project_file` 启动不了(固件只执行 Studio 结构的 3mf,`gcode_file` 命令直接返回 fail)——所以远程要用 Print now
- **G-code 文件预览**:拖入文本 .gcode 时,浏览器内渲染 XY 刀路(挤出/空移分色、层滑块、支持 G2/G3 圆弧)
- **Stream**:逐行代发(Printrun 式),可暂停/停止,进度条 + 预览图上高亮已执行部分;适合几百行的课堂演示文件
- 状态栏实时显示打印机状态和 WiFi 信号

> 踩坑记录:手工拼的最小 3mf(哪怕带全套元数据)固件会"受理但不执行"或在屏幕上卡 preparing;打印机屏幕启动裸 gcode 时会在 `/cache` 写一个 `.bbl` JSON 任务描述,但复刻它的字段也没用——只有 Studio 真品骨架能过。另外打印机若处于卡死的 "device is busy" 状态,所有启动命令都会被静默丢弃,重启打印机即可。

加 `--host 0.0.0.0` 可以让同一局域网里的学生用手机/平板访问(注意:谁都能控制,下课记得关)。

## 课堂演示参考序列

```gcode
G28              ; 全部归零(必须先做)
G90              ; 绝对坐标
G1 X128 Y128 F6000   ; 移到台面中心
G1 Z50 F1200     ; 抬高 50mm
M104 S150        ; 喷嘴加热到 150°C(演示够用,不出丝、不烫伤耗材)
M104 S0          ; 关加热
G1 X10 Y10 F12000    ; 快速移动对比进给速度
```

A1 行程约 256×256×256mm,X 是横梁、Y 是热床(bed-slinger),课堂上正好用来讲运动学。

## 已知限制(和串口 Pronterface 的差别)

- **只有"受理"回执,没有回读**:每条指令打印机会回 `ok`/`FAILED`,表示"收到并接受",**不代表动作执行完毕**;`M114`(查坐标)、`M503`(查参数)这类查询指令不会返回内容。温度等状态靠打印机周期推送(`status` 命令查看)。
- **不能整份发 .gcode 文件**:`gcode_line` 只适合逐行/小段演示。要跑完整作业请正常用 Bambu Studio / OrcaSlicer 切片打印。
- **TLS 校验默认关闭**:打印机证书由 Bambu 私有 CA 签发,脚本在局域网内跳过校验。要严格校验可信任 [OpenBambuAPI 的 ca_cert.pem](https://github.com/Doridian/OpenBambuAPI/blob/main/examples/ca_cert.pem) 并改用 `tls_set()`。
- 固件是 Marlin 方言:常用指令(G0/G1/G28/G90/G91/G92/M104/M109/M140/M400 等)都支持,个别 Bambu 私有 M-code 行为不同。

## 其他可选方案

| 方案 | 适合场景 |
|------|----------|
| [OctoPrint-BambuPrinter](https://github.com/jneilliii/OctoPrint-BambuPrinter) 插件(装 **0.1.8rc 预发布版**,别用 0.1.7) | 想要完整 Web 界面 + Terminal 标签页,原理同样是 MQTT 转发 |
| OctoPrint 自带 [Virtual Printer](https://docs.octoprint.org/en/main/bundledplugins/virtual_printer.html) | **零硬件**课堂演示:学生输 G-code,虚拟固件回 ok/M105/M114,协议教学最真实 |
| [ncviewer.com](https://ncviewer.com) | 浏览器里改一行 G-code 立刻看三维刀路,配合投影很直观 |
| 修好 Anycubic Kobra:第三方 Vyper/Kobra 兼容一体式热端(24V 40W,约 $15–30) | 换上就恢复真·USB 串口 Pronterface;串口在主板上,和热端无关 |

## 协议参考

- [OpenBambuAPI — MQTT 文档](https://github.com/Doridian/OpenBambuAPI/blob/main/mqtt.md)(`gcode_line` 报文格式出处)
- [Bambu Wiki — Enable Developer Mode](https://wiki.bambulab.com/en/knowledge-sharing/enable-developer-mode)
- [SimplyPrint — LAN-only Mode & Developer Mode 开启教程](https://help.simplyprint.io/en/article/bambu-lab-lan-only-mode-and-developer-mode-how-to-enable-xa0hch/)

## License

MIT
