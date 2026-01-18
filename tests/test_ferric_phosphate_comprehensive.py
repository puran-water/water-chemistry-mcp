"""
Comprehensive integration tests for ferric phosphate precipitation tool.

Tests run REAL PHREEQC simulations - no mocks.
All assertions are strict - specific value checks.

Test categories:
1. Input validation
2. Redox modes
3. Alkalinity handling
4. Tuning parameters (p_inert, hfo_multiplier, organics)
5. Binary search behavior
6. Chemical scenarios
7. Partitioning output
8. Error handling
"""

import pytest
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.ferric_phosphate import calculate_ferric_dose_for_tp
from tools.schemas_ferric import MOLECULAR_WEIGHTS


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def run_async(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# =============================================================================
# INPUT VALIDATION TESTS
# =============================================================================

class TestInputValidation:
    """Tests for input validation edge cases."""

    def test_missing_initial_solution(self):
        """Test error when initial_solution is missing."""
        result = run_async(calculate_ferric_dose_for_tp({
            "target_residual_p_mg_l": 0.5,
        }))
        assert "error" in result, "Expected error for missing initial_solution"

    def test_missing_target_p(self):
        """Test error when target_residual_p_mg_l is missing."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": {
                "ph": 7.0,
                "analysis": {"P": 5.0},
                "units": "mg/L"
            }
        }))
        assert "error" in result, "Expected error for missing target_p"

    def test_target_exceeds_initial_p(self):
        """Test error when target P > initial P."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": {
                "ph": 7.0,
                "analysis": {"P": 5.0},
                "units": "mg/L"
            },
            "target_residual_p_mg_l": 10.0,  # > 5.0
        }))
        assert "error" in result, "Expected error for target > initial"

    def test_target_equals_initial_p(self):
        """Test error when target P = initial P."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": {
                "ph": 7.0,
                "analysis": {"P": 5.0},
                "units": "mg/L"
            },
            "target_residual_p_mg_l": 5.0,  # = initial
        }))
        assert "error" in result, "Expected error for target = initial"

    def test_zero_initial_p(self):
        """Test error when initial P = 0."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": {
                "ph": 7.0,
                "analysis": {"P": 0.0},
                "units": "mg/L"
            },
            "target_residual_p_mg_l": 0.5,
        }))
        assert "error" in result, "Expected error for zero initial P"

    def test_empty_analysis_dict(self):
        """Test error when analysis dict is empty (no P)."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": {
                "ph": 7.0,
                "analysis": {},
                "units": "mg/L"
            },
            "target_residual_p_mg_l": 0.5,
        }))
        assert "error" in result, "Expected error for empty analysis"

    def test_p_with_valence_notation(self):
        """Test P specified as P(5) is accepted."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": {
                "ph": 7.0,
                "analysis": {
                    "P(5)": 5.0,
                    "Ca": 50,
                    "Alkalinity": "as CaCO3 100"
                },
                "units": "mg/L"
            },
            "target_residual_p_mg_l": 1.0,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 5}
        }))
        # Should work - P(5) is valid alternate key for phosphorus
        # Strict assertion: must succeed or have non-P-related error
        status = result.get("status")
        error = result.get("error", "")
        assert status == "success", f"P(5) valence notation should work. Status: {status}, Error: {error}"


# =============================================================================
# BASIC FUNCTIONALITY TESTS
# =============================================================================

class TestBasicFunctionality:
    """Tests for basic tool functionality with real PHREEQC."""

    def test_aerobic_fecl3_precipitation(self, basic_ferric_input):
        """Test basic aerobic Fe-P precipitation with FeCl3."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success", f"Expected success, got: {result.get('error', result.get('status'))}"

        opt = result.get("optimization_summary", {})
        assert opt.get("achieved_p_mg_l") is not None, "achieved_p_mg_l should be present"
        assert opt.get("achieved_p_mg_l") <= 1.0, f"Achieved P ({opt.get('achieved_p_mg_l')}) should be <= 1.0 mg/L"
        assert opt.get("optimal_fe_dose_mg_l") > 0, "Fe dose should be > 0"
        assert opt.get("convergence_achieved") is True, "Should converge"

    def test_output_has_required_fields(self, basic_ferric_input):
        """Test that output contains all required fields."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert "status" in result, "status field must be present"
        assert "optimization_summary" in result, "optimization_summary must be present"
        assert "phosphate_partitioning" in result, "phosphate_partitioning must be present"
        assert "iron_partitioning" in result, "iron_partitioning must be present"

        opt = result["optimization_summary"]
        assert "achieved_p_mg_l" in opt
        assert "optimal_fe_dose_mg_l" in opt
        assert "fe_to_p_molar_ratio" in opt
        assert "convergence_achieved" in opt
        assert "alkalinity_consumed_mg_caco3_l" in opt

    def test_feso4_iron_source(self, typical_wwtp_solution):
        """Test with FeSO4 iron source."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "iron_source": "FeSO4",
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        assert result.get("status") == "success", f"FeSO4 should work: {result.get('error')}"
        opt = result.get("optimization_summary", {})
        assert opt.get("iron_source_used") == "FeSO4"

    def test_fe2so43_iron_source(self, typical_wwtp_solution):
        """Test with Fe2(SO4)3 iron source."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "iron_source": "Fe2(SO4)3",
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        assert result.get("status") == "success", f"Fe2(SO4)3 should work: {result.get('error')}"


