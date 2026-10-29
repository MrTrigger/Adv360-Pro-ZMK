#!/usr/bin/env python3
"""Native Buildkite entrypoint. Event metadata comes from the trusted event broker."""
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
ENV = os.environ


def run(command, **kwargs):
    return subprocess.run(command, check=True, cwd=ROOT, **kwargs)


def output(command):
    return subprocess.check_output(command, cwd=ROOT, text=True).strip()


def context():
    if ENV.get('CI_REPOSITORY') != REPOSITORY:
        raise ValueError('Unexpected CI_REPOSITORY')
    if output(['git', 'rev-parse', 'HEAD']) != ENV['CI_COMMIT']:
        raise ValueError('Checkout must match the event source commit')
    if ENV['CI_EVENT'] not in {'push', 'pull_request', 'workflow_dispatch', 'release', 'schedule'}:
        raise ValueError('Unsupported CI_EVENT')


def comparison():
    base = ENV.get('CI_BASE_SHA', '')
    if not base and ENV['CI_EVENT'] == 'pull_request':
        branch = ENV['CI_BASE_BRANCH']
        run(['git', 'fetch', 'origin', '+refs/heads/' + branch + ':refs/remotes/origin/' + branch])
        return 'refs/remotes/origin/' + branch
    if base and re.fullmatch(r'[0-9a-fA-F]{40,64}', base) and set(base) != {'0'}:
        if subprocess.run(['git', 'cat-file', '-e', base + '^{commit}'], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            run(['git', 'fetch', 'origin', base])
        return base
    return ''


def changed(patterns):
    base = comparison()
    if not base:
        return True
    try:
        if ENV['CI_EVENT'] == 'pull_request':
            base = output(['git', 'merge-base', base, ENV['CI_COMMIT']])
        paths = output(['git', 'diff', '--name-only', base, ENV['CI_COMMIT']]).splitlines()
    except subprocess.CalledProcessError:
        return True
    patterns = list(patterns) + ['.buildkite/*', 'scripts/buildkite-ci.py']
    return any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)


def scope(keys):
    run(['python3', '.github/ci/check-toolchains.py', '--root', str(ROOT)])
    result = run(['bash', '.github/ci-scope.sh', ENV['CI_EVENT'], ENV.get('CI_PR_ACTION', ''),
                  comparison(), ENV['CI_COMMIT'], ENV.get('CI_PR_DRAFT', 'false')], capture_output=True, text=True)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if any(values.get(key) not in {'true', 'false'} for key in keys):
        raise ValueError('Scope script returned invalid booleans')
    return {key: values[key] == 'true' for key in keys}


def step(key, label, timeout=30, **kwargs):
    return {'key': key, 'label': label, 'command': 'python3 -B scripts/buildkite-ci.py ' + key,
            'timeout_in_minutes': timeout, **kwargs}


def upload(steps):
    if not steps:
        steps = [{'label': 'No checks selected', 'command': 'true', 'timeout_in_minutes': 1}]
    for job in steps:
        if job.get("agents", {}).get("capacity") == "large":
            job.update(concurrency=3, concurrency_group="personal-large", concurrency_method="eager")
    run(['buildkite-agent', 'pipeline', 'upload', '--no-interpolation'],
        input=json.dumps({'agents': {'queue': 'personal'}, 'steps': steps}), text=True)


