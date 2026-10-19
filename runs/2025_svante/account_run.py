import re, glob, json, sys, collections, os
from multiprocessing import Pool
import os
R=os.environ.get('SLICE_LOGS','/net/fs12/d1/shreya22/Parser/Output/aeic_run_2025/full_2025/slice_logs')
HDR=re.compile(r'Error simulating mission (\w+) -> (\w+) \((\w+), ([\d.]+) km\)')
EXC=re.compile(r'^(\w+(?:Error|Exception)): (.*)')
TS=re.compile(r'^\d{4}-\d\d-\d\d \d\d:')
def parse(f):
    flights=fails=0; causes=collections.Counter(); uids=collections.Counter(); nopm=0; block=None; last=None
    def close():
        nonlocal block,last
        if block is None: return
        msg=last or 'NO-EXCEPTION-LINE'
        if 'No usable nvPM data' in msg:
            m=re.search(r'Engine UID (\S+)',msg); causes['nvPM smoke-number gap']+=1; uids[m.group(1) if m else '?']+=1
        elif 'Ground-distance' in msg: causes['ground-distance non-convergence']+=1
        else: causes[msg[:90]]+=1
        block=None; last=None
    for line in open(f,errors='replace'):
        if TS.match(line):
            close()
            if 'Error simulating mission' in line: block=True
            elif 'Flights to process in slice' in line: flights=int(line.rsplit(':',1)[1])
            elif 'complete:' in line: fails=int(re.search(r'complete: (\d+) failed',line).group(1))
            elif 'no performance model for mission' in line: nopm+=1
        elif block:
            m=EXC.match(line)
            if m: last=m.group(1)+': '+m.group(2)
    close()
    return f,flights,fails,dict(causes),dict(uids),nopm
if __name__=='__main__':
    files=sorted(glob.glob(R+'/*/slice_*.log'))
    with Pool(16) as p: res=p.map(parse,files,chunksize=8)
    F=sum(r[1] for r in res); X=sum(r[2] for r in res); N=sum(r[5] for r in res)
    C=collections.Counter(); U=collections.Counter()
    for r in res: C.update(r[3]); U.update(r[4])
    print('slice logs',len(files),'flights in slices',F,'reported failed',X,'no-model skipped',N)
    print('classified failures',sum(C.values()))
    for k,v in C.most_common(12): print('%9d  %s'%(v,k))
    print('nvPM failures by engine UID (top 40):'); print(dict(U.most_common(40)))
    json.dump(dict(F=F,X=X,N=N,C=C,U=U,perslice={r[0]:[r[1],r[2]] for r in res}),open(os.environ.get('ACCOUNT_OUT','/net/fs12/d1/shreya22/Parser/Output/aeic_run_2025/run_accounting.json'),'w'))
