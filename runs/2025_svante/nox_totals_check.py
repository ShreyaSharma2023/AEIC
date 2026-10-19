"""NOx and fuel totals of the GCHP case files of a run ($RUN_DIR/cases_species_mass), as GCHP will read them.

Ported from ~/14.7.1/Av_checks/nox_totals_check.py (the v2026.10.12 check), with two corrections:
- The per-class lines there were the no_wide / no_narrow / no_small case files labelled "wide", "narrow",
  "small" (so the class shares summed to 2). Here a class is all - no_class, the aviation removed in that run.
- It scaled the 12 files' 367 days to 365. Here the files' periods must add up to exactly 365 days (the flux is
  over the calendar month; the two UTC edge days are folded into January and December), so nothing is scaled.
Level heights: GEOS-Chem 72-level edges at 1013.25 hPa, ISA (as the gridder places emissions)."""
import os
import numpy as np, netCDF4 as nc
from AEIC.utils.geos_levels import AP_HPA, BP

D = os.environ['RUN_DIR'] + '/cases_species_mass'
M = {'NO': 30.0061, 'NO2': 46.0055, 'HONO': 47.0134}; N = 14.0067
FILES = ('all', 'no_wide', 'no_narrow', 'no_small'); CLASSES = ('wide', 'narrow', 'small')
SPECIES = ('NO', 'NO2', 'HONO', 'FUELBURN')
p = np.sort(AP_HPA + BP * 1013.25)[::-1]                     # surface first
ze = 44330.77 * (1 - (p / 1013.25) ** 0.190263) / 1000.0     # km edges, ascending
lev = {f: {s: np.zeros(72) for s in SPECIES} for f in FILES}; monthly = []; hemi = np.zeros(2); seconds = 0.0
for m in range(1, 13):
    for f in FILES:
        ds = nc.Dataset(f'{D}/AEIC_2025_{f}_{m:02d}.0.5x0.625.72L.nc'); ds.set_auto_mask(False)
        area = ds['AREA'][:].astype('f8'); ps = float(ds.period_seconds); lat = ds['lat'][:]
        tot = 0.0
        for s in SPECIES:
            x = ds[s][0].astype('f8'); lev[f][s] += (x * area).sum(axis=(1, 2)) * ps
            if s != 'FUELBURN' and f == 'all':
                tot += (x * area).sum() * ps * M['NO2'] / M[s]
                hemi += [(x[:, lat >= 0] * area[lat >= 0]).sum() * ps * M['NO2'] / M[s],
                         (x[:, lat < 0] * area[lat < 0]).sum() * ps * M['NO2'] / M[s]]
        if f == 'all': monthly.append(tot / 1e9); seconds += ps
        ds.close()
assert seconds == 365 * 86400.0, f'the 12 files cover {seconds / 86400} days, not 365: flux periods are wrong'
for c in CLASSES:
    lev[c] = {s: lev['all'][s] - lev[f'no_{c}'][s] for s in SPECIES}
def nox_lvl(k): return sum(lev[k][s] * M['NO2'] / M[s] for s in ('NO', 'NO2', 'HONO'))   # kg NO2 per level
def tmol_n(k): return sum(lev[k][s].sum() / M[s] for s in ('NO', 'NO2', 'HONO')) * 1e3 / 1e12  # kg/(g/mol) -> Tmol
print(f'files cover {seconds / 86400:.0f} days; totals are per year as read by GCHP (no scaling)')
for k in ('all',) + CLASSES:
    t = nox_lvl(k).sum() / 1e9; f = lev[k]['FUELBURN'].sum() / 1e9
    print(f'{k:7s} NOx {t:.3f} Tg NO2/yr = {tmol_n(k) * N:.3f} Tg N/yr | fuel {f:.1f} Tg/yr | EI(NOx) {t / f * 1000:.2f} g NO2/kg fuel'
          + ('' if k == 'all' else '   (all - no_' + k + ')'))
a = nox_lvl('all').sum()
print('class sum / all NOx =', round(sum(nox_lvl(c).sum() for c in CLASSES) / a, 6), ' (1 for additive classes)')
for c in CLASSES:
    print(f'  share of NOx {c}: {nox_lvl(c).sum() / a * 100:.1f}%  share of fuel: {lev[c]["FUELBURN"].sum() / lev["all"]["FUELBURN"].sum() * 100:.1f}%')
print('all-aviation species, Tg/yr as stored:', {s: round(lev['all'][s].sum() / 1e9, 3) for s in ('NO', 'NO2', 'HONO')},
      ' NO share of NOx (NO2 basis): %.1f%%' % (lev['all']['NO'].sum() * M['NO2'] / M['NO'] / a * 100))
nl = nox_lvl('all'); cum = np.cumsum(nl) / nl.sum()
for h in (1, 3, 8, 9, 10, 11, 12):
    i = np.searchsorted(ze[1:], h); print(f'  NOx below {h:>2d} km (levels <= {i}): {cum[min(i, 71)] * 100:5.1f}%')
print('  NOx between 8 and 13.5 km: %.1f%%' % (nl[(ze[:-1] >= 8) & (ze[1:] <= 13.5)].sum() / nl.sum() * 100))
print('  NOx in model level 1 (surface): %.2f%%; levels 1-7 (to ~931 m): %.2f%%' % (cum[0] * 100, cum[6] * 100))
print('  hemisphere NH/SH share of NOx: %.1f%% / %.1f%%' % tuple(hemi / hemi.sum() * 100))
mo = np.array(monthly); print('  monthly NOx (Tg NO2/month):', np.round(mo, 3), ' max/min=%.2f' % (mo.max() / mo.min()))
# Stock AEIC2019 for reference, same conversion.
S = '/net/geoschem/data/gcgrid/data/ExtData/HEMCO/AEIC2019/v2022-03/2019_monmean'; Rr = 6371007.2; st = {s: 0.0 for s in SPECIES}
for m in range(1, 13):
    d = nc.Dataset(f'{S}/AEIC_monmean_2019{m:02d}.0.5x0.625.36L.nc'); d.set_auto_mask(False); la = d['lat'][:]
    e = np.concatenate([[-90], (la[:-1] + la[1:]) / 2, [90]])
    ar = Rr * Rr * np.deg2rad(0.625) * (np.sin(np.deg2rad(e[1:])) - np.sin(np.deg2rad(e[:-1])))
    sc = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1] * 86400.0
    for s in st: st[s] += float((d[s][0].astype('f8').sum(axis=0) * ar[:, None]).sum()) * sc
    d.close()
sn = sum(st[s] * M['NO2'] / M[s] for s in ('NO', 'NO2', 'HONO')) / 1e9; sf = st['FUELBURN'] / 1e9
print(f'stock AEIC2019: NOx {sn:.3f} Tg NO2/yr, fuel {sf:.1f} Tg/yr, EI {sn / sf * 1000:.2f} g NO2/kg fuel;  '
      f'new/stock NOx = {a / 1e9 / sn:.3f}, fuel = {lev["all"]["FUELBURN"].sum() / 1e9 / sf:.3f}')
print('NOX CHECK DONE')
