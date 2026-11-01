"""Build source-attributed observations through the read-only MCP stdio API."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

PAGE_SIZE = 1000
MAX_PUBLIC_GROUPS = 20000
MAX_PUBLIC_SCRIPTS = 5000
GOOGLE_HOSTS = {'www.google.com', 'www.gstatic.com', 'recaptcha.google.com'}
# Only documented, fixed member names enter public output. Dynamic JS property
# names remain hashed because a property name can itself contain session data.
CATEGORY_MEMBERS = {
    'browser_environment': {
        'Navigator': 'userAgent platform vendor language languages hardwareConcurrency deviceMemory cookieEnabled webdriver maxTouchPoints plugins mimeTypes appVersion appName product productSub oscpu buildID pdfViewerEnabled',
        'Screen': 'width height availWidth availHeight colorDepth pixelDepth orientation',
        'Window': 'devicePixelRatio innerWidth innerHeight outerWidth outerHeight screenX screenY matchMedia',
    },
    'graphics_and_layout': {
        'HTMLCanvasElement': 'getContext toDataURL toBlob width height',
        'CanvasRenderingContext2D': 'getImageData measureText fillText strokeText isPointInPath',
        'WebGLRenderingContext': 'getParameter getExtension getSupportedExtensions readPixels getShaderPrecisionFormat',
        'WebGL2RenderingContext': 'getParameter getExtension getSupportedExtensions readPixels getShaderPrecisionFormat',
        'Element': 'getBoundingClientRect getClientRects clientWidth clientHeight scrollWidth scrollHeight',
        'HTMLElement': 'offsetWidth offsetHeight offsetTop offsetLeft',
        'FontFaceSet': 'check load',
    },
    'audio_and_media': {
        'AudioContext': 'sampleRate state createOscillator createAnalyser createGain',
        'OfflineAudioContext': 'startRendering createOscillator createAnalyser createGain',
        'AudioBuffer': 'getChannelData copyFromChannel',
        'AnalyserNode': 'getFloatFrequencyData getByteFrequencyData',
        'MediaDevices': 'enumerateDevices getUserMedia',
        'HTMLMediaElement': 'canPlayType',
    },
    'interaction_and_focus': {
        'Event': 'isTrusted timeStamp type target',
        'MouseEvent': 'clientX clientY screenX screenY movementX movementY buttons button',
        'PointerEvent': 'pointerType pressure width height isPrimary',
        'KeyboardEvent': 'key code repeat',
        'TouchEvent': 'touches changedTouches targetTouches',
        'Document': 'hasFocus visibilityState hidden activeElement',
        'EventTarget': 'addEventListener removeEventListener dispatchEvent',
    },
    'storage_and_session': {
        'Document': 'cookie',
        'Window': 'localStorage sessionStorage indexedDB',
        'Storage': 'getItem setItem removeItem key length clear',
        'IDBFactory': 'open deleteDatabase databases',
    },
    'timing_and_locale': {
        'Performance': 'now timeOrigin timing getEntries getEntriesByType getEntriesByName',
        'Date': 'getTimezoneOffset now getTime',
        'DateTimeFormat': 'resolvedOptions format',
    },
    'network_and_permissions': {
        'Window': 'fetch',
        'XMLHttpRequest': 'open send setRequestHeader status responseType',
        'Navigator': 'sendBeacon connection permissions',
        'Permissions': 'query',
        'NetworkInformation': 'effectiveType downlink rtt saveData',
        'RTCPeerConnection': 'createOffer createDataChannel setLocalDescription',
    },
}
PUBLIC_APIS = {}
for _category, _interfaces in CATEGORY_MEMBERS.items():
    for _interface, _members in _interfaces.items():
        for _member in _members.split():
            for _prefix in ('', 'get ', 'set '):
                PUBLIC_APIS[f'{_interface}.{_prefix}{_member}'] = _category


def digest(value):
    return hashlib.sha256(value.encode('utf-8', 'surrogatepass')).hexdigest()


def safe_url(value, *, origin=False):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname:
            return None
        host = parsed.hostname
        port = f':{parsed.port}' if parsed.port else ''
        return f'{parsed.scheme}://{host}{port}' + ('' if origin else parsed.path)
    except ValueError:
        return None


def google_source(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.hostname in GOOGLE_HOSTS and parsed.path.startswith('/recaptcha/')
    except ValueError:
        return False


def integer(value):
    return value if type(value) is int else None


def source_hash(value):
    return value if isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value) else None


def build_report(summary, events, scripts):
    groups = {}
    categories = Counter()
    attributed = 0
    api_names = set()
    for event in events:
        source = event.get('source') or {}
        if not google_source(source.get('url')):
            continue
        attributed += 1
        api = event.get('api')
        api = api if isinstance(api, str) else ''
        api_names.add(api)
        category = PUBLIC_APIS.get(api, 'other_observed_api')
        categories[category] += 1
        row = {
            'api': api if api in PUBLIC_APIS else None,
            'api_sha256': digest(api),
            'category': category,
            'operation': event.get('op') if event.get('op') in ('get', 'set', 'call', 'construct') else None,
            'channel': event.get('channel', 'binding'),
            'source_url': safe_url(source.get('url')),
            'source_sha256': source_hash(event.get('source_sha256')),
            'line': integer(source.get('line')), 'column': integer(source.get('column')),
            'source_offset': integer(event.get('source_offset')),
            'source_offset_unit': 'utf16' if event.get('source_offset_unit') == 'utf16' else None,
            'pid': integer(event.get('pid')), 'tid': integer(event.get('tid')),
            'script_id': integer(event.get('script_id')),
            'isolate_sha256': digest(event['isolate']) if isinstance(event.get('isolate'), str) else None,
            'origin': safe_url(event.get('origin'), origin=True),
        }
        key = json.dumps(row, sort_keys=True)
        group = groups.setdefault(key, {**row, 'count': 0, 'examples': [], 'native_mono_us_min': None, 'native_mono_us_max': None})
        group['count'] += 1
        if len(group['examples']) < 3:
            group['examples'].append({name: integer(event.get(name)) for name in ('seq', 'record')})
        stamp = integer(event.get('mono_us'))
        if stamp is not None:
            group['native_mono_us_min'] = min(stamp, group['native_mono_us_min']) if group['native_mono_us_min'] is not None else stamp
            group['native_mono_us_max'] = max(stamp, group['native_mono_us_max']) if group['native_mono_us_max'] is not None else stamp
    public_scripts = []
    for script in scripts:
        if google_source(script.get('url')):
            public_scripts.append({
                **{key: integer(script.get(key)) for key in ('index', 'pid', 'tid', 'script_id', 'parent_id', 'utf16_length')},
                'url': safe_url(script.get('url')), 'source_sha256': source_hash(script.get('source_sha256')),
                'origin': safe_url(script.get('origin'), origin=True),
            })
    rows = sorted(groups.values(), key=lambda row: (-row['count'], row['api_sha256'], json.dumps(row, sort_keys=True)))
    return {
        'schema': 1, 'transport': 'MCP SDK stdio JSON-RPC',
        'server_risk_cause_confirmed': False,
        'integrity': summary.get('integrity'), 'statuses': summary.get('statuses', []),
        'issue_counts': summary.get('issue_counts', {}),
        'coverage': {'events_received': len(events), 'summary_event_count': summary.get('event_count'),
                     'scripts_received': len(scripts), 'google_source_events': attributed,
                     'distinct_google_api_labels': len(api_names), 'observation_groups': len(rows),
                     'exported_observation_groups': min(len(rows), MAX_PUBLIC_GROUPS),
                     'omitted_observation_groups': max(0, len(rows) - MAX_PUBLIC_GROUPS),
                     'google_scripts': len(public_scripts),
                     'omitted_scripts': max(0, len(public_scripts) - MAX_PUBLIC_SCRIPTS)},
        'categories': [{'category': name, 'observed_events': count,
                        'interpretation': 'Possible signal family inferred from documented API semantics; collection purpose is unconfirmed.'}
                       for name, count in sorted(categories.items())],
        'observations': rows[:MAX_PUBLIC_GROUPS], 'scripts': public_scripts[:MAX_PUBLIC_SCRIPTS],
        'limitations': [
            'API use is observed; its role in a server decision and its risk weight are not observed.',
            'Calls, reads, writes and duplicate engine/binding observations are not distinct risk rules.',
            'Only events attributed to observed Google reCAPTCHA script URLs enter these groups.',
            'Unknown provenance and stream losses remain unknown; no attribution is reconstructed.',
            'Public names use a fixed documented API allowlist; all other labels are hashed and private MCP receipts retain their originals.',
            'Native engine events lack monotonic timestamps and stable frame/request IDs; page-action timing cannot be joined exactly.',
            'Native monotonic times are not calibrated to page-relative timestamps.',
            'No known loss does not certify the producer stream tail or unobserved execution paths.',
        ],
    }


def validate_page(page, field, offset, matched):
    count = page.get('matched')
    items = page.get(field)
    if type(count) is not int or count < 0 or (matched is not None and count != matched):
        raise ValueError('MCP matched count changed or is invalid')
    if page.get('offset') != offset or not isinstance(items, list):
        raise ValueError('MCP page offset or items are invalid')
    next_offset = page.get('next_offset')
    end = offset + len(items)
    if next_offset is not None and (type(next_offset) is not int or next_offset != end or next_offset <= offset):
        raise ValueError('MCP next offset does not advance by the returned page')
    if end > count or (next_offset is None and end != count):
        raise ValueError('MCP matched count does not equal completed pagination')
    return count, next_offset


async def _analyze(trace, private, public, page_size):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    trace = trace.resolve(strict=True)
    params = StdioServerParameters(command=sys.executable,
        args=['-m', 'camoufox_trace_mcp', 'serve', '--trace-root', str(trace.parent)],
        env={key: value for key, value in os.environ.items() if key in ('PATH', 'PYTHONPATH', 'VIRTUAL_ENV', 'LANG', 'LC_ALL')})
    private.mkdir(parents=True, exist_ok=True)
    public.mkdir(parents=True, exist_ok=True)
    tool_calls = 0
    receipt_path = private / 'mcp-tools.jsonl'
    def private_stream(path):
        return os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8')

    with private_stream(receipt_path) as receipts, private_stream(private / 'mcp-server.stderr') as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as session:
                initialization = await session.initialize()
                receipts.write(json.dumps({'initialize': initialization.model_dump(mode='json')}, ensure_ascii=True) + '\n')

                async def call(tool, args):
                    nonlocal tool_calls
                    result = await session.call_tool(tool, args)
                    tool_calls += 1
                    receipts.write(json.dumps({'tool': tool, 'arguments': args, 'result': result.model_dump(mode='json')}, ensure_ascii=True) + '\n')
                    receipts.flush()
                    if result.isError:
                        raise ValueError(f'MCP tool failed: {tool}')
                    value = result.structuredContent
                    if not isinstance(value, dict):
                        raise ValueError(f'MCP tool omitted structured content: {tool}')
                    return value

                async def pages(tool, field):
                    items, offset, matched = [], 0, None
                    while True:
                        page = await call(tool, {'path': trace.name, 'offset': offset, 'limit': page_size})
                        matched, following = validate_page(page, field, offset, matched)
                        items.extend(page[field])
                        if following is None:
                            return items
                        offset = following

                summary = await call('summarize_trace', {'path': trace.name})
                events = await pages('get_trace_events', 'events')
                scripts = await pages('get_trace_scripts', 'scripts')
                if summary.get('event_count') != len(events):
                    raise ValueError('MCP summary and event pagination disagree')
                if summary.get('engine', {}).get('script_count') != len(scripts):
                    raise ValueError('MCP summary and script pagination disagree')
    report = build_report(summary, events, scripts)
    report['tool_calls'] = tool_calls
    report_path = public / 'mcp-observations.json'
    encoded = (json.dumps(report, ensure_ascii=True, indent=2) + '\n').encode()
    report_path.write_bytes(encoded)
    return {'passed': True, 'transport': report['transport'], 'tool_calls': tool_calls,
            'report': report_path.name, 'report_sha256': hashlib.sha256(encoded).hexdigest(),
            'integrity': report['integrity'], 'coverage': report['coverage']}


def analyze_with_mcp(trace: Path, private: Path, public: Path, *, page_size=PAGE_SIZE):
    """Run a real, read-only MCP session; failures are fatal to this contract."""
    if type(page_size) is not int or not 1 <= page_size <= 1000:
        raise ValueError('page_size must be between 1 and 1000')
    private, public = Path(private).resolve(), Path(public).resolve()
    if private == public or private.is_relative_to(public):
        raise ValueError('Private MCP receipts must be outside public output')
    return asyncio.run(_analyze(Path(trace), private, public, page_size))
