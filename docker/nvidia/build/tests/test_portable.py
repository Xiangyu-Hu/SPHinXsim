"""Run with: python3 -m unittest discover -s tests -v (no Docker/network required)."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BUNDLE = Path(__file__).resolve().parents[1]


class PortableBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sphinxsim-portable-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / 'arbitrary-extracted-name'
        shutil.copytree(BUNDLE, self.bundle, ignore=shutil.ignore_patterns('__pycache__'))
        self.source = self.root / 'downloaded-source'
        for name in ('CMakeLists.txt', 'sphinxsim/sphinxsys/CMakeLists.txt',
                     'sphinxsim/sph_simulation/CMakeLists.txt', 'sphinxsim/config/schemas.py'):
            file = self.source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('# source\n')
        self.context = self.root / 'new-context'

    def prepare(self, *extra):
        return subprocess.run([sys.executable, str(self.bundle / 'prepare-context.py'),
                               str(self.context), '--source', str(self.source), *extra],
                              text=True, capture_output=True)

    def test_source_archive_without_git_and_local_edits(self):
        (self.source / 'CMakeLists.txt').write_text('# local modification\n')
        # Local installer caches must not leak into the distributed build sources.
        (self.bundle / 'packages/oneapi-for-nvidia-gpus-2025.2.0-linux.sh').write_text('local cache')
        for name in ('.git/private', '.env', 'build/old.o', 'result-image.tar.gz', 'docker/history'):
            file = self.source / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('exclude me')
        run = self.prepare()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.context / 'source/CMakeLists.txt').read_text(), '# local modification\n')
        self.assertFalse((self.context / 'source/.git').exists())
        self.assertFalse((self.context / 'source/.env').exists())
        self.assertFalse((self.context / 'source/result-image.tar.gz').exists())
        self.assertFalse((self.context / 'source/docker').exists())
        info = json.loads((self.context / 'source-manifest.json').read_text())
        self.assertTrue(info['revision'].startswith('source-'))
        self.assertEqual(info['files']['CMakeLists.txt'], hashlib.sha256(b'# local modification\n').hexdigest())
        self.assertTrue((self.context / 'build/runtime/run_case.py').is_file())
        self.assertFalse((self.context / 'build/packages/oneapi-for-nvidia-gpus-2025.2.0-linux.sh').exists())
        # Refuse accidental overwrite on retry.
        self.assertNotEqual(self.prepare().returncode, 0)

    def test_missing_dependency_source_and_nested_destination_rejected(self):
        (self.source / 'sphinxsim/sphinxsys/CMakeLists.txt').unlink()
        self.assertNotEqual(self.prepare().returncode, 0)
        self.assertFalse(self.context.exists())
        self.context = self.source / 'nested-context'
        self.assertNotEqual(self.prepare().returncode, 0)

    def test_external_symlink_rejected(self):
        external = self.root / 'private-file'
        external.write_text('not part of source')
        (self.source / 'external').symlink_to(external)
        self.assertNotEqual(self.prepare().returncode, 0)
        self.assertFalse(self.context.exists())

    @unittest.skipUnless(shutil.which('git'), 'Git needed only for revision-mode check')
    def test_explicit_git_revision_ignores_worktree_changes(self):
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        subprocess.run(['git', '-C', str(self.source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Test',
                        '-c', 'user.email=test@example.com', 'commit', '-qm', 'fixture'], check=True)
        (self.source / 'CMakeLists.txt').write_text('# uncommitted\n')
        run = self.prepare('--revision', 'HEAD')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.context / 'source/CMakeLists.txt').read_text(), '# source\n')
        revision = subprocess.check_output(['git', '-C', str(self.source), 'rev-parse', 'HEAD'], text=True).strip()
        self.assertEqual((self.context / 'source-revision.txt').read_text().strip(), revision)

    def test_manual_cache_is_required_and_verified_before_copy(self):
        self.assertEqual(self.prepare().returncode, 0)
        cache = self.root / 'vendor-cache'
        cache.mkdir()
        name = 'oneapi-for-nvidia-gpus-2025.2.0-linux.sh'
        payload = b'vendor fixture'
        lock_path = self.context / 'build/dependencies.lock.json'
        lock_path.write_text(json.dumps({name: {
            'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
            'variants': ['cuda12'], 'url': None}}))
        command = [sys.executable, str(self.context / 'build/dependencies.py'), 'cuda12']
        missing = subprocess.run(command, text=True, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('provide --cache', missing.stderr)
        (cache / name).write_bytes(b'corrupt')
        self.assertNotEqual(subprocess.run(command + ['--cache', str(cache)], capture_output=True).returncode, 0)
        dest = self.context / 'downloads' / name
        self.assertFalse(dest.exists())
        (cache / name).write_bytes(payload)
        result = subprocess.run(command + ['--cache', str(cache)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dest.read_bytes(), payload)
        dest.write_bytes(b'corrupt')
        self.assertNotEqual(subprocess.run(command + ['--check'], capture_output=True).returncode, 0)

    def test_large_proxy_download_guard_before_network(self):
        self.assertEqual(self.prepare().returncode, 0)
        lock_path = self.context / 'build/dependencies.lock.json'
        lock = json.loads(lock_path.read_text())
        name = 'intel-oneapi-base-toolkit-2025.2.0.592_offline.sh'
        lock_path.write_text(json.dumps({name: lock[name]}))
        result = subprocess.run([sys.executable, str(self.context / 'build/dependencies.py'),
                                 'cuda12', '--download', '--proxy', 'http://127.0.0.1:1'],
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('exceeds 1 GB', result.stderr)
        self.assertEqual(list((self.context / 'downloads').iterdir()), [])

    def test_one_command_routes_source_to_image(self):
        # Real prepare/dependency code, tiny verified cache fixtures, mock only Docker.
        cache = self.root / 'cache'
        cache.mkdir()
        lock_path = self.bundle / 'dependencies.lock.json'
        lock = json.loads(lock_path.read_text())
        for name, spec in lock.items():
            if 'cuda13' in spec['variants']:
                payload = ('fixture:' + name).encode()
                (cache / name).write_bytes(payload)
                spec.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        lock_path.write_text(json.dumps(lock))
        binary = self.root / 'bin'
        binary.mkdir()
        docker = binary / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['DOCKER_CALLS'], 'a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1]=='info': print('linux/x86_64')
elif sys.argv[1]=='image': print('[]')
''')
        docker.chmod(0o755)
        calls = self.root / 'docker-calls.jsonl'
        env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                   DOCKER_CALLS=str(calls), PACKAGE_CACHE=str(cache), DOWNLOAD_DEPENDENCIES='0')
        for name in ('SOURCE_REVISION', 'IMAGE_TAG', 'IMAGE_PREFIX', 'DOWNLOAD_PROXY'):
            env.pop(name, None)
        result = subprocess.run(['bash', str(self.bundle / 'build-from-source.sh'), 'h20-cuda13',
                                 str(self.source), str(self.context)], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        build = next(row for row in map(json.loads, calls.read_text().splitlines()) if row[0] == 'build')
        self.assertEqual(build[build.index('-t') + 1], 'sphinxsim:h20-cuda13')
        self.assertEqual(build[build.index('-f') + 1], str(self.context / 'build/h20-cuda13/Dockerfile'))


if __name__ == '__main__':
    unittest.main()
