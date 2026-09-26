"""Tests for how `aeic run` chooses and configures its trajectory builder.

The choice lives in a plain function so it can be tested directly. Click only
passes the command line values in.
"""

import pytest
from click.testing import CliRunner

import AEIC.trajectories.builders as tb
from AEIC.commands.run_simulations import make_trajectory_builder, run_simulations


def test_the_defaults_are_the_legacy_builder_without_mass_iteration():
    """What `aeic run` did before the options existed."""
    builder = make_trajectory_builder()

    assert type(builder) is tb.LegacyBuilder
    assert builder.options.iterate_mass is False
    assert builder.options.use_weather is False


def test_the_adjustable_builder_can_be_chosen():
    builder = make_trajectory_builder('adjustable')

    assert type(builder) is tb.AdjustableLegacyBuilder
    assert builder.descent_distance_from_model is False


@pytest.mark.parametrize('builder_name', ['legacy', 'adjustable'])
def test_mass_iteration_and_weather_reach_the_builder_options(builder_name):
    builder = make_trajectory_builder(builder_name, iterate_mass=True, use_weather=True)

    assert builder.options.iterate_mass is True
    assert builder.options.use_weather is True


def test_estimating_the_descent_from_the_model_reaches_the_adjustable_builder():
    builder = make_trajectory_builder('adjustable', descent_distance_from_model=True)

    assert builder.descent_distance_from_model is True


def test_estimating_the_descent_from_the_model_is_an_error_for_the_legacy_builder():
    """`LegacyBuilder` reproduces the legacy code and does not have this option.
    Ignoring it would quietly leave flights landing short of the destination."""
    with pytest.raises(ValueError, match='--builder adjustable'):
        make_trajectory_builder('legacy', descent_distance_from_model=True)


def test_an_unknown_builder_is_an_error():
    with pytest.raises(ValueError, match="Unknown trajectory builder: 'dymos'"):
        make_trajectory_builder('dymos')


def test_the_options_are_on_the_command_line():
    """Smoke test only: the behaviour is covered above."""
    result = CliRunner().invoke(run_simulations, ['--help'])

    assert result.exit_code == 0
    for option in (
        '--builder',
        '--iterate-mass',
        '--no-iterate-mass',
        '--descent-distance-from-model',
    ):
        assert option in result.output