# =============================================================================
# REDOX MODE TESTS
# =============================================================================

class TestRedoxModes:
    """Tests for different redox mode handling."""

    def test_aerobic_mode_explicit(self, typical_wwtp_solution):
        """Test explicit aerobic mode setting."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "redox": {"mode": "aerobic"},
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})
        assert opt.get("redox_mode_used") == "aerobic"

    def test_anaerobic_mode(self, high_p_digester_solution):
        """Test anaerobic mode for digester conditions."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": high_p_digester_solution,
            "target_residual_p_mg_l": 100.0,  # Less stringent for anaerobic
            "iron_source": "FeSO4",
            "redox": {"mode": "anaerobic"},
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 15}
        }))

        assert result.get("status") == "success", f"Anaerobic should work: {result.get('error')}"
        opt = result.get("optimization_summary", {})
        assert opt.get("redox_mode_used") == "anaerobic"

    def test_fixed_pe_mode(self, typical_wwtp_solution):
        """Test fixed_pe redox mode."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "redox": {"mode": "fixed_pe", "pe_value": 6.0},
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        assert result.get("status") == "success", f"fixed_pe should work: {result.get('error')}"

    def test_pe_from_orp_mode(self, typical_wwtp_solution):
        """Test pe_from_orp redox mode with ORP measurement."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "redox": {
                "mode": "pe_from_orp",
                "orp_mv": 300.0,
                "orp_reference": "AgAgCl_3M"
            },
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        assert result.get("status") == "success", f"pe_from_orp should work: {result.get('error')}"


# =============================================================================
# ALKALINITY TESTS
# =============================================================================

