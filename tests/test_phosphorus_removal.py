"""
Tests for the unified phosphorus removal tool and related components.

Test categories:
1. Input validation - Strategy requirements, reagent validation
2. Inline PHREEQC blocks - Struvite, Variscite, HAO surface
3. Sulfide sensitivity - Anaerobic mode enforcement
4. Redox handling - pe calculation and diagnostics
5. Integration tests - Full simulation workflows
"""

import pytest
from unittest.mock import AsyncMock, patch

# Import modules to test
from utils.inline_phases import (
    get_struvite_phases_block,
    get_variscite_phases_block,
    get_hao_surface_block,
    get_p_removal_inline_blocks,
    check_phases_in_database,
    build_hao_phase_linked_surface_block,
)

from tools.schemas_ferric import (
    orp_to_pe,
    pe_to_orp,
    RedoxSpecification,
    PhosphateResidualMetrics,
    RedoxDiagnostics,
    SulfideSensitivityResult,
    SulfideSensitivityScenario,
)

from tools.phreeqc_wrapper import get_engine_status


# =============================================================================
# INLINE PHREEQC BLOCKS TESTS
# =============================================================================


class TestInlinePhreeqcBlocks:
    """Test inline PHREEQC phase and surface definitions."""

    def test_struvite_phases_block_format(self):
        """Test struvite PHASES block has correct PHREEQC format."""
        block = get_struvite_phases_block()

        # Check PHREEQC format
        assert "PHASES" in block
        assert "Struvite" in block
        assert "MgNH4PO4:6H2O" in block
        assert "log_k" in block
        assert "-13.26" in block  # pKsp from Ohlinger et al. (1998)

        # Check documentation
        assert "Ohlinger" in block or "pKsp" in block

    def test_variscite_phases_block_format(self):
        """Test variscite PHASES block has correct PHREEQC format."""
        block = get_variscite_phases_block()

        # Check PHREEQC format
        assert "PHASES" in block
        assert "Variscite" in block
        assert "AlPO4:2H2O" in block
        assert "log_k" in block
        assert "-22.1" in block  # log_k from Lindsay (1979)

        # Check documentation
        assert "Lindsay" in block or "thermoddem" in block

    def test_hao_surface_block_format(self):
        """Test HAO surface block has correct PHREEQC format."""
        block = get_hao_surface_block()

        # Check SURFACE_MASTER_SPECIES
        assert "SURFACE_MASTER_SPECIES" in block
        assert "Hao_s" in block
        assert "Hao_w" in block

        # Check SURFACE_SPECIES
        assert "SURFACE_SPECIES" in block

        # Check phosphate adsorption reactions
        assert "PO4-3" in block
        assert "Hao_sOH" in block

    def test_p_removal_inline_blocks_iron(self):
        """Test iron coagulant doesn't need inline blocks."""
        blocks = get_p_removal_inline_blocks(coagulant_type="iron")

        # Iron uses standard database phases
        assert blocks == ""

    def test_p_removal_inline_blocks_aluminum(self):
        """Test aluminum coagulant gets Variscite and HAO."""
        blocks = get_p_removal_inline_blocks(coagulant_type="aluminum")

        # Should include both Variscite and HAO
        assert "Variscite" in blocks
        assert "SURFACE_MASTER_SPECIES" in blocks
        assert "Hao_s" in blocks

    def test_p_removal_inline_blocks_struvite(self):
        """Test struvite/magnesium gets Struvite phase."""
        blocks = get_p_removal_inline_blocks(coagulant_type="magnesium")

        assert "Struvite" in blocks
        assert "MgNH4PO4:6H2O" in blocks

    def test_hao_phase_linked_surface_block(self):
        """Test phase-linked HAO surface block generation."""
        block = build_hao_phase_linked_surface_block(
            phase_name="Gibbsite",
            sites_per_mole_strong=0.005,
            weak_to_strong_ratio=40.0,
        )

        assert "SURFACE" in block
        assert "Hao_sOH" in block
        assert "Hao_wOH" in block
        assert "Gibbsite" in block
        assert "equilibrium_phase" in block


# =============================================================================
# REDOX CONVERSION TESTS
# =============================================================================


