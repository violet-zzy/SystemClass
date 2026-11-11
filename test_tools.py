#!/usr/bin/env python3
"""Check collection parsing and predicate/GT validation without performance claims."""
import csv
import importlib.util
import math
import struct
import tempfile
from pathlib import Path

spec=importlib.util.spec_from_file_location('runner',Path(__file__).with_name('run_experiments.py'))
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
sample='''  320   500  0.7000  10.50  300.00  20  30  40
ADAPTIVE_METRICS,L=320,queries=2,fallback_queries=1,fallback_fraction=0.5,mean_final_l=800
'''
r=runner.parse_metrics(sample,320)
assert r['mean_latency_us']==2000 and r['recall_percent']==70 and r['fallback_fraction']==.5
try:runner.parse_metrics(sample,640)
except ValueError:pass
else:raise AssertionError('Missing row accepted')
try:runner.parse_metrics(sample+sample,320)
except ValueError:pass
else:raise AssertionError('Duplicate row accepted')

def spmat(path,rows):
    ptr=[0];indices=[];weights=[]
    for row in rows:
        indices+=row;weights+=[1.0]*len(row);ptr.append(len(indices))
    path.write_bytes(struct.pack('<qqq',len(rows),10,len(indices))+
                    struct.pack('<'+'q'*len(ptr),*ptr)+
                    struct.pack('<'+'i'*len(indices),*indices)+
                    struct.pack('<'+'f'*len(weights),*weights))

with tempfile.TemporaryDirectory() as td:
    root=Path(td);work=root/'workloads'/'high';work.mkdir(parents=True)
    spmat(root/'base.metadata.spmat',[[1],[2],[1,2]])
    spmat(work/'query.metadata.spmat',[[1],[2]])
    (work/'ground_truth.ids.ibin').write_bytes(struct.pack('<IIiiii',2,2,0,2,1,2))
    detail=root/'queries.csv'
    def write(ids):
        with detail.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['query','latency_us','ids'])
            for i,v in enumerate(ids):w.writerow([i,10+i,v])
    write(['0;2','1;2'])
    validator=runner.ResultValidator(root)
    checked=validator.validate(work,detail,k=2)
    assert checked['verified_recall_percent']==100 and checked['missing_slots']==0
    write(['0;4294967295','1;2'])
    checked=validator.validate(work,detail,k=2)
    assert checked['verified_recall_percent']==75 and checked['missing_slots']==1
    write(['1;2','1;2'])
    try:validator.validate(work,detail,k=2)
    except ValueError as e:assert 'predicate' in str(e)
    else:raise AssertionError('Invalid label result accepted')
    write(['0;0','1;2'])
    try:validator.validate(work,detail,k=2)
    except ValueError as e:assert 'Duplicate' in str(e)
    else:raise AssertionError('Duplicate result accepted')
    validator.base.close()
print('PASS collection parser, recall recomputation, sparse predicate, missing slots and duplicate checks')
