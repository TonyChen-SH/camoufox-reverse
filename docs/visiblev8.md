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

`CAMOUFOX_ENGINE_MAX_RECORDS` 是每进程逻辑记录上限，包含脚本及上下文记录；默认 `0` 表示无限制。非法配置、达到上限、源码读取错误/OOM 或序列化失败会输出 `channel:"engine"` 的 `limit`，采集须标记不完整。消费者和采集器还有独立、显式的资源上限。

## 捕获位置

- 点属性和索引属性的读取、写入、复合赋值和增减，通过字节码发射器调用引擎私有 native intrinsic；先复制已有栈值，再执行原操作，不重新求值 receiver/key/RHS。
- `Reflect.get/set/has` 显式到达同一观察器；数字属性键保持 `ToPropertyKey` 后的字符串形式。存在性查询按参考格式记录为 `g`，不制造返回布尔值。
- WebIDL 构建宿主函数时注册稳定 native 代码指针，方法、静态成员、构造、旧式工厂以及可调用 DOM proxy 在实际引擎调用边界观察。普通 Math/Array/Object builtin 不因此变成宿主调用。
- 接口 prototype 与 namespace 注册其实际 JSClass；WindowProxy 使用关联 Window 的原生类名。跨进程 DOM remote proxy 只在其原生 handler family 内读取 final `className`，按真实 prototype ID 区分 Window、Location 等接口；普通 Proxy 保留其自身类型，原生 wrapper 保留原对象 identity。
- 监控开启时，native、bound、call/apply 和 class-hook 的直接 CallIC 路径不附着；Ion 通用 native ABI 路径转入现有 VM 调用。普通脚本仍可使用 JIT；关闭时不增加监控字节码或该运行跳转。
- direct/indirect eval 和动态 Function 使用专用入口。动态 Function 按参考约定记录 `c`，即使使用 `new Function`。
- 字节码转码 build ID 在开启时增加模式后缀，防止误用未插桩缓存；关闭时保留原 ID。

