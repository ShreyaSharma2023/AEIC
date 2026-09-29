"""Vertical coordinate of the GEOS-5 / MERRA2 72-level hybrid grid.

MERRA2 (and GEOS-Chem's copy of it) puts its winds on terrain-following
"hybrid sigma-pressure" levels. The pressure at the edges of level ``L`` is

    p_edge(L) = Ap(L) + Bp(L) * PS

where ``PS`` is the surface pressure of the column, so the same level index is
at a different pressure in every column. ``Ap`` [hPa] and ``Bp`` (unitless)
are the published GMAO values, taken from GEOS-Chem's ``GeosUtil/pressure_mod.F90``
(the 72-level branch). Edge 1 is the surface (``Ap = 0``, ``Bp = 1``) and edge
73 the model top (0.01 hPa), so pressure falls as the edge index rises, and
level ``L`` lies between edges ``L`` and ``L + 1``.
"""

import numpy as np

AP_HPA = np.array(
    [
        0.000000e00,
        4.804826e-02,
        6.593752e00,
        1.313480e01,
        1.961311e01,
        2.609201e01,
        3.257081e01,
        3.898201e01,
        4.533901e01,
        5.169611e01,
        5.805321e01,
        6.436264e01,
        7.062198e01,
        7.883422e01,
        8.909992e01,
        9.936521e01,
        1.091817e02,
        1.189586e02,
        1.286959e02,
        1.429100e02,
        1.562600e02,
        1.696090e02,
        1.816190e02,
        1.930970e02,
        2.032590e02,
        2.121500e02,
        2.187760e02,
        2.238980e02,
        2.243630e02,
        2.168650e02,
        2.011920e02,
        1.769300e02,
        1.503930e02,
        1.278370e02,
        1.086630e02,
        9.236572e01,
        7.851231e01,
        6.660341e01,
        5.638791e01,
        4.764391e01,
        4.017541e01,
        3.381001e01,
        2.836781e01,
        2.373041e01,
        1.979160e01,
        1.645710e01,
        1.364340e01,
        1.127690e01,
        9.292942e00,
        7.619842e00,
        6.216801e00,
        5.046801e00,
        4.076571e00,
        3.276431e00,
        2.620211e00,
        2.084970e00,
        1.650790e00,
        1.300510e00,
        1.019440e00,
        7.951341e-01,
        6.167791e-01,
        4.758061e-01,
        3.650411e-01,
        2.785261e-01,
        2.113490e-01,
        1.594950e-01,
        1.197030e-01,
        8.934502e-02,
        6.600001e-02,
        4.758501e-02,
        3.270000e-02,
        2.000000e-02,
        1.000000e-02,
    ]
)
"""Ap at the 73 level edges, surface first [hPa]."""

BP = np.array(
    [
        1.000000e00,
        9.849520e-01,
        9.634060e-01,
        9.418650e-01,
        9.203870e-01,
        8.989080e-01,
        8.774290e-01,
        8.560180e-01,
        8.346609e-01,
        8.133039e-01,
        7.919469e-01,
        7.706375e-01,
        7.493782e-01,
        7.211660e-01,
        6.858999e-01,
        6.506349e-01,
        6.158184e-01,
        5.810415e-01,
        5.463042e-01,
        4.945902e-01,
        4.437402e-01,
        3.928911e-01,
        3.433811e-01,
        2.944031e-01,
        2.467411e-01,
        2.003501e-01,
        1.562241e-01,
        1.136021e-01,
        6.372006e-02,
        2.801004e-02,
        6.960025e-03,
        8.175413e-09,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
        0.000000e00,
    ]
)
"""Bp at the 73 level edges, surface first."""

N_LEVELS = 72


def edge_pressures_hpa(surface_pressure_hpa: np.ndarray) -> np.ndarray:
    """Pressure at the 73 level edges [hPa], with the levels as the first axis
    in front of the axes of `surface_pressure_hpa`."""
    ps = np.asarray(surface_pressure_hpa, dtype=float)
    shape = (-1,) + (1,) * ps.ndim
    return AP_HPA.reshape(shape) + BP.reshape(shape) * ps[np.newaxis]


def mid_pressures_hpa(surface_pressure_hpa: np.ndarray) -> np.ndarray:
    """Pressure at the middle of the 72 levels [hPa], the mean of each level's
    two edge pressures, with the levels as the first axis."""
    edges = edge_pressures_hpa(surface_pressure_hpa)
    return 0.5 * (edges[:-1] + edges[1:])
