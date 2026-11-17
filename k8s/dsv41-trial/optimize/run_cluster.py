"""Bounded DeepSeek search in a temporary exclusive-GPU Job; restore serving in finally."""
import argparse
import base64
import copy
import datetime
import json
import math
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

K = ['/usr/bin/kubectl', '-n', 'vllm']
PY = '/opt/strata/terminal/python/bin/python3'
DEPLOY = 'strata-dsv41-trial'
SSH_HOST = os.environ.get('DSV41_SSH_HOST')
HOST_DATA = '/media/muflo/2TBfast1/strata-cache/dsv41'


def ssh(args):
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            '-o', 'ServerAliveInterval=10', SSH_HOST, shlex.join(args)]


def call(args, **kw):
    if not SSH_HOST:
        return subprocess.check_output(K + args, text=True, **kw)
    args = list(args)
    if args[0] in ('apply', 'create'):
        args.append('--validate=false')  # avoid downloading OpenAPI through the slow management proxy
    payload = None
    if '-f' in args:
        i = args.index('-f') + 1
        if Path(args[i]).is_file():
            payload = Path(args[i]).read_text()
            args[i] = '-'
    command = ssh(['sudo', '-n', '/var/lib/rancher/rke2/bin/kubectl',
                   '--kubeconfig=/home/muflo/.kube/config', '--request-timeout=12s', '-n', 'vllm', *args])
    attempts = 1 if 'exec' in args else 10
    for attempt in range(attempts):
        try:
            return subprocess.check_output(command, input=payload, text=True, **kw)
        except subprocess.CalledProcessError:
            if attempt + 1 == attempts:
                raise
            time.sleep(3)


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')
    path.chmod(0o600)


