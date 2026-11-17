"""Measure one DeepSeek configuration in the isolated GPU Job, using stdlib only."""
import argparse
import ast
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, '/runtime')


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')
    path.chmod(0o600)


def emit(event, **data):
    print(json.dumps({'event': event, **data}), flush=True)


def resources():
    c = Path('/sys/fs/cgroup')
    out = {k: (c / k).read_text().strip() for k in
           ('cpu.max', 'cpu.stat', 'memory.current', 'memory.peak', 'memory.max', 'memory.events', 'io.stat')}
    mem = dict(x.split(':', 1) for x in Path('/proc/meminfo').read_text().splitlines())
    out['host_available_bytes'] = int(mem['MemAvailable'].split()[0]) * 1024
    return out


def counters(raw):
    return {k: int(v) for k, v in (x.split() for x in raw.splitlines())}


def arguments(args, changes):
    args = list(args)
    for flag, value in changes.items():
        while flag in args:
            i = args.index(flag)
            del args[i:i + (1 if flag == '--profile-readonly' else 2)]
        if value is not None:
            args += [flag] + ([] if value is True else [str(value)])
    return args


def cases():
    incident = '\n'.join(
        f'Section {i}: deployment payments-{i % 7}, namespace production. '
        f'Observed replica count 3; readiness probe GET /ready on port 8080. '
        f'The application log reports upstream timeout after 5 seconds. '
        f'NetworkPolicy allows DNS UDP53 and TCP5432 to the database. '
        f'Node pool worker-{i % 4}; image version 2026.11.{i % 17 + 1}; '
        'no OOM event. The change under review increased request concurrency from 8 to 32.'
        for i in range(70))
    return [
        ('coding', 'You are a Python coding assistant. Output code only.',
         'Implement a complete thread-safe TTL LRU cache in Python using only the standard library. '
         'Include capacity and TTL validation, monotonic time, get, put, delete, clear, statistics, '
         'and unittest tests for expiration, eviction, overwriting, missing keys and concurrent access. '
         'Output the complete module and the full test suite without prose.'),
        ('technical', 'Rispondi in italiano con spiegazioni tecniche concrete.',
         'Scrivi una guida operativa dettagliata, di almeno 900 parole, per diagnosticare un servizio '
         'Kubernetes che alterna risposte lente e 503. Descrivi come distinguere saturazione CPU, '
         'pressione di memoria, I/O, probe, Service, EndpointSlice, DNS, Ingress e connessioni database. '
         'Per ogni passaggio includi un comando, il risultato atteso e come interpretarlo. '
         'Prosegui fino a completare tutti i passaggi.'),
        ('long-context', 'You are an infrastructure engineer. Use the supplied incident evidence.',
         incident + '\nWrite a detailed incident analysis of at least 900 words. '
         'Separate observed facts from hypotheses, explain how the concurrency change could expose '
         'database connection-pool exhaustion, and give a reproducible investigation plan with '
         'commands and expected observations. Include mitigations, validation and rollback.')]


def pure_code_check(text):
    match = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.S)
    code = match.group(1) if match else text.strip()
    tree = ast.parse(code)
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef), 'expected exactly one function'
    fn = tree.body[0]
    assert fn.name == 'merge_intervals' and not fn.decorator_list, 'unexpected function name/decorator'
    safe_calls = {'sorted', 'len', 'range', 'min', 'max', 'list', 'tuple', 'enumerate', 'merge_intervals'}
    for n in ast.walk(tree):
        assert not isinstance(n, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef)), type(n).__name__
        if isinstance(n, ast.Name):
            assert not n.id.startswith('__'), 'dunder name'
        if isinstance(n, ast.Attribute):
            assert n.attr in {'append', 'pop', 'sort', 'copy', 'extend'}, 'method ' + n.attr
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            assert n.func.id in safe_calls, 'call ' + n.func.id
    # The generated function runs without file/network/import builtins, with an independent oracle.
    harness = '''import json,sys,resource
resource.setrlimit(resource.RLIMIT_CPU,(2,2))
resource.setrlimit(resource.RLIMIT_AS,(256*1024**2,256*1024**2))
d={"__builtins__":{k:__builtins__.__dict__[k]for k in ("sorted","len","range","min","max","list","tuple","enumerate")}}
exec(json.load(sys.stdin),d)
tests=[([],[]),([[1,3],[2,6],[8,10],[15,18]],[[1,6],[8,10],[15,18]]),([[1,4],[4,5]],[[1,5]]),([[5,7],[1,2],[2,3]],[[1,3],[5,7]]),([[-3,-1],[-2,2],[9,9]],[[-3,2],[9,9]]),([[1,10],[2,3],[4,8]],[[1,10]])]
for data,expected in tests:
 got=d["merge_intervals"](data)
 assert [list(x)for x in got]==expected,(got,expected)
print("six independent merge tests passed")
'''
    p = subprocess.run([sys.executable, '-I', '-c', harness], input=json.dumps(code),
                       text=True, capture_output=True, timeout=6)
    assert p.returncode == 0, p.stderr[-500:]
    return p.stdout.strip()


