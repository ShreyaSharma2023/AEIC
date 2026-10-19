"""Preflight for the 2025 v2 run. Read-only. Every check prints PASS/FAIL; exit 1 if any FAIL.
usage: preflight.py RUN_DIR MISSION_DB"""
import sys, csv, sqlite3, tomllib, collections, datetime as dt, math
from pathlib import Path
import pandas as pd
R=Path(sys.argv[1]); DB=sys.argv[2]
fails=[]
def check(name, ok, detail=''):
    print(('PASS ' if ok else 'FAIL ')+name+(' : '+str(detail) if detail!='' else '')); 
    if not ok: fails.append(name)
t=pd.read_csv(R/'inputs/aircraft_parameters.csv',dtype=str,keep_default_na=False); k=t[t.performance_model_key!='']
keys=set(k.performance_model_key)
models={p.stem for p in (R/'models').glob('*.toml')}-{'config'}
check('table keys == model files', keys==models, f'{len(keys)} keys, {len(models)} models')
c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True)
n_s=c.execute('select count(*) from schedules').fetchone()[0]
check('schedule rows', 33_000_000<n_s<35_000_000, n_s)
check('every schedule has a flight level', c.execute('select count(*) from schedules where flight_level is null').fetchone()[0]==0)
lo,hi=c.execute('select min(flight_level),max(flight_level) from schedules').fetchone(); check('flight level range plausible (FL100-FL500)',100<=lo and hi<=500,(lo,hi))
nk=c.execute('select count(*) from schedules s join flights f on f.id=s.flight_id where f.performance_model_key is null').fetchone()[0]
check('no scheduled flight without a model key', nk==0, nk)
print('INFO flight rows with no key and no schedules (never flown):',c.execute('select count(*) from flights f where performance_model_key is null and not exists (select 1 from schedules s where s.flight_id=f.id)').fetchone()[0])
dbkeys={r[0] for r in c.execute('select distinct performance_model_key from flights where performance_model_key is not null')}
check('every database key has a model', dbkeys<=models, sorted(dbkeys-models)[:8])
print('INFO keys in table with no flights:',len(keys-dbkeys),'| keys in database:',len(dbkeys))
# flights per key vs table weights
fl=dict(c.execute('select f.performance_model_key,count(*) from schedules s join flights f on f.id=s.flight_id group by 1'))
w=k.assign(w=k.key_weighted_flights.astype(float)).set_index('performance_model_key').w
tot=sum(fl.values()); big=[]
for key,n in fl.items():
    if key in w.index and w[key]>5000 and abs(n-w[key])>0.1*w[key]: big.append((key,n,int(w[key])))
print('INFO keys with >5000 weighted flights whose database count differs from the table by >10%:',len(big),sorted(big,key=lambda x:-abs(x[1]-x[2]))[:5])
# models vs table
bad=[]
for r in k.itertuples():
    d=tomllib.load(open(R/'models'/f'{r.performance_model_key}.toml','rb')); L=d['LTO_performance']
    if not (L['ICAO_UID']==r.edb_uid and d.get('APU_name')==r.apu_name and d['number_of_engines']==int(r.n_engines) and d['aircraft_class']==r.aircraft_class and abs(d['operating_empty_mass_kg']-float(r.oew_kg))<1e-6): bad.append(r.performance_model_key)
check('each model matches its table row (uid, APU, engines, class, OEW)', not bad, bad[:5])
# load factors
have={(r['country_code_iso'],int(r['month'])) for r in csv.DictReader(open('/net/fs12/d1/shreya22/Parser/Input/load_factors.csv'))}
ctry=[r[0] for r in c.execute('select distinct a.country from flights f join airports a on a.id=f.origin')]+[r[0] for r in c.execute('select distinct a.country from flights f join airports a on a.id=f.destination')]
ctry=sorted({x for x in ctry if x})
miss=[(x,m) for x in ctry for m in range(1,13) if (x,m) not in have]
check('load factors for every country in the database and every month',not miss,(len(ctry),miss[:6]))
nullc=c.execute('select count(*) from flights f join airports a on a.id=f.origin join airports b on b.id=f.destination where a.country is null or b.country is null').fetchone()[0]
check('every flight has origin and destination country',nullc==0,nullc)
sys.path.insert(0,'/net/fs01/home/shreya22/AEIC/src')
from AEIC.config import Config
Config.load()
from AEIC.utils.airports import airport
codes=[r[0] for r in c.execute('select distinct iata_code from airports where id in (select origin from flights union select destination from flights)')]
unres=[x for x in codes if airport(x) is None]; check('every airport of the database resolves in AEIC airports table',not unres,unres[:8])
noelev=[x for x in codes if airport(x) is not None and airport(x).elevation is None]
n=c.execute("select count(*) from schedules s join flights f on f.id=s.flight_id join airports a on a.id in (f.origin,f.destination) where a.iata_code in (%s)"%','.join('?'*len(noelev)),noelev).fetchone()[0] if noelev else 0
print('WARN airports with no elevation in the AEIC airports table (flown as 0 m):',len(noelev),'| schedule rows touching them:',n,noelev[:12])
# MERRA2 content
import xarray as xr, numpy as np
base=Path('/net/geoschem/data/gcgrid/data/ExtData/GEOS_0.5x0.625/MERRA2'); badm=[]
d=dt.date(2024,10,30)
while d<=dt.date(2025,10,31):
    for stream,var,first in (('A3dyn','U','T01:30'),('I3','PS','T00:00')):
        f=base/f'{d:%Y/%m}/MERRA2.{d:%Y%m%d}.{stream}.05x0625.nc4'
        try:
            with xr.open_dataset(f) as x:
                ok=var in x.data_vars and x.sizes['time']==8 and str(x.time.values[0])[:16]==f'{d:%Y-%m-%d}'+first
        except Exception: ok=False
        if not ok: badm.append(f'{d}:{stream}')
    d+=dt.timedelta(1)
check('MERRA2 A3dyn/I3 files open with the right variable and first timestamp, 2024-10-30..2025-10-31',not badm,badm[:6])
print('FAILED CHECKS:',fails or 'none'); sys.exit(1 if fails else 0)
