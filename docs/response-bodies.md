# 响应体读取

Juggler 的 `ResponseStorage` 保存网络响应体，供 Playwright 的 `response.body()` 读取。Firefox 缓存可以把 JavaScript 的序列化字节码交给脚本加载器，同时保留原响应的 `Content-Encoding`。这些替代数据不是源码：把它们作为 identity 正文返回会得到错误字节，把它们交给 gzip 解码器会报 `NS_ERROR_INVALID_CONTENT_ENCODING`，即使页面仍能执行脚本。

补丁检查 `nsICacheInfoChannel.alternativeDataType`。存在替代数据时，从**同一缓存项**调用 `getOriginalInputStream(receiver)` 获取原始主数据流，用 `nsIInputStreamPump` 异步读取，不重新请求网络、不关闭缓存，也不把读取错误替换成先前脚本内容。入口先登记待完成响应，再调用可能同步回调的 receiver；协议读取等待该响应完成。`PageHandler` 和 `Dispatcher` 已使用异步返回值，因此不需要修改协议。

新增缓存流每次读取最多 64 KiB，未完成的缓存读取也计入原有的单响应和总存储预算。预算统计进入 `ResponseStorage` 的原始存储输入，其中可能仍是压缩字节；解压后的输出不在这项预算内。普通网络响应在进入存储前，也已由 `NetworkRequest._responseBodyChunks` 完整积累。因此，64 KiB 是新增缓存流的单次读取上限，存储预算也不是解压输出或浏览器进程峰值内存的硬上限。

存储输入超限会淘汰响应、取消 pump 并关闭输入流；协议保持原有 `evicted` 返回形式。空流、打开失败和流读取失败会保留为协议错误。并发和重复读取共享一次原始缓存读取，不反复打开流。

原始缓存流仍按原响应的 `Content-Encoding` 解码。通道的 `applyConversion`、`hasContentDecompressed` 可能描述交给页面的替代数据，不能用于跳过原始缓存流的解码；普通响应仍遵守这两个标志，fulfilled 响应仍直接使用提供的正文。`gzip, deflate` 表示先 gzip、再 deflate，读取按 `response.encodings.slice().reverse()` 处理，保持编码数组不变。

实现依据固定 Firefox 源码的 [nsICacheInfoChannel.idl](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/netwerk/base/nsICacheInfoChannel.idl)、[nsHttpChannel.cpp](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/netwerk/protocol/http/nsHttpChannel.cpp) 和 [nsIInputStreamPump.idl](https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/netwerk/base/nsIInputStreamPump.idl)。`getOriginalInputStream` 的 receiver 可能立即调用，且成功返回不保证获得非空流；`alternativeDataInputStream` 则是替代数据流，不能用于恢复原正文。

输入文件来自固定 Camoufox 提交的 `browser/additions/juggler/NetworkObserver.js`，准备源码时复制到 `juggler/NetworkObserver.js`。`tools/fetch_test_source.py` 从固定提交下载输入；`baseline.json` 核对修改前后哈希，`tools/generate_patch.py` 使用 `tools/response_storage_patch.py` 生成补丁。不要手改生成的 diff。

`tests/test_response_storage.py` 执行从真实源码提取的完整 `ResponseStorage`，使用 Node zlib 提供真实 gzip/deflate 字节，按 Gecko 接口形状提供同步/延迟 receiver 和分块 pump。测试覆盖普通响应的 20 种编码/解码状态组合、缓存主数据恢复、并发与重复读取、空流、打开/初始化/读取失败、两种存储输入容量限制、待完成响应被淘汰、缓存流单次读取的 64 KiB 上限及 fulfilled 响应。旧实现上的缓存恢复测试先失败，修复后通过。

原始问题已在编译浏览器中通过固定六阶段 fixture 复现：缓存 identity 返回错误正文，缓存 gzip 解码失败，未缓存对照正常；ScriptLoader 日志确认失败阶段收到序列化字节码。默认缓存策略要求源码长度至少 1,024 字符、同一缓存项读取计数至少 4，然后等待文档加载完成及空闲任务。仅加载三次或等待固定时间不能证明覆盖了字节码缓存。修复后的实际 Gecko、72 次 fixture 加载和 Google 页面验证仍须由同一版本的编译产物完成；Node 回归不替代浏览器验收。
