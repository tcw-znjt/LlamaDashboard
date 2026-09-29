# LlamaDashboard

llama.cpp 运行时**设备 + 模型运行状况**的终端监控台(TUI),与你的 `run.bat` 启动脚本**配合且解耦**:
bat 单独跑一切如旧;dashboard 单独跑能附着到任何方式启动的 server;dashboard 退出对 server 零影响。

```
+---------------- 你的既有资产(零改动) ----------------+
| starts/<profile>/run.bat -> run.ps1 -> llama-server    |
|        :8688(端口回退)  --api-key     server-*.log    |
+---------------------------------------------------------+
        ^ 执行(启动/重启/换模型 = 原样跑你的 bat)
        |                          | tail + 解析
+-------+---------------- llamadashboard ----------------+
| 发现: 活进程 cmdline(port/key/-m/-c/--parallel/exe)   |
| 实时: GET /slots(默认开启) + 差分                      |
| 历史: server-<stamp>.log 结算块(prompt/eval/MTP/耗时) |
| 静态: GET /props(build_info)  设备: NVML               |
| 控制: s/r/m + 二次确认(默认取消)                       |
+---------------------------------------------------------+
```

## 快速开始

```bat
:: 1) 安装
pip install -r requirements.txt

:: 2) 启动监控台
python -m llamadashboard

:: 常用参数
python -m llamadashboard --starts-root <根1> --starts-root <根2> --interval 1 --dump session.jsonl


:: 3) **按 `m` 选模型**:方向键选中 → `Enter` → 确认浮层(默认取消,`Enter`/`y` 生效)→ dashboard 原样执行该
profile 的 `run.bat`(新控制台窗口,和你双击完全一致),等 1-2 分钟模型加载完,面板开始出数。

server 已经在跑(比如你双击过 `run.bat`,或是别的方式起的)也没关系:dashboard 会自动附着上去,
不需要先停再起,退出也不影响它。
```

### 新机器起步:照 starts 目录里的文件做一个 profile

一个 **profile** = starts 根下的一个目录,里面有 `run.bat` + `run.ps1`。dashboard 对它的唯一要求就是
"目录里有 `run.bat`",其余内容**原样执行、从不改写**。

最快做法 = 复制一个能跑的 profile 再改字段(**目录名就是 `m` 列表里显示的名字**):


`run.bat` —— 一般**不用改**(默认把两个受支持的 server 进程名都先杀掉,保证单实例;
只用某一个 build 时可以删掉另一段):

```bat
@echo off
rem 单实例:两个受支持的 server 进程名只要还在跑就先结束(切换 profile 时避免双开)
tasklist /fi "imagename eq llama-kvmem-server.exe" 2>nul | find /i "llama-kvmem-server" >nul
if not errorlevel 1 taskkill /f /im llama-kvmem-server.exe
tasklist /fi "imagename eq llama-server.exe" 2>nul | find /i "llama-server" >nul
if not errorlevel 1 taskkill /f /im llama-server.exe
echo Starting llama-server, model loads in 1-2 minutes...
echo A new log file (server-*.log) is created in this folder for each run.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
pause
```

`run.ps1` —— **要改的字段全在这**(下表按 gsq-iq3s 的真实内容标注):

| 字段 | 必改? | 说明 | 本机取值 |
|---|---|---|---|
| `$exe = "…"` | ★必改 | server 可执行文件绝对路径(受支持的 build 见下节);换 build 目录时改这里 | `E:\2-TCW\workspace\llama.cpp\b11223\llama-server.exe` |
| `-m "…"` | ★必改 | 要服务的 .gguf 绝对路径 | `D:\models\Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf` |
| `$port = …` / `foreach ($cand in …)` | 建议改 | 首选端口 + 回退端口列表(被占用或撞上 Hyper-V 排除段时自动回退) | `8088` / `9188, 10188, 18888, 28088` |
| `--api-key …` | 建议改 | bearer 密钥;dashboard 从活进程 cmdline 读,改了自动跟上 | `123456` |
| `-c …` / `--parallel …` | 按显存调 | 上下文 / 槽位数;太大装不进显存会起不来 | `65536` / `1` |
| `--alias …` | 可改 | `/v1/models` 显示的名字 | `Qwen3.8` |
| `--host …` | 可选 | 默认只本机;要局域网访问才写 `0.0.0.0` | `0.0.0.0` |
| `-ngl 99 -ngld 99 -fa on`、`--cache-type-k/v q8_0`、`--jinja` | 可选 | 卸载层数 / FlashAttention / KV 量化 / 模板,按模型增删 | 见现有 ps1 |
| `--spec-type draft-mtp` | 仅 `-mtp` 模型 | 非 mtp 模型加了会报错 | gsq-iq3s 有 |
| `$log = "$PSScriptRoot\server-$stamp.log"` | **别改** | 日志必须落在本 profile 目录,dashboard 的 `l` 日志与请求历史靠它 | 每次运行新建 `server-<时间戳>.log` |

