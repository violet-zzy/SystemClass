#!/usr/bin/env python3
"""Run real baseline/disabled/adaptive searches. No synthetic performance results."""
import argparse
import array
import csv
import datetime
import hashlib
import json
import math
import os
import random
import re
import statistics
import struct
import subprocess
import sys
from pathlib import Path

PATTERN = re.compile(r'^\s*(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$', re.M)

def parse_metrics(text, expected_l):
    rows = [m.groups() for m in PATTERN.finditer(text) if int(m[1]) == expected_l]
    if len(rows) != 1:
        raise ValueError('Expected exactly one timing row for L=' + str(expected_l))
    l, qps, recall, ios, skips, io_us, tunnel_us, process_us = map(float, rows[0])
    if qps <= 0 or not (0 <= recall <= 1):
        raise ValueError('Invalid reported performance row')
    result = dict(L=int(l), qps=qps, recall_percent=recall*100,
                  mean_latency_us=1e6/qps, mean_ios=ios, filter_skips=skips,
                  io_time_us=io_us, tunnel_time_us=tunnel_us, process_time_us=process_us)
    for line in text.splitlines():
        if line.startswith('ADAPTIVE_METRICS,'):
            for item in line.split(',')[1:]:
                key, value = item.split('=', 1)
                if key != 'L': result[key] = float(value)
    return result

def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values)*fraction)-1)]