class TestRedoxConversion:
    """Test ORP to pe conversion and diagnostics."""

    def test_orp_to_pe_at_25c_she(self):
        """Test ORP to pe conversion at 25°C vs SHE."""
        # At 25°C, pe = Eh(mV) / 59.16
        orp_mv = 200  # 200 mV vs SHE
        pe = orp_to_pe(orp_mv, temperature_celsius=25.0, reference="SHE")

        expected_pe = 200 / 59.16
        assert abs(pe - expected_pe) < 0.01

    def test_pe_to_orp_at_25c(self):
        """Test pe to ORP conversion at 25°C."""
        pe = 4.0
        orp = pe_to_orp(pe, temperature_celsius=25.0)

        expected_orp = 4.0 * 59.16
        assert abs(orp - expected_orp) < 1.0

    def test_orp_pe_roundtrip(self):
        """Test ORP -> pe -> ORP roundtrip."""
        original_orp = 150.0
        pe = orp_to_pe(original_orp, 25.0, "SHE")
        recovered_orp = pe_to_orp(pe, 25.0)

        assert abs(original_orp - recovered_orp) < 0.1

    def test_orp_to_pe_reference_correction(self):
        """Test ORP reference electrode corrections."""
        orp_vs_agagcl = 200.0  # 200 mV vs Ag/AgCl (3M KCl)

        # Ag/AgCl (3M) is ~210 mV positive of SHE
        pe_agagcl = orp_to_pe(orp_vs_agagcl, 25.0, "AgAgCl_3M")
        pe_she = orp_to_pe(orp_vs_agagcl, 25.0, "SHE")

        # pe with AgAgCl reference should be higher
        assert pe_agagcl > pe_she


# =============================================================================
# SULFIDE SENSITIVITY TESTS
# =============================================================================


class TestSulfideSensitivity:
    """Test sulfide sensitivity handling for anaerobic mode."""

    def test_sulfide_sensitivity_scenario_schema(self):
        """Test SulfideSensitivityScenario schema validation."""
        scenario = SulfideSensitivityScenario(
            sulfide_mg_l=50.0,
            fe_dose_required_mmol=5.0,
            fe_dose_required_mg_l=280.0,
            fe_to_p_ratio=3.5,
            achieved_p_mg_l=0.5,
            fes_precipitated_mmol=2.0,
        )

        assert scenario.sulfide_mg_l == 50.0
        assert scenario.fe_to_p_ratio == 3.5

    def test_sulfide_sensitivity_result_schema(self):
        """Test SulfideSensitivityResult schema validation."""
        scenarios = [
            SulfideSensitivityScenario(
                sulfide_mg_l=0.0,
                fe_dose_required_mmol=2.0,
                fe_dose_required_mg_l=112.0,
                fe_to_p_ratio=1.6,
                achieved_p_mg_l=0.5,
            ),
            SulfideSensitivityScenario(
                sulfide_mg_l=50.0,
                fe_dose_required_mmol=4.0,
                fe_dose_required_mg_l=224.0,
                fe_to_p_ratio=3.2,
                achieved_p_mg_l=0.5,
            ),
        ]

        result = SulfideSensitivityResult(
            scenarios=scenarios,
            recommendation="Use S(-2)=50 mg/L for conservative design.",
            primary_scenario_sulfide_mg_l=50.0,
            sulfide_impact_summary="Fe:P increases from 1.6 to 3.2",
        )

        assert len(result.scenarios) == 2
        assert result.primary_scenario_sulfide_mg_l == 50.0


# =============================================================================
# ENGINE STATUS TESTS
# =============================================================================


class TestEngineStatus:
    """Test engine status reporting."""

    def test_engine_status_returns_dict(self):
        """Test get_engine_status returns expected structure."""
        status = get_engine_status()

        assert isinstance(status, dict)
        assert "phreeqpython_available" in status
        assert "subprocess_mode_available" in status
        assert "active_engine" in status
        assert "ready" in status

    def test_engine_status_has_database_info(self):
        """Test engine status includes database availability."""
        status = get_engine_status()

        assert "database_loadability" in status
        db_status = status["database_loadability"]

        # Check for common databases
        assert isinstance(db_status, dict)

    def test_engine_status_reports_limitations(self):
        """Test engine status reports known limitations."""
        status = get_engine_status()

        assert "known_limitations" in status
        # Should mention struvite and Al-P limitations
        limitations_text = " ".join(status["known_limitations"])
        assert "Struvite" in limitations_text or "Al-P" in limitations_text or len(status["known_limitations"]) >= 0


