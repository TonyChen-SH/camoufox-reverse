# VisibleV8 引擎事件兼容

参考固定到 [VisibleV8 068c617f](https://github.com/wspr-ncsu/visiblev8/tree/068c617f28686f5c43d27c891803cd21510aa141)，对应 Chromium 155.0.8059.30 / V8 15.5.35.14。这里实现的是 SpiderMonkey 上的宿主操作观察及日志互通。生成入口数量、源码审查或消费者测试均不能证明全部运行路径等价；浏览器、参考引擎和后处理器分别保留实际运行证据。

## 开启和采集

原有 WebIDL 通道由 `CAMOUFOX_TRACE=1` 开启；引擎通道独立使用 `CAMOUFOX_ENGINE_TRACE=1`。两者可以同时开启。现有 WebIDL 下载包尚不包含这项新增引擎功能；应使用通过引擎运行门禁的 `camoufox-engine-trace-linux-x86_64` 构建。

```bash
CAMOUFOX_ENGINE_TRACE=1 python -m camoufox_trace_mcp collect \
  --engine --no-bindings --output traces/engine.jsonl --timeout 180 -- python your_script.py
python -m camoufox_trace_mcp summarize traces/engine.jsonl --channel engine
python -m camoufox_trace_mcp scripts traces/engine.jsonl
python -m camoufox_trace_mcp export-vv8 traces/engine.jsonl --output traces/vv8-export
```

从安装好的分析器环境运行，先创建 `traces/`；采集文件和导出目录必须不存在。脚本须将 `executable_path` 指向监控版浏览器，继承环境变量，并在 Playwright 场景设置 `DEBUG=pw:browser DEBUG_COLORS=0` 转发浏览器 stderr。参考 [分析器协议与命令](https://github.com/TonyChen-SH/camoufox-reverse-mcp/blob/main/docs/vv8.md)。

默认排除**当前执行 realm** 为 system 的操作；这不是对所有调用者权限的过滤。`CAMOUFOX_ENGINE_PRIVILEGED=1` 可包括当前 system realm。原生调用没有脚本帧时使用标准 `!?`，不伪造脚本来源。

`CAMOUFOX_ENGINE_MAX_RECORDS` 是每进程逻辑记录上限，包含脚本及上下文记录；默认 `0` 表示无限制。非法配置、达到上限、源码/序列化失败会输出 `channel:"engine"` 的 `limit`，采集须标记不完整。消费者和采集器还有独立、显式的资源上限。

## 捕获位置

- 点属性和索引属性的读取、写入、复合赋值和增减，通过字节码发射器调用引擎私有 native intrinsic；先复制已有栈值，再执行原操作，不重新求值 receiver/key/RHS。
- `Reflect.get/set/has` 显式到达同一观察器；数字属性键保持 `ToPropertyKey` 后的字符串形式。存在性查询按参考格式记录为 `g`，不制造返回布尔值。
- WebIDL 构建宿主函数时注册稳定 native 代码指针，方法、静态成员、构造、旧式工厂以及可调用 DOM proxy 在实际引擎调用边界观察。普通 Math/Array/Object builtin 不因此变成宿主调用。
- 监控开启时，native、bound、call/apply 和 class-hook 的直接 CallIC 路径不附着；Ion 通用 native ABI 路径转入现有 VM 调用。普通脚本仍可使用 JIT；关闭时不增加监控字节码或该运行跳转。
- direct/indirect eval 和动态 Function 使用专用入口。动态 Function 按参考约定记录 `c`，即使使用 `new Function`。
- 字节码转码 build ID 在开启时增加模式后缀，防止误用未插桩缓存；关闭时保留原 ID。

只添加 VM `GetProperty` 或 IC 附着日志不能覆盖每次执行。本实现的浏览器门禁要求热循环逐次计数，并核对关闭/开启后的页面结果。

## 原始记录与传输

标准记录包括 `~` runtime/isolate、`@` 文档或 Worker href/安全令牌、`$` 完整源码与原生 eval 父来源、`!` 活跃脚本，以及 `g/s/c/n`。脚本按首次观察惰性注册，父来源先于子脚本；不保证枚举未执行监控操作的脚本。线程标识是进程内唯一的线程流 ID，不是 frame/request ID。

源码、参数不使用 WebIDL 通道的短字符串摘要。原始字段按 UTF-16 码元转义，保留控制字符、冒号、反斜杠、CRLF 和非 BMP 字符；源码位置保留 SpiderMonkey 的源位置并转成 UTF-16 offset。不同引擎的具体定位点可能不同，不能把 offset 数值相等当作跨引擎行为相等。

每条原始记录由一个或多个 `CAMOUFOX_TRACE ` JSON envelope 承载，字段为 `schema:1,event:"vv8",pid,tid,record,part,final,data`。每个物理片段不超过 4096 字节；完整原始记录不含终止换行。分析器严格重组并按线程导出标准 VV8 日志，保留原始字段，不把缺段修补成完整日志。浏览器继续使用继承的 stderr 与内容沙箱。

## 语义与验证边界

`g/s/c/n` 记录操作尝试，没有桌面 VV8 未提供的返回值或成功状态。WebIDL 通道另有结果、异常和耗时；分析时必须区分 `engine` 与 `binding`，避免重复计数。

对象使用 GC 稳定身份与原生类名/安全可得的构造名。调用参数中的普通 Object/Array可展开一层自有可枚举数据属性；不执行访问器，也不触发普通 JS proxy 的用户 trap。参考序列化器在某些 proxy/interceptor 情况会执行操作，本实现保留安全边界并明确这一差异。函数、symbol、正则和对象快照仍是有损表示。

裸词法/全局名称解析、纯 JS 调用、任意对象全部属性、描述符枚举、异步 Promise 结果与网站服务端判断，不属于本参考版本明确提供的通用记录能力。普通 JS proxy 自身不视为宿主对象，内部实际执行的宿主操作可另行记录。

Linux Firefox、原始 Chromium/V8、Android Gin/WebView 是不同验证产品。读取 `j` 记录不代表存在 Firefox 的 Android Java bridge；未运行的平台不能标为通过。原版 Go/Rust/PostgreSQL 验证入口位于独立的参考构建工作流，原项目已失效或损坏的模式须保留明确结果，不能由替代算法伪造通过。
