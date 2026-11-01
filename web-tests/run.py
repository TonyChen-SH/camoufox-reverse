"""Exercise a verified native Camoufox build against documented reCAPTCHA pages."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tarfile
import threading
import time
from urllib.parse import urlsplit

MODES = ('off', 'binding', 'engine')
SCENARIOS = ('demo', 'test-key', 'script-failure')
ALL_SCENARIOS = SCENARIOS + ('demo-activation',)
RAW_STDERR_BYTES = 64 * 1024 * 1024
SOURCE_COMMIT = 'dee8bb66c386e76cfe771c60e80c407d4827ed62'
HTML = b'''<!doctype html><meta charset="utf-8"><title>reCAPTCHA test-key integration</title>
<h1>Official reCAPTCHA test-key integration</h1><div id="widget"></div>
<button id="reset" disabled>Reset test widget</button><pre id="state">{}</pre>
<script>
const audit={ready:false,callbacks:0,errors:0,expired:0,loadError:false,resets:0};
let widget;
function snapshot(){
 const response=audit.ready ? grecaptcha.getResponse(widget) : '';
 const field=document.querySelector('[name="g-recaptcha-response"]');
 document.querySelector('#state').textContent=JSON.stringify({...audit,
  responseLength:response.length,responseMatchesTextarea:!!field && field.value===response});
}
function ready(){widget=grecaptcha.render('widget',{
 sitekey:'6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI',
 callback:function(){audit.callbacks++;snapshot()},
 'error-callback':function(){audit.errors++;snapshot()},
 'expired-callback':function(){audit.expired++;snapshot()}
});audit.ready=true;document.querySelector('#reset').disabled=false;snapshot()}
document.querySelector('#reset').onclick=function(){grecaptcha.reset(widget);audit.resets++;snapshot()};
snapshot();
</script><script src="https://www.google.com/recaptcha/api.js?onload=ready&render=explicit"
async defer onerror="audit.loadError=true;snapshot()"></script>'''


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n')
    temporary.replace(path)


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def clean_url(url):
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if parsed.scheme in ('http', 'https'):
        # Never retain credentials, fragments or query parameters in public evidence.
        host = parsed.hostname or ''
        port = ':' + str(parsed.port) if parsed.port else ''
        return f'{parsed.scheme}://{host}{port}{parsed.path}'
    return 'about:' + parsed.path if parsed.scheme == 'about' else parsed.scheme + ':'


def google_source(url):
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return (parsed.hostname in ('www.google.com', 'www.gstatic.com', 'recaptcha.google.com')
            and '/recaptcha/' in parsed.path)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *_):
        pass


def browser_case(binary, mode, scenario, private, public):
    from native_stderr import create_wrapper, wait_receipt
    from playwright.sync_api import sync_playwright
    environment = os.environ.copy()
    if environment.get('MOZ_DISABLE_CONTENT_SANDBOX'):
        raise RuntimeError('Content sandbox must remain enabled')
    environment.update(CAMOUFOX_TRACE='1' if mode == 'binding' else '0',
                       CAMOUFOX_ENGINE_TRACE='1' if mode == 'engine' else '0')
    for key in ('CAMOUFOX_TRACE_MAX_EVENTS', 'CAMOUFOX_ENGINE_MAX_RECORDS',
                'CAMOUFOX_ENGINE_PRIVILEGED', 'CAMOUFOX_TRACE_STACK'):
        environment.pop(key, None)
    os.environ['DEBUG'] = 'pw:browser'
    os.environ['DEBUG_COLORS'] = '0'
    result = {'mode': mode, 'scenario': scenario, 'checks': {}, 'errors': [],
              'requests': [], 'responses': [], 'failures': [], 'frames': [],
              'scripts': [], 'workers': [], 'actions': [], 'console': [], 'page_errors': []}
    private.mkdir(parents=True, exist_ok=True)
    public.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    frames, requests = {}, {}
    def stamp():
        return round((time.monotonic() - start) * 1000, 2)
    def identifier(mapping, value, prefix):
        if value is None:
            return None
        if value not in mapping:
            mapping[value] = prefix + str(len(mapping) + 1)
        return mapping[value]
    def fid(frame):
        return identifier(frames, frame, 'f')
    def rid(request):
        return identifier(requests, request, 'r')
    def request_frame(request):
        try:
            return fid(request.frame)
        except Exception:
            return None
    def checkpoint():
        write_json(private / 'page.json', result)
    def frame_event(kind, frame):
        result['frames'].append({'event': kind, 't_ms': stamp(), 'frame_id': fid(frame),
            'parent_id': fid(frame.parent_frame), 'url': clean_url(frame.url)})
    def finished(request):
        if request.resource_type != 'script':
            return
        entry = {'request_id': rid(request), 'frame_id': request_frame(request),
                 'url': clean_url(request.url), 't_ms': stamp()}
        try:
            body = request.response().body()
            entry.update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
        except Exception as error:
            entry['read_error'] = type(error).__name__
            entry['read_error_message'] = str(error)
        result['scripts'].append(entry)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checkpoint()
    try:
        with sync_playwright() as playwright:
            launcher = create_wrapper(binary, private, max_bytes=RAW_STDERR_BYTES)
            browser = playwright.firefox.launch(executable_path=str(launcher), headless=False,
                env=environment, timeout=60000)
            result['browser_version'] = browser.version
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            page.on('frameattached', lambda f: frame_event('attached', f))
            page.on('framenavigated', lambda f: frame_event('navigated', f))
            page.on('framedetached', lambda f: frame_event('detached', f))
            page.on('request', lambda r: result['requests'].append({'t_ms': stamp(),
                'request_id': rid(r), 'frame_id': request_frame(r), 'type': r.resource_type,
                'method': r.method, 'url': clean_url(r.url)}))
            page.on('response', lambda r: result['responses'].append({'t_ms': stamp(),
                'request_id': rid(r.request), 'status': r.status, 'url': clean_url(r.url)}))
            page.on('requestfailed', lambda r: result['failures'].append({'t_ms': stamp(),
                'request_id': rid(r), 'url': clean_url(r.url), 'failure': r.failure}))
            page.on('requestfinished', finished)
            page.on('worker', lambda w: result['workers'].append({'t_ms': stamp(), 'url': clean_url(w.url)}))
            page.on('console', lambda m: result['console'].append({'type': m.type,
                'text_sha256': hashlib.sha256(m.text.encode()).hexdigest()}))
            page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
            if scenario == 'script-failure':
                context.route('https://www.google.com/recaptcha/api.js*',
                              lambda route: route.abort('internetdisconnected'))
            url = ('https://www.google.com/recaptcha/api2/demo' if scenario in ('demo', 'demo-activation')
                   else f'http://127.0.0.1:{server.server_port}/')
            try:
                response = page.goto(url, wait_until='domcontentloaded', timeout=30000)
                result['checks']['document_http_200'] = response.status == 200
                result['domcontentloaded_ms'] = stamp()
                checkpoint()
                if scenario == 'script-failure':
                    page.locator('#state').filter(has_text='"loadError":true').wait_for(timeout=10000)
                    state = json.loads(page.locator('#state').inner_text())
                    result['checks'].update(load_error_reported=state['loadError'],
                                            widget_not_ready=not state['ready'])
                else:
                    anchor = page.frame_locator('iframe[src*="/recaptcha/api2/anchor"]').locator('#recaptcha-anchor')
                    anchor.wait_for(state='visible', timeout=30000)
                    result['checks']['checkbox_visible'] = True
                    result['widget_ready_ms'] = stamp()
                    checkpoint()
                    if scenario == 'demo-activation':
                        result['actions'].append({'event': 'official_checkbox_click_attempt', 't_ms': stamp()})
                        checkpoint()
                        anchor.click(timeout=10000)
                        result['activation_observations'] = []
                        deadline = time.monotonic() + 20
                        while True:
                            checked = anchor.get_attribute('aria-checked', timeout=2000) == 'true'
                            frames_visible = page.locator('iframe[src*="/recaptcha/api2/bframe"]').first.is_visible()
                            result['activation_observations'].append({'t_ms': stamp(),
                                'checkbox_checked': checked, 'bframe_visible': frames_visible})
                            if checked or frames_visible or time.monotonic() >= deadline:
                                result['ui_outcome'] = ('checkbox_checked' if checked else
                                    'bframe_visible_unclassified' if frames_visible else 'no_terminal_ui_before_deadline')
                                break
                            page.wait_for_timeout(200)
                        if frames_visible:
                            result['activation_frame_text'] = page.frame_locator(
                                'iframe[src*="/recaptcha/api2/bframe"]').locator('body').inner_text(timeout=5000)
                        result['checks']['single_click_observation_completed'] = True
                        checkpoint()
                    elif scenario == 'test-key':
                        page.locator('#state').filter(has_text='"ready":true').wait_for(timeout=10000)
                        before = json.loads(page.locator('#state').inner_text())
                        result['checks']['initial_response_empty'] = before['responseLength'] == 0
                        result['actions'].append({'event': 'test_checkbox_click', 't_ms': stamp()})
                        anchor.click(timeout=10000)
                        page.locator('#state').filter(has_text='"callbacks":1').wait_for(timeout=15000)
                        state = json.loads(page.locator('#state').inner_text())
                        result['checks']['test_callback_and_response'] = state['responseLength'] > 0
                        result['checks']['response_matches_textarea'] = state['responseMatchesTextarea']
                        result['callback_state'] = state
                        checkpoint()
                        page.screenshot(path=str(public / 'before-reset.png'), timeout=10000)
                        result['actions'].append({'event': 'reset_requested', 't_ms': stamp()})
                        page.locator('#reset').click(timeout=10000)
                        page.locator('#state').filter(has_text='"resets":1').wait_for(timeout=10000)
                        after = json.loads(page.locator('#state').inner_text())
                        result['checks']['reset_clears_response'] = after['responseLength'] == 0
                        result['checks']['checkbox_reset'] = anchor.get_attribute('aria-checked', timeout=10000) == 'false'
                        result['actions'].append({'event': 'reset_completed', 't_ms': stamp()})
                        checkpoint()
                    page.wait_for_timeout(2000)
                result['frame_snapshot'] = [{'frame_id': fid(f), 'parent_id': fid(f.parent_frame),
                                            'url': clean_url(f.url)} for f in page.frames]
            except Exception as error:
                result['errors'].append({'type': type(error).__name__, 'message': str(error)})
            finally:
                try:
                    page.screenshot(path=str(public / 'page.png'), timeout=10000)
                except Exception as error:
                    result['screenshot_error'] = type(error).__name__
                result['elapsed_ms'] = stamp()
                result['page_checks_passed'] = bool(result['checks']) and all(result['checks'].values()) and not result['errors']
                checkpoint()
                context.close()
                browser.close()
                result['native_stderr'] = wait_receipt(private, timeout=5)
                result['browser_closed'] = True
                checkpoint()
    except Exception as error:
        result['errors'].append({'type': type(error).__name__, 'message': str(error)})
        result['page_checks_passed'] = False
        checkpoint()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0 if result.get('page_checks_passed') and result.get('browser_closed') else 1


def safe_page(page):
    # Error text can contain signed URLs; publish only types and hashes.
    result = {k: v for k, v in page.items() if k not in ('errors', 'page_errors', 'activation_frame_text')}
    if 'activation_frame_text' in page:
        result['activation_frame_text_sha256'] = hashlib.sha256(page['activation_frame_text'].encode()).hexdigest()
    result['errors'] = [{'type': e['type'], 'message_sha256': hashlib.sha256(e['message'].encode()).hexdigest()}
                        for e in page.get('errors', [])]
    result['scripts'] = [{**{k: v for k, v in script.items() if k != 'read_error_message'},
        **({'read_error_message_sha256': hashlib.sha256(script['read_error_message'].encode()).hexdigest()}
           if 'read_error_message' in script else {})} for script in page.get('scripts', [])]
    result['page_errors'] = [hashlib.sha256(e.encode()).hexdigest() for e in page.get('page_errors', [])]
    return result


def analyze_case(trace, mode, scenario, private, public, collection):
    from camoufox_trace_mcp import read_capture, summarize
    from camoufox_trace_mcp.reader import MAX_EVENTS, MAX_RECORDS, MAX_INPUT_BYTES
    from camoufox_trace_mcp.collector import DEFAULT_OUTPUT_BYTES
    capture = read_capture(trace)
    summary = summarize(capture)
    write_json(private / 'analysis.json', summary)
    page_path = private / 'page.json'
    page = json.loads(page_path.read_text()) if page_path.exists() else {'page_checks_passed': False}
    attributed = [event for event in capture.events if google_source((event.get('source') or {}).get('url'))]
    sources = Counter(clean_url((event.get('source') or {}).get('url')) for event in attributed)
    has_channels = (summary['event_count'] == 0 if mode == 'off' else
                    summary[mode + '_event_count'] > 0 and summary['engine_event_count' if mode == 'binding' else 'binding_event_count'] == 0)
    known_loss = summary['integrity'] != 'no_known_loss'
    passed = (page.get('page_checks_passed', False) and page.get('browser_closed', False)
              and has_channels and not known_loss and collection['termination'] == 'normal_exit'
              and collection['returncode'] == 0
              and collection.get('raw_stderr', {}).get('status') == 'complete'
              and page.get('native_stderr', {}).get('status') == 'complete'
              and (mode == 'off' or scenario == 'script-failure' or bool(attributed)))
    scripts = [{'url': clean_url(s.get('url')), 'sha256': s['source_sha256'],
                'utf16_length': s['utf16_length']} for s in capture.engine.scripts]
    result = {'mode': mode, 'scenario': scenario, 'passed': passed, 'page': safe_page(page),
        'capture': {k: summary[k] for k in ('integrity', 'statuses', 'event_count', 'binding_event_count', 'engine_event_count', 'input', 'first_loss_issue', 'loss_statuses', 'issue_samples')},
        'collection': collection, 'channel_checks_passed': has_channels,
        'google_attributed_events': len(attributed),
        'google_attributed_distinct_apis': len({event['api'] for event in attributed}),
        'google_sources': [{'url': key, 'events': count} for key, count in sorted(sources.items())],
        'engine_scripts': scripts, 'engine_script_count': len(scripts),
        'source_unavailable_count': len(capture.engine.source_unavailable),
        'source_provenance_complete': capture.engine.source_availability()['source_provenance_complete'],
        'trace_sha256': sha256(trace), 'trace_bytes': trace.stat().st_size,
        'budgets': {'binding_max_events_per_process': 10000, 'engine_max_records_per_process': 0,
                    'reader_max_events': MAX_EVENTS, 'reader_max_records': MAX_RECORDS,
                    'reader_max_input_bytes': MAX_INPUT_BYTES, 'collector_max_output_bytes': DEFAULT_OUTPUT_BYTES}}
    write_json(public / 'result.json', result)
    return result


def encrypt_private(private, recipient, destination):
    archive = private.parent / (private.name + '.tar.gz')
    try:
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(private, arcname='private-evidence')
        subprocess.run(['openssl', 'cms', '-encrypt', '-binary', '-aes256', '-in', str(archive),
                        '-outform', 'DER', '-out', str(destination), str(recipient)], check=True)
    finally:
        archive.unlink(missing_ok=True)
    return {'encrypted_bytes': destination.stat().st_size, 'sha256': sha256(destination),
            'recipient_sha256': sha256(recipient), 'format': 'OpenSSL CMS DER, AES-256-CBC with RSA recipient'}


def matrix(args):
    from camoufox_trace_mcp.collector import collect
    binary = args.binary.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise FileNotFoundError('The verified compiled Camoufox executable is required')
    if not args.recipient.is_file() or not args.source_baseline.is_file():
        raise FileNotFoundError('Source baseline and evidence recipient are required')
    if args.private_output.resolve().is_relative_to(args.output.resolve()):
        raise ValueError('Private captures must be outside the public output directory')
    args.output.mkdir(parents=True, exist_ok=False)
    args.private_output.mkdir(parents=True, exist_ok=False)
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'native_camoufox_tested': False,
              'source_commit': SOURCE_COMMIT, 'artifact_id': 12984328007, 'build_run': 41064733272,
              'binary_sha256': sha256(binary), 'libxul_sha256': sha256(binary.parent / 'libxul.so'),
              'baseline_sha256': sha256(args.source_baseline), 'display': 'Xvfb, headed Firefox, default packaged configuration',
              'scope': 'Compatibility and observability; public demo load and official v2 test-key integration only',
              'cases': []}
    write_json(args.output / 'report.json', report)
    try:
        for scenario in args.scenarios:
            for mode in args.modes:
                private = args.private_output / scenario / mode
                public = args.output / scenario / mode
                private.mkdir(parents=True)
                public.mkdir(parents=True)
                trace = private / 'trace.jsonl'
                before = resource.getrusage(resource.RUSAGE_CHILDREN)
                started = time.monotonic()
                os.environ['CAMOUFOX_ENGINE_TRACE'] = '1' if mode == 'engine' else '0'
                collection = collect([sys.executable, str(Path(__file__).resolve()), '--child',
                    '--binary', str(binary), '--mode', mode, '--scenario', scenario,
                    '--output', str(public.resolve()), '--private-output', str(private.resolve())],
                    trace, timeout=150, engine=mode == 'engine', binding=mode == 'binding',
                    raw_stderr_path=private / 'forwarded-stderr.bin', max_raw_stderr_bytes=RAW_STDERR_BYTES)
                try:
                    result = analyze_case(trace, mode, scenario, private, public, collection)
                    from mcp_observations import analyze_with_mcp
                    result['mcp'] = analyze_with_mcp(trace, private, public)
                    result['passed'] = result['passed'] and result['mcp']['passed']
                except Exception as error:
                    write_json(private / 'analysis-error.json', {'type': type(error).__name__, 'message': str(error)})
                    result = {'mode': mode, 'scenario': scenario, 'passed': False,
                              'page': safe_page(json.loads((private / 'page.json').read_text())) if (private / 'page.json').exists() else {},
                              'capture': {'event_count': None, 'integrity': 'analysis_failed'},
                              'analysis_error_type': type(error).__name__, 'collection': collection}
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                result['process_cpu_seconds'] = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
                result['collection_wall_seconds'] = round(time.monotonic() - started, 3)
                write_json(public / 'result.json', result)
                report['cases'].append(result)
                report['native_camoufox_tested'] = any(c['page'].get('browser_version') for c in report['cases'])
                write_json(args.output / 'report.json', report)
                print(json.dumps({'scenario': scenario, 'mode': mode, 'passed': result['passed'],
                      'page_passed': result['page'].get('page_checks_passed', False),
                      'events': result['capture']['event_count'], 'integrity': result['capture']['integrity']}), flush=True)
        report['passed'] = len(report['cases']) == len(args.scenarios) * len(args.modes) and all(c['passed'] for c in report['cases'])
    finally:
        report['private_evidence'] = encrypt_private(args.private_output, args.recipient, args.output / 'private-evidence.cms')
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(args.output / 'report.json', report)
    return 0 if report.get('passed') else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--private-output', required=True, type=Path)
    parser.add_argument('--source-baseline', type=Path)
    parser.add_argument('--recipient', type=Path)
    parser.add_argument('--child', action='store_true')
    parser.add_argument('--mode', choices=MODES)
    parser.add_argument('--scenario', choices=ALL_SCENARIOS)
    parser.add_argument('--modes', nargs='+', choices=MODES, default=MODES)
    parser.add_argument('--scenarios', nargs='+', choices=ALL_SCENARIOS, default=SCENARIOS)
    args = parser.parse_args()
    if args.child:
        if args.mode is None or args.scenario is None:
            parser.error('child mode requires --mode and --scenario')
        return browser_case(args.binary.resolve(), args.mode, args.scenario,
                            args.private_output.resolve(), args.output.resolve())
    if args.source_baseline is None or args.recipient is None:
        parser.error('matrix requires --source-baseline and --recipient')
    return matrix(args)


if __name__ == '__main__':
    raise SystemExit(main())