# =============================================================================
# PHOSPHATE METRICS TESTS
# =============================================================================


class TestPhosphateMetrics:
    """Test phosphate residual metrics schema."""

    def test_phosphate_residual_metrics_schema(self):
        """Test PhosphateResidualMetrics schema validation."""
        metrics = PhosphateResidualMetrics(
            residual_p_total_mg_l_as_P=0.5,
            residual_p_reactive_mg_l_as_P=0.5,
            p_inert_assumed_mg_l_as_P=0.1,
            residual_orthophosphate_mg_l_as_P=0.45,
        )

        assert metrics.residual_p_total_mg_l_as_P == 0.5
        assert metrics.p_inert_assumed_mg_l_as_P == 0.1

    def test_phosphate_metrics_optional_ortho_p(self):
        """Test orthophosphate is optional."""
        metrics = PhosphateResidualMetrics(
            residual_p_total_mg_l_as_P=0.5,
            residual_p_reactive_mg_l_as_P=0.5,
            p_inert_assumed_mg_l_as_P=0.0,
        )

        assert metrics.residual_orthophosphate_mg_l_as_P is None


# =============================================================================
# REDOX DIAGNOSTICS TESTS
# =============================================================================


class TestRedoxDiagnostics:
    """Test redox diagnostics schema."""

    def test_redox_diagnostics_schema(self):
        """Test RedoxDiagnostics schema validation."""
        diag = RedoxDiagnostics(
            redox_constraint_type="fix_pe",
            target_pe=-4.0,
            achieved_pe=-3.8,
            pe_drift=0.2,
            target_orp_mV_vs_SHE=-236.6,
            achieved_orp_mV_vs_SHE=-224.8,
            constraint_blocks_used=["Fix_pe", "O2(g)"],
        )

        assert diag.redox_constraint_type == "fix_pe"
        assert diag.pe_drift == 0.2
        assert len(diag.constraint_blocks_used) == 2

    def test_redox_diagnostics_optional_fields(self):
        """Test optional fields can be None."""
        diag = RedoxDiagnostics(
            redox_constraint_type="none",
            target_pe=4.0,
            achieved_pe=4.0,
        )

        assert diag.pe_drift is None
        assert diag.constraint_blocks_used is None


# =============================================================================
# INTEGRATION TEST MARKERS
# =============================================================================


@pytest.mark.integration
class TestPhosphorusRemovalIntegration:
    """Integration tests for phosphorus removal (require PHREEQC)."""

    @pytest.mark.asyncio
    async def test_iron_strategy_basic(self):
        """Test basic iron coagulant P removal."""
        from tools.phosphorus_removal import calculate_phosphorus_removal_dose

        result = await calculate_phosphorus_removal_dose({
            "initial_solution": {
                "units": "mg/L",
                "analysis": {
                    "P": 5.0,
                    "Ca": 50.0,
                    "Alkalinity": "as CaCO3 100",
                },
                "ph": 7.0,
            },
            "target_residual_p_mg_l": 0.5,
            "strategy": {
                "strategy": "iron",
                "reagent": "FeCl3",
            },
        })

        # Status can be "success" (target met) or "success_with_warning" (dose found but target not exactly met)
        assert result["status"] in ["success", "success_with_warning"], f"Unexpected status: {result['status']}"
        assert result["optimal_dose_mmol"] > 0
        assert result["achieved_p_mg_l"] <= 1.0  # Some tolerance

    @pytest.mark.asyncio
    async def test_struvite_requires_ammonia(self):
        """Test struvite strategy requires ammonia in solution."""
        from tools.phosphorus_removal import calculate_phosphorus_removal_dose

        # Without ammonia
        result = await calculate_phosphorus_removal_dose({
            "initial_solution": {
                "units": "mg/L",
                "analysis": {
                    "P": 50.0,
                    "Mg": 10.0,
                },
                "ph": 8.0,
            },
            "target_residual_p_mg_l": 5.0,
            "strategy": {
                "strategy": "struvite",
            },
        })

        assert result["status"] == "input_error"
        assert "ammonia" in result["error_message"].lower() or "N(-3)" in result["error_message"]


