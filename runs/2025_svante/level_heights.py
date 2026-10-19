import netCDF4 as nc, numpy as np
d = nc.Dataset('/net/fs12/d1/shreya22/Parser/Output/aeic_runs/2025_full_v1/grids_v2/full_2025/full_2025-2025-01.nc')
A, B = np.asarray(d['hyai'][:]), np.asarray(d['hybi'][:]); p = A + B * 1013.25
z = 44330.77 * (1 - (p / 1013.25) ** 0.190263)
print('level  bottom_m  top_m  thickness_m   (ISA heights, surface pressure 1013.25 hPa)')
for k in range(12): print(f'{k + 1:4d}  {z[k]:8.0f} {z[k + 1]:6.0f} {z[k + 1] - z[k]:9.0f}')
print('3,000 ft = 914 m is reached at level', int(np.searchsorted(z, 914.4)), '(top of level', int(np.searchsorted(z, 914.4)), 'is at %.0f m)' % z[np.searchsorted(z, 914.4)])
