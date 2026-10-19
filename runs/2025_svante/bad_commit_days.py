import os
import json, re, collections
res = json.load(open(os.environ['RUN_DIR'] + '/full/check_store.json'))
days = collections.Counter(); alld = collections.Counter()
for r in res:
    d = re.search(r'(\d{4}-\d\d-\d\d)-\d{3}\.nc$', r['f']).group(1); alld[d] += 1
    if not r['commit']: days[d] += 1
print('days with empty commit:', len(days), '| files per such day:', collections.Counter(days.values()))
print(' '.join(sorted(days)))
open(os.environ['RUN_DIR'] + '/logs/empty_commit_days.txt', 'w').write('\n'.join(sorted(days)) + '\n')