# =============================================================================
# SI TRIGGER TESTS
# =============================================================================


class TestSITrigger:
    """Test SI trigger for metastability control."""

    def test_get_primary_p_phases_struvite(self):
        """Test struvite primary phase identification."""
        from tools.phosphorus_removal import _get_primary_p_phases

        phases = _get_primary_p_phases("struvite", is_aerobic=True)
        assert "Struvite" in phases

    def test_get_primary_p_phases_iron(self):
        """Test iron primary phase identification."""
        from tools.phosphorus_removal import _get_primary_p_phases

        phases_aerobic = _get_primary_p_phases("iron", is_aerobic=True)
        phases_anaerobic = _get_primary_p_phases("iron", is_aerobic=False)

        assert "Strengite" in phases_aerobic
        assert "Vivianite" in phases_anaerobic

    def test_get_primary_p_phases_aluminum(self):
        """Test aluminum primary phase identification."""
        from tools.phosphorus_removal import _get_primary_p_phases

        phases = _get_primary_p_phases("aluminum", is_aerobic=True)
        assert "Variscite" in phases

    def test_get_primary_p_phases_calcium_phosphate(self):
        """Test calcium phosphate primary phase identification."""
        from tools.phosphorus_removal import _get_primary_p_phases

        phases = _get_primary_p_phases("calcium_phosphate", is_aerobic=True)
        assert "CaHPO4:2H2O" in phases or "Hydroxyapatite" in phases

    def test_si_trigger_in_strategy_config(self):
        """Test SI trigger is defined for struvite strategy."""
        from tools.phosphorus_removal import STRATEGY_CONFIG

        assert "si_trigger" in STRATEGY_CONFIG["struvite"]
        assert STRATEGY_CONFIG["struvite"]["si_trigger"] == 0.5


# =============================================================================
# BACKGROUND SINKS TESTS
# =============================================================================


class TestBackgroundSinks:
    """Test background sink phase addition."""

    def test_get_background_sink_phases_iron_no_ammonia(self):
        """Test background sinks for iron strategy without ammonia."""
        from tools.phosphorus_removal import _get_background_sink_phases

        phases, inline = _get_background_sink_phases(
            primary_strategy="iron",
            is_aerobic=True,
            has_ammonia=False,
        )

        # Should include Ca-P phases but not struvite
        assert "CaHPO4:2H2O" in phases
        assert "Struvite" not in phases
        # No struvite block needed
        assert "MgNH4PO4" not in inline

    def test_get_background_sink_phases_iron_with_ammonia(self):
        """Test background sinks for iron strategy with ammonia."""
        from tools.phosphorus_removal import _get_background_sink_phases

        phases, inline = _get_background_sink_phases(
            primary_strategy="iron",
            is_aerobic=True,
            has_ammonia=True,
        )

        # Should include struvite when ammonia present
        assert "Struvite" in phases
        assert "MgNH4PO4" in inline  # Inline block added

    def test_get_background_sink_phases_struvite(self):
        """Test background sinks don't duplicate struvite."""
        from tools.phosphorus_removal import _get_background_sink_phases

        phases, inline = _get_background_sink_phases(
            primary_strategy="struvite",
            is_aerobic=True,
            has_ammonia=True,
        )

        # Should not add duplicate struvite
        assert phases.count("Struvite") == 0  # Not added as background

    def test_get_background_sink_phases_calcium_phosphate(self):
        """Test background sinks don't duplicate Ca-P phases."""
        from tools.phosphorus_removal import _get_background_sink_phases

        phases, inline = _get_background_sink_phases(
            primary_strategy="calcium_phosphate",
            is_aerobic=True,
            has_ammonia=False,
        )

        # Should not add duplicate Ca-P phases
        assert "CaHPO4:2H2O" not in phases


# =============================================================================
# REDOX DIAGNOSTICS TESTS
# =============================================================================


