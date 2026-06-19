"""Tests for the free marginal-emissions estimator."""

import marginal


class TestEstimateMarginal:
    def test_linear_series_recovers_marginal(self):
        # Baseline 100 MWh hydro (24 gCO2/kWh) plus gas (490) covering load
        # changes of 30 then 60 MWh. The marginal generator is gas -> 490.
        series = [
            (100.0, 100 * 24),  # gen, weighted co2
            (130.0, 100 * 24 + 30 * 490),
            (190.0, 100 * 24 + 90 * 490),
        ]
        est = marginal.estimate_marginal(series)
        assert est["marginal"] == 490
        assert est["r_squared"] == 1.0
        assert est["n"] == 2

    def test_average_is_generation_weighted(self):
        series = [(100.0, 2400.0), (130.0, 17100.0), (190.0, 46500.0)]
        est = marginal.estimate_marginal(series)
        # (2400+17100+46500) / (100+130+190) = 66000/420 ~ 157
        assert est["average"] == 157

    def test_noise_lowers_r_squared(self):
        # Same load changes produce opposite emission moves -> load change
        # explains none of the variation, so r_squared collapses toward 0.
        series = [(100.0, 0.0), (110.0, 100.0), (130.0, 200.0), (140.0, 100.0), (160.0, 0.0)]
        est = marginal.estimate_marginal(series)
        assert est is not None
        assert est["r_squared"] < 0.5  # uninformative -> distrust the number

    def test_too_few_intervals_returns_none(self):
        assert marginal.estimate_marginal([(100.0, 2400.0)]) is None
        assert marginal.estimate_marginal([(100.0, 2400.0), (120.0, 3000.0)]) is None

    def test_flat_load_returns_none(self):
        # Generation never moves -> no marginal signal at all
        series = [(100.0, 2400.0), (100.0, 2500.0), (100.0, 2600.0)]
        assert marginal.estimate_marginal(series) is None

    def test_marginal_is_clamped_to_physical_range(self):
        # An absurd jump would imply >1100 gCO2/kWh; clamp it.
        series = [(100.0, 0.0), (150.0, 500000.0), (200.0, 1000000.0)]
        est = marginal.estimate_marginal(series)
        assert est["marginal"] == 1100

    def test_skips_none_rows(self):
        series = [(100.0, 2400.0), (None, 5000.0), (130.0, 17100.0), (190.0, 46500.0)]
        est = marginal.estimate_marginal(series)
        assert est is not None and est["marginal"] == 490
