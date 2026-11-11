#!/usr/bin/env python3
"""Copy the user's checkout into a separate source tree and install checked files."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
COMMIT = '71e87cbf0e25c0400c0109dd25a64a7c4dccc9dd'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('course_root', type=Path)
    args = ap.parse_args()
    course = args.course_root.expanduser().resolve()
    original = course / 'systems/GateANN-public'
    destination = course / 'systems/GateANN-adaptive'
    if destination.exists():
        raise SystemExit('Destination already exists; preserved without changes: ' + str(destination))
    actual = subprocess.check_output(['git', '-C', str(original), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != COMMIT:
        raise SystemExit('Unexpected baseline commit: ' + actual)
    manifest = json.loads((BUNDLE / 'manifest.json').read_text())
    for name, hashes in manifest.items():
        if sha(original / name) != hashes['before_sha256']:
            raise SystemExit('Baseline source differs; no files changed: ' + name)
        if sha(BUNDLE / 'files' / name) != hashes['after_sha256']:
            raise SystemExit('Bundle integrity failure: ' + name)
    ignore = shutil.ignore_patterns('.git', 'build', 'build-*', '__pycache__', 'logs')
    # Includes third_party and existing AIO compatibility changes.
    shutil.copytree(original, destination, ignore=ignore)
    for name in manifest:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BUNDLE / 'files' / name, target)
    changed = subprocess.check_output(['git', '-C', str(original), 'diff'], text=True)
    (destination / 'course_baseline_changes.patch').write_text(changed, encoding='utf-8')
    (destination / 'adaptive_source_manifest.json').write_text(json.dumps({
        'baseline_commit': actual, 'files': manifest,
        'description': 'Separate source copy; original checkout/build/data unchanged'
    }, indent=2), encoding='utf-8')
    print('Prepared separate optimized source:', destination)
    print('Original checkout and baseline binary preserved:', original)

if __name__ == '__main__':
    main()