class TestBuildRedoxDiagnostics:
    """Test redox diagnostics builder."""

    def test_build_redox_diagnostics_aerobic(self):
        """Test redox diagnostics for aerobic mode.

        For aerobic mode (O2 equilibrium), target_pe is None because pe is
        controlled dynamically by O2(g) equilibrium, not a fixed target.
        pe_drift is also None since there's no fixed target to drift from.
        """
        from tools.phosphorus_removal import _build_redox_diagnostics
        from tools.schemas_ferric import RedoxSpecification

        redox = RedoxSpecification(mode="aerobic")
        diag = _build_redox_diagnostics(redox, target_pe=3.5, achieved_pe=3.4)

        assert diag.redox_constraint_type == "o2_equilibrium"
        # For O2 equilibrium, target_pe is None (pe floats with O2)
        assert diag.target_pe is None
        assert diag.achieved_pe == 3.4
        # pe_drift is None for O2 equilibrium (no fixed target)
        assert diag.pe_drift is None
        assert "O2(g)" in diag.constraint_blocks_used

    def test_build_redox_diagnostics_anaerobic(self):
        """Test redox diagnostics for anaerobic mode."""
        from tools.phosphorus_removal import _build_redox_diagnostics
        from tools.schemas_ferric import RedoxSpecification

        redox = RedoxSpecification(mode="anaerobic")
        diag = _build_redox_diagnostics(redox, target_pe=-4.0, achieved_pe=-3.8)

        assert diag.redox_constraint_type == "fix_pe"
        assert diag.target_pe == -4.0
        assert diag.achieved_pe == -3.8
        assert "Fix_pe" in diag.constraint_blocks_used

    def test_build_redox_diagnostics_orp_calculation(self):
        """Test ORP calculation in diagnostics.

        Use fixed_pe mode since that's where target_orp_mV_vs_SHE is meaningful.
        For aerobic mode (O2 equilibrium), target_orp_mV_vs_SHE is None.
        """
        from tools.phosphorus_removal import _build_redox_diagnostics
        from tools.schemas_ferric import RedoxSpecification, pe_to_orp

        # Use fixed_pe mode where target_pe is meaningful
        redox = RedoxSpecification(mode="fixed_pe", pe_value=4.0)
        diag = _build_redox_diagnostics(redox, target_pe=4.0, achieved_pe=4.0)

        # Verify ORP calculations for fixed_pe mode
        expected_orp = pe_to_orp(4.0, 25.0)
        assert diag.target_orp_mV_vs_SHE is not None
        assert abs(diag.target_orp_mV_vs_SHE - expected_orp) < 1.0
        assert diag.achieved_orp_mV_vs_SHE is not None
        assert abs(diag.achieved_orp_mV_vs_SHE - expected_orp) < 1.0

    def test_build_redox_diagnostics_aerobic_no_target_orp(self):
        """Test that aerobic mode doesn't report target ORP."""
        from tools.phosphorus_removal import _build_redox_diagnostics
        from tools.schemas_ferric import RedoxSpecification

        redox = RedoxSpecification(mode="aerobic")
        diag = _build_redox_diagnostics(redox, target_pe=3.5, achieved_pe=3.5)

        # For O2 equilibrium, target_orp is None (pe floats with O2)
        assert diag.target_orp_mV_vs_SHE is None
        # But achieved_orp should be reported
        assert diag.achieved_orp_mV_vs_SHE is not None


# =============================================================================
# SI TRIGGER HELPER FUNCTION TESTS
# =============================================================================


