# Camoufox Reverse

把浏览器 WebIDL 绑定的执行定位到 **脚本 URL、行列、参数与返回值摘要、可选调用栈**，用于排查兼容性和指纹不一致。配套分析器：[camoufox-reverse-mcp](https://github.com/TonyChen-SH/camoufox-reverse-mcp)。

独立的 SpiderMonkey 引擎通道捕获宿主属性查询、方法引用、读写、native 调用/构造、eval 与完整脚本来源，并可导出标准 VisibleV8 日志。Linux 原生编译、22 项引擎门禁、真实跨进程与 Ion 采样验证已通过。功能和边界见 [引擎事件兼容说明](docs/visiblev8.md)。

通用版本已移除原来的 16 名称白名单，覆盖生成的实例/静态 getter、setter、方法及构造入口。Linux x86_64 完整源码编译已通过；实际生成清单包含 **1,030 个接口、9,884 个不同 API 名、12,852 个操作入口**。这是实验性 WebIDL 监控，Windows/macOS 尚未构建验证。

## 下载与使用

[下载已验证的 Linux x86_64 引擎安装包](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41064733272/artifacts/12984328007)（约 1.24 GB，需要登录 GitHub）。[本次完整运行](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41064733272)通过五种 WebIDL 模式、22 项引擎检查和全部上游门禁；[小体积验证证据](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41064733272/artifacts/12983923464)含入口清单、真实 trace 与报告。该包同时支持 WebIDL 和引擎通道，包含 `binding-inventory.json` 和 `examples/`。

后续版本在 [Native browser build and runtime](https://github.com/TonyChen-SH/camoufox-reverse/actions/workflows/native-browser.yml) 选择成功运行的 **camoufox-engine-trace-linux-x86_64**。该名称只在全部核心门禁通过后上传；`candidate` 包仅表示编译完成。

Actions 文件保留 14 天。过期后，仓库所有者可在同一工作流选择 **Run workflow** 重新构建与验证。

解压下载的 ZIP，在包含 `SHA256SUMS` 的目录执行：

```bash
sha256sum --check SHA256SUMS
tar --use-compress-program=zstd -xf camoufox-trace-linux-x86_64.tar.zst
```

包内包括浏览器、字体、fontconfig、配置 schema、默认扩展、源码基线、入口清单、测试页面、许可证、运行报告和上游回归报告。浏览器入口为 `bin/camoufox-bin`。验证环境为 Ubuntu 24.04，需要 `zstd`、GTK/音频/X11 等 Firefox 运行库；完整依赖与验证步骤见 [构建工作流](.github/workflows/native-browser.yml)。

有桌面会话时，可先用随包页面验证采集。在一个终端启动测试页面：

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory examples
```

另一个终端仍从解压目录启动独立测试配置，完成页面测试后正常关闭浏览器：

```bash
trace_profile=$(mktemp -d)
CAMOUFOX_TRACE=1 CAMOUFOX_TRACE_STACK=1 ./bin/camoufox-bin \
  -no-remote -profile "$trace_profile" http://127.0.0.1:8000/ 2>capture.log
```

`capture.log` 中应有 `CAMOUFOX_TRACE ` 事件；页面显示结果还需与日志核对。实际排查时将网址换为目标页面，并复现被拦截的步骤。静态页面没有执行这些绑定时可能没有事件。

采集宿主操作尝试与完整源码时，使用 `CAMOUFOX_TRACE=0 CAMOUFOX_ENGINE_TRACE=1` 启动同一浏览器。WebIDL 的返回值/异常摘要与引擎的 `g/s/c/n` 是不同通道；两者可同时启用，分析时须按 `channel` 区分。完整命令见 [引擎采集](docs/visiblev8.md#开启和采集)。

接入现有 Camoufox 自动化脚本时，将 `executable_path` 指向监控版二进制，并按 [分析器采集说明](https://github.com/TonyChen-SH/camoufox-reverse-mcp#采集) 打开 Playwright 浏览器日志转发。无桌面会话可使用现有虚拟显示自动化配置；工作流的 `xvfb-run` 联调脚本提供自动启动与关闭的完整示例。

## 验证与覆盖数量

[通用版本完整编译](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/40990481675/job/123851485701) 已通过。清单来自该次实际生成的 C++：

| 统计口径 | 数量 |
|---|---:|
| 有插桩入口的接口 | 1,030 |
| 不同 `Interface.member` 名 | 9,884 |
| 按 `(api, op, scope)` 去重的操作入口 | 12,852 |
| 物理插桩位置 | 12,852 |

操作入口分布为：getter **6,068**、setter **2,967**、方法调用 **3,396**、构造 **421**；作用域分布为实例 **11,979**、静态 **452**、构造 **421**。

清单统计生成入口，不代表每个成员都可从任意网页访问，也不是网站风控规则数。完整条目、文件来源和读写/调用/构造分类见随包 `binding-inventory.json`。

当前验证包括：

- 64 个固定源码、安装器、生成器、真实 C++ UBSan 与回归测试，另有 18 个原生 profile 证据、负向与真实 Reader 接口测试。
- 配套分析器的 88 个核心测试、5 个真实 MCP SDK 测试，以及实际引擎日志的全部七工具联调。
- 五种 WebIDL 浏览器模式；带栈采集 924 条事件，官方 Python 启动器模式采集 1,715 条事件。35 个必检 API 和 24 个操作组合通过，覆盖 CSS、构造、静态方法、Reflect、Storage call/apply、Promise、WebRTC 和 Worker。
- 22 项引擎检查，本次采集 11,157 条事件、51 份源码；验证逐次循环计数、源码与 UTF-16 位置、Worker/frame/history/eval，以及标准 raw/manifest/MCP 往返。

[完整原生运行](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41064733272/job/124102747169)的关闭/开启页面结果一致。全部 **56/56** 个上游补丁守卫通过；build-tester 在 **8 组配置下通过 1,048/1,048 项计分检查，等级 A**。报告保留其未计入门禁的设备重复与空语音诊断；等级不表示任意网站不会拦截。

独立的 [Fission/Ion 原生运行](https://github.com/TonyChen-SH/camoufox-reverse/actions/runs/41069021017)复用同一候选浏览器：实际主/跨站 PID 为 3630/3681，全部 22 项检查通过；开启监控时三个函数具有真实 Ion 栈采样，80,000 条目标循环事件完整，页面值一致。[运行方法与证据口径](runtime-profiles/README.md)区分实际采样、配置开关与逐事件执行层信息。

核心工作流全部通过后提供验证包；Fission/Ion 证据由独立工作流保存。报告为 `runtime-report.json`、`engine-runtime-report.json`、`patch-guards.json`、`build-tester.json` 及对应 evidence 文件。生成入口数与逐项实测数分别报告；Chromium/V8 参考、后处理器和未运行的平台分别说明，不把这些数量当成网站风控规则数。

## 版本与范围

这是独立补丁工具仓库。固定到 [Camoufox b6205b0](https://github.com/daijro/camoufox/tree/b6205b0ee1e4c2e8e4bb9eb0e084472028a9c6a3)，其 `browser/upstream.sh` 为 Firefox **158.0.1 / beta.34**；Firefox 文件来自 [9378fbc](https://github.com/mozilla-firefox/firefox/tree/9378fbc58660847fbc02c3486a0ecd6049978e25)。这与旧 Camoufox-Reverse 的 152 系列基线不同，也不声称支持所有写着“最新版”的安装包。具体源文件哈希见 [baseline.json](baseline.json)。

通用插桩不再按 API 名白名单筛选，覆盖生成器产生的实例 getter/setter/方法、静态 getter/setter/方法以及普通和 HTML 构造入口。模板化属性在独立转发入口记录，避免多个 CSS 属性共用错误名称或重复记录。`Reflect.get/set` 以及 `.call/.apply` 到达这些绑定时也会产生事件。

构建会从实际生成的 C++ 输出 `binding-inventory.json`，列出接口数、API 名数量、按 `(api, op, scope)` 去重的入口数和物理插桩位置数。清单统计生成入口，不表示这些成员在所有页面环境都可访问，也不能当作已逐个运行验证的数量。

日志的 `op` 为 `get`、`set`、`call`、`construct`；`scope` 为 `instance`、`static`、`constructor`。赋值记录写入参数，成功返回摘要为 `undefined`；构造器返回对象类名或异常状态。浏览器特权 system realm 和没有脚本调用者的内部访问被排除。

`threw=true` 表示记录的原生绑定退出时有待处理的 JS 异常。外层 Promise 包装器可能随后把该异常转换为 rejected Promise，因此不能仅凭此字段断定页面观察到了同步抛出。

原 16 个入口保留为运行回归的一部分：

| 接口 | 成员 |
|---|---|
| Navigator、WorkerNavigator | userAgent、appVersion、platform、hardwareConcurrency |
| Screen | width、height |
| WebGLRenderingContext、WebGL2RenderingContext | getParameter |
| CanvasRenderingContext2D | getImageData |
| HTMLCanvasElement | toDataURL |
| AudioBuffer | getChannelData、copyFromChannel |

通过 WebIDL 绑定的 RAII 记录调用入口和退出；默认关闭，无页面 JavaScript 包装器，不修改内容沙箱配置。启用后会改变时序、内存分配和 I/O，必须与同一环境下未启用监控的运行对照。

## 应用到准备好的源码

在 Linux 构建机按 [Camoufox 构建说明](https://github.com/daijro/camoufox/blob/b6205b0ee1e4c2e8e4bb9eb0e084472028a9c6a3/AGENTS.md#building) 准备固定提交的源码。以下命令从本仓库根目录运行，路径按实际目录替换：

```bash
git clone https://github.com/daijro/camoufox.git ../camoufox-upstream
git -C ../camoufox-upstream checkout b6205b0ee1e4c2e8e4bb9eb0e084472028a9c6a3
make -C ../camoufox-upstream/browser bootstrap
python3 tools/apply.py --source ../camoufox-upstream/browser/camoufox-158.0.1-beta.34 --check
python3 tools/apply.py --source ../camoufox-upstream/browser/camoufox-158.0.1-beta.34
make -C ../camoufox-upstream/browser build
make -C ../camoufox-upstream/browser stage-fonts
```

`bootstrap` 会按上游流程安装依赖、下载并准备完整 Firefox 源码。完整自动化流程及字体、默认扩展的打包步骤见 [构建工作流](.github/workflows/native-browser.yml)。安装器先校验三个文件的 Git blob 哈希、补丁 SHA-256 和三个新增文件 SHA-256，在临时目录应用并核对结果，再写入目标；未知版本、已有改动、缺失覆盖文件和部分安装均报错。重复应用相同版本返回 `already_applied`。多文件安装不保证进程被强杀时的原子性；发生部分安装应恢复源树后重试。

## 采集与定位

将以下环境变量传给监控版浏览器进程（以及派生的内容进程）：

```bash
CAMOUFOX_TRACE=1 CAMOUFOX_TRACE_STACK=1 /path/to/camoufox-bin 2>capture.log
```

`CAMOUFOX_TRACE_STACK=1` 可选，默认仅记录直接调用位置；`CAMOUFOX_TRACE_MAX_EVENTS` 为每进程入口数量上限，默认 10000，可设 1–1000000。非法上限会输出 `limit` 并停止记录，不静默采用默认值。超过上限也会输出 `limit`，分析器应标记采集不完整。

本地回归页面可在另一个终端运行 `python3 -m http.server 8000 --bind 127.0.0.1 --directory examples`，再用构建后的浏览器打开 `http://127.0.0.1:8000/`。检查 Worker、Canvas、Audio 和可用的 WebGL 事件，以及一个预期 Canvas 异常。页面显示的结果不是原生监控已生效的证明，必须核对日志。

日志每行以 `CAMOUFOX_TRACE ` 开头，包含协议 1 JSON；浏览器驱动可能在标记前添加文本。分析仓库可直接读该 stderr 日志或 JSONL：

```bash
python3 -m camoufox_trace_mcp summarize capture.log
python3 -m camoufox_trace_mcp events capture.log --api WebGLRenderingContext.getParameter --limit 20
```

先看脚本 URL/行列和返回值，再对照同一步骤正常与异常运行的结果。API 被读取只能说明被观察，不能直接认定它触发了风控；IP、账号状态、TLS、HTTP、行为或服务端模型需要其他证据。

## 数据边界

- `source` 是 SpiderMonkey 的直接调用位置，列号 **从 1 开始**；没有脚本调用者的访问会被排除，调用者文件名仍可能为 null。没有 source map 还原。
- 没有稳定 frame ID、Worker ID、request ID；`pid` 和 `realm_class` 不能代替这些 ID。构造器名称为 `Interface.constructor`，旧式工厂保留其 IDL 成员名。
- 序号在入口分配、事件在退出写出，因此嵌套/并发调用可乱序。`mono_us` 是本机单调时钟微秒，不能当成网站时间线；`duration_us` 包含记录开销。
- 字符串使用有界 UTF-8 摘要并标记截断；最多 4 个参数。对象只取原生类名，不执行属性读取、代理、序列化或用户转换。Promise 结果、ImageData 像素、Float32Array 内容和 `copyFromChannel` 的输出缓冲区未采集；相同摘要不能证明内容相同。
- 栈最多 12 层和 512 个编码字节；`stack_status` 区分 disabled/captured/unavailable。每条记录最多 4096 字节，经 stderr 写出；管道被关闭或跨进程传输破损仍可能丢失日志。
- 这是生成的 WebIDL 绑定入口覆盖。JIT 优化、常量/缓存、DOM proxy 的命名/索引特殊路径、非 WebIDL 的 SpiderMonkey 原生函数、纯 JavaScript 运算和其他进程路径可能绕过入口；未关闭 JIT，也未验证各优化层的覆盖率。它不等同于 VisibleV8 的 V8 引擎插桩。
- 通用包装器在进入专用绑定前拒绝的非法接收者调用不会记录；仅查询属性描述符或取得方法引用而不执行绑定，也不产生对应的 getter/方法事件。
- 已通过真实页面的异常抛出、恢复与单次参数转换回归；不代表所有网页路径或高负载场景均已验证。监控本身的时序开销也可能影响页面行为。

## 开发检查

Python 3.11+、Git、支持 C++17 与 UBSan 的 `g++`。从固定上游下载 3 个文件即可做这些检查，不会得到完整 Firefox 源码：

```bash
python3 tools/fetch_test_source.py .test-source
python3 tools/generate_patch.py --source .test-source --check
FIREFOX_SOURCE=.test-source python3 -m unittest discover -s tests -v
```

缺少源码或编译器会失败。修改生成规则时，编辑 `tools/generate_patch.py` 并从固定源文件重新生成 diff，更新 `baseline.json` 中对应的输出哈希；不要手改 `.patch`。修改覆盖文件也必须更新其 SHA-256。

原生文件与对 Mozilla 文件的修改遵循 MPL-2.0，见 [LICENSE](LICENSE)。