def main(root):
    root = root.resolve()
    resume_file = root / 'resume-state.json'
    resume = json.loads(resume_file.read_text()) if resume_file.exists() else None
    original = json.loads((root / 'deployment-before.json').read_text())
    stamp = root.name.removeprefix('dsv41-optimize-')
    name = 'strata-dsv41-opt-' + stamp.lower()
    remote = '/data/optimization/' + root.name
    cm = name + '-code'
    pod = None
    container_id = None
    scaled = bool(resume)  # a resumed experiment owns restoration of the maintenance window
    ledger = copy.deepcopy(resume['ledger']) if resume else []
    n = len(ledger)
    started = time.monotonic() - (resume.get('elapsed_minutes', 0) * 60 if resume else 0)
    runner = Path(__file__).with_name('run_variant.py')
    cm_doc = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': cm, 'namespace': 'vllm'},
              'data': {'run_variant.py': runner.read_text()}}
    spec = copy.deepcopy(original['spec']['template']['spec'])
    spec['restartPolicy'] = 'Never'
    spec['terminationGracePeriodSeconds'] = 30
    spec['volumes'].append({'name': 'optimizer', 'configMap': {'name': cm}})
    c = spec['containers'][0]
    for key in ('startupProbe', 'readinessProbe', 'livenessProbe', 'ports'):
        c.pop(key, None)
    c['command'] = [PY, '-c', 'import signal,sys,time;signal.signal(signal.SIGTERM,lambda *a:sys.exit(0));time.sleep(6800)']
    c['volumeMounts'].append({'name': 'optimizer', 'mountPath': '/optimizer', 'readOnly': True})
    job = {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': 'vllm'},
           'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 6900,
                    'template': {'metadata': {'labels': {'app.kubernetes.io/name': 'strata-dsv41-optimize',
                                                       'experiment': stamp.lower()}}, 'spec': spec}}}
    write(root / 'configmap.json', cm_doc)
    write(root / 'job.json', job)
    for p in ('configmap.json', 'job.json'):
        call(['apply', '--dry-run=server', '-f', str(root / p)])
    # Frozen launch and placement. The private config retains its key on the original NVMe only.
    init = f'''import pathlib,json,shutil,os
r=pathlib.Path({remote!r});r.mkdir(parents=True,exist_ok=False,mode=0o700)
c=json.load(open('/data/state/strata/launch/dsv41.json'))
p=r/'base-config.json';p.write_text(json.dumps(c,indent=2));p.chmod(0o600)
pack=pathlib.Path(c['args'][c['args'].index('--pack')+1])
shutil.copyfile(pack/'expert-profile.bin',r/'frozen-expert-profile.bin')
(r/'frozen-expert-profile.bin').chmod(0o600)
print('Frozen launch and expert profile saved')'''
    if SSH_HOST:
        host_root = HOST_DATA + remote.removeprefix('/data')
        init_host = f'''import pathlib,json,shutil
r=pathlib.Path({host_root!r});r.mkdir(parents=True,exist_ok=True,mode=0o700)
c=json.load(open({(HOST_DATA+'/state/strata/launch/dsv41.json')!r}))
p=r/'base-config.json';p.write_text(json.dumps(c,indent=2));p.chmod(0o600)
pack=pathlib.Path({HOST_DATA!r}+c['args'][c['args'].index('--pack')+1].removeprefix('/data'))
shutil.copyfile(pack/'expert-profile.bin',r/'frozen-expert-profile.bin')
(r/'frozen-expert-profile.bin').chmod(0o600)
print('Frozen launch saved on NVMe')'''
        print(subprocess.check_output(ssh(['sudo', '-n', '/usr/bin/python3', '-c', init_host]), text=True), flush=True)
    else:
        print(call(['exec', 'deployment/' + DEPLOY, '--', PY, '-c', init]), flush=True)

    def production_call(args):
        if not SSH_HOST:
            return call(['exec', 'deployment/' + DEPLOY, '--', *args])
        cri = ['sudo', '-n', '/var/lib/rancher/rke2/bin/crictl', '--runtime-endpoint',
               'unix:///run/k3s/containerd/containerd.sock']
        items = json.loads(subprocess.check_output(ssh([*cri, 'ps', '--name', 'strata', '-o', 'json']), text=True))['containers']
        matches = [x for x in items if x['labels'].get('io.kubernetes.pod.namespace') == 'vllm'
                   and x['labels'].get('io.kubernetes.pod.name', '').startswith(DEPLOY + '-')]
        assert len(matches) == 1, 'expected one serving container'
        return subprocess.check_output(ssh([*cri, 'exec', matches[0]['id'], *args]), text=True)

    def pod_call(args):
        if SSH_HOST and container_id:
            return subprocess.check_output(ssh(['sudo', '-n', '/var/lib/rancher/rke2/bin/crictl',
                '--runtime-endpoint', 'unix:///run/k3s/containerd/containerd.sock',
                'exec', container_id, *args]), text=True)
        return call(['exec', pod, '--', *args])

    def execute(variant, label, tokens=256, rounds=1, quality=False, reference=False):
        nonlocal n
        n += 1
        v = copy.deepcopy(variant)
        v['id'] = f'{n:02d}-{label}'
        desired = v.get('quota', 24)
        limits = copy.deepcopy(c['resources'])
        limits['limits']['cpu'] = str(desired)
        patch = {'spec': {'containers': [{'name': c['name'], 'resources': limits}]}}
        call(['patch', 'pod', pod, '--subresource=resize', '--type=strategic', '-p', json.dumps(patch)])
        for _ in range(30):
            quota = pod_call(['cat', '/sys/fs/cgroup/cpu.max']).split()[0]
            if int(quota) == desired * 100000:
                break
            time.sleep(1)
        else:
            raise RuntimeError('CPU quota was not applied')
        child = [PY, '/optimizer/run_variant.py', '--root', remote,
                 '--variant', json.dumps(v), '--tokens', str(tokens), '--rounds', str(rounds)]
        if quality:
            child.append('--quality')
        if reference:
            child.append('--reference-quality')
        print(json.dumps({'event': 'starting', 'variant': v, 'tokens': tokens, 'rounds': rounds}), flush=True)
        result = None
        if SSH_HOST:
            console = remote + '/' + v['id'] + '-console.log'
            launch = f'''import subprocess
f=open({console!r},'w')
p=subprocess.Popen({child!r},stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
print(p.pid)'''
            pod_call([PY, '-c', launch])
            position = 0
            host_root = HOST_DATA + remote.removeprefix('/data')
            until = time.monotonic() + 760
            with (root / 'progress.jsonl').open('a') as log:
                while time.monotonic() < until:
                    poll = f'''import pathlib,json
r=pathlib.Path({host_root!r});f=r/({v['id']!r}+'-console.log');s=r/{v['id']!r}/'summary.json'
with f.open('rb')as h:
 h.seek({position});t=h.read();pos=h.tell()
print(json.dumps({{'console':t.decode(errors='replace'),'position':pos,'summary':json.loads(s.read_text())if s.exists()else None}}))'''
                    try:
                        d = json.loads(subprocess.check_output(ssh(['sudo', '-n', '/usr/bin/python3', '-c', poll]), text=True))
                    except (subprocess.CalledProcessError, ValueError):
                        time.sleep(5)
                        continue
                    position = d['position']
                    if d['console']:
                        print(d['console'].rstrip(), flush=True)
                        log.write(d['console'])
                        log.flush()
                    if d['summary'] is not None:
                        result = d['summary']
                        break
                    time.sleep(5)
        else:
            argv = K + ['exec', pod, '--', *child]
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            with (root / 'progress.jsonl').open('a') as log:
                for line in proc.stdout:
                    print(line.rstrip(), flush=True)
                    log.write(line)
                    log.flush()
                    try:
                        d = json.loads(line)
                        if d.get('event') == 'result':
                            result = d
                    except ValueError:
                        pass
            proc.wait(timeout=20)
        if result is None:
            result = {'ok': False, 'variant': v, 'error': 'no result from runner'}
        ledger.append(result)
        write(root / 'ledger.json', ledger)
        write(root / 'status.json', {'last': result, 'elapsed_minutes': (time.monotonic() - started) / 60})
        return result

    def changed(base, args=None, env=None, quota=None):
        v = copy.deepcopy(base)
        v.pop('id', None)
        v['args'] = {**v.get('args', {}), **(args or {})}
        v['env'] = {**v.get('env', {}), **(env or {})}
        if quota:
            v['quota'] = quota
        return v

    def admissible(candidate, reference, gain=.05):
        if not candidate.get('ok'):
            return False
        ratios = [candidate['per_case'][k] / reference['per_case'][k] for k in reference['per_case']]
        return candidate['score'] > reference['score'] * (1 + gain) and min(ratios) >= .90

    def budget():
        return n < 18 and (time.monotonic() - started) < 68 * 60 and not (root / 'STOP').exists()

    try:
        # Two idle observations before releasing the production GPU.
        check = '''import urllib.request,json,os
r=urllib.request.Request('http://127.0.0.1:8080/metrics',headers={'Authorization':'Bearer '+os.environ['API_KEY']})
d=json.load(urllib.request.urlopen(r))['live'];print(json.dumps(d));assert d['state']=='idle'and not d.get('queued')'''
        current = json.loads(call(['get', 'deployment', DEPLOY, '-o', 'json']))
        already_down = bool(resume) and current['spec']['replicas'] == 0
        if not already_down:
            for _ in range(2):
                production_call([PY, '-c', check])
                time.sleep(1)
        call(['apply', '-f', str(root / 'configmap.json')])
        scaled = True
        call(['scale', 'deployment', DEPLOY, '--replicas=0'])
        for _ in range(75):
            old = json.loads(call(['get', 'pods', '-l', 'app.kubernetes.io/name=' + DEPLOY, '-o', 'json']))
            if not old['items']:
                break
            time.sleep(2)
        else:
            raise TimeoutError('serving pod did not release the GPU')
        call(['apply', '-f', str(root / 'job.json')])
        for _ in range(90):
            pods = json.loads(call(['get', 'pods', '-l', 'job-name=' + name, '-o', 'json']))
            if pods['items'] and all(x.get('ready') for x in pods['items'][0].get('status', {}).get('containerStatuses', [])) and pods['items'][0].get('status', {}).get('containerStatuses'):
                break
            time.sleep(2)
        else:
            raise TimeoutError('isolated Job was not ready')
        pod = pods['items'][0]['metadata']['name']
        container_id = pods['items'][0]['status']['containerStatuses'][0]['containerID'].split('://', 1)[1]
        write(root / 'runtime.json', {'job': name, 'pod': pod, 'configmap': cm, 'remote': remote})
        # Shutdown saves the serving process's latest placement counts. Freeze those, once.
        frozen_source = ('/data/optimization/' + Path(resume['source']).name + '/frozen-expert-profile.bin') if resume else None
        freeze = f'''import pathlib,json,shutil,hashlib
r=pathlib.Path({remote!r});c=json.load(open(r/'base-config.json'))
p=pathlib.Path({frozen_source!r}) if {bool(frozen_source)!r} else pathlib.Path(c['args'][c['args'].index('--pack')+1])/'expert-profile.bin'
shutil.copyfile(p,r/'frozen-expert-profile.bin')
print('Frozen profile SHA256',hashlib.sha256((r/'frozen-expert-profile.bin').read_bytes()).hexdigest())'''
        print(pod_call([PY, '-c', freeze]), flush=True)
        baseline = {'args': {}, 'env': {}, 'quota': 24}
        if resume:
            ref = ledger[0]
            best, best_variant = ref, baseline
            for out in ledger[1:]:
                if admissible(out, best):
                    best = out
                    best_variant = {k: v for k, v in out['variant'].items() if k != 'id'}
            print(json.dumps({'event': 'resumed', 'valid_runs': n, 'candidate': best_variant}), flush=True)
        else:
            ref = execute(baseline, 'baseline', quality=True, reference=True)
            if not ref['ok']:
                raise RuntimeError('baseline failed: ' + ref.get('error', 'unknown'))
            best, best_variant = ref, baseline
        # Greedy, one-knob candidates. Screening alone never promotes a production change.
        for block in (() if resume else (1, 2, 3, 5)):
            if not budget():
                break
            v = changed(baseline, {'--dspark': 'on', '--dspark-block': block, '--dspark-conf': '0.65'})
            out = execute(v, f'dspark-block{block}')
            if admissible(out, best):
                best, best_variant = out, v
        if not resume and best_variant.get('args', {}).get('--dspark') == 'on':
            for conf in ('0.50', '0.80'):
                if budget():
                    v = changed(best_variant, {'--dspark-conf': conf})
                    out = execute(v, 'dspark-conf' + conf)
                    if admissible(out, best):
                        best, best_variant = out, v
        choices = [
            ('quota32', {}, {}, 32),
            ('cpu16', {'--cpu-threads': '16'}, {}, None),
            ('pcores', {'--cpu-threads': None}, {'STRATA_DS4_CPU_LANE': 'pcores'}, None),
            ('smt32', {'--cpu-threads': '32'}, {'STRATA_DS4_CPU_LANE': 'smt'}, 32),
            ('ram48', {'--ssd-cache': '48'}, {}, None),
            ('ram40', {'--ssd-cache': '40'}, {}, None),
            ('policy-lfu', {'--ssd-policy': 'lfu'}, {}, None),
            ('policy-lfulru', {'--ssd-policy': 'lfulru'}, {}, None),
            ('readers4', {}, {'DS4_STORE_THREADS': '4'}, None),
            ('readers16', {}, {'DS4_STORE_THREADS': '16'}, None),
            ('ahead2', {}, {'DS4_AHEAD': '2'}, None),
            ('store-gpu', {}, {'DS4_STORE_ZC': '1'}, None)]
        confirmation_only = resume.get('confirmation_candidate') if resume else None
        for label, args, env, quota in (() if confirmation_only else choices):
            if resume and label in resume['completed_labels']:
                continue
            if not budget():
                break
            v = changed(best_variant, args, env, quota)
            out = execute(v, label)
            if admissible(out, best):
                best, best_variant = out, v
        # A separately bounded follow-up can isolate a quality-preserving hardware
        # setting after a model-side candidate failed, reusing the immediately
        # preceding A control with the same frozen profile and protocol.
        if confirmation_only:
            best_variant = confirmation_only
        # Independent starts in A/B/B/A order, longer outputs and quality gates.
        if best_variant != baseline:
            a1 = resume['confirmation_reference'] if confirmation_only else execute(baseline, 'confirm-A1', 512, 2, True, True)
            if confirmation_only:
                assert a1['ok'] and a1['tokens'] == 512 and a1['rounds'] == 2
                assert {k: v for k, v in a1['variant'].items() if k != 'id'} == baseline
                assert a1['quality']['capital'] and a1['quality']['code']
            b1 = execute(best_variant, 'confirm-B1', 512, 2, True)
            b2 = execute(best_variant, 'confirm-B2', 512, 2, True)
            a2 = execute(baseline, 'confirm-A2', 512, 2, True, True)
            ok = all(x.get('ok') for x in (a1, b1, b2, a2))
            ratios = {}
            if ok:
                for case in a1['per_case']:
                    aa = math.sqrt(a1['per_case'][case] * a2['per_case'][case])
                    bb = math.sqrt(b1['per_case'][case] * b2['per_case'][case])
                    ratios[case] = bb / aa
                gain = math.exp(sum(math.log(x) for x in ratios.values()) / len(ratios)) - 1
                ok = gain > .05 and min(ratios.values()) >= .95
            else:
                gain = None
            conclusion = {'confirmed': ok, 'candidate': best_variant, 'geomean_gain': gain,
                          'per_case_ratios': ratios, 'screening_best': None if confirmation_only else best}
            if confirmation_only:
                conclusion['reference_source'] = resume['confirmation_reference_source']
        else:
            control = execute(baseline, 'final-baseline', 512, 2, True, True)
            conclusion = {'confirmed': False, 'candidate': baseline,
                          'reason': 'no screening candidate met threshold', 'control': control}
        write(root / 'conclusion.json', conclusion)
        print(json.dumps({'event': 'conclusion', **{k: v for k, v in conclusion.items()
                                                   if k not in ('screening_best', 'control')}}), flush=True)
    finally:
        # A second cancellation must not interrupt GPU release or serving restoration.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if pod:
            # Archive public summaries, native logs and per-run telemetry; private API configs stay on NVMe.
            archive = f'''import pathlib,tarfile,io,base64
r=pathlib.Path({remote!r});b=io.BytesIO()
with tarfile.open(fileobj=b,mode='w:gz') as t:
 for p in r.rglob('*'):
  if p.is_file()and p.name not in ('config.json','base-config.json')and 'ds4-slots'not in p.relative_to(r).parts:t.add(p,arcname=str(p.relative_to(r)))
print(base64.b64encode(b.getvalue()).decode())'''
            try:
                (root / 'results.tar.gz').write_bytes(base64.b64decode(pod_call([PY, '-c', archive])))
            except Exception as exc:
                print('Archive unavailable:', type(exc).__name__, flush=True)
        try:
            call(['delete', 'job', name, '--ignore-not-found', '--wait=false'])
            call(['delete', 'configmap', cm, '--ignore-not-found'])
        finally:
            if scaled:
                call(['scale', 'deployment', DEPLOY, '--replicas=' + str(original['spec']['replicas'])])
                for _ in range(120):
                    d = json.loads(call(['get', 'deployment', DEPLOY, '-o', 'json']))
                    if d.get('status', {}).get('readyReplicas', 0) == original['spec']['replicas']:
                        break
                    time.sleep(2)
                else:
                    raise TimeoutError('serving did not return Ready')
                print(json.dumps({'event': 'serving-restored'}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args()
    signal.signal(signal.SIGTERM, lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    main(a.root)