def run(root, variant, tokens, rounds, quality, reference=False):
    from gpu_guard import verify_gpu
    verify_gpu()
    work = root / variant['id']
    work.mkdir(mode=0o700)
    cfg = json.loads((root / 'base-config.json').read_text())
    # serve.server merges these standing overrides after args; the experiment
    # starts from the same effective baseline but owns every per-trial override.
    cfg['user_args'] = []
    cfg['args'] = arguments(cfg['args'], {
        '--expert-profile': str(root / 'frozen-expert-profile.bin'),
        '--profile-readonly': True, '--experts': '5', **variant.get('args', {})})
    cfg['log'] = str(work / 'engine.log')
    cfg['env'] = {**(cfg.get('env') or {}), 'DS4_LANE_CAL': 'none', **variant.get('env', {})}
    cfg['engine_ready_s'] = 240
    save(work / 'config.json', cfg)
    env = {**os.environ, **{k: str(v) for k, v in cfg['env'].items()}}
    env['LD_LIBRARY_PATH'] = ':'.join(cfg.get('lib_dirs', []) + [env.get('LD_LIBRARY_PATH', '')])
    port = 8081
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    key = cfg.get('api_key') or os.environ['API_KEY']
    stopped = threading.Event()
    alerts = []
    samples = []
    started = time.monotonic()
    log = (work / 'server.log').open('w')
    proc = subprocess.Popen([sys.executable, '-m', 'serve.server', '--engine', 'strata',
                             '--config', str(work / 'config.json'), '--host', '127.0.0.1',
                             '--port', str(port)], cwd='/opt/strata/terminal', env=env,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def terminate():
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def watch():
        low = 0
        while not stopped.wait(2):
            d = resources()
            d['elapsed_s'] = round(time.monotonic() - started, 2)
            with (work / 'resources.jsonl').open('a') as f:
                f.write(json.dumps(d) + '\n')
            low = low + 1 if (int(d['memory.current']) > .96 * int(d['memory.max'])
                              or d['host_available_bytes'] < 1300 * 1024**2) else 0
            if low >= 3 or time.monotonic() - started > 680:
                alerts.append('resource or time guard')
                terminate()
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    def http(route, body=None, timeout=30):
        req = urllib.request.Request(f'http://127.0.0.1:{port}/' + route,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        with opener.open(req, timeout=timeout) as r:
            return json.load(r)

    def chat(system, user, n, label):
        body = {'model': 'deepseek-v4.1-flash', 'messages': [
            {'role': 'system', 'content': label + '\n' + system},
            {'role': 'user', 'content': user}], 'temperature': 0, 'seed': 1729,
            'max_tokens': n, 'stream': False, 'chat_template_kwargs': {'enable_thinking': False}}
        begin = resources()
        t = time.monotonic()
        response = http('v1/chat/completions', body, timeout=300)
        elapsed = time.monotonic() - t
        end = resources()
        m = http('metrics')
        hist = m['requests'][0]
        message = response['choices'][0]['message']
        text = message.get('content') or ''
        c0, c1 = counters(begin['cpu.stat']), counters(end['cpu.stat'])
        periods = c1['nr_periods'] - c0['nr_periods']
        s = {'label': label, 'request_sha256': hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
             'elapsed_s': elapsed, 'decode_tps': hist['decode_tok_s'], 'metrics': hist,
             'usage': response.get('usage'), 'finish_reason': response['choices'][0].get('finish_reason'),
             'output': message, 'output_sha256': hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest(),
             'cpu_cores': (c1['usage_usec'] - c0['usage_usec']) / 1e6 / elapsed,
             'throttled_percent': 100 * (c1['nr_throttled'] - c0['nr_throttled']) / periods if periods else 0,
             'cgroup_before': begin, 'cgroup_after': end, 'hardware': m.get('hardware')}
        assert not alerts, alerts
        return text, s

    result = {'variant': variant, 'tokens': tokens, 'rounds': rounds, 'samples': samples, 'quality': {}}
    try:
        for _ in range(240):
            if proc.poll() is not None:
                raise RuntimeError('server exited during startup')
            try:
                h = http('health', timeout=2)
                if h.get('loaded'):
                    assert h['max_context'] == 262144 and h['images']
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        else:
            raise TimeoutError('engine startup')
        native = []
        for p in Path('/proc').iterdir():
            if not p.name.isdigit():
                continue
            try:
                argv = [x.decode() for x in (p / 'cmdline').read_bytes().split(bytes([0])) if x]
                if argv and Path(argv[0]).name == 'strata-dsv41':
                    native.append(argv)
            except OSError:
                pass
        assert len(native) == 1, 'expected one native benchmark engine'
        actual = native[0]
        for flag in ('--max-context', '--ssd-cache', '--dspark', '--dspark-block',
                     '--dspark-conf', '--cpu-threads', '--ssd-policy', '--experts'):
            if flag in cfg['args']:
                expected = cfg['args'][cfg['args'].index(flag) + 1]
                assert flag in actual and actual[actual.index(flag) + 1] == expected, (flag, expected, actual)
        result['effective_args'] = actual
        save(work / 'effective-args.json', actual)
        emit('loaded', variant=variant['id'], seconds=round(time.monotonic() - started, 1))
        if quality:
            for tag, question, expected in [
                ('capital', 'What is the capital of France? Answer with the city only.', 'paris'),
                ('arithmetic', 'Calculate 1729 * 37. Output only the integer.', '63973')]:
                text, s = chat('Follow the requested output format.', question, 64, 'Quality ' + tag)
                result['quality'][tag] = expected in text.lower()
                save(work / ('quality-' + tag + '.json'), s)
                # The already-deployed reference has an observed arithmetic failure.
                # Retain it in the evidence; every proposed replacement must still pass.
                if not reference or tag != 'arithmetic':
                    assert result['quality'][tag], (tag, text)
            text, s = chat('Output Python code only, no imports and no type annotations.',
                           'Define exactly one function def merge_intervals(intervals): which merges overlapping '
                           'or touching closed integer intervals and returns them sorted. Handle empty input '
                           'and unsorted input. Do not include examples or tests.', 512, 'Quality code')
            save(work / 'quality-code.json', s)
            result['quality']['code'] = pure_code_check(text)
        for name, system, user in cases():
            _, s = chat(system, user, 128, 'Warmup ' + name)
            s['case'], s['warmup'] = name, True
            samples.append(s)
            emit('warmup', variant=variant['id'], case=name, tps=s['decode_tps'])
        for round_id in range(rounds):
            for name, system, user in cases():
                _, s = chat(system, user, tokens, f'Measure {round_id} {name}')
                s['case'], s['warmup'], s['round'] = name, False, round_id
                samples.append(s)
                assert s['metrics']['output_tokens'] >= .85 * tokens, 'answer too short for speed comparison'
                save(work / 'report.json', result)
                emit('sample', variant=variant['id'], case=name, round=round_id,
                     tps=s['decode_tps'], cpu=round(s['cpu_cores'], 1), memory_GB=round(int(s['cgroup_after']['memory.current']) / 1e9, 2))
        measured = [s for s in samples if not s['warmup']]
        result['per_case'] = {name: math.exp(sum(math.log(s['decode_tps']) for s in measured if s['case'] == name) / rounds)
                              for name, _, _ in cases()}
        result['score'] = math.exp(sum(math.log(v) for v in result['per_case'].values()) / len(result['per_case']))
        result['ok'] = True
    except Exception as exc:
        result['ok'] = False
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['resources_end'] = resources()
        result['alerts'] = alerts
        terminate()
        try:
            proc.wait(timeout=35)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        stopped.set()
        watcher.join(timeout=4)
        log.close()
        result['elapsed_s'] = time.monotonic() - started
        save(work / 'report.json', result)
        compact = {k: v for k, v in result.items() if k not in ('samples', 'resources_end')}
        save(work / 'summary.json', compact)
        emit('result', **compact)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--variant', required=True)
    p.add_argument('--tokens', type=int, default=256)
    p.add_argument('--rounds', type=int, default=1)
    p.add_argument('--quality', action='store_true')
    p.add_argument('--reference-quality', action='store_true')
    a = p.parse_args()
    a.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    run(a.root, json.loads(a.variant), a.tokens, a.rounds, a.quality, a.reference_quality)