def container(image, command, env=None):
    cache = Path(ENV.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'buildkite-native' / REPOSITORY.split('/')[1]
    cache.mkdir(parents=True, exist_ok=True)
    name = 'bk-' + ENV['BUILDKITE_JOB_ID']
    args = ['docker', 'run', '--rm', '--name', name, '--label', 'ci.job=' + ENV['BUILDKITE_JOB_ID'],
            '--user', str(os.getuid()) + ':' + str(os.getgid()), '-v', str(ROOT) + ':' + str(ROOT),
            '-v', str(cache) + ':/ci-cache', '-w', str(ROOT), '-e', 'HOME=/ci-cache',
            '-e', 'CI=1']
    for key, value in (env or {}).items():
        args += ['-e', key + '=' + value]
    try:
        run([*args, image, 'bash', '-euo', 'pipefail', '-c', command])
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def github(method, path, body=None):
    args = ['gh', 'api', '--method', method, path]
    kwargs = {'capture_output': True, 'text': True}
    if body is not None:
        args += ['--input', '-']
        kwargs['input'] = json.dumps(body)
    token = ENV.get('CI_GITHUB_TOKEN') or ENV.get('GH_TOKEN')
    if not token:
        raise ValueError('Scoped CI_GITHUB_TOKEN is required for GitHub metadata updates')
    kwargs['env'] = dict(ENV, GH_TOKEN=token)
    result = run(args, **kwargs)
    return json.loads(result.stdout) if result.stdout.strip() else None


def publish(repository, dockerfile='Dockerfile', tags=(), buildargs=(), provenance=True):
    if ENV['CI_EVENT'] not in {'push', 'workflow_dispatch'}:
        raise ValueError('Publication requires a trusted branch event')
    token = ENV.get('REGISTRY_PASSWORD')
    if not token:
        raise ValueError('Missing step-scoped REGISTRY_PASSWORD')
    with tempfile.TemporaryDirectory(prefix='bk-docker-auth-') as directory:
        environment = dict(ENV, DOCKER_CONFIG=directory)
        run(['docker', 'login', 'registry.wallintech.eu', '-u', ENV.get('REGISTRY_USERNAME', 'ci-publisher'), '--password-stdin'],
            input=token, text=True, env=environment)
        command = ['docker', 'buildx', 'build', '--file', dockerfile, '--push']
        if not provenance:
            command += ['--provenance=false']
        for tag in tags:
            command += ['--tag', repository + ':' + tag]
        for value in buildargs:
            command += ['--build-arg', value]
        run([*command, '.'], env=environment)


def interrupted(signum, frame):
    raise SystemExit(128 + signum)


REPOSITORY = 'MrTrigger/Adv360-Pro-ZMK'


def pipeline():
    selected = scope(('app', 'full', 'firmware'))
    if selected['app']:
        run(['python3', '-B', '-m', 'unittest', 'discover', '-s', 'scripts/tests'])
        run(['python3', 'scripts/bazel/zephyr/tools/native_tools_test.py'])
    steps = []
    if selected['firmware']:
        for flavor, name in [('legacy', 'firmware-no-clique'), ('studio', 'firmware-clique')]:
            steps.append(step(flavor, name, 60, agents={'queue': 'personal', 'capacity': 'large'}, artifact_paths='.buildkite-results/' + name + '/*.uf2'))
    upload(steps)


def firmware(flavor):
    os.environ['CI'] = '1'
    os.environ['ADV_BAZEL_CACHE'] = 'read'
    if ENV.get('PB_BAZEL_CACHE_READER'):
        os.environ['PERSONAL_BAZEL_CACHE_CREDENTIAL'] = ENV['PB_BAZEL_CACHE_READER']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M')
    run(['scripts/build-firmware', '--flavor', flavor, '--commit', ENV['CI_COMMIT'][:7],
         '--branch', ENV['CI_BRANCH'][:4], '--timestamp', stamp])
    name = 'firmware-no-clique' if flavor == 'legacy' else 'firmware-clique'
    target = ROOT / '.buildkite-results' / name
    target.mkdir(parents=True, exist_ok=True)
    suffix = '' if flavor == 'legacy' else '-clique'
    for side in ('left', 'right'):
        filename = stamp + '-' + ENV['CI_COMMIT'][:7] + '-' + side + suffix + '.uf2'
        source = ROOT / 'firmware' / filename
        if not source.is_file():
            raise ValueError('Missing firmware artifact: ' + filename)
        shutil.copyfile(source, target / filename)


ACTIONS = {'legacy': lambda: firmware('legacy'), 'studio': lambda: firmware('studio')}


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    context()
    action = sys.argv[1]
    if action == 'pipeline':
        pipeline()
    elif action in ACTIONS:
        ACTIONS[action]()
    else:
        raise SystemExit('Unknown Buildkite operation: ' + action)
