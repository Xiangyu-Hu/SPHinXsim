#!/usr/bin/env python3
"""Prepare a standalone Docker context from a source directory or Git revision."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

EXCLUDED_DIRS = {
    '.git', '.hg', '.svn', '.venv', 'venv', '.build-temp', 'build', 'dist',
    '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'node_modules',
    '.idea', '.vscode', '.codex',
}
EXCLUDED_SUFFIXES = ('.pyc', '.pyo', '.so', '.dylib', '.dll', '.o', '.a', '.tar.gz', '.tar.bz2', '.tar.xz', '.zip')
REQUIRED_SOURCE = ('CMakeLists.txt', 'sphinxsim/sphinxsys/CMakeLists.txt',
                   'sphinxsim/sph_simulation/CMakeLists.txt', 'sphinxsim/config/schemas.py')


def validate_source(source):
    missing = [name for name in REQUIRED_SOURCE if not (source / name).is_file()]
    if missing:
        raise ValueError('Incomplete SPHinXsim source tree: ' + ', '.join(missing)
                         + '. Download the full source, including SPHinXsys.')


def manifest(source):
    """Hash portable paths and contents, rejecting symlinks outside the snapshot."""
    entries = {}
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if source not in resolved.parents:
                raise ValueError(f'Source symlink escapes snapshot: {relative}')
            if Path(path.readlink()).is_absolute():
                raise ValueError(f'Absolute source symlink is not portable: {relative}')
            entries[relative] = 'symlink:' + str(path.readlink())
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            entries[relative] = digest.hexdigest()
    encoded = json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()
    return entries, hashlib.sha256(encoded).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New directory outside the source and build bundle')
    parser.add_argument('--source', '--repository', dest='source', type=Path, required=True,
                        help='SPHinXsim source root, from Git clone or extracted ZIP')
    parser.add_argument('--revision', help='Optional Git commit/tag; omit to copy the supplied source files')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    bundle = Path(__file__).resolve().parent
    dest = args.destination.resolve()
    if dest.exists() or any(dest == p or p in dest.parents for p in (source, bundle)):
        parser.error('destination must be new and outside both source and build bundle')
    # Validate the selected snapshot after export in Git mode; the checkout can differ.
    if not args.revision:
        validate_source(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.sphinxsim-context-', dir=dest.parent))
    try:
        info = {}
        if args.revision:
            revision = subprocess.check_output(
                ['git', '-C', str(source), 'rev-parse', '--verify', args.revision + '^{commit}'], text=True).strip()
            tree = subprocess.check_output(['git', '-C', str(source), 'ls-tree', '-r', revision], text=True)
            if any(line.startswith('160000 ') for line in tree.splitlines()):
                raise ValueError('Git snapshot contains submodules; initialize them and use --source without --revision')
            archive = staging / 'source.tar'
            subprocess.run(['git', '-C', str(source), 'archive', '--format=tar',
                            '--output=' + str(archive), revision], check=True)
            (staging / 'source').mkdir()
            subprocess.run(['tar', '-xf', str(archive), '-C', str(staging / 'source')], check=True)
            archive.unlink()
            info.update(revision=revision, source_policy='committed Git snapshot; working-tree edits excluded')
            # The delivered build bundle is copied separately, not taken from the source revision.
            embedded = staging / 'source/docker'
            if embedded.is_symlink():
                embedded.unlink()
            elif embedded.is_dir():
                shutil.rmtree(embedded)
        else:
            def ignored(directory, names):
                directory = Path(directory)
                omitted = []
                for name in names:
                    path = directory / name
                    if (name in EXCLUDED_DIRS or name.startswith('cmake-build-')
                            or name.startswith('.env') or name == '.DS_Store'
                            or name.endswith(EXCLUDED_SUFFIXES)
                            or (directory == source and name in {'docker', 'output', 'results', 'restart', 'reload'})
                            or path.resolve() == bundle):
                        omitted.append(name)
                return omitted
            shutil.copytree(source, staging / 'source', ignore=ignored, symlinks=True)
            info['source_policy'] = 'copy supplied source directory, including local edits, excluding documented build/cache artifacts'
        validate_source(staging / 'source')
        entries, digest = manifest(staging / 'source')
        info.update(source_tree_sha256=digest, file_count=len(entries), files=entries)
        info.setdefault('revision', 'source-' + digest[:16])
        # Reject a missing runtime/build helper here rather than halfway through a Docker build.
        for name in ('dependencies.py', 'dependencies.lock.json', 'build.sh',
                     'runtime/run_case.py', 'runtime/sycl_probe.cpp'):
            if not (bundle / name).is_file():
                raise ValueError('Incomplete build bundle: ' + name)
        shutil.copytree(bundle, staging / 'build',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store',
                                                     '*.tar.*', '*.deb', '*-linux.sh'))
        shutil.copyfile(staging / 'build/context.dockerignore', staging / '.dockerignore')
        (staging / 'downloads').mkdir()
        (staging / 'source-revision.txt').write_text(info['revision'] + '\n')
        (staging / 'source-manifest.json').write_text(json.dumps(info, indent=2) + '\n')
        staging.rename(dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f'Prepared {dest}\nSource identity: {info["revision"]}\nFiles: {len(entries)}\nNo packages downloaded.')


if __name__ == '__main__':
    main()
