"""
Unit tests for PHREEQC helper functions in utils/helpers.py.

Tests:
- build_solution_block
- build_reaction_block
- build_equilibrium_phases_block
- build_mix_block
- build_gas_phase_block
- build_surface_block
- build_kinetics_block
- build_selected_output_block
- build_phase_linked_surface_block
- build_user_punch_for_partitioning
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.helpers import (
    build_solution_block,
    build_reaction_block,
    build_equilibrium_phases_block,
    build_mix_block,
    build_gas_phase_block,
    build_surface_block,
    build_kinetics_block,
    build_selected_output_block,
    build_phase_linked_surface_block,
    build_user_punch_for_partitioning,
)
from utils.exceptions import (
    InputValidationError,
    KineticsDefinitionError,
    SurfaceDefinitionError,
    GasPhaseError,
)


# =============================================================================
# BUILD SOLUTION BLOCK TESTS
# =============================================================================

class TestBuildSolutionBlock:
    """Tests for build_solution_block function."""

    def test_minimal_solution(self):
        """Test solution with minimal required fields."""
        data = {"ph": 7.0, "analysis": {"Ca": 40}}
        result = build_solution_block(data)

        assert "SOLUTION 1" in result
        assert "pH        7.0" in result
        assert "Ca" in result
        assert "40" in result

    def test_solution_with_temperature(self):
        """Test temperature is included."""
        data = {"ph": 7.0, "temperature_celsius": 25.0, "analysis": {}}
        result = build_solution_block(data)

        assert "temp      25.0" in result

    def test_solution_with_pressure(self):
        """Test pressure is included."""
        data = {"ph": 7.0, "pressure_atm": 1.5, "analysis": {}}
        result = build_solution_block(data)

        assert "pressure  1.5" in result

    def test_solution_with_units(self):
        """Test units are included."""
        data = {"ph": 7.0, "units": "mmol/kgw", "analysis": {}}
        result = build_solution_block(data)

        assert "units     mmol/kgw" in result

    def test_solution_with_pe(self):
        """Test pe value is included."""
        data = {"ph": 7.0, "pe": -4.0, "analysis": {}}
        result = build_solution_block(data)

        assert "pe        -4.0" in result

    def test_solution_uppercase_pH(self):
        """Test uppercase pH key is handled."""
        data = {"pH": 7.5, "analysis": {}}
        result = build_solution_block(data)

        assert "pH        7.5" in result

    def test_solution_with_density(self):
        """Test density is included when specified."""
        data = {"ph": 7.0, "density": 1.02, "analysis": {}}
        result = build_solution_block(data)

        assert "density   1.02" in result

    def test_solution_without_density(self):
        """Test density is not included when None."""
        data = {"ph": 7.0, "density": None, "analysis": {}}
        result = build_solution_block(data)

        assert "density" not in result

    def test_solution_number_override(self):
        """Test solution_number parameter overrides solution_num."""
        data = {"ph": 7.0, "analysis": {}}
        result = build_solution_block(data, solution_num=1, solution_number=5)

        assert "SOLUTION 5" in result

    def test_charge_balance_mode(self):
        """Test charge balance on specified element."""
        data = {"ph": 7.0, "charge_balance": "Cl", "analysis": {"Cl": 100}}
        result = build_solution_block(data)

        assert "Cl charge" in result
        assert "pH        7.0" in result

    def test_redox_couple_mode(self):
        """Test redox couple specification."""
        data = {"ph": 7.0, "redox": "Fe(2)/Fe(3)", "analysis": {}}
        result = build_solution_block(data)

        assert "redox     Fe(2)/Fe(3)" in result

    def test_alkalinity_string_format(self):
        """Test alkalinity with 'as CaCO3' format."""
        data = {"ph": 7.0, "analysis": {"Alkalinity": "as CaCO3 100"}}
        result = build_solution_block(data)

        # PHREEQC expects: Alkalinity    100 as CaCO3
        assert "Alkalinity" in result
        assert "100" in result
        assert "as CaCO3" in result

    def test_element_mapping_nitrogen(self):
        """Test N is mapped to N(5) for nitrate."""
        data = {"ph": 7.0, "analysis": {"N": 10}}
        result = build_solution_block(data)

        assert "N(5)" in result

    def test_element_mapping_iron(self):
        """Test Fe is mapped to Fe(2) for ferrous."""
        data = {"ph": 7.0, "analysis": {"Fe": 5}}
        result = build_solution_block(data)

        assert "Fe(2)" in result

    def test_element_mapping_sulfur(self):
        """Test S is mapped to S(6) for sulfate."""
        data = {"ph": 7.0, "analysis": {"S": 50}}
        result = build_solution_block(data)

        assert "S(6)" in result

    def test_phosphorus_not_mapped(self):
        """Test P is NOT mapped to P(5) - stays as P."""
        data = {"ph": 7.0, "analysis": {"P": 5}}
        result = build_solution_block(data)

        # P should be in output as-is, not mapped to P(5)
        lines = result.split("\n")
        p_line = [l for l in lines if l.strip().startswith("P") and "pe" not in l.lower()][0]
        assert "P(5)" not in p_line  # Should NOT map P to P(5)

    def test_explicit_valence_preserved(self):
        """Test explicitly specified valence is preserved."""
        data = {"ph": 7.0, "analysis": {"P(5)": 5, "S(-2)": 10}}
        result = build_solution_block(data)

        assert "P(5)" in result
        assert "S(-2)" in result

    def test_numeric_value(self):
        """Test numeric element values."""
        data = {"ph": 7.0, "analysis": {"Ca": 40.5, "Mg": 10}}
        result = build_solution_block(data)

        assert "40.5" in result
        assert "10" in result

    def test_dict_value_format(self):
        """Test dict format for element with 'as' specification."""
        data = {"ph": 7.0, "analysis": {"Ca": {"value": 100, "as": "CaCO3"}}}
        result = build_solution_block(data)

        assert "Ca" in result
        assert "100" in result
        assert "as CaCO3" in result

    def test_no_ph_defaults_applied(self):
        """Test that defaults are applied when no pH specified."""
        data = {"analysis": {"Ca": 40}}
        result = build_solution_block(data)

        # Should warn and use defaults
        assert "pH        7.0" in result
        assert "pe        4.0" in result


# =============================================================================
# BUILD REACTION BLOCK TESTS
# =============================================================================

class TestBuildReactionBlock:
    """Tests for build_reaction_block function."""

    def test_single_reactant(self):
        """Test reaction with single reactant."""
        reactants = [{"formula": "FeCl3", "amount": 0.1, "units": "mmol"}]
        result = build_reaction_block(reactants)

        assert "REACTION 1" in result
        assert "FeCl3 1.0" in result
        assert "0.1 mmol in 1 steps" in result

    def test_multiple_reactants(self):
        """Test reaction with multiple reactants."""
        reactants = [
            {"formula": "FeCl3", "amount": 0.1},
            {"formula": "NaOH", "amount": 0.3},
        ]
        result = build_reaction_block(reactants)

        assert "FeCl3 1.0" in result
        assert "NaOH 1.0" in result
        assert "0.4" in result  # Total amount

    def test_custom_reaction_num(self):
        """Test custom reaction block number."""
        reactants = [{"formula": "HCl", "amount": 1.0}]
        result = build_reaction_block(reactants, reaction_num=5)

        assert "REACTION 5" in result

    def test_empty_reactants_raises(self):
        """Test empty reactants list raises error."""
        with pytest.raises(InputValidationError) as exc_info:
            build_reaction_block([])

        assert "No valid reactants" in str(exc_info.value)

    def test_missing_formula_raises(self):
        """Test missing formula raises error."""
        reactants = [{"amount": 1.0}]

        with pytest.raises(InputValidationError):
            build_reaction_block(reactants)

    def test_missing_amount_raises(self):
        """Test missing amount raises error."""
        reactants = [{"formula": "FeCl3"}]

        with pytest.raises(InputValidationError):
            build_reaction_block(reactants)

    def test_default_units_mmol(self):
        """Test default units is mmol."""
        reactants = [{"formula": "FeCl3", "amount": 0.5}]
        result = build_reaction_block(reactants)

        assert "mmol" in result

    def test_advanced_steps(self):
        """Test advanced step specifications."""
        reactants = [{
            "formula": "FeCl3",
            "amount": 1.0,
            "steps": {"amounts": [0.1, 0.2, 0.3], "units": "mol", "count": 3}
        }]
        result = build_reaction_block(reactants)

        assert "0.1 0.2 0.3" in result
        assert "mol" in result
        assert "in 3 steps" in result


# =============================================================================
# BUILD EQUILIBRIUM PHASES BLOCK TESTS
# =============================================================================

class TestBuildEquilibriumPhasesBlock:
    """Tests for build_equilibrium_phases_block function."""

    def test_single_phase(self):
        """Test single equilibrium phase."""
        phases = [{"name": "Calcite", "target_si": 0.0}]
        result = build_equilibrium_phases_block(phases)

        assert "EQUILIBRIUM_PHASES 1" in result
        assert "Calcite" in result
        assert "0.0" in result

    def test_multiple_phases(self):
        """Test multiple equilibrium phases."""
        phases = [
            {"name": "Calcite", "target_si": 0.0},
            {"name": "Gypsum", "target_si": -0.5},
        ]
        result = build_equilibrium_phases_block(phases)

        assert "Calcite" in result
        assert "Gypsum" in result

    def test_precipitation_only_mode(self):
        """Test precipitation_only=True uses initial_moles=0."""
        phases = [{"name": "Ferrihydrite"}]
        result = build_equilibrium_phases_block(phases, precipitation_only=True)

        # initial_moles should be 0.0
        assert "0.0" in result or "0" in result

    def test_dissolution_mode(self):
        """Test precipitation_only=False uses initial_moles=10."""
        phases = [{"name": "Calcite"}]
        result = build_equilibrium_phases_block(phases, precipitation_only=False)

        # initial_moles should be 10
        assert "10" in result

    def test_custom_initial_moles(self):
        """Test custom initial_moles overrides default."""
        phases = [{"name": "Calcite", "initial_moles": 5.0}]
        result = build_equilibrium_phases_block(phases)

        assert "5.0" in result

    def test_empty_phases_raises(self):
        """Test empty phases list raises error."""
        with pytest.raises(InputValidationError):
            build_equilibrium_phases_block([])

    def test_empty_phases_allow_empty(self):
        """Test allow_empty=True returns empty string."""
        result = build_equilibrium_phases_block([], allow_empty=True)

        assert result == ""

    def test_phase_without_name_skipped(self):
        """Test phases without name are skipped."""
        phases = [{"target_si": 0.0}]  # Missing name

        with pytest.raises(InputValidationError):
            build_equilibrium_phases_block(phases)

    def test_custom_block_num(self):
        """Test custom block number."""
        phases = [{"name": "Calcite"}]
        result = build_equilibrium_phases_block(phases, block_num=3)

        assert "EQUILIBRIUM_PHASES 3" in result


# =============================================================================
# BUILD MIX BLOCK TESTS
# =============================================================================

class TestBuildMixBlock:
    """Tests for build_mix_block function."""

    def test_two_solutions(self):
        """Test mixing two solutions."""
        result = build_mix_block(1, {1: 0.5, 2: 0.5})

        assert "MIX 1" in result
        assert "1" in result
        assert "0.5" in result
        assert "2" in result

    def test_different_ratios(self):
        """Test different mixing ratios."""
        result = build_mix_block(2, {1: 0.7, 2: 0.3})

        assert "MIX 2" in result
        assert "0.7" in result
        assert "0.3" in result

    def test_empty_map_raises(self):
        """Test empty solution_map raises error."""
        with pytest.raises(InputValidationError) as exc_info:
            build_mix_block(1, {})

        assert "Empty solution_map" in str(exc_info.value)


# =============================================================================
# BUILD GAS PHASE BLOCK TESTS
# =============================================================================

class TestBuildGasPhaseBlock:
    """Tests for build_gas_phase_block function."""

    def test_fixed_pressure_gas(self):
        """Test fixed pressure gas phase."""
        gas_def = {
            "type": "fixed_pressure",
            "fixed_pressure_atm": 1.0,
            "initial_components": {"CO2(g)": 0.0003, "N2(g)": 0.78}
        }
        result = build_gas_phase_block(gas_def)

        assert "GAS_PHASE 1" in result
        assert "-fixed_pressure" in result
        assert "CO2(g)" in result
        assert "N2(g)" in result

    def test_fixed_volume_gas(self):
        """Test fixed volume gas phase."""
        gas_def = {
            "type": "fixed_volume",
            "initial_volume_liters": 10.0,
            "initial_components": {"CO2(g)": 0.1}
        }
        result = build_gas_phase_block(gas_def)

        assert "-fixed_volume" in result
        assert "10.0" in result

    def test_unknown_gas_type_raises(self):
        """Test unknown gas type raises error."""
        gas_def = {"type": "unknown_type", "initial_components": {"CO2(g)": 0.1}}

        with pytest.raises(GasPhaseError) as exc_info:
            build_gas_phase_block(gas_def)

        assert "unknown_type" in str(exc_info.value).lower()

    def test_no_components_raises(self):
        """Test no gas components raises error."""
        gas_def = {"type": "fixed_pressure", "initial_components": {}}

        with pytest.raises(GasPhaseError):
            build_gas_phase_block(gas_def)

    def test_custom_block_num(self):
        """Test custom block number."""
        gas_def = {"type": "fixed_pressure", "initial_components": {"CO2(g)": 0.1}}
        result = build_gas_phase_block(gas_def, block_num=2)

        assert "GAS_PHASE 2" in result


# =============================================================================
# BUILD SURFACE BLOCK TESTS
# =============================================================================

class TestBuildSurfaceBlock:
    """Tests for build_surface_block function."""

    def test_raw_surface_block_string(self):
        """Test raw surface block string passthrough."""
        surface_def = {
            "surface_block_string": """SURFACE 1
    Hfo_w  0.2  600  1.0
    Hfo_s  0.05  600  1.0"""
        }
        result = build_surface_block(surface_def)

        assert "SURFACE 1" in result
        assert "Hfo_w" in result
        assert "Hfo_s" in result

    def test_structured_sites_info(self):
        """Test structured sites_info format."""
        surface_def = {
            "sites_info": [
                {"name": "Hfo_w", "moles": 0.2, "specific_area_m2_g": 600, "mass_g": 1.0}
            ]
        }
        result = build_surface_block(surface_def)

        assert "SURFACE 1" in result
        assert "Hfo_w" in result
        assert "0.2" in result

    def test_simplified_sites_format(self):
        """Test simplified sites format with defaults."""
        surface_def = {
            "sites": ["Hfo_w", "Hfo_s"],
            "site_density": 0.01
        }
        result = build_surface_block(surface_def)

        assert "Hfo_w" in result
        assert "Hfo_s" in result

    def test_no_edl_option(self):
        """Test no_edl option is included."""
        surface_def = {
            "sites": ["Hfo_w"],
            "no_edl": True
        }
        result = build_surface_block(surface_def)

        assert "-no_edl" in result

    def test_donnan_option(self):
        """Test donnan option is included."""
        surface_def = {
            "sites": ["Hfo_w"],
            "donnan": True
        }
        result = build_surface_block(surface_def)

        assert "-donnan" in result

    def test_invalid_surface_def_raises(self):
        """Test invalid surface definition raises error."""
        surface_def = {}  # No valid fields

        with pytest.raises(SurfaceDefinitionError):
            build_surface_block(surface_def)

    def test_equilibrate_with_solution(self):
        """Test equilibrate_with_solution_number."""
        surface_def = {
            "sites": ["Hfo_w"],
            "equilibrate_with_solution_number": 5
        }
        result = build_surface_block(surface_def)

        assert "-equilibrate 5" in result


# =============================================================================
# BUILD KINETICS BLOCK TESTS
# =============================================================================

class TestBuildKineticsBlock:
    """Tests for build_kinetics_block function."""

    def test_raw_kinetics_block(self):
        """Test raw kinetics block string."""
        kinetics_def = {
            "kinetics_block_string": "Calcite\n    -formula CaCOite 1",
            "rates_block_string": "RATES\nCalcite\n-start\n10 rate = 0\n-end"
        }
        time_def = {"time_values": [3600], "units": "seconds"}

        rates_str, kinetics_str = build_kinetics_block(kinetics_def, time_def)

        assert "RATES" in rates_str
        assert "KINETICS 1" in kinetics_str

    def test_structured_reactions(self):
        """Test structured reaction definitions."""
        kinetics_def = {
            "reactions": [
                {"name": "Calcite", "formula": "CaCO3 1"}
            ]
        }
        time_def = {"duration": 3600, "count": 10, "duration_units": "seconds"}

        rates_str, kinetics_str = build_kinetics_block(kinetics_def, time_def)

        assert "KINETICS 1" in kinetics_str
        assert "Calcite" in kinetics_str
        assert "in 10 steps" in kinetics_str

    def test_no_reactions_raises(self):
        """Test no kinetics reactions raises error."""
        kinetics_def = {}
        time_def = {"duration": 3600, "count": 10}

        with pytest.raises(KineticsDefinitionError):
            build_kinetics_block(kinetics_def, time_def)

    def test_time_values_list(self):
        """Test time_values list format."""
        kinetics_def = {"kinetics_block_string": "Test\n    -formula X 1"}
        time_def = {"time_values": [100, 200, 300], "units": "seconds"}

        _, kinetics_str = build_kinetics_block(kinetics_def, time_def)

        assert "100 200 300" in kinetics_str
        assert "seconds" in kinetics_str

    def test_default_time_fallback(self):
        """Test default time step when no time info provided."""
        kinetics_def = {"kinetics_block_string": "Test\n    -formula X 1"}
        time_def = {}  # No time info

        _, kinetics_str = build_kinetics_block(kinetics_def, time_def)

        assert "3600" in kinetics_str  # Default 1 hour


# =============================================================================
# BUILD SELECTED OUTPUT BLOCK TESTS
# =============================================================================

class TestBuildSelectedOutputBlock:
    """Tests for build_selected_output_block function."""

    def test_default_options(self):
        """Test default selected output options."""
        result = build_selected_output_block()

        assert "SELECTED_OUTPUT 1" in result
        assert "-pH true" in result
        assert "-pe true" in result
        assert "-temp true" in result

    def test_totals_option(self):
        """Test totals option."""
        result = build_selected_output_block(totals=True)
        assert "-tot true" in result

        result = build_selected_output_block(totals=False)
        assert "-tot true" not in result

    def test_molalities_option(self):
        """Test molalities option."""
        result = build_selected_output_block(molalities=True)
        assert "-mol true" in result

    def test_saturation_indices_option(self):
        """Test saturation indices option."""
        result = build_selected_output_block(saturation_indices=True)
        assert "-si true" in result

    def test_composite_parameters(self):
        """Test composite parameters adds USER_PUNCH."""
        result = build_selected_output_block(composite_parameters=True)

        assert "-user_punch true" in result
        assert "Total_Hardness" in result
        assert "TOT" in result

    def test_custom_block_num(self):
        """Test custom block number."""
        result = build_selected_output_block(block_num=3)
        assert "SELECTED_OUTPUT 3" in result


# =============================================================================
# BUILD PHASE LINKED SURFACE BLOCK TESTS
# =============================================================================

class TestBuildPhaseLinkedSurfaceBlock:
    """Tests for build_phase_linked_surface_block function."""

    def test_basic_hfo_surface(self):
        """Test basic HFO phase-linked surface."""
        result = build_phase_linked_surface_block(
            surface_name="Hfo",
            phase_name="Ferrihydrite"
        )

        assert "SURFACE 1" in result
        assert "Hfo_sOH" in result
        assert "Hfo_wOH" in result
        assert "Ferrihydrite" in result
        assert "equilibrium_phase" in result

    def test_custom_site_density(self):
        """Test custom site density."""
        result = build_phase_linked_surface_block(
            surface_name="Hfo",
            phase_name="Ferrihydrite",
            sites_per_mole=0.01
        )

        assert "0.01" in result

    def test_weak_to_strong_ratio(self):
        """Test weak to strong ratio."""
        result = build_phase_linked_surface_block(
            surface_name="Hfo",
            phase_name="Ferrihydrite",
            sites_per_mole=0.005,
            weak_to_strong_ratio=40.0
        )

        # Weak sites = 0.005 * 40 = 0.2
        assert "0.2" in result

    def test_no_edl_option(self):
        """Test no_edl option."""
        result = build_phase_linked_surface_block(
            surface_name="Hfo",
            phase_name="Ferrihydrite",
            no_edl=True
        )

        assert "-no_edl" in result

    def test_missing_surface_name_raises(self):
        """Test missing surface_name raises error."""
        with pytest.raises(SurfaceDefinitionError):
            build_phase_linked_surface_block(
                surface_name="",
                phase_name="Ferrihydrite"
            )

    def test_missing_phase_name_raises(self):
        """Test missing phase_name raises error."""
        with pytest.raises(SurfaceDefinitionError):
            build_phase_linked_surface_block(
                surface_name="Hfo",
                phase_name=""
            )

    def test_equilibrate_solution_number(self):
        """Test equilibrate_solution parameter."""
        result = build_phase_linked_surface_block(
            surface_name="Hfo",
            phase_name="Ferrihydrite",
            equilibrate_solution=5
        )

        assert "-equilibrate 5" in result

    def test_custom_block_num(self):
        """Test custom block number."""
        result = build_phase_linked_surface_block(
            surface_name="Hfo",
            phase_name="Ferrihydrite",
            block_num=3
        )

        assert "SURFACE 3" in result


# =============================================================================
# BUILD USER PUNCH FOR PARTITIONING TESTS
# =============================================================================

class TestBuildUserPunchForPartitioning:
    """Tests for build_user_punch_for_partitioning function."""

    def test_basic_punch_block(self):
        """Test basic USER_PUNCH block generation."""
        result = build_user_punch_for_partitioning(
            phases=["Strengite", "Vivianite"]
        )

        assert "USER_PUNCH 1" in result
        assert "-headings" in result
        assert "equi_Strengite" in result
        assert "equi_Vivianite" in result
        assert "EQUI(" in result

    def test_default_elements_p_fe(self):
        """Test default elements are P and Fe."""
        result = build_user_punch_for_partitioning(phases=["Strengite"])

        assert "surf_P" in result
        assert "surf_Fe" in result
        assert "tot_P" in result
        assert "tot_Fe" in result

    def test_custom_elements(self):
        """Test custom elements."""
        result = build_user_punch_for_partitioning(
            phases=["Calcite"],
            elements=["Ca", "C"]
        )

        assert "surf_Ca" in result
        assert "surf_C" in result
        assert "tot_Ca" in result
        assert "tot_C" in result

    def test_custom_surface_name(self):
        """Test custom surface name."""
        result = build_user_punch_for_partitioning(
            phases=["Strengite"],
            surface_name="Goethite"
        )

        assert "surf_P_Goethite" in result
        assert "Goethite" in result

    def test_no_solution_totals(self):
        """Test include_solution_totals=False."""
        result = build_user_punch_for_partitioning(
            phases=["Strengite"],
            include_solution_totals=False
        )

        assert "tot_P" not in result
        assert "tot_Fe" not in result
        assert "surf_P" in result  # Surface still included

    def test_phase_with_parentheses(self):
        """Test phase names with parentheses are escaped."""
        result = build_user_punch_for_partitioning(
            phases=["Fe(OH)3(a)"]
        )

        # Variable names should have parentheses replaced
        assert "equi_Fe_OH_3_a_" in result or "equi_Fe(OH)3(a)" in result

    def test_start_end_blocks(self):
        """Test USER_PUNCH has -start and -end."""
        result = build_user_punch_for_partitioning(phases=["Strengite"])

        assert "-start" in result
        assert "-end" in result

    def test_punch_statement(self):
        """Test PUNCH statement is generated."""
        result = build_user_punch_for_partitioning(phases=["Strengite"])

        assert "PUNCH" in result

    def test_custom_block_num(self):
        """Test custom block number."""
        result = build_user_punch_for_partitioning(
            phases=["Strengite"],
            block_num=5
        )

        assert "USER_PUNCH 5" in result


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
