import json, re, collections
res = json.load(open('/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/full/check_store.json'))
days = collections.Counter(); alld = collections.Counter()
for r in res:
    d = re.search(r'(\d{4}-\d\d-\d\d)-\d{3}\.nc$', r['f']).group(1); alld[d] += 1
    if not r['commit']: days[d] += 1
print('days with empty commit:', len(days), '| files per such day:', collections.Counter(days.values()))
print(' '.join(sorted(days)))
open('/net/fs12/d1/shreya22/Parser/Output/aeic_runs/v2026.10.12/logs/empty_commit_days.txt', 'w').write('\n'.join(sorted(days)) + '\n')