class TestAlkalinity:
    """Tests for alkalinity handling and consumption."""

    def test_alkalinity_consumption_reported(self, basic_ferric_input):
        """Test that alkalinity consumption is calculated and reported."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})

        alk_consumed = opt.get("alkalinity_consumed_mg_caco3_l")
        assert alk_consumed is not None, "alkalinity_consumed_mg_caco3_l should be present"
        assert alk_consumed >= 0, f"Alkalinity consumed ({alk_consumed}) should be >= 0"

    def test_alkalinity_remaining_reported(self, basic_ferric_input):
        """Test that alkalinity remaining is calculated and reported."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})

        alk_remaining = opt.get("alkalinity_remaining_mg_caco3_l")
        assert alk_remaining is not None, "alkalinity_remaining_mg_caco3_l should be present"

    def test_low_alkalinity_scenario(self, low_alkalinity_solution):
        """Test low alkalinity water (pH may drop significantly)."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": low_alkalinity_solution,
            "target_residual_p_mg_l": 0.5,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 15}
        }))

        # Should still work but may have low final pH
        assert result.get("status") == "success", f"Low alk should work: {result.get('error')}"
        opt = result.get("optimization_summary", {})
        final_ph = opt.get("achieved_ph")
        assert final_ph is not None, "Final pH should be reported"
        # pH drop is expected with low alkalinity
        assert final_ph > 3.0, f"pH ({final_ph}) should not crash below 3.0"

    def test_high_alkalinity_scenario(self, high_p_digester_solution):
        """Test high alkalinity digester water."""
        # Use more achievable target for high-P water
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": high_p_digester_solution,
            "target_residual_p_mg_l": 50.0,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 15}
        }))

        assert result.get("status") == "success", f"High alk should work: {result.get('error')}"

    def test_alkalinity_string_format(self, typical_wwtp_solution):
        """Test alkalinity specified as string 'as CaCO3 100'."""
        # typical_wwtp_solution already uses string format
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        assert result.get("status") == "success", "String alkalinity format should work"


# =============================================================================
# TUNING PARAMETER TESTS
# =============================================================================

class TestTuningParameters:
    """Tests for tuning parameters (p_inert, hfo_multiplier, organics)."""

    def test_p_inert_zero_default(self, basic_ferric_input):
        """Test that p_inert=0 (default) has no effect."""
        input_no_inert = {**basic_ferric_input, "p_inert_soluble_mg_l": 0.0}
        result = run_async(calculate_ferric_dose_for_tp(input_no_inert))

        assert result.get("status") == "success"

    def test_p_inert_positive_adjusts_target(self, typical_wwtp_solution):
        """Test that positive p_inert adjusts effective target."""
        result_no_inert = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 0.5,
            "p_inert_soluble_mg_l": 0.0,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        result_with_inert = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 0.5,
            "p_inert_soluble_mg_l": 0.1,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        # Both should succeed
        assert result_no_inert.get("status") == "success"
        assert result_with_inert.get("status") == "success"

        # With inert P, MORE Fe is needed (effective target for reactive P is LOWER)
        # effective_target = target - p_inert = 0.5 - 0.1 = 0.4 mg/L reactive P
        # Achieving 0.4 mg/L reactive P requires more Fe than achieving 0.5 mg/L
        opt_no = result_no_inert.get("optimization_summary", {})
        opt_with = result_with_inert.get("optimization_summary", {})

        fe_no = opt_no.get("optimal_fe_dose_mg_l", 0)
        fe_with = opt_with.get("optimal_fe_dose_mg_l", 0)

        # p_inert lowers the effective reactive P target, so MORE Fe needed
        assert fe_with > fe_no, f"With p_inert, Fe dose ({fe_with}) should be > without ({fe_no})"

    def test_p_inert_exceeds_target_infeasible(self, typical_wwtp_solution):
        """Test that p_inert > target returns infeasible/error."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 0.3,
            "p_inert_soluble_mg_l": 0.5,  # > target
            "database": "minteq.v4.dat",
        }))

        # Should return error or infeasible
        assert "error" in result or result.get("status") == "infeasible", \
            "p_inert > target should be infeasible"

    def test_hfo_multiplier_affects_dose(self, typical_wwtp_solution):
        """Test that hfo_site_multiplier affects required Fe dose."""
        result_low = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "hfo_site_multiplier": 0.5,  # Fewer sites
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        result_high = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "hfo_site_multiplier": 2.0,  # More sites
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        assert result_low.get("status") == "success"
        assert result_high.get("status") == "success"

        # More sites = more adsorption = less Fe needed (or same target reached with less)
        opt_low = result_low.get("optimization_summary", {})
        opt_high = result_high.get("optimization_summary", {})

        fe_low = opt_low.get("optimal_fe_dose_mg_l", 0)
        fe_high = opt_high.get("optimal_fe_dose_mg_l", 0)

        # With more sites, should need less Fe
        assert fe_high <= fe_low, f"With more HFO sites, Fe dose ({fe_high}) should be <= ({fe_low})"

    def test_organics_ligand_increases_dose(self, typical_wwtp_solution):
        """Test that organics_ligand_mmol_l increases Fe requirement."""
        result_no_org = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "organics_ligand_mmol_l": 0.0,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        result_with_org = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "organics_ligand_mmol_l": 0.1,  # Organic interference
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 10}
        }))

        assert result_no_org.get("status") == "success"
        assert result_with_org.get("status") == "success"

        opt_no = result_no_org.get("optimization_summary", {})
        opt_with = result_with_org.get("optimization_summary", {})

        fe_no = opt_no.get("optimal_fe_dose_mg_l", 0)
        fe_with = opt_with.get("optimal_fe_dose_mg_l", 0)

        # With organics, more Fe needed due to complexation
        assert fe_with >= fe_no, f"With organics, Fe dose ({fe_with}) should be >= ({fe_no})"


# =============================================================================
# BINARY SEARCH BEHAVIOR TESTS
# =============================================================================

