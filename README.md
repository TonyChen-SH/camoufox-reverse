# Camoufox Reverse — native trace pilot

把页面访问浏览器 API 的行为定位到 **脚本 URL、行列、参数和返回值摘要**，用于排查兼容性与指纹不一致。配套分析器：[camoufox-reverse-mcp](https://github.com/TonyChen-SH/camoufox-reverse-mcp)。

**当前状态：实验性源码补丁，尚未完成完整 Firefox/Camoufox 编译与实机运行验证。没有提供可用浏览器二进制。** 已验证补丁能应用到固定源码、生成器选择范围、安装拒绝/重复应用逻辑，以及 C++ JSON 缓冲区。CI 的绿色检查只代表这些测试，不代表浏览器构建成功。

## 版本与范围

这是独立补丁工具仓库。固定到 [Camoufox b6205b0](https://github.com/daijro/camoufox/tree/b6205b0ee1e4c2e8e4bb9eb0e084472028a9c6a3)，其 `browser/upstream.sh` 为 Firefox **158.0.1 / beta.34**；Firefox 文件来自 [9378fbc](https://github.com/mozilla-firefox/firefox/tree/9378fbc58660847fbc02c3486a0ecd6049978e25)。这与旧 Camoufox-Reverse 的 152 系列基线不同，也不声称支持所有写着“最新版”的安装包。具体源文件哈希见 [baseline.json](baseline.json)。

首批 16 个绑定入口：

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

`bootstrap` 会按上游流程安装依赖、下载并准备完整 Firefox 源码。当前工作区只下载了补丁核对所需的文件；上述完整构建尚未执行。安装器先校验三个文件的 Git blob 哈希、补丁 SHA-256 和三个新增文件 SHA-256，在临时目录应用并核对结果，再写入目标；未知版本、已有改动、缺失覆盖文件和部分安装均报错。重复应用相同版本返回 `already_applied`。多文件安装不保证进程被强杀时的原子性；发生部分安装应恢复源树后重试。

## 采集与定位

构建验证通过后，将以下环境变量传给浏览器进程（以及派生的内容进程）：

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

- `source` 是 SpiderMonkey 的直接调用位置，列号 **从 1 开始**；找不到调用者时各字段为 null。没有 source map 还原。
- 没有稳定 frame ID、Worker ID、request ID；`pid` 和 `realm_class` 不能代替这些 ID。
- 序号在入口分配、事件在退出写出，因此嵌套/并发调用可乱序。`mono_us` 是本机单调时钟微秒，不能当成网站时间线；`duration_us` 包含记录开销。
- 字符串使用有界 UTF-8 摘要并标记截断；最多 4 个参数。对象只取原生类名，不执行属性读取、代理、序列化或用户转换。Promise 结果、ImageData 像素、Float32Array 内容和 `copyFromChannel` 的输出缓冲区未采集；相同摘要不能证明内容相同。
- 栈最多 12 层和 512 个编码字节；`stack_status` 区分 disabled/captured/unavailable。每条记录最多 4096 字节，经 stderr 写出；管道被关闭或跨进程传输破损仍可能丢失日志。
- 这是绑定入口覆盖。JIT 优化、常量/缓存、未覆盖的 API、静态方法、setter、纯 JavaScript 运算和其他进程路径均可能漏过；未关闭 JIT，也未验证各优化层的覆盖率。
- 保留 JS 原有异常状态与 errno 是设计目标，完整构建和异常/高负载运行回归仍待完成。未通过这些检查前，不应用于判定目标网站的风控原因。

## 开发检查

Python 3.11+、Git、支持 C++17 与 UBSan 的 `g++`。从固定上游下载 3 个文件即可做这些检查，不会得到完整 Firefox 源码：

```bash
python3 tools/fetch_test_source.py .test-source
python3 tools/generate_patch.py --source .test-source --check
FIREFOX_SOURCE=.test-source python3 -m unittest discover -s tests -v
```

缺少源码或编译器会失败。修改生成规则时，编辑 `tools/generate_patch.py` 并从固定源文件重新生成 diff，更新 `baseline.json` 中对应的输出哈希；不要手改 `.patch`。修改覆盖文件也必须更新其 SHA-256。

原生文件与对 Mozilla 文件的修改遵循 MPL-2.0，见 [LICENSE](LICENSE)。
