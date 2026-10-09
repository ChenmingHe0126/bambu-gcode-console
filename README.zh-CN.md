# Bambu G-code Console(中文说明)

一个给 Bambu Lab 打印机用的、Printrun 风格的小控制台,为**教学**而写:手动点动喷头、设温度、一行一行地输入 G-code 看机器照做、预览 G-code 文件的走刀路径、逐行流式发送,或者一键传进 SD 卡开始打印。

- 在 **Bambu Lab A1**(固件 01.08.01.00,2026 年 10 月)上实测。其他 Bambu 机型协议相同,但没有测过。
- Windows / macOS / Linux 都能跑。只需要 Python 3.9+ 和一个库(`paho-mqtt`)。
- 功能参考 [Printrun / Pronterface](https://github.com/kliment/Printrun)。与 Bambu Lab 无关。
- 由 Chenming He 使用 [Claude Code](https://claude.com/claude-code)(agentic coding,即让 AI 代理完成编码)为一门大学 3D 打印工作坊课程编写。MIT 许可证。

[English README → README.md](README.md)

> **安全提示。** 这个工具会驱动一台喷嘴 220 °C、热床 65 °C 的机器。请守在打印机旁,手不要伸进打印空间,知道电源开关在哪。风险自负,见文末[免责声明](#免责声明)。

## 为什么会有这个项目

老式打印机(Ender、Prusa、Anycubic……)有一个 USB 口,行为像一根串口线:Printrun 这类程序发一行 G-code,收到 `ok`,再发下一行。课堂上这太好用了——学生敲 `G1 X100`,喷头就走 100 mm。

Bambu A1 没有这个口。但在触摸屏上打开**仅局域网模式(LAN-only Mode)**和**开发者模式(Developer Mode)**之后,它会在局域网里开一个小服务:通过 **MQTT**(一种"往某个主题发一条消息"的轻量协议,物联网设备常用)接收指令,通过 **FTPS**(经典的 FTP 文件传输协议外面套一层 TLS 加密)接收文件。本项目就是一座薄薄的桥,把这两个通道重新变回 Printrun 的体验:浏览器里的网页和你电脑上的一个小 Python 程序说话,Python 程序再和打印机说话。

## 功能

| 网页里 | 作用 |
|---|---|
| **Jog 点动面板** | X / Y / Z 方向键,步进 0.1 / 1 / 10 / 50 mm,外加归零(`G28`)。 |
| **温度** | 喷嘴和热床实时读数,一键预设(喷嘴 150 / 200 / 220 °C,热床 45 / 60 °C)和关闭。 |
| **G-code 控制台** | 输入任意 G-code 回车,看到打印机回的 `ok` / `FAILED`。上下箭头翻历史。 |
| **文件预览** | 拖入 `.gcode`,页面里画出 XY 走刀图(绿色 = 挤出,灰色 = 空移),带层滑块。支持 `G2`/`G3` 圆弧和 CRLF / CR / LF 三种换行。 |
| **Stream 流式发送** | 把加载的文件**一行一行**发出去、每行等回复——就是 Printrun 的方式。可暂停 / 停止,有进度条,预览图上高亮已发送部分。适合几百行的演示文件。 |
| **Print now 一键打印** | 把文件传进 SD 卡并作为正常打印任务启动,打印机自己全速跑。裸 `.gcode` 会被自动包装成固件要求的格式(见[一键打印的原理](#一键打印的原理));Bambu Studio 导出的 `.gcode.3mf` 原样发送。 |
| **Send to SD 发送到 SD 卡** | 只是把文件原封不动拷进 SD 卡(加载文件时默认自动执行)。之后在打印机屏幕上启动,或在 SD 列表里选中它按 Print。 |
| **SD 卡文件** | 列表、打印、删除。从列表打印裸 `.gcode` 会即时转换。 |
| **打印机控制** | 对当前正在打印的任务:暂停 / 继续 / 停止。 |

还有一个命令行版本 `bambu_console.py`,在终端里提供同样的逐行控制台。

## 你需要什么

1. 一台和电脑在同一个 Wi-Fi / 局域网里的 Bambu Lab 打印机。A1 实测;A1 mini、P1、X1 系列指令相同但未测试。
2. 打印机上开启 **LAN-only 模式 + 开发者模式**(步骤见下)。固件 01.05.00.00 起,不开开发者模式打印机会拒绝一切控制指令。
3. Python 3.9 或更新。Windows 用户去 [python.org](https://www.python.org/downloads/) 安装,勾选 *Add Python to PATH*。
4. 已经装好耗材,并在打印机自己的菜单里至少调平过一次热床——示例文件复用保存的调平网格。

## 打印机设置(只做一次)

1. 触摸屏:**设置 → 网络(WLAN)→ 仅局域网模式 → 开**。打印机会要求重启,让它重启。
2. 回到同一菜单,打开**开发者模式**。(Bambu 的原话是它"开放 MQTT 通道、直播流和 FTP",并关闭指令校验。此模式下 Bambu 不提供售后支持。)
3. 记下 LAN-only 页面上显示的**访问码(Access Code)**,8 位。如果显示全 0,把 LAN-only 模式关掉再打开。
4. 打印机 IP 在同一页面;序列号在 *设置 → 设备信息*。通常都不用管:控制台会自己找到打印机。

LAN-only 模式下失去的东西:Bambu Handy 手机 App、云端监控、OTA 固件升级(改用 SD 卡升级)。两个开关跨固件升级保留。

## 安装与运行

```bash
git clone https://github.com/ChenmingHe0126/bambu-gcode-console.git
cd bambu-gcode-console
```

(或在 GitHub 页面点 **Code → Download ZIP** 解压。)

- **Windows:** 双击 `start_console.bat`。
- **macOS / Linux:** 双击 `start_console.sh`(macOS 第一次要右键 → 打开),或在终端里 `./start_console.sh`。

启动器会自动安装缺失的 `paho-mqtt`,在 `http://127.0.0.1:8347` 起桥并打开浏览器。输入访问码,按 **Connect**。访问码、IP、序列号会记在 `~/.bambu-gcode-console.json`,下次只需一键。

关掉黑色终端窗口即停止。同一时间只能开一个控制台(占用 8347 端口)。

手动启动及参数:

```bash
python bambu_web.py --code 12345678            # 第一次;之后会记住
python bambu_web.py --ip 192.168.1.50          # 自动发现找不到打印机时
python bambu_web.py --host 0.0.0.0 --port 8347 # 让学生手机也能打开页面(先读安全提示)
python bambu_console.py                        # 终端版
```

找打印机的顺序:你给的 `--ip`(探测 8883 端口确认可达)→ 上次成功的地址 → 监听打印机的 SSDP 广播(UDP 2021/1990,最多 20 秒)。校园网和办公网常常过滤广播,Windows 在标记为"公用"的网络下也会拦截;发现失败时,去打印机屏幕上看 IP,在 Connect 面板里填一次即可。

## 快速上手:画一个方形

仓库自带可直接运行的文件,在 [`examples/`](examples/) 里。`examples/square.gcode` 用 PLA 画一个 60 × 60 mm 的方形轮廓。把它拖到页面上,看预览,然后要么 **Stream**(慢的、教学的方式:可以在行与行之间暂停),要么按 **Print now**。

这个文件的核心只有这几行:

```gcode
G90                          ; 绝对 XY 坐标
M83                          ; 相对挤出:每个 E 都是"这一步要推多少耗材"
G0 F18000 X98 Y98 Z1.0       ; 空移到第一个角,离床 1 mm(不出料)
G1 F1200 Z0.2                ; 下降到层高
G1 F600 E0.8                 ; 预挤出:先推 0.8 mm 耗材,线条才能从角上就开始
G1 F1200 X158 Y98  E2.22     ; 第 1 边 -> 右(60 mm)
G1 F1200 X158 Y158 E2.22     ; 第 2 边 -> 后
G1 F1200 X98  Y158 E2.22     ; 第 3 边 -> 左
G1 F1200 X98  Y98  E2.22     ; 第 4 边 -> 前,方形闭合
G1 F600 E-0.8                ; 回抽,抬起时喷嘴不流口水
G0 F1200 Z5                  ; 抬离打印件
```

逐行解读:

- `G90` / `M83` 定规矩:位置用绝对坐标,挤出量用相对值。
- `G0` 是空移(不出料),`G1` 是工作移动。`F18000` 表示 18 000 mm/min = 300 mm/s;`F1200` 是 20 mm/s——故意放慢,让人看得清。
- `E2.22` 是画一条 60 mm 边要推的耗材长度。它来自几何:一条 0.45 mm 宽、0.2 mm 高的线,截面积 0.09 mm²;1.75 mm 耗材截面积 2.405 mm²。所以每毫米线条需要 0.09 / 2.405 ≈ 0.037 mm 耗材,60 mm 就是 2.22 mm。改线宽或层高,这个数就变——这就是切片软件的全部秘密。
- 开头 0.8 mm 的预挤出和结尾的回抽决定了角是否干净。没有预挤出,第一厘米是空的,因为喷头空移之后腔里没料。

完整文件把这段"正文"夹在 `A1_start_minimal.gcode`(加热、归零、清料、沿左边画一条引导线)和 `A1_end_minimal.gcode`(关加热、抬升、把热床推到前面)之间。两个都很短、有注释——读一遍,再按你的耗材改温度(`M140 S65`、`M109 S220`)。

| 文件 | 内容 |
|---|---|
| `examples/square.gcode` | 上面的方形,含完整起止段。约 90 行。 |
| `examples/star.gcode` | 用 Rhino/Grasshopper 生成的五角星,单层。约 2 400 行,很适合演示 Stream。 |
| `examples/hello_world.gcode` | 用耗材写出的 "Hello World",单层。约 4 000 行。 |
| `examples/A1_start_minimal.gcode` | A1 的最小起始段(不调平,复用保存的网格)。 |
| `examples/A1_end_minimal.gcode` | 最小结束段。 |

自己写:从 `square.gcode` 出发,保留起止段,替换中间。坐标保持在 X 0–256、Y 0–256(A1 床面),第一层用 `Z0.2`,任何移动前都要先 `G28`——起始段已经帮你做了。

## 课堂上怎么用

- **先归零。** 点动面板和控制台发的是原始移动指令;`G28` 跑之前打印机不知道喷头在哪。每节课一开始按 ⌂(或输入 `G28`)。
- **Jog** 用来讲 X、Y、Z 在"动床式"机器上分别是什么:X 动喷头,Y 动热床,Z 动横梁。
- **控制台**是 Printrun 时刻:`G1 X128 Y128 F6000`、`M104 S150`、`G1 Z50`。每行单独发出,回复显示在下面。
- **Stream** 一个短文件,说明打印不过是这些行重复成千上万次。中途暂停,指着预览图讲,再继续。
- **Print now** 做真打。打印机自己全速跑、自己显示进度;页面显示状态和温度,提供暂停 / 停止。

## 工作原理

```
浏览器 (web_ui.html)  ──HTTP, localhost:8347──▶  bambu_web.py  ──MQTT over TLS, 端口 8883──▶  打印机
                                                       └───────FTPS, 端口 990──────────────▶  SD 卡
```

- **MQTT** 负责控制。每条指令是一个 JSON 消息,发布到 `device/<序列号>/request`;打印机在 `device/<序列号>/report` 上回复。一行 G-code 就是 `{"print":{"command":"gcode_line","param":"G28\n"}}`。用户名永远是 `bblp`,密码是访问码。
- **回复只是"受理"。** 打印机说的是"收到了",不是"做完了",而且从不返回指令的输出——`M114`(报告位置)、`M503`(报告设置)什么都不会回。温度、状态、进度靠周期性的状态推送。这是固件的性质,不是本工具的限制。
- **FTPS** 负责文件。上传落在 SD 卡根目录。

### 一键打印的原理

固件的"开始打印"命令(`project_file`)只执行 Bambu Studio 结构的 `.gcode.3mf`——一个 zip,里面有 G-code 加十几个元数据文件。SD 卡上的裸 `.gcode` 可以在触摸屏上启动,但不能远程启动;手工拼的 3mf 会被静默忽略或卡在"准备中"(我们试了很久)。

所以 **Print now** 把你的 G-code 注入一个真品骨架 `a1_skeleton.gcode.3mf`——用 Bambu Studio 的命令行切片器、用 A1 的默认配置切一个 10 mm 立方体得到的:保留骨架的头部注释块,可执行块整个换成你的文件,重算 MD5 校验文件,上传并启动。本身已带 Bambu Studio 头部的文件(Studio 导出的任何文件)原样使用。A1 上实测:几秒内 `IDLE → PREPARE → RUNNING`。

协议知识来自:[OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI)、[ha-bambulab](https://github.com/greghesp/ha-bambulab)、[bambulabs_api](https://github.com/acse-ci223/bambulabs_api)、[bambuddy](https://github.com/maziggy/bambuddy)、[open-bamboo-networking](https://github.com/ClusterM/open-bamboo-networking)。

## 排障

| 现象 | 原因与处理 |
|---|---|
| Connect 面板显示 *connection refused* | 访问码错了,或开发者模式没开。去 LAN-only 页面重新读码;切换过模式后重启打印机。 |
| *Printer not found* | 广播发现被拦(校园网、Windows"公用"网络)。把打印机屏幕上的 IP 填进 Connect 面板。同时关掉 Bambu Studio / OrcaSlicer——它们占用发现端口。 |
| 指令回 `ok` 但什么都不发生;屏幕显示 *device is busy* | 固件的任务管理器卡死了。给打印机断电重启。 |
| 打印开始了,但第一条线的前一厘米是空的 | 起始段之后没有预挤出。在第一条画线前加 `G1 E0.8 F600`(见方形示例)。 |
| *FTP upload failed: timed out* | 打印机的 FTP 服务偶尔关连接关得晚。上传通常其实已经完成;在 SD 列表里按 Refresh。 |
| *port 8347 unavailable* | 另一个控制台窗口还开着。关掉它(或改用 `--port 8350`)。 |
| 按了 Stop 后打印机显示 `FAILED` | 那只是 Bambu 对"已取消"的叫法。下一次打印正常启动。 |
| 非 A1 机型远程打印不启动 | 骨架是给 A1 切的。在 Bambu Studio 里给你的机型切任意一个小物体,导出为 `.gcode.3mf`,替换掉 `a1_skeleton.gcode.3mf`。 |

## 限制与注意

- 没有位置回读(`M114`)——见上文。需要讲回复格式的话,用 OctoPrint 的虚拟打印机之类的模拟器。
- `Stream` 每行等受理回执,但打印机会缓冲移动,所以"暂停"要晚几步才生效。它是用来演示的,不是用来打 5 万行文件的。
- `--host 0.0.0.0` 会把页面**无密码**地发布给网络里所有人。谁打开都能加热、移动打印机。只在可信的教室网络里用,用完停掉桥。
- 访问码以明文存在 `~/.bambu-gcode-console.json`(它只是局域网设备密码,但请相应对待;共用电脑上用完删掉)。
- 对打印机的 TLS 证书校验是关闭的(它用 Bambu 的私有 CA)。在你自己掌控的局域网里没问题。

## 免责声明

这是一位老师和一个 AI 结对编程伙伴用几个晚上写出来的课堂软件。按"现状"提供,不附带任何形式的保证。它使用的是 Bambu Lab 未公开、未文档化、随时可能更改的接口;在开发者模式下运行打印机不在 Bambu 的售后范围内。本工具连接期间打印机做的任何事都由你自己负责。与 Bambu Lab 无关,亦未获其认可。

## 许可证

[MIT](LICENSE) © 2026 Chenming He