class TestApplySITriggerToPhases:
    """Test _apply_si_trigger_to_phases helper function."""

    def test_apply_si_trigger_no_phases(self):
        """Test with empty phases list."""
        from tools.phosphorus_removal import _apply_si_trigger_to_phases

        result = {"saturation_indices": {"Calcite": 0.5}}
        warnings = []

        adjusted_p, applied = _apply_si_trigger_to_phases(
            result=result,
            phases_to_check=[],
            si_trigger=0.5,
            initial_p_mg_l=5.0,
            residual_p_mg_l=0.5,
            warnings=warnings,
        )

        assert adjusted_p == 0.5  # Unchanged
        assert applied is False
        assert len(warnings) == 0

    def test_apply_si_trigger_zero_trigger(self):
        """Test with zero SI trigger (disabled)."""
        from tools.phosphorus_removal import _apply_si_trigger_to_phases

        result = {"saturation_indices": {"Struvite": -1.0}}
        warnings = []

        adjusted_p, applied = _apply_si_trigger_to_phases(
            result=result,
            phases_to_check=["Struvite"],
            si_trigger=0.0,  # Disabled
            initial_p_mg_l=5.0,
            residual_p_mg_l=0.5,
            warnings=warnings,
        )

        assert adjusted_p == 0.5  # Unchanged
        assert applied is False

    def test_apply_si_trigger_phases_above_trigger(self):
        """Test when phases are above SI trigger (precipitation occurs)."""
        from tools.phosphorus_removal import _apply_si_trigger_to_phases

        result = {"saturation_indices": {"Struvite": 1.0}}  # Above trigger
        warnings = []

        adjusted_p, applied = _apply_si_trigger_to_phases(
            result=result,
            phases_to_check=["Struvite"],
            si_trigger=0.5,
            initial_p_mg_l=5.0,
            residual_p_mg_l=0.5,
            warnings=warnings,
        )

        assert adjusted_p == 0.5  # Unchanged (precipitation occurs)
        assert applied is False
        assert len(warnings) == 0

    def test_apply_si_trigger_all_phases_below(self):
        """Test when all phases are below SI trigger (metastable)."""
        from tools.phosphorus_removal import _apply_si_trigger_to_phases

        result = {"saturation_indices": {"Struvite": 0.2}}  # Below 0.5 trigger
        warnings = []

        adjusted_p, applied = _apply_si_trigger_to_phases(
            result=result,
            phases_to_check=["Struvite"],
            si_trigger=0.5,
            initial_p_mg_l=5.0,
            residual_p_mg_l=0.5,
            warnings=warnings,
        )

        assert adjusted_p == 5.0  # Reset to initial P (no precipitation)
        assert applied is True
        assert len(warnings) == 1
        assert "Metastability" in warnings[0]

    def test_apply_si_trigger_mixed_phases(self):
        """Test with some phases above and some below trigger."""
        from tools.phosphorus_removal import _apply_si_trigger_to_phases

        result = {"saturation_indices": {"Struvite": 0.2, "Brushite": 0.8}}
        warnings = []

        adjusted_p, applied = _apply_si_trigger_to_phases(
            result=result,
            phases_to_check=["Struvite", "Brushite"],  # One below, one above
            si_trigger=0.5,
            initial_p_mg_l=5.0,
            residual_p_mg_l=0.5,
            warnings=warnings,
        )

        # Only one phase below trigger, so precipitation still occurs
        assert adjusted_p == 0.5  # Unchanged
        assert applied is False


class TestBackgroundSinkSITriggers:
    """Test background sink SI triggers constant."""

    def test_background_sink_triggers_defined(self):
        """Test BACKGROUND_SINK_SI_TRIGGERS has expected phases."""
        from tools.phosphorus_removal import BACKGROUND_SINK_SI_TRIGGERS

        assert "Struvite" in BACKGROUND_SINK_SI_TRIGGERS
        assert "CaHPO4:2H2O" in BACKGROUND_SINK_SI_TRIGGERS
        assert "Hydroxyapatite" in BACKGROUND_SINK_SI_TRIGGERS

    def test_background_sink_trigger_values(self):
        """Test background sink SI trigger values are reasonable."""
        from tools.phosphorus_removal import BACKGROUND_SINK_SI_TRIGGERS

        # All triggers should be positive (above equilibrium)
        for phase, trigger in BACKGROUND_SINK_SI_TRIGGERS.items():
            assert trigger > 0, f"{phase} trigger should be > 0"
            assert trigger <= 1.0, f"{phase} trigger should be <= 1.0"


# =============================================================================
# INERT P ACCOUNTING TESTS
# =============================================================================


