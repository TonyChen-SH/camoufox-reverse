"""Render public native/MCP observations as an offline Chinese HTML/CSV report."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

CATEGORIES = {
    'browser_environment': '浏览器与设备环境', 'graphics_and_layout': '图形与页面布局',
    'audio_and_media': '音频与媒体能力', 'interaction_and_focus': '输入事件与焦点',
    'storage_and_session': '存储与会话访问', 'timing_and_locale': '计时与区域设置',
    'network_and_permissions': '网络与权限', 'other_observed_api': '其他或未分类 API',
}
CSV_FIELDS = ('scenario', 'mode', 'integrity', 'category', 'api', 'api_sha256', 'operation',
              'count', 'channel', 'source_url', 'source_sha256', 'line', 'column',
              'source_offset', 'source_offset_unit', 'pid', 'tid', 'script_id',
              'isolate_sha256', 'origin', 'native_mono_us_min', 'native_mono_us_max')


def text(value):
    if value is None:
        return '未知'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def e(value):
    return escape(text(value), quote=True)


def csv_value(value):
    result = '' if value is None else text(value)
    return "'" + result if result[:1] in ('=', '+', '-', '@', '\t', '\r', '\n') else result


def google_script(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return parsed.hostname in ('www.google.com', 'www.gstatic.com', 'recaptcha.google.com') and parsed.path.startswith('/recaptcha/')
    except ValueError:
        return False


def script_comparisons(cases):
    grouped = defaultdict(list)
    for case in cases:
        scripts = [item for item in case.get('page', {}).get('scripts', []) if google_script(item.get('url'))]
        pairs = sorted({(item['url'], item.get('sha256')) for item in scripts if item.get('sha256')})
        grouped[case['scenario']].append({'mode': case['mode'], 'pairs': pairs,
                                         'complete': bool(scripts) and all(item.get('sha256') for item in scripts)})
    results = []
    for scenario, modes in sorted(grouped.items()):
        sets = {tuple(item['pairs']) for item in modes}
        complete = all(item['complete'] for item in modes)
        state = ('different' if len(sets) > 1 else 'same_observed_set' if len(modes) > 1 and complete else 'insufficient')
        results.append({'scenario': scenario, 'state': state, 'complete': complete, 'modes': modes})
    return results


def table(headers, rows, *, css=''):
    cells = ''.join('<tr>' + ''.join('<td>' + e(value) + '</td>' for value in row) + '</tr>' for row in rows)
    return '<div class="scroll"><table class="' + css + '"><thead><tr>' + ''.join('<th>' + e(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + cells + '</tbody></table></div>'


def render_report(output: Path):
    output = Path(output).resolve(strict=True)
    report = json.loads((output / 'report.json').read_text())
    cases = report.get('cases', [])
    chunks = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
              '<meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>Camoufox 原生监控与 MCP 检测行为报告</title>',
              '<style>body{font:15px/1.65 system-ui,sans-serif;color:#1f2937;background:#f5f7fb;margin:0;padding:26px;max-width:1600px;margin:auto}h1,h2{line-height:1.3}section{background:white;padding:20px;margin:18px 0;border:1px solid #dbe2eb;border-radius:10px}.notice{border-left:5px solid #d97706;padding:12px;background:#fff8e9}.scroll{overflow:auto;max-height:620px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid #e5e7eb;padding:9px;vertical-align:top;overflow-wrap:anywhere;min-width:75px}th{position:sticky;top:0;background:#edf2f8}code{overflow-wrap:anywhere}input{padding:10px;width:min(600px,90%)}details{margin:12px 0}summary{cursor:pointer}small{color:#526176}</style>',
              '<h1>Camoufox 原生监控与 MCP 检测行为报告</h1>',
              '<p class="notice">这里列出实际观察到的 API 访问及其源码位置。API 次数不等于风控规则数；API 的出现不能证明它导致了验证码。客户端日志不提供 Google 服务端权重、评分或最终拒绝原因。</p>',
              '<section><h2>验收结果与浏览器身份</h2>']
    passed = sum(case.get('passed') is True for case in cases)
    chunks.append(f'<p>综合检查：{passed}/{len(cases)} 通过；其余用例保留未通过或未知状态。实际原生浏览器运行标记：{e(report.get("native_camoufox_tested"))}。</p>')
    chunks.append(table(('项目', '实际记录'), [(key, report.get(key)) for key in (
        'source_commit', 'binary_sha256', 'libxul_sha256', 'baseline_sha256', 'build_run', 'artifact_id', 'started_at', 'finished_at')]))
    chunks.append('</section><section><h2>场景与采集完整性</h2>')
    case_rows, observations, coverage_rows, page_sections = [], [], [], []
    for case in cases:
        scenario, mode = case.get('scenario'), case.get('mode')
        if not all(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_-]*', value) for value in (scenario, mode)):
            raise ValueError('Case names must be safe relative path components')
        path = output / scenario / mode / 'mcp-observations.json'
        if not path.resolve().is_relative_to(output):
            raise ValueError('MCP report path escapes public output')
        mcp = json.loads(path.read_text()) if path.is_file() else None
        capture, page = case.get('capture', {}), case.get('page', {})
        case_rows.append((scenario, mode, '通过' if case.get('passed') is True else '未通过 / 未完成',
                          '通过' if page.get('page_checks_passed') is True else '未通过 / 未完成',
                          capture.get('integrity'), capture.get('event_count'), capture.get('statuses', []),
                          '已生成' if mcp is not None else '缺少 MCP 观察报告'))
        loss = capture.get('first_loss_issue')
        details = '<details><summary>' + e(scenario + ' / ' + mode + '：页面操作、网络结果与首个丢失') + '</summary>'
        if loss:
            numeric_fields = ('line', 'pid', 'tid', 'expected', 'observed', 'missing', 'missing_records', 'record')
            safe_loss = {key: loss[key] for key in numeric_fields if type(loss.get(key)) is int}
            reason = loss.get('reason', '')
            if isinstance(reason, str):
                for key in ('pid', 'tid', 'expected', 'observed', 'missing'):
                    match = re.search(r'(?:^| )' + key + r'=([0-9]+)(?: |$)', reason)
                    if match:
                        safe_loss[key] = int(match.group(1))
            for key in ('status', 'code'):
                if key in loss:
                    safe_loss[key] = loss[key]
            details += '<p>首个已知丢失：<code>' + e(safe_loss) + '</code></p>'
        else:
            details += '<p>没有可展示的首个丢失定位；以完整性状态为准，这不证明流尾完整。</p>'
        details += '<p>页面结果：' + e(page.get('ui_outcome')) + '；请求 ' + e(len(page.get('requests', []))) + '，响应 ' + e(len(page.get('responses', []))) + '，网络失败 ' + e(len(page.get('failures', []))) + '。</p>'
        details += table(('操作', '页面相对时间 ms'), [(action.get('event'), action.get('t_ms')) for action in page.get('actions', [])])
        details += table(('响应 URL', 'HTTP 状态', '请求 ID'), [(row.get('url'), row.get('status'), row.get('request_id')) for row in page.get('responses', [])])
        details += table(('失败 URL', '请求 ID', '错误类型'), [(row.get('url'), row.get('request_id'), row.get('failure')) for row in page.get('failures', [])])
        details += '<p><small>这些记录用于场景对照。原生引擎事件缺少稳定的 frame/request 标识与单调时间；页面时间与原生时间未校准，不能精确逐次关联，也不能据时间相邻认定因果。</small></p></details>'
        page_sections.append(details)
        if mcp is None:
            coverage_rows.append((scenario, mode, '缺少 MCP 观察报告', None, None, None, None, None))
            continue
        coverage = mcp.get('coverage', {})
        category_counts = Counter()
        for row in mcp.get('observations', []):
            item = {key: row.get(key) for key in CSV_FIELDS}
            item.update(scenario=scenario, mode=mode, integrity=capture.get('integrity'))
            observations.append(item)
            category_counts[row.get('category')] += row.get('count', 0)
        coverage_rows.append((scenario, mode, coverage.get('events_received'), coverage.get('google_source_events'),
                              coverage.get('distinct_google_api_labels'), coverage.get('observation_groups'),
                              category_counts.get('other_observed_api', 0), coverage.get('omitted_observation_groups')))
    chunks.append(table(('场景', '模式', '综合检查', '页面检查', '完整性', '事件数', '状态', 'MCP 报告'), case_rows))
    chunks.append('</section><section><h2>观察覆盖与未知部分</h2>')
    chunks.append(table(('场景', '模式', 'MCP 事件总数', 'Google 源码归属事件', '不同 API 标签', '调用位置/上下文组', '已导出未分类事件', '省略组数'), coverage_rows))
    chunks.append('<p>未分类条目仍保留计数和 API 标签哈希；完整原始标签保存在加密私有 MCP 证据中。仅已观察到 Google reCAPTCHA 源码归属的事件进入下表。未知来源、未执行路径和丢失事件不会被补算。</p></section>')
    chunks.append('<section><h2>跨模式脚本可比性</h2>')
    for comparison in script_comparisons(cases):
        explanations = {'different': '脚本 URL/哈希集合不同。可能存在动态版本更新或采集差异，不能把次数、耗时变化直接归因于监控或风控。',
                        'same_observed_set': '已观察到的网络脚本 URL/哈希集合一致；这仍不证明执行路径、服务端状态和计时样本可比。',
                        'insufficient': '缺少足够的脚本哈希或对照模式，无法判断可比性。'}
        chunks.append('<details><summary>' + e(comparison['scenario']) + '：' + e(explanations[comparison['state']]) + '</summary>')
        chunks.append('<p>各模式脚本哈希采集完整：' + e(comparison['complete']) + '</p>')
        chunks.append(table(('模式', '网络脚本 URL', 'SHA-256'), [(mode['mode'], url, sha) for mode in comparison['modes'] for url, sha in mode['pairs']]))
        chunks.append('</details>')
    chunks.append('</section><section><h2>页面操作与网络结果</h2>' + ''.join(page_sections) + '</section>')
    families = Counter()
    for row in observations:
        families[row['category']] += row['count'] or 0
    chunks.append('<section><h2>已观察 API 类别</h2><p>分类仅依据公开 API 语义推测可能用途。存储、计时、设备环境等访问，也可能用于普通页面功能；采集目的尚未确认。</p>')
    chunks.append(table(('类别', '已导出分组中的观察次数'), [(CATEGORIES.get(key, key), value) for key, value in sorted(families.items(), key=lambda item: str(item[0]))]))
    chunks.append('</section><section><h2>源码位置与上下文明细</h2><p>空白或“未知”表示日志未提供。多个进程/线程、脚本或偏移保留为不同分组；数值不代表独立检测规则。</p><label>搜索场景、API、源码、哈希或上下文：<input id="search" type="search"></label><p id="count"></p>')
    chunks.append(table(('场景/模式', 'API / 标签哈希', '类别 / 操作 / 次数', '源码 URL / SHA-256',
                         '行:列 / UTF-16 偏移', 'PID / TID / 脚本 / Origin', '完整性'), [
        (row['scenario'] + '/' + row['mode'], row['api'] or '未公开动态标签 · ' + text(row['api_sha256']),
         text(CATEGORIES.get(row['category'], row['category'])) + ' / ' + text(row['operation']) + ' / ' + text(row['count']),
         text(row['source_url']) + '\n' + text(row['source_sha256']),
         text(row['line']) + ':' + text(row['column']) + ' / ' + text(row['source_offset']),
         ' / '.join(text(row[name]) for name in ('pid', 'tid', 'script_id', 'origin')), row['integrity']) for row in observations
    ], css='observations'))
    chunks.append('<p><a href="observations.csv">下载全部已导出观察分组 CSV</a> · CSV 中未写入参数、返回值、源码正文或验证码响应。</p></section>')
    chunks.append('<script>const rows=Array.from(document.querySelectorAll(".observations tbody tr"));const input=document.getElementById("search"),count=document.getElementById("count");function filter(){const q=input.value.toLocaleLowerCase();let n=0;for(const row of rows){row.hidden=!row.textContent.toLocaleLowerCase().includes(q);if(!row.hidden)n++;}count.textContent=`显示 ${n} / ${rows.length} 个观察分组`;}input.addEventListener("input",filter);filter();</script></html>')
    html_path, csv_path = output / 'report.html', output / 'observations.csv'
    html_path.write_text('\n'.join(chunks), encoding='utf-8')
    with csv_path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows({key: csv_value(row.get(key)) for key in CSV_FIELDS} for row in observations)
    return {'html': html_path.name, 'csv': csv_path.name, 'observation_rows': len(observations),
            'html_sha256': hashlib.sha256(html_path.read_bytes()).hexdigest(),
            'csv_sha256': hashlib.sha256(csv_path.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    print(json.dumps(render_report(parser.parse_args().output), ensure_ascii=True))