顺利执行的检查清单:

1. `$exe` 路径存在,且与你要用的 build 对应(升级 llama.cpp 后记得换目录);
2. `-m` 的 gguf 存在,且 `-c`(上下文)× 模型规模装得进显存;
3. 首选端口和回退端口至少一个可用(全被占用会打印 `ERROR: no available port` 并退出);
4. 日志行保持 `$PSScriptRoot\server-$stamp.log` —— 改到别处 dashboard 就找不到历史;
5. 先双击 `run.bat` 自己跑通(能生成 `server-<时间戳>.log`、`/health` 返回 ok),再让 dashboard 调它。

### 支持监控的 server 进程

dashboard 按**活进程名**发现 server,目前识别两个 exe(大小写不敏感),谁在跑就监控谁
(同一时刻只监控一个,多个时取 pid 最小的那个):

| 进程名 | 说明 |
|---|---|
| `llama-server.exe` | 官方 llama.cpp server |
| `llama-kvmem-server.exe` | KV 显存管理改版 build —— 同一套监控:`/slots`、`/props`、日志通道全部适用 |

连接事实(端口、api-key、模型、ctx)一律从**活进程命令行**读,所以换 build 不需要改任何 dashboard 配置;
头部会显示当前被监控的进程名(如 `llama-kvmem-server`),待机态提示里也会列出这两个名字。

**对应的 `run.bat` 要按实际 exe 名杀旧进程**,否则"换模型/重启=单实例"的保证会失效(两处都要换):

```bat
rem profile 用 llama-server.exe
tasklist /fi "imagename eq llama-server.exe" 2>nul | find /i "llama-server" >nul
if not errorlevel 1 taskkill /f /im llama-server.exe

rem profile 用 llama-kvmem-server.exe(只换名字)
tasklist /fi "imagename eq llama-kvmem-server.exe" 2>nul | find /i "llama-kvmem-server" >nul
if not errorlevel 1 taskkill /f /im llama-kvmem-server.exe
```

`run.ps1` 里相应地 `$exe = "…\llama-kvmem-server.exe"`,其余参数照旧。

**profile 也可以不直接调 exe**,而是调一个包装脚本(如 KVMem 的 `start-bonsai.ps1`,最终起的仍是
`llama-kvmem-server.exe`)。这类包装脚本自己不落日志也不做端口回退,所以 `run.ps1` 仍要负责这两件事:
探测/回退端口(把选中的端口透传给包装脚本),以及把控制台输出留存成 `server-<时间戳>.log`。
dashboard 从 `run.ps1` 里读 `-m "….gguf"` / `-Model "….gguf"` 做模型匹配,包装脚本式 recipe 因此照样能被认领
(参考样例:`starts/qwen38-bonsai`)。

### 启动后选择 starts 目录下的模型

`m` 列表 = **每个 starts 根的直接子目录里含 `run.bat` 的目录** + 状态里记住的配方。所以"让模型出现在 `m` 里"只要两步:

1. 告诉 dashboard starts 根在哪(二选一):
   - 命令行:`python -m llamadashboard --starts-root E:\2-TCW\workspace\llama.cpp\starts`(可重复传多个根);
   - 界面:按 `m` → "浏览启动脚本目录…" → 选中配方目录 → "记住并切换",该位置写进状态文件,以后免参数;
2. 启动后按 `m` → 方向键选中目标 profile → `Enter` → 确认浮层(默认取消)→ 原样执行该目录的 `run.bat`。