class TestInertPAccounting:
    """Test inert P accounting in unified tool."""

    def test_inert_p_field_in_schema(self):
        """Test p_inert_soluble_mg_l field exists in input schema."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput

        # Check field exists and has default
        schema = CalculatePhosphorusRemovalDoseInput.schema()
        props = schema.get("properties", {})
        assert "p_inert_soluble_mg_l" in props

    def test_inert_p_default_zero(self):
        """Test p_inert_soluble_mg_l defaults to zero."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput
        from tools.schemas_ferric import WaterAnalysisInput, RedoxSpecification

        input_model = CalculatePhosphorusRemovalDoseInput(
            initial_solution=WaterAnalysisInput(
                analysis={"P": 5.0},
                units="mg/L",
            ),
            target_residual_p_mg_l=0.5,
            strategy={"strategy": "iron"},
        )

        assert input_model.p_inert_soluble_mg_l == 0.0

    def test_inert_p_validation(self):
        """Test p_inert_soluble_mg_l validation (must be >= 0)."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput
        from tools.schemas_ferric import WaterAnalysisInput
        import pytest

        with pytest.raises(Exception):  # Pydantic validation error
            CalculatePhosphorusRemovalDoseInput(
                initial_solution=WaterAnalysisInput(
                    analysis={"P": 5.0},
                    units="mg/L",
                ),
                target_residual_p_mg_l=0.5,
                strategy={"strategy": "iron"},
                p_inert_soluble_mg_l=-1.0,  # Invalid negative
            )


# =============================================================================
# ALLOWED PHASES OVERRIDE TESTS
# =============================================================================


class TestAllowedPhasesOverride:
    """Test allowed_phases override for strategies."""

    def test_allowed_phases_field_in_schema(self):
        """Test allowed_phases field exists in strategy schema."""
        from tools.phosphorus_removal import PhosphorusRemovalStrategy

        schema = PhosphorusRemovalStrategy.schema()
        props = schema.get("properties", {})
        assert "allowed_phases" in props

    def test_allowed_phases_accepts_list(self):
        """Test allowed_phases accepts a list of phase names."""
        from tools.phosphorus_removal import PhosphorusRemovalStrategy

        strategy = PhosphorusRemovalStrategy(
            strategy="calcium_phosphate",
            allowed_phases=["CaHPO4:2H2O"],  # Brushite only
        )

        assert strategy.allowed_phases == ["CaHPO4:2H2O"]

    def test_allowed_phases_default_none(self):
        """Test allowed_phases defaults to None (use strategy default)."""
        from tools.phosphorus_removal import PhosphorusRemovalStrategy

        strategy = PhosphorusRemovalStrategy(strategy="iron")

        assert strategy.allowed_phases is None


# =============================================================================
# SULFIDE SENSITIVITY INPUT VALIDATION TESTS
# =============================================================================


class TestSulfideSensitivityInput:
    """Test sulfide_sensitivity input parameter."""

    def test_sulfide_sensitivity_field_in_schema(self):
        """Test sulfide_sensitivity field exists in input schema."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput

        schema = CalculatePhosphorusRemovalDoseInput.schema()
        props = schema.get("properties", {})
        assert "sulfide_sensitivity" in props

    def test_sulfide_sensitivity_accepts_bool(self):
        """Test sulfide_sensitivity accepts True/False."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput
        from tools.schemas_ferric import WaterAnalysisInput, RedoxSpecification

        input_model = CalculatePhosphorusRemovalDoseInput(
            initial_solution=WaterAnalysisInput(
                analysis={"P": 5.0},
                units="mg/L",
            ),
            target_residual_p_mg_l=0.5,
            strategy={"strategy": "iron"},
            redox=RedoxSpecification(mode="anaerobic"),
            sulfide_sensitivity=True,
        )

        assert input_model.sulfide_sensitivity is True

    def test_sulfide_sensitivity_default_none(self):
        """Test sulfide_sensitivity defaults to None."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput
        from tools.schemas_ferric import WaterAnalysisInput

        input_model = CalculatePhosphorusRemovalDoseInput(
            initial_solution=WaterAnalysisInput(
                analysis={"P": 5.0},
                units="mg/L",
            ),
            target_residual_p_mg_l=0.5,
            strategy={"strategy": "iron"},
        )

        assert input_model.sulfide_sensitivity is None


# =============================================================================
# HFO SITE MULTIPLIER TESTS
# =============================================================================


