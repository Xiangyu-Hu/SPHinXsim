#!/usr/bin/env python3
"""Check, reuse or download pinned build archives. No implicit proxy or downloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request

LIMIT = 1_000_000_000


def verify(path, spec):
    if path.stat().st_size != spec['bytes']:
        raise ValueError(f'{path.name}: incorrect size (expected {spec["bytes"]} bytes)')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != spec['sha256']:
        raise ValueError(f'{path.name}: SHA-256 mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('variant', choices=['cuda12', 'cuda13', 'all'])
    parser.add_argument('--context', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--cache', type=Path, action='append', default=[], help='Existing archive directory; repeatable')
    parser.add_argument('--download', action='store_true', help='Allow downloads for missing public archives')
    parser.add_argument('--proxy', help='Explicit HTTP(S) proxy, e.g. http://proxy.example.com:7890')
    parser.add_argument('--allow-large-proxy', action='store_true', help='Use only AFTER approval for proxy downloads >1 GB')
    parser.add_argument('--check', action='store_true', help='Verify only; never write or download')
    args = parser.parse_args()
    context = args.context.resolve()
    if not (context / 'source/CMakeLists.txt').is_file():
        parser.error('context must contain source/CMakeLists.txt; run prepare-context.py first')
    lock = json.loads((Path(__file__).resolve().parent / 'dependencies.lock.json').read_text())
    selected = {name: spec for name, spec in lock.items()
                if args.variant == 'all' or args.variant in spec['variants']}
    caches = args.cache
    download_dir = context / 'downloads'
    if not args.check:
        download_dir.mkdir(exist_ok=True)
    # Do not inherit HTTP_PROXY/HTTPS_PROXY from the shell or macOS system settings.
    proxies = {'http': args.proxy, 'https': args.proxy} if args.proxy else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    for name, spec in selected.items():
        dest = download_dir / name
        if dest.exists():
            verify(dest, spec)
            print(f'OK {name}', flush=True)
            continue
        if args.check:
            parser.error(f'missing {dest}')
        cached = next((p / name for p in caches if (p / name).is_file()), None)
        if cached:
            verify(cached, spec)
            descriptor, partial = tempfile.mkstemp(prefix='.' + name, dir=download_dir)
            os.close(descriptor)
            try:
                shutil.copyfile(cached, partial)
                verify(Path(partial), spec)
                os.replace(partial, dest)
            finally:
                Path(partial).unlink(missing_ok=True)
            print(f'Copied verified cache: {name}', flush=True)
            continue
        if not args.download or not spec['url']:
            parser.error(f'missing {name}; provide --cache DIRECTORY with this verified file'
                         + (' or use --download' if spec['url'] else ' (see packages/README.md for vendor acquisition instructions)'))
        print(f'{name}: {spec["bytes"]:,} bytes; {spec["url"]}', flush=True)
        if args.proxy and spec['bytes'] > LIMIT and not args.allow_large_proxy:
            parser.error('proxy download exceeds 1 GB; obtain approval, then pass --allow-large-proxy')
        descriptor, partial = tempfile.mkstemp(prefix='.' + name, dir=download_dir)
        os.close(descriptor)
        try:
            with opener.open(spec['url'], timeout=60) as response, open(partial, 'wb') as stream:
                size = response.headers.get('Content-Length')
                if size is not None and int(size) != spec['bytes']:
                    raise ValueError(f'{name}: server size differs from pinned size; refusing download')
                count = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > spec['bytes']:
                        raise ValueError(f'{name}: response exceeds pinned size')
                    stream.write(chunk)
            verify(Path(partial), spec)
            os.replace(partial, dest)
        finally:
            Path(partial).unlink(missing_ok=True)
        print(f'Download verified: {name}', flush=True)


if __name__ == '__main__':
    main()