`python -m llamadashboard --starts-root E:\2-TCW\workspace\llama.cpp\starts` 后按 `m`,
`gsq-iq3s` / `swift-iq3xs` / `swift15-iq3s` 三个 profile 都会出现。

列表为空时先查两点:根路径是不是 profile 的**上一级**(传 `...\starts\gsq-iq3s` 是错的),
以及该目录下是否真的有 `run.bat`;都没有就走上面的"浏览"路径。

## 键位

| 键 | 作用 | 说明 |
|---|---|---|
| `q` | 退出 | 只退 dashboard,server 不受影响 |
| `m` | 换模型 | 列出 starts 根下所有含 `run.bat` 的 profile + 记住的条目(带 `[记住]`),选中后确认,执行其 bat(由 bat 自带的 taskkill 保证单实例) |
| `s` | 停止 | 终止发现的 server PID(需确认) |
| `r` | 重启 | 有匹配 profile 时跑其 bat;attach 模式重放发现的命令行(需确认) |
| `l` | 日志 | 尾随当前 profile 最新 `server-*.log` 的浮层,任意关闭键退出 |
| `p` | 暂停 | 冻结采样与刷新(数据年龄同步冻结),再按恢复 |
| `c` | 清历史 | 清空会话内统计与历史表,日志文件不动 |
| `h` | 温度 | 展开/收起温度明细行(显存/分区温度,不支持则显示 —) |
| `Delete`/`d` | 删除记住的条目(仅 `m` 列表内) | 对带 `[记住]` 标记的条目生效,经确认后从本机状态移除;目录与 server 不受影响 |

**确认浮层默认取消**:除 `Enter`/`y` 外任何键都等于放弃操作,防误触杀服务。

## 零改动契约(它如何与 bat 配合又解耦)

1. dashboard **从不修改**任何 bat/ps1,也不要求新增配置文件;
2. 连接事实(port、api-key、模型、ctx)从**活进程命令行**读取 —— 你把端口改成 8688/8699,它自动跟上;
3. 实时数据走 `GET /slots`(llama-server 默认开启);历史数据走 ps1 本就在写的每运行日志;
4. 控制动作 = 原样执行你的 `run.bat`(新控制台窗口,和你双击完全一致);
5. 退出 dashboard = 仅退自己(`q` 后可用 `curl /health` 验证 server 存活)。

### 降级矩阵

| 能力 | bat 启动的 server | attach 到可匹配 profile | attach 到外来 server |
|---|---|---|---|
| 监控全面板 | ✓ | ✓ | ✓ |
| `l` 日志 | ✓ | ✓ | 显示不可用说明 |
| `r` 重启 | 跑其 bat | 跑其 bat | 重放发现的命令行 |
| `m` 换模型 | ✓ | ✓ | ✓(profile 列表始终可用) |

## 面板与字段来源

- **头部**:模型名/端口/运行时长 ← 进程 cmdline;ctx/槽位 ← cmdline 或 `/props`;进程名 ← 被监控的 exe;build ← `/props.build_info`
- **输出速度** ← 每次重绘的实时值(上一 tick → 本 tick 的 `n_decoded` 增量 ÷ 实际间隔);无进展的 tick 保留上一值、不归零
- **输入速度** ← prompt 阶段同法实时值;阶段结束(进入 decode 或请求结束)后**冻结**,直到下一次 prompt 阶段开始,不回落到上一请求
- **运行均值** ← **服务自身报告的 avg speed**。取值顺序:`/metrics` 的 `llamacpp:tokens_predicted_second` / `llamacpp:prompt_tokens_second`(仅当该 build 开启该端点),否则用**日志结算块**里与自带 web 界面同源的每请求速率;两者都不可得时降级为会话内时间加权均值,并在面板标注 `(会话)`
  - 实测:`llama-kvmem-server` 没有 `/metrics`(`endpoint_metrics=false`,exe 亦无 `--metrics` 开关),其 web 界面的 “Avg speed” 取自流式响应的 `timings`,与日志结算块的 `eval time / prompt eval time` 完全一致(见 `tests/fixtures/README.md`)
