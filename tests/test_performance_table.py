# TODO: Remove this when we move to Python 3.14+.
from __future__ import annotations

import pytest

from AEIC.performance.models.legacy import (
    PerformanceTable,
    PerformanceTableInput,
    ROCDFilter,
)


def test_create_performance_table():
    def fuel_flow(fl: int, tas: int) -> float:
        return round(0.5 + 0.001 * fl + 0.0001 * tas, 6)

    def tas(fl: int) -> int:
        return 220 + (fl - 300) // 10

    cols = ['FL', 'FUEL_FLOW', 'TAS', 'ROCD', 'MASS']
    rows = []
    for fl in (330, 350):
        for mass in (60000, 70000, 80000):
            v = tas(fl)
            ff = fuel_flow(fl, v)
            rows.append([fl, ff, v, 0.0, mass])
    model = PerformanceTable.from_input(
        PerformanceTableInput(cols=cols, data=rows), rocd_type=ROCDFilter.ZERO
    )

    assert model.fl == [330, 350]
    assert model.mass == [60000, 70000, 80000]

    assert model.df[
        (model.df.fl == 350)
        & (model.df.tas == tas(350))
        & (model.df.rocd == 0)
        & (model.df.mass == 60000)
    ].fuel_flow.values[0] == fuel_flow(350, tas(350))


def test_create_performance_table_missing_output_column():
    with pytest.raises(
        ValueError, match='Missing required "fuel_flow" column in performance table'
    ):
        _ = PerformanceTableInput(cols=['FL', 'TAS'], data=[[330, 220]])
