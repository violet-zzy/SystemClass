#!/usr/bin/env python3
import hashlib
import json
import sys
from pathlib import Path

bundle=Path(__file__).resolve().parent
course=Path(sys.argv[1]).expanduser().resolve()
original=course/'systems/GateANN-public'
optimized=course/'systems/GateANN-adaptive'
manifest=json.loads((bundle/'manifest.json').read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
for name,hashes in manifest.items():
    if sha(original/name)!=hashes['before_sha256']:
        raise SystemExit('Baseline has changed: '+name)
    if sha(optimized/name)!=hashes['after_sha256']:
        raise SystemExit('Optimized source differs from this bundle: '+name)
print('PASS baseline preserved and optimized source hashes verified')
