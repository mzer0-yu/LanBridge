import hashlib
import json
from pathlib import Path
import zipfile

import pytest
from scripts.build_release import ROOT_FILES, build, snapshot


def source_tree(root):
    root.mkdir()
    for name in ROOT_FILES:
        (root / name).write_text('example source\n', encoding='utf-8')
    (root / 'ui').mkdir()
    (root / 'ui' / 'index.html').write_text('<html>Example</html>', encoding='utf-8')
    return [*ROOT_FILES, 'ui/index.html']


def test_release_is_reproducible_and_manifest_matches_zip(tmp_path):
    root = tmp_path / 'source'
    names = source_tree(root)
    first = build(root, names, tmp_path / 'first', '1.0.0')
    second = build(root, reversed(names), tmp_path / 'second', '1.0.0')
    assert Path(first['archive']).read_bytes() == Path(second['archive']).read_bytes()
    manifest = json.loads(Path(first['manifest']).read_text())
    with zipfile.ZipFile(first['archive']) as bundle:
        assert len(bundle.namelist()) == len(names) + 1
        for item in manifest['files']:
            data = bundle.read('LanBridge-v1.0.0/' + item['path'])
            assert len(data) == item['bytes']
            assert hashlib.sha256(data).hexdigest() == item['sha256']
    for line in (tmp_path / 'first' / 'SHA256SUMS.txt').read_text().splitlines():
        digest, filename = line.split('  ', 1)
        assert hashlib.sha256((tmp_path / 'first' / filename).read_bytes()).hexdigest() == digest
    with pytest.raises(ValueError, match='already exists'):
        build(root, names, tmp_path / 'first', '1.0.0')


@pytest.mark.parametrize('name', ['data/state.sqlite', 'bin/cloudflared.exe', '.env',
                                   'ui/.env', '../outside.py', 'scripts/__pycache__/old.py', 'private.txt'])
def test_release_rejects_runtime_private_and_unknown_paths(tmp_path, name):
    with pytest.raises(ValueError):
        snapshot(tmp_path, [name])


def test_release_rejects_missing_required_files_and_invalid_version(tmp_path):
    with pytest.raises(ValueError, match='Required'):
        build(tmp_path, [], tmp_path / 'out', '1.0.0')
    with pytest.raises(ValueError, match='version'):
        build(tmp_path, [], tmp_path / 'out', '../escape')