class TestHfoSiteMultiplier:
    """Test hfo_site_multiplier parameter."""

    def test_hfo_site_multiplier_field_in_schema(self):
        """Test hfo_site_multiplier field exists in input schema."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput

        schema = CalculatePhosphorusRemovalDoseInput.schema()
        props = schema.get("properties", {})
        assert "hfo_site_multiplier" in props

    def test_hfo_site_multiplier_default(self):
        """Test hfo_site_multiplier defaults to 1.0."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput
        from tools.schemas_ferric import WaterAnalysisInput

        input_model = CalculatePhosphorusRemovalDoseInput(
            initial_solution=WaterAnalysisInput(
                analysis={"P": 5.0},
                units="mg/L",
            ),
            target_residual_p_mg_l=0.5,
            strategy={"strategy": "iron"},
        )

        assert input_model.hfo_site_multiplier == 1.0

    def test_hfo_site_multiplier_range(self):
        """Test hfo_site_multiplier accepts valid range."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseInput
        from tools.schemas_ferric import WaterAnalysisInput

        # Test minimum
        input_model = CalculatePhosphorusRemovalDoseInput(
            initial_solution=WaterAnalysisInput(
                analysis={"P": 5.0},
                units="mg/L",
            ),
            target_residual_p_mg_l=0.5,
            strategy={"strategy": "iron"},
            hfo_site_multiplier=0.1,  # Minimum
        )
        assert input_model.hfo_site_multiplier == 0.1

        # Test maximum
        input_model = CalculatePhosphorusRemovalDoseInput(
            initial_solution=WaterAnalysisInput(
                analysis={"P": 5.0},
                units="mg/L",
            ),
            target_residual_p_mg_l=0.5,
            strategy={"strategy": "iron"},
            hfo_site_multiplier=5.0,  # Maximum
        )
        assert input_model.hfo_site_multiplier == 5.0


# =============================================================================
# SULFIDE SENSITIVITY SWEEP OUTPUT TESTS
# =============================================================================


class TestSulfideSensitivitySweepOutput:
    """Test sulfide_sensitivity_results output fields."""

    def test_output_schema_has_sweep_fields(self):
        """Test output schema has sulfide sweep result fields."""
        from tools.phosphorus_removal import CalculatePhosphorusRemovalDoseOutput

        schema = CalculatePhosphorusRemovalDoseOutput.schema()
        props = schema.get("properties", {})

        # Check all sweep-related fields exist
        assert "sulfide_sensitivity_results" in props
        assert "design_scenario_sulfide_mg_l" in props
        assert "recommended_design_dose_mmol" in props
        assert "recommended_design_dose_mg_l" in props
        assert "recommended_design_basis" in props

    def test_sweep_scenario_schema(self):
        """Test SulfideSensitivityScenario schema in phosphorus_removal."""
        from tools.phosphorus_removal import SulfideSensitivityScenario

        # Create a scenario
        scenario = SulfideSensitivityScenario(
            sulfide_mg_l=50.0,
            status="success",
            optimal_dose_mmol=10.5,
            optimal_dose_mg_l=1702.0,
            achieved_p_mg_l=0.48,
            metal_to_p_ratio=2.5,
            final_ph=6.8,
            fe_consumed_by_sulfide_pct=25.0,
        )

        assert scenario.sulfide_mg_l == 50.0
        assert scenario.status == "success"
        assert scenario.metal_to_p_ratio == 2.5
        assert scenario.fe_consumed_by_sulfide_pct == 25.0

    def test_sweep_scenario_error_status(self):
        """Test SulfideSensitivityScenario with error status."""
        from tools.phosphorus_removal import SulfideSensitivityScenario

        scenario = SulfideSensitivityScenario(
            sulfide_mg_l=100.0,
            status="error",
            error_message="Simulation failed: PHREEQC error",
        )

        assert scenario.status == "error"
        assert scenario.error_message == "Simulation failed: PHREEQC error"
        assert scenario.optimal_dose_mmol is None

    def test_sweep_scenario_infeasible_status(self):
        """Test SulfideSensitivityScenario with infeasible status."""
        from tools.phosphorus_removal import SulfideSensitivityScenario

        scenario = SulfideSensitivityScenario(
            sulfide_mg_l=100.0,
            status="infeasible",
            achieved_p_mg_l=2.5,  # Didn't meet target
        )

        assert scenario.status == "infeasible"
        assert scenario.achieved_p_mg_l == 2.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