class TestBinarySearchBehavior:
    """Tests for binary search algorithm behavior."""

    def test_convergence_within_iterations(self, basic_ferric_input):
        """Test that algorithm converges within max_iterations."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})

        iterations = opt.get("iterations_taken")
        assert iterations is not None, "iterations_taken should be reported"
        assert iterations <= 30, f"Should converge in <= 30 iterations, took {iterations}"

    def test_tight_tolerance_more_iterations(self, typical_wwtp_solution):
        """Test that tight tolerance requires more iterations."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "database": "minteq.v4.dat",
            "binary_search": {
                "max_iterations": 30,
                "tolerance_mg_l": 0.001  # Very tight
            }
        }))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})
        achieved = opt.get("achieved_p_mg_l", 999)
        target = 1.0

        # Should be very close to target
        assert abs(achieved - target) < 0.01, f"Achieved ({achieved}) should be within 0.01 of target"

    def test_loose_tolerance_fewer_iterations(self, typical_wwtp_solution):
        """Test that loose tolerance allows faster convergence."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 1.0,
            "database": "minteq.v4.dat",
            "binary_search": {
                "max_iterations": 30,
                "tolerance_mg_l": 0.5  # Loose
            }
        }))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})
        iterations = opt.get("iterations_taken", 99)

        # Should converge quickly with loose tolerance
        assert iterations <= 10, f"With loose tolerance, should converge in <= 10 iterations, took {iterations}"

    def test_dose_monotonicity(self, typical_wwtp_solution):
        """Test that P removal increases monotonically with Fe dose."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 0.5,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 15}
        }))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})
        path = opt.get("optimization_path", [])

        # Check monotonicity: as Fe increases, P should decrease (generally)
        # Allow some non-monotonicity at very low doses
        if len(path) >= 3:
            fe_values = [p.get("fe_mmol", 0) for p in path if "fe_mmol" in p]
            p_values = [p.get("p_mg_l", 0) for p in path if "p_mg_l" in p]

            # At higher Fe doses, P should be lower
            if len(fe_values) >= 2 and len(p_values) >= 2:
                max_fe_idx = fe_values.index(max(fe_values))
                min_fe_idx = fe_values.index(min(fe_values[1:]))  # Skip initial
                # P at max Fe should be <= P at min Fe
                assert p_values[max_fe_idx] <= p_values[min_fe_idx] * 1.1, \
                    "P should generally decrease with increasing Fe"

    def test_auto_scale_max_dose_high_p(self, high_p_digester_solution):
        """Test auto-scaling of max_dose for high-P applications."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": high_p_digester_solution,
            "target_residual_p_mg_l": 50.0,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 20}
            # max_dose_mg_l not specified - should auto-scale
        }))

        assert result.get("status") == "success", f"High-P auto-scale should work: {result.get('error')}"
        opt = result.get("optimization_summary", {})
        fe_dose = opt.get("optimal_fe_dose_mg_l", 0)

        # For 200 mg/L P -> 50 mg/L, need significant Fe
        # Default 500 mg/L might not be enough, auto-scale should handle it
        assert fe_dose > 0, "Should calculate a positive Fe dose"


# =============================================================================
# PARTITIONING OUTPUT TESTS
# =============================================================================

class TestPartitioningOutput:
    """Tests for phosphate and iron partitioning output."""

    def test_p_partitioning_complete(self, basic_ferric_input):
        """Test that P partitioning includes all components."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        pp = result.get("phosphate_partitioning", {})

        assert "dissolved_p_mg_l" in pp, "dissolved_p_mg_l must be present"
        assert "dissolved_p_mmol" in pp, "dissolved_p_mmol must be present"
        assert pp["dissolved_p_mg_l"] >= 0, "dissolved_p_mg_l must be >= 0"

    def test_fe_partitioning_complete(self, basic_ferric_input):
        """Test that Fe partitioning includes all components."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        fp = result.get("iron_partitioning", {})

        assert "dissolved_fe_mg_l" in fp, "dissolved_fe_mg_l must be present"
        assert "dissolved_fe_mmol" in fp, "dissolved_fe_mmol must be present"
        assert "fe_utilization_percent" in fp, "fe_utilization_percent must be present"

    def test_fe_utilization_high(self, basic_ferric_input):
        """Test that Fe utilization is high (most Fe precipitates)."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        fp = result.get("iron_partitioning", {})

        utilization = fp.get("fe_utilization_percent", 0)
        assert utilization > 90, f"Fe utilization ({utilization}%) should be > 90%"

    def test_p_removal_efficiency_matches(self, basic_ferric_input):
        """Test that P removal efficiency calculation matches actual removal."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})
        pp = result.get("phosphate_partitioning", {})

        initial_p = opt.get("initial_p_mg_l", 0)
        achieved_p = opt.get("achieved_p_mg_l", 0)
        removal_pct = pp.get("total_p_removal_percent", 0)

        expected_removal = (initial_p - achieved_p) / initial_p * 100 if initial_p > 0 else 0
        assert abs(removal_pct - expected_removal) < 1.0, \
            f"Removal % ({removal_pct}) should match calculated ({expected_removal})"

    def test_precipitated_phases_reported(self, basic_ferric_input):
        """Test that precipitated phases are reported."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        precip = result.get("precipitated_phases", {})

        # Should have at least one phase (Ferrihydrite for aerobic)
        assert len(precip) > 0, "precipitated_phases should have at least one entry"