class ResultValidator:
    """Check identity tags for this course index against the real sparse predicate/GT."""
    def __init__(self, tier):
        self.base = (tier / 'base.metadata.spmat').open('rb')
        self.nrow, _, self.nnz = struct.unpack('<qqq', self.base.read(24))
        self.ptr = array.array('q')
        self.ptr.fromfile(self.base, self.nrow + 1)
        if sys.byteorder != 'little': self.ptr.byteswap()
        self.indices_offset = 24 + (self.nrow + 1)*8
        self.values_offset = self.indices_offset + self.nnz*4
        self.labels = {}

    def node_labels(self, node):
        if node not in self.labels:
            if node < 0 or node >= self.nrow: raise ValueError('Out-of-range result ID')
            start, end = self.ptr[node], self.ptr[node + 1]
            count = end - start
            self.base.seek(self.indices_offset + start*4)
            labels = struct.unpack('<' + 'i'*count, self.base.read(count*4))
            self.base.seek(self.values_offset + start*4)
            weights = struct.unpack('<' + 'f'*count, self.base.read(count*4))
            self.labels[node] = {a for a,b in zip(labels,weights) if b != 0}
        return self.labels[node]

    def validate(self, workload, detail, k=10):
        with detail.open(newline='') as f: rows = list(csv.DictReader(f))
        with (workload / 'query.metadata.spmat').open('rb') as f:
            nq, _, nnz = struct.unpack('<qqq', f.read(24))
            ptr = struct.unpack('<' + 'q'*(nq+1), f.read((nq+1)*8))
            labels = struct.unpack('<' + 'i'*nnz, f.read(nnz*4))
            weights = struct.unpack('<' + 'f'*nnz, f.read(nnz*4))
        with (workload / 'ground_truth.ids.ibin').open('rb') as f:
            ngt, width = struct.unpack('<II', f.read(8))
            gt = struct.unpack('<' + 'i'*(ngt*width), f.read(ngt*width*4))
        if len(rows) != nq or ngt < nq: raise ValueError('Query output count mismatch')
        recalls, latencies = [], []
        missing = 0
        for i,row in enumerate(rows):
            if int(row['query']) != i: raise ValueError('Query order mismatch')
            required = {labels[j] for j in range(ptr[i],ptr[i+1]) if weights[j] != 0}
            result = [int(v) for v in row['ids'].split(';')]
            if len(result) != k: raise ValueError('Expected K output slots')
            valid = [v for v in result if v != 0xffffffff]
            missing += k-len(valid)
            if len(set(valid)) != len(valid): raise ValueError('Duplicate search result')
            for node in valid:
                if not required.issubset(self.node_labels(node)):
                    raise ValueError('Filter predicate violated at query '+str(i))
            expected = {v for v in gt[i*width:i*width+min(width,k)] if v != -1}
            if expected: recalls.append(len(expected.intersection(valid))/len(expected))
            latency = float(row['latency_us'])
            if not math.isfinite(latency) or latency < 0: raise ValueError('Invalid per-query latency')
            latencies.append(latency)
        return dict(verified_recall_percent=statistics.mean(recalls)*100 if recalls else 0,
                    query_latency_median_us=statistics.median(latencies),
                    query_latency_p99_us=percentile(latencies,.99),missing_slots=missing,
                    result_validation='PASS')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--course-root', type=Path, default=Path.home()/'fvs-course-lab')
    ap.add_argument('--smoke', action='store_true', help='debug high only, one repetition')
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--max-l', type=int, default=1280)
    ap.add_argument('--candidates-per-k', type=int, default=4)
    ap.add_argument('--skip-threshold', type=float, default=.85)
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    if args.repeats < 1 or not (320 <= args.max_l <= 1000000): ap.error('Invalid repeats/max L')
    if not (1 <= args.candidates_per_k <= 1000000) or not (0 <= args.skip_threshold <= 1): ap.error('Invalid policy')
    if os.name != 'posix' or not Path('/usr/bin/time').exists():
        raise SystemExit('Run on Linux/WSL with /usr/bin/time (sudo apt install time).')
    course = args.course_root.expanduser().resolve()
    baseline = course/'systems/GateANN-public/build/tests/search_disk_index_yfcc'
    optimized_repo = course/'systems/GateANN-adaptive'
    optimized = optimized_repo/'build/tests/search_disk_index_yfcc'
    for p in [baseline,optimized]:
        if not p.is_file(): raise SystemExit('Missing binary: '+str(p))
    tier_name = 'debug_100000' if args.smoke else 'formal_1000000'
    tier = course/'datasets/yfcc_course_v1'/tier_name
    prefix = course/'course_runs/indices/gateann'/tier_name/'yfcc'
    # The course results use identity tags. Reject a separate tag mapping here.
    if Path(str(prefix)+'_disk.index.tags').exists():
        raise SystemExit('Validator requires course identity tags; a tags file is present.')
    out = (args.output or course/'course_runs/optimization'/datetime.datetime.now().strftime('adaptive-%Y%m%d-%H%M%S')).resolve()
    out.mkdir(parents=True, exist_ok=False)
    fields = ['repeat','bucket','mode','L','max_l','qps','recall_percent','mean_latency_us','mean_ios',
              'filter_skips','io_time_us','tunnel_time_us','process_time_us','peak_rss_kb',
              'queries','fallback_queries','fallback_fraction','mean_final_l','mean_sum_l',
              'mean_pilot_exact','mean_exact_evaluations','verified_recall_percent',
              'query_latency_median_us','query_latency_p99_us','missing_slots','result_validation','raw_log']
    rows, commands = [], []
    def flush():
        with (out/'measurements.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader();w.writerows(rows)
        (out/'commands.json').write_text(json.dumps(commands,indent=2))
    def capture(command):
        return subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT).stdout
    env_info = dict(timestamp=datetime.datetime.now().astimezone().isoformat(), settings=vars(args).copy(),
                    baseline_commit=capture(['git','-C',str(course/'systems/GateANN-public'),'rev-parse','HEAD']).strip(),
                    uname=capture(['uname','-a']),cpu=capture(['lscpu']),memory=capture(['free','-h']),
                    compiler=capture(['c++','--version']),cache_protocol='No OS cache flush; randomized interleaving; FullAdj MAP_POPULATE',
                    source_manifest=json.loads((optimized_repo/'adaptive_source_manifest.json').read_text()))
    (out/'environment.json').write_text(json.dumps(env_info,default=str,indent=2))
    # Same binary with adaptation disabled is a regression control.
    configs = [('baseline',320),('patched_off',320),('adaptive',320)]
    if not args.smoke: configs += [('baseline',640),('baseline',1280)]
    buckets = ['high'] if args.smoke else ['high','medium','low']
    validator = ResultValidator(tier)
    rng = random.Random(149)
    for repeat in range(1,(1 if args.smoke else args.repeats)+1):
        jobs = [(bucket,mode,l) for bucket in buckets for mode,l in configs]
        rng.shuffle(jobs)
        for bucket,mode,l in jobs:
            job = out/f'r{repeat}-{bucket}-{mode}-L{l}';job.mkdir()
            workload = tier/'workloads'/bucket
            cmd = [str(baseline if mode=='baseline' else optimized),'uint8',str(prefix),'1','8',
                   str(workload/'query.u8bin'),str(tier/'base.metadata.spmat'),
                   str(workload/'query.metadata.spmat'),str(workload/'ground_truth.ids.ibin'),
                   '10','l2','pq','8','0','0','32',str(l)]
            child_env=os.environ.copy();child_env['LC_ALL']='C'
            child_env['GATEANN_ADAPTIVE_MAX_L']=str(args.max_l if mode=='adaptive' else 0)
            child_env['GATEANN_ADAPTIVE_CANDIDATES_PER_K']=str(args.candidates_per_k)
            child_env['GATEANN_ADAPTIVE_SKIP_THRESHOLD']=str(args.skip_threshold)
            child_env.pop('GATEANN_QUERY_CSV',None)
            if mode!='baseline':child_env['GATEANN_QUERY_CSV']=str(job/'queries.csv')
            timed=['/usr/bin/time','-v','-o',str(job/'memory.txt'),*cmd]
            print(f'[r{repeat}] {bucket} {mode} L={l}',flush=True)
            with (job/'run.log').open('w') as f:
                proc=subprocess.run(timed,env=child_env,stdout=f,stderr=subprocess.STDOUT,timeout=1800)
            commands.append(dict(repeat=repeat,bucket=bucket,mode=mode,command=cmd,exit_code=proc.returncode))
            flush()
            if proc.returncode:raise RuntimeError('Search failed; see '+str(job/'run.log'))
            row=parse_metrics((job/'run.log').read_text(),l)
            rss=re.search(r'Maximum resident set size \(kbytes\):\s*(\d+)',(job/'memory.txt').read_text())
            if not rss:raise ValueError('Missing peak RSS')
            row.update(repeat=repeat,bucket=bucket,mode=mode,max_l=args.max_l if mode=='adaptive' else l,
                       peak_rss_kb=int(rss[1]),raw_log=str((job/'run.log').relative_to(out)))
            if mode!='baseline':
                row.update(validator.validate(workload,job/'queries.csv'))
                if abs(row['verified_recall_percent']-row['recall_percent'])>.0051:
                    raise ValueError('Reported recall disagrees with saved result IDs')
            if mode=='adaptive' and 'fallback_fraction' not in row:raise ValueError('Adaptive telemetry missing')
            rows.append(row);flush()
    for bucket in buckets:
        before=statistics.median(r['recall_percent'] for r in rows if r['bucket']==bucket and r['mode']=='baseline' and r['L']==320)
        after=statistics.median(r['recall_percent'] for r in rows if r['bucket']==bucket and r['mode']=='patched_off')
        if abs(before-after)>.05:raise ValueError('Disabled-path recall regression: '+bucket)
    groups=sorted({(r['bucket'],r['mode'],r['L']) for r in rows})
    summary=[]
    for bucket,mode,l in groups:
        subset=[r for r in rows if (r['bucket'],r['mode'],r['L'])==(bucket,mode,l)]
        result=dict(bucket=bucket,mode=mode,L=l,runs=len(subset))
        for key in ['recall_percent','mean_latency_us','qps','mean_ios','peak_rss_kb','fallback_fraction','mean_final_l','mean_sum_l','query_latency_p99_us']:
            vals=[r[key] for r in subset if key in r]
            if vals:result[key]=statistics.median(vals)
        summary.append(result)
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))
    print('Measured results saved in',out)
    print('Compare at matched recall; a restart can increase total latency/IO.')

if __name__=='__main__':main()