- **KV 命中率** 本次 ← `/slots.n_prompt_tokens_cache ÷ 本次输入`;每请求/累计 ← 日志结算块(kvmem 直接给 `cache = N`,`n_prompt` 即本次输入;llama-server 用 释放点 n_tokens − 输出 推导)
  - 注:kvmem 的 `n_prompt_tokens` **不**随生成 token 增长,llama-server 会 —— 面板按运行时观测自动区分
- **MTP 接受率** ← 日志的推测解码统计行,接受 draft token ÷ 生成 draft token:
  - llama-server:`slot print_timing: … | draft acceptance = X (A accepted / G generated)`
  - `llama-kvmem-server`:**没有** `draft acceptance` 行,改读 `--verbosity 4` 下每请求一行的
    `spec … statistics …: #gen tokens = G, #acc tokens = A`(累计值,面板取相邻两行的差值);
    该 build 的 `/slots` 只有 `speculative: true`、响应 `timings` 也没有 draft 字段
  - 已启用但日志没给统计(如 verbosity 不够)显示 `—` 并标注 **启用·无统计**;未启用显示 `—` / **未启用**
  - 启动要求:kvmem 必须带 `--verbosity 4`(3 不打印该行)。本机 `scripts/windows/start-iq3.ps1`
    已改为 4(日志量明显增大;想减小就把它降回 3,MTP 会退回 **启用·无统计**)
- **请求历史** ← 日志结算块(精确 ms),权威源;`/slots` 首观快照补充
- **GPU 面板** ← NVML;`功率受限` 等文案 = clocks event reasons 翻译;`降频余量` = 降频阈值 − 当前温度
- **布局** 自上而下:头部 → 速率与命中行 → **GPU / 实时·prompt / 实时·decode 三框同一行** → 请求历史 → 键位/状态
  - 宽度原则:**先满足文字内容的最小宽度,其次才是为美观适当变宽对齐**。三个上方面板各包住自己的内容;请求历史表在内容最小宽度之上与上方行对齐 —— 三框同行时等于三框宽度之和,三框堆叠(宽度不足)时等于四个面板中最宽者,内容更宽时以内容为准,且不超过终端宽度
  - 三框自然宽度之和超过终端可用宽度时,整行退化为**上下排列**,数值不裁切
  - 实时·prompt = 阶段 / 处理输入 / 输入速度 / 本次输入与缓存命中;实时·decode = 阶段 / 已生成·剩余 / 输出速度 / 上下文峰值
  - 两个实时面板**默认常显最全字段**:当前取不到实时值时显示最近一次有效值(冻结速率、上次输入输出),从未有数据显示 `—`;面板不会因空闲或阶段切换而少行
  - **终端尺寸变化(尤其是变窄)时四框立即重算宽度**,不等下一次 1Hz 重绘(重算钩子在 `RowLayout.on_resize`,`App.on_resize` 实测不会触发),任何框都不会越出终端右边界;暂停采样时也生效
  - 请求历史表的**列宽贴合内容**(不写死列宽),列间只留必要 padding,数值列仍右对齐——写死列宽时短数值会在列内留下大片空白(「槽」与「输入」之间曾有 8 列空档)

## FAQ

**为什么重启/换模型会弹一个新的控制台窗口?**
因为 dashboard 执行的就是你原来的 `run.bat` —— 弹窗、taskkill、日志 tee 全部保持你手动双击时的行为,这就是解耦的代价与保证。

**某些格子一直是 `—`?**
三种含义:运行未启用(如 MTP)、本机驱动不提供(如 GeForce 的热点/分区温度)、通道降级(如 server 编译版禁用了 `/slots`)。状态行的 `src a/b/c/d` 向量与"采样成功/失败"计数会告诉你是哪种。

**日志通道报失败?**
llama.cpp 升级后日志格式漂移时,未知行只计入失败计数、面板照常工作(解析器是容错注册表)。状态行的失败计数会提示这一点。

**如何拿到会话请求的结构化数据?**
`python -m llamadashboard --dump session.jsonl`,每个结算的请求追加一行 JSON。

**方言实测(b11223)**:`/slots` 键面与 master 一致、`/props` 含 build_info、`/health` 免密钥、`/metrics` 默认关(不依赖)。
