"""Build a deterministic source snapshot; never commit, push or contact GitHub."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT_FILES = {'AGENTS.md', 'README.md', 'SECURITY.md', 'COPYRIGHT.md', 'LICENSE',
              'VERSION', '.gitignore', 'config.example.json', 'install.ps1',
              'start.cmd', 'start.ps1', 'run.py', 'mcp_server.py',
              'requirements.txt', 'requirements.lock.txt'}
EXTENSIONS = {'lanbridge': {'.py'}, 'tests': {'.py', '.js', '.cjs', '.ps1', '.md'},
              'scripts': {'.py', '.ps1'}, 'skills': {'.md'},
              'docs': {'.md', '.png', '.svg'}, 'ui': {'.html', '.css', '.js', '.png', '.ico', '.svg'}}


def snapshot(root, names):
    entries = []
    for name in sorted(set(names)):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise ValueError('Unsafe archive path')
        if any(part.startswith('.') for part in path.parts) and name != '.gitignore':
            raise ValueError(f'Private or hidden path: {name}')
        allowed = name in ROOT_FILES or (len(path.parts) > 1 and path.suffix in EXTENSIONS.get(path.parts[0], set()))
        if not allowed or '__pycache__' in path.parts:
            raise ValueError(f'Unapproved source path: {name}')
        source = root.joinpath(*path.parts)
        if any(root.joinpath(*path.parts[:i]).is_symlink() for i in range(1, len(path.parts) + 1)):
            raise ValueError(f'Symlink excluded: {name}')
        source.resolve().relative_to(root.resolve())
        if not source.is_file():
            raise ValueError(f'Missing source: {name}')
        entries.append((name, source.read_bytes()))
    return entries


def build(root, names, output, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Invalid release version')
    entries = snapshot(root, names)
    required = ROOT_FILES - {'.gitignore', 'COPYRIGHT.md'}
    if not required <= {name for name, _ in entries}:
        raise ValueError('Required release files are missing')
    output.mkdir(parents=True, exist_ok=True)
    prefix = f'LanBridge-v{version}'
    archive = output / f'{prefix}-source.zip'
    manifest = output / 'manifest.json'
    checksums = output / 'SHA256SUMS.txt'
    if any(path.exists() for path in (archive, manifest, checksums)):
        raise ValueError('Output already exists; choose a new output directory')
    document = {'schema': 'lanbridge-source-manifest/v1', 'version': version,
                'source': 'local working-tree snapshot, including eligible untracked files',
                'files': [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in entries]}
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, data in [*entries, ('manifest.json', encoded)]:
            info = zipfile.ZipInfo(f'{prefix}/{name}', date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('Archive CRC verification failed')
        for name, data in entries:
            if bundle.read(f'{prefix}/{name}') != data:
                raise ValueError(f'Archive content verification failed: {name}')
    manifest.write_bytes(encoded)
    checksums.write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in (archive, manifest)), encoding='utf-8')
    return {'archive': str(archive), 'manifest': str(manifest), 'files': len(entries), 'bytes': archive.stat().st_size}


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=root / '.test-artifacts' / 'release')
    args = parser.parse_args()
    # Git supplies candidates; the explicit allowlist supplies the publication boundary.
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode('utf-8').split('\0')
    result = build(root, [name for name in names if name], args.output_dir.resolve(), (root / 'VERSION').read_text().strip())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
