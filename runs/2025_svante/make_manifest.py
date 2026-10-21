"""Write manifest.json for the run: path, size, mtime and sha256 of every input and of run_config.toml, the models'
combined hash, and the commit, tag and clean state of the three repositories. Refuses to overwrite an existing manifest.
usage: make_manifest.py [note ...]"""
import os, sys, json, hashlib, subprocess, glob, tomllib, datetime as dt
from pathlib import Path
O = Path(os.environ['RUN_DIR'])
cfg = tomllib.load(open(O / 'run_config.toml', 'rb'))
out = O / os.environ.get('MANIFEST', 'manifest.json')
assert not out.exists(), f'{out} exists, not overwriting'
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 24), b''): h.update(b)
    return h.hexdigest()
def info(p):
    p = Path(p); s = p.stat(); return dict(path=str(p.resolve()), size=s.st_size, mtime=dt.datetime.fromtimestamp(s.st_mtime, dt.UTC).isoformat(), sha256=sha(p))
def git(repo, *a): return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True).stdout.strip()
repos = {}
for n, r in cfg['repos'].items():
    head = git(r['path'], 'rev-parse', 'HEAD')
    repos[n] = dict(path=r['path'], remote=r['remote'], branch=git(r['path'], 'branch', '--show-current'), commit=head, expected_commit=r['commit'],
                    matches_config=head == r['commit'], tag=git(r['path'], 'describe', '--tags', '--exact-match'), dirty=bool(git(r['path'], 'status', '--porcelain', '--untracked-files=no')))
    assert repos[n]['matches_config'] and not repos[n]['dirty'] and repos[n]['tag'] == cfg['version']['tag'], (n, repos[n])
P = cfg['paths']
inputs = {'run_config': O / 'run_config.toml', 'mission_db': P['mission_db'], 'edb': P['edb'], 'load_factors': P['load_factors'],
          'aircraft_parameters': P['aircraft_parameters'], 'engine_sn_overrides': P['sn_overrides'], 'selector_config': Path(P['models_dir']) / 'config.toml',
          'grid_geoschem_0.5x0.625': Path(repos['aeic']['path']) / 'src/AEIC/data/grids/geoschem-0.5x0.625.toml'}
models = sorted(glob.glob(P['models_dir'] + '/*.toml'))
m = dict(created=dt.datetime.now(dt.UTC).isoformat(), version=cfg['version']['tag'], repos=repos, inputs={k: info(v) for k, v in inputs.items()},
         models=dict(directory=P['models_dir'], count=len(models) - 1, sha256_of_sorted_file_hashes=hashlib.sha256(''.join(sorted(sha(p) for p in models)).encode()).hexdigest()),
         merra2_source=P['merra2_source'], aeic_run_args=cfg['aeic_run']['args'], notes=sys.argv[1:])
out.write_text(json.dumps(m, indent=1)); print('wrote', out); print({k: (v['commit'][:7], v['tag'], v['dirty']) for k, v in repos.items()}, 'models', m['models']['count'])