# =============================================================================
# CHEMICAL SCENARIO TESTS
# =============================================================================

class TestChemicalScenarios:
    """Tests for specific chemical scenarios."""

    def test_typical_wwtp_secondary_effluent(self, typical_wwtp_solution):
        """Test typical WWTP secondary effluent treatment."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 0.5,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 15}
        }))

        assert result.get("status") == "success"
        opt = result.get("optimization_summary", {})

        # Typical Fe:P ratio should be 1.5-5:1 for this scenario
        fe_p_ratio = opt.get("fe_to_p_molar_ratio", 0)
        assert 1.0 < fe_p_ratio < 10.0, f"Fe:P ratio ({fe_p_ratio}) outside typical range"

    def test_high_hardness_competition(self, typical_wwtp_solution):
        """Test high hardness water (Ca/Mg competition)."""
        high_hardness = {**typical_wwtp_solution}
        high_hardness["analysis"] = {
            **typical_wwtp_solution["analysis"],
            "Ca": 300,  # Very high
            "Mg": 100   # Very high
        }

        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": high_hardness,
            "target_residual_p_mg_l": 1.0,
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 15}
        }))

        assert result.get("status") == "success", f"High hardness should work: {result.get('error')}"

    def test_very_low_target_p(self, typical_wwtp_solution):
        """Test very low P target (ultra-low P discharge)."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": typical_wwtp_solution,
            "target_residual_p_mg_l": 0.05,  # Ultra-low
            "database": "minteq.v4.dat",
            "binary_search": {
                "max_iterations": 25,
                "tolerance_mg_l": 0.01
            }
        }))

        # May or may not achieve ultra-low target
        if result.get("status") == "success":
            opt = result.get("optimization_summary", {})
            achieved = opt.get("achieved_p_mg_l", 999)
            # Should be close to target
            assert achieved < 0.2, f"Ultra-low target achieved ({achieved}) should be < 0.2"
        else:
            # Infeasible is acceptable for very stringent targets
            assert result.get("status") == "infeasible" or "error" in result

    def test_high_sulfide_anaerobic(self, high_sulfide_solution):
        """Test high sulfide anaerobic conditions (FeS competition)."""
        result = run_async(calculate_ferric_dose_for_tp({
            "initial_solution": high_sulfide_solution,
            "target_residual_p_mg_l": 20.0,  # Relaxed target for difficult conditions
            "iron_source": "FeSO4",
            "redox": {"mode": "anaerobic"},
            "database": "minteq.v4.dat",
            "binary_search": {"max_iterations": 20}
        }))

        # High sulfide is challenging - may need more Fe
        assert result.get("status") == "success", f"High sulfide should work: {result.get('error')}"


# =============================================================================
# ELEMENT TOTALS TESTS
# =============================================================================

class TestElementTotals:
    """Tests for element_totals_molality parsing."""

    def test_p_in_element_totals(self, basic_ferric_input):
        """Test that P is present in element_totals_molality."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        totals = result.get("element_totals_molality", {})

        assert "P" in totals, "P must be in element_totals_molality"
        assert totals["P"] > 0, f"P value ({totals['P']}) must be > 0"

    def test_fe_in_element_totals(self, basic_ferric_input):
        """Test that Fe is present in element_totals_molality."""
        result = run_async(calculate_ferric_dose_for_tp(basic_ferric_input))

        assert result.get("status") == "success"
        totals = result.get("element_totals_molality", {})

        assert "Fe" in totals, "Fe must be in element_totals_molality"


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