远程类型来自固定版本的 [RemoteObjectProxyBase::className](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/dom/bindings/RemoteObjectProxy.cpp#L121)，它直接查原生接口名称表；[RemoteOuterWindowProxy](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/dom/base/RemoteOuterWindowProxy.cpp#L29) 指定 `Window` prototype ID。此路径保留 wrapper 身份，未读取目标属性。

跨 compartment 观察只解开真实原生 Wrapper 来检查宿主元数据；普通跨 realm Object/Array 的包装不展开。运行用例通过用户 click 打开同源 popup，在其 iframe 中验证 foreign Window/Navigator/DOM；另以两个 loopback 主机名检查跨站 frame 的 `Window.closed` 类型，并报告实际 PID 是否跨进程。未观测到跨进程时，不能把同进程结果称为 Fission 运行证据。

只添加 VM `GetProperty` 或 IC 附着日志不能覆盖每次执行。本实现的浏览器门禁要求热循环逐次计数，并核对关闭/开启后的页面结果。

## 原始记录与传输

标准记录包括 `~` runtime/isolate、`@` 文档或 Worker href/安全令牌、`$` 完整源码与原生 eval 父来源、`!` 活跃脚本，以及 `g/s/c/n`。脚本按首次观察惰性注册，父来源先于子脚本；不保证枚举未执行监控操作的脚本。线程标识是进程内唯一的线程流 ID，不是 frame/request ID。

源码通过原生 `ScriptSource::tryLoadSource` 读取，遵守当前调用者的 source hook 权限。Firefox 允许 discarded/Missing 源码，以及暂时无法回取的 lazy source。当前源码不可用时保留 API 尝试，用标准 `!?` 和 offset `-1`；有完整子源码而介绍祖先不可用或成环时，保留子源码并用 `#?` 表示未知父来源。不会发布伪空 `$` 记录，retrievable 来源在后续观察时可重试。

这些情况另发 `schema:1,event:"source_unavailable",channel:"engine"` 控制记录，携带 `pid,tid,source_id,reason,scope,filename,introduction_type,retrievable,introduction_source_id`。`scope` 区分当前源码和介绍关系，常见原因是 `source_missing`、`source_not_loaded`、`introduction_cycle`；有界诊断字段显式保留 truncation flags。消费者保留该元数据并报告 `source_provenance_complete=false`，不把它归为传输丢失。真正的 source hook 失败、OOM、substring/linear 错误仍发停止捕获的 `limit`，并附 source ID、文件名、介绍类型与具体 `stage`，完整导出须拒绝这些丢失场景。

该区分来自固定版本 [tryLoadSource](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/js/src/vm/JSScript.cpp#L835) 的布尔结果与 `loaded` 输出参数；[XPCJSSourceHook](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/js/xpconnect/src/XPCJSRuntime.cpp) 可在当前权限不允许时成功返回但不提供源码。

`@` 的 href 来自原生文档 URI 或 Worker ClientInfo，第二字段使用 Firefox principal 的 `GetOriginNoSuffix` 文本；它不是 V8 security token 对象的逐值复制。对象身份、runtime 指针及脚本 ID 都属于各自引擎的运行实例，不能跨浏览器按数值匹配。

源码、参数不使用 WebIDL 通道的短字符串摘要。原始字段按 UTF-16 码元转义，保留控制字符、冒号、反斜杠、CRLF 和非 BMP 字符。普通属性表达式保存所属 Firefox AST 的起点，跨 receiver、key 和 RHS 发射仍保留该坐标；合成访问及 native 调用使用可得的 caller-PC 位置。

offset 使用 UTF-16 码元，但定位约定不等于 V8：V8 的普通点属性可定位到属性名、计算属性定位到 `[`、写入可定位到赋值运算符。Firefox 嵌套成员表达式还可能共享起点。保留各引擎原始位置，不伪造相同 offset，也不把数值相等当成行为等价。

每条原始记录由一个或多个 `CAMOUFOX_TRACE ` JSON envelope 承载，字段为 `schema:1,event:"vv8",pid,tid,record,part,final,data`。每个物理片段不超过 4096 字节；完整原始记录不含终止换行。分析器严格重组并按线程导出标准 VV8 日志，保留原始字段，不把缺段修补成完整日志。浏览器继续使用继承的 stderr 与内容沙箱。

## 语义与验证边界

`g/s/c/n` 记录操作尝试，没有桌面 VV8 未提供的返回值或成功状态。WebIDL 通道另有结果、异常和耗时；分析时必须区分 `engine` 与 `binding`，避免重复计数。

一次属性读取或写入不能推定必有额外的 getter/setter `c`。V8 的模板访问器、IC、TurboFan/Maglev 与 Firefox 的 native 调用、DOM IC/专用 ABI 使用不同路径；描述符查询还可能在 V8 中实例化访问器并改变后续路径。属性 `g/s` 与显式宿主调用分别核验，不能把某个冷路径的 `c` 数量外推到所有热路径。

对象使用 GC 稳定身份与原生类名/安全可得的构造名。调用参数中的普通 Object/Array可展开一层自有可枚举数据属性；不执行访问器，也不触发普通 JS proxy 的用户 trap。参考序列化器在某些 proxy/interceptor 情况会执行操作，本实现保留安全边界并明确这一差异。函数、symbol、正则和对象快照仍是有损表示。

裸词法/全局名称解析、纯 JS 调用、任意对象全部属性、描述符枚举、异步 Promise 结果与网站服务端判断，不属于本参考版本明确提供的通用记录能力。普通 JS proxy 自身不视为宿主对象，内部实际执行的宿主操作可另行记录。

Linux Firefox、原始 Chromium/V8、Android Gin/WebView 是不同验证产品。读取 `j` 记录不代表存在 Firefox 的 Android Java bridge；未运行的平台不能标为通过。原版 Go/Rust/PostgreSQL 验证入口位于独立的参考构建工作流，原项目已失效或损坏的模式须保留明确结果，不能由替代算法伪造通过。
