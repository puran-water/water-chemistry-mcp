"""
Unit tests for ferric phosphate schema validators and utility functions.

Tests:
- Unit conversion functions (mg_l_to_mmol, mmol_to_mg_l)
- ORP/pe conversion functions
- Schema validators (RedoxSpecification, CalculateFerricDoseInput)
- Boundary conditions and edge cases
"""

import pytest
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.schemas_ferric import (
    MOLECULAR_WEIGHTS,
    mg_l_to_mmol,
    mmol_to_mg_l,
    orp_to_pe,
    pe_to_orp,
    RedoxSpecification,
    SurfaceComplexationOptions,
    BinarySearchOptions,
    CalculateFerricDoseInput,
    WaterAnalysisInput,
)
from pydantic import ValidationError


# =============================================================================
# UNIT CONVERSION TESTS
# =============================================================================

class TestMgLToMmol:
    """Tests for mg/L to mmol/L conversion."""

    def test_p_conversion(self):
        """Test phosphorus conversion: 30.97 mg/L P = 1 mmol/L."""
        result = mg_l_to_mmol(30.97, "P")
        assert abs(result - 1.0) < 0.001, f"Expected ~1.0, got {result}"

    def test_fe_conversion(self):
        """Test iron conversion: 55.85 mg/L Fe = 1 mmol/L."""
        result = mg_l_to_mmol(55.85, "Fe")
        assert abs(result - 1.0) < 0.001, f"Expected ~1.0, got {result}"

    def test_fecl3_conversion(self):
        """Test FeCl3 conversion: 162.2 mg/L FeCl3 = 1 mmol/L."""
        result = mg_l_to_mmol(162.2, "FeCl3")
        assert abs(result - 1.0) < 0.001, f"Expected ~1.0, got {result}"

    def test_feso4_conversion(self):
        """Test FeSO4 conversion."""
        result = mg_l_to_mmol(151.9, "FeSO4")
        assert abs(result - 1.0) < 0.001, f"Expected ~1.0, got {result}"

    def test_fecl2_conversion(self):
        """Test FeCl2 conversion."""
        result = mg_l_to_mmol(126.75, "FeCl2")
        assert abs(result - 1.0) < 0.001, f"Expected ~1.0, got {result}"

    def test_zero_value(self):
        """Test zero input returns zero."""
        result = mg_l_to_mmol(0.0, "P")
        assert result == 0.0, f"Expected 0.0, got {result}"

    def test_small_value(self):
        """Test small value conversion (0.01 mg/L P)."""
        result = mg_l_to_mmol(0.01, "P")
        expected = 0.01 / 30.97
        assert abs(result - expected) < 1e-6, f"Expected {expected}, got {result}"

    def test_large_value(self):
        """Test large value conversion (1000 mg/L Fe)."""
        result = mg_l_to_mmol(1000.0, "Fe")
        expected = 1000.0 / 55.85
        assert abs(result - expected) < 0.01, f"Expected {expected}, got {result}"

    def test_unknown_element_raises_error(self):
        """Test unknown element raises ValueError."""
        with pytest.raises(ValueError, match="Unknown molecular weight"):
            mg_l_to_mmol(100.0, "UnknownElement")

    def test_empty_element_raises_error(self):
        """Test empty element string raises ValueError."""
        with pytest.raises(ValueError):
            mg_l_to_mmol(100.0, "")


class TestMmolToMgL:
    """Tests for mmol/L to mg/L conversion."""

    def test_p_conversion(self):
        """Test phosphorus conversion: 1 mmol/L P = 30.97 mg/L."""
        result = mmol_to_mg_l(1.0, "P")
        assert abs(result - 30.97) < 0.01, f"Expected ~30.97, got {result}"

    def test_fe_conversion(self):
        """Test iron conversion: 1 mmol/L Fe = 55.85 mg/L."""
        result = mmol_to_mg_l(1.0, "Fe")
        assert abs(result - 55.85) < 0.01, f"Expected ~55.85, got {result}"

    def test_roundtrip_p(self):
        """Test mg/L -> mmol -> mg/L roundtrip for P."""
        original = 5.0
        mmol = mg_l_to_mmol(original, "P")
        result = mmol_to_mg_l(mmol, "P")
        assert abs(result - original) < 0.001, f"Roundtrip failed: {original} -> {result}"

    def test_roundtrip_fe(self):
        """Test mg/L -> mmol -> mg/L roundtrip for Fe."""
        original = 37.2
        mmol = mg_l_to_mmol(original, "Fe")
        result = mmol_to_mg_l(mmol, "Fe")
        assert abs(result - original) < 0.001, f"Roundtrip failed: {original} -> {result}"

    def test_zero_value(self):
        """Test zero input returns zero."""
        result = mmol_to_mg_l(0.0, "Fe")
        assert result == 0.0, f"Expected 0.0, got {result}"

    def test_unknown_element_raises_error(self):
        """Test unknown element raises ValueError."""
        with pytest.raises(ValueError, match="Unknown molecular weight"):
            mmol_to_mg_l(1.0, "Xyz")


# =============================================================================
# ORP/PE CONVERSION TESTS
# =============================================================================

class TestOrpToPe:
    """Tests for ORP to pe conversion."""

    def test_she_reference_positive_orp(self):
        """Test ORP vs SHE reference with positive ORP."""
        # At 25C: pe = ORP(mV) / 59.16
        pe = orp_to_pe(250.0, 25.0, "SHE")
        expected = 250.0 / 59.16
        assert abs(pe - expected) < 0.1, f"Expected ~{expected}, got {pe}"

    def test_she_reference_zero_orp(self):
        """Test ORP = 0 mV vs SHE returns pe ~ 0."""
        pe = orp_to_pe(0.0, 25.0, "SHE")
        assert abs(pe) < 0.1, f"Expected ~0, got {pe}"

    def test_she_reference_negative_orp(self):
        """Test negative ORP (reducing conditions)."""
        pe = orp_to_pe(-200.0, 25.0, "SHE")
        expected = -200.0 / 59.16
        assert abs(pe - expected) < 0.1, f"Expected ~{expected}, got {pe}"

    def test_agagcl_3m_reference(self):
        """Test ORP vs Ag/AgCl 3M KCl reference (+210 mV correction)."""
        # AgAgCl_3M is typically +210 mV vs SHE
        pe = orp_to_pe(100.0, 25.0, "AgAgCl_3M")
        # Should be (100 + 210) / 59.16
        expected = (100.0 + 210.0) / 59.16
        assert abs(pe - expected) < 0.2, f"Expected ~{expected}, got {pe}"

    def test_agagcl_sat_reference(self):
        """Test ORP vs Ag/AgCl saturated KCl reference (+197 mV correction)."""
        pe = orp_to_pe(100.0, 25.0, "AgAgCl_sat")
        expected = (100.0 + 197.0) / 59.16
        assert abs(pe - expected) < 0.2, f"Expected ~{expected}, got {pe}"

    def test_temperature_effect_low(self):
        """Test temperature effect at 5C (higher Nernst factor)."""
        pe_25c = orp_to_pe(250.0, 25.0, "SHE")
        pe_5c = orp_to_pe(250.0, 5.0, "SHE")
        # At lower temp, Nernst factor is smaller, so pe should be higher
        assert pe_5c > pe_25c, f"pe at 5C ({pe_5c}) should be > pe at 25C ({pe_25c})"

    def test_temperature_effect_high(self):
        """Test temperature effect at 35C (lower Nernst factor)."""
        pe_25c = orp_to_pe(250.0, 25.0, "SHE")
        pe_35c = orp_to_pe(250.0, 35.0, "SHE")
        # At higher temp, Nernst factor is larger, so pe should be lower
        assert pe_35c < pe_25c, f"pe at 35C ({pe_35c}) should be < pe at 25C ({pe_25c})"


class TestPeToOrp:
    """Tests for pe to ORP conversion."""

    def test_positive_pe(self):
        """Test positive pe to ORP conversion."""
        orp = pe_to_orp(8.0, 25.0)
        expected = 8.0 * 59.16  # ~473 mV
        assert abs(orp - expected) < 5, f"Expected ~{expected}, got {orp}"

    def test_negative_pe(self):
        """Test negative pe to ORP conversion."""
        orp = pe_to_orp(-4.0, 25.0)
        expected = -4.0 * 59.16  # ~-237 mV
        assert abs(orp - expected) < 5, f"Expected ~{expected}, got {orp}"

    def test_zero_pe(self):
        """Test pe = 0 returns ORP ~ 0."""
        orp = pe_to_orp(0.0, 25.0)
        assert abs(orp) < 1, f"Expected ~0, got {orp}"

    def test_roundtrip(self):
        """Test pe -> ORP -> pe roundtrip."""
        original_pe = 6.5
        orp = pe_to_orp(original_pe, 25.0)
        result_pe = orp_to_pe(orp, 25.0, "SHE")
        assert abs(result_pe - original_pe) < 0.01, f"Roundtrip failed: {original_pe} -> {result_pe}"


# =============================================================================
# REDOX SPECIFICATION VALIDATOR TESTS
# =============================================================================

class TestRedoxSpecificationValidator:
    """Tests for RedoxSpecification Pydantic validators."""

    def test_aerobic_mode_valid(self):
        """Test aerobic mode is valid without additional fields."""
        redox = RedoxSpecification(mode="aerobic")
        assert redox.mode == "aerobic"

    def test_anaerobic_mode_valid(self):
        """Test anaerobic mode is valid without additional fields."""
        redox = RedoxSpecification(mode="anaerobic")
        assert redox.mode == "anaerobic"

    def test_pe_from_orp_requires_orp_mv(self):
        """Test pe_from_orp mode requires orp_mv field."""
        with pytest.raises(ValidationError) as exc_info:
            RedoxSpecification(mode="pe_from_orp")
        assert "orp_mv" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()

    def test_pe_from_orp_with_orp_mv_valid(self):
        """Test pe_from_orp mode with orp_mv is valid."""
        redox = RedoxSpecification(mode="pe_from_orp", orp_mv=250.0)
        assert redox.mode == "pe_from_orp"
        assert redox.orp_mv == 250.0

    def test_pe_from_orp_with_reference(self):
        """Test pe_from_orp with custom reference electrode."""
        redox = RedoxSpecification(
            mode="pe_from_orp",
            orp_mv=100.0,
            orp_reference="AgAgCl_3M"
        )
        assert redox.orp_reference == "AgAgCl_3M"

    def test_fixed_pe_requires_pe_value(self):
        """Test fixed_pe mode requires pe_value field."""
        with pytest.raises(ValidationError) as exc_info:
            RedoxSpecification(mode="fixed_pe")
        assert "pe" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()

    def test_fixed_pe_with_pe_value_valid(self):
        """Test fixed_pe mode with pe_value is valid."""
        redox = RedoxSpecification(mode="fixed_pe", pe_value=-4.0)
        assert redox.mode == "fixed_pe"
        assert redox.pe_value == -4.0

    def test_fixed_fe2_fraction_valid(self):
        """Test fixed_fe2_fraction mode."""
        redox = RedoxSpecification(mode="fixed_fe2_fraction", fe2_fraction=0.5)
        assert redox.mode == "fixed_fe2_fraction"
        assert redox.fe2_fraction == 0.5


# =============================================================================
# SURFACE COMPLEXATION OPTIONS TESTS
# =============================================================================

class TestSurfaceComplexationOptions:
    """Tests for SurfaceComplexationOptions validation."""

    def test_default_values(self):
        """Test default values are applied."""
        opts = SurfaceComplexationOptions()
        assert opts.enabled is True
        assert opts.surface_name == "Hfo"
        assert opts.sites_per_mole_strong == 0.005
        assert opts.weak_to_strong_ratio == 40.0

    def test_custom_values(self):
        """Test custom values are accepted."""
        opts = SurfaceComplexationOptions(
            enabled=False,
            surface_name="Custom",
            sites_per_mole_strong=0.01,
            weak_to_strong_ratio=20.0
        )
        assert opts.enabled is False
        assert opts.surface_name == "Custom"
        assert opts.sites_per_mole_strong == 0.01
        assert opts.weak_to_strong_ratio == 20.0

    def test_sites_must_be_positive(self):
        """Test sites_per_mole_strong must be > 0."""
        with pytest.raises(ValidationError):
            SurfaceComplexationOptions(sites_per_mole_strong=0.0)

    def test_ratio_must_be_positive(self):
        """Test weak_to_strong_ratio must be > 0."""
        with pytest.raises(ValidationError):
            SurfaceComplexationOptions(weak_to_strong_ratio=0.0)


# =============================================================================
# BINARY SEARCH OPTIONS TESTS
# =============================================================================

class TestBinarySearchOptions:
    """Tests for BinarySearchOptions validation."""

    def test_default_values(self):
        """Test default values are applied."""
        opts = BinarySearchOptions()
        assert opts.max_iterations == 30
        assert opts.tolerance_mg_l == 0.01
        assert opts.initial_dose_multiplier == 3.0

    def test_max_iterations_minimum(self):
        """Test max_iterations minimum is 5."""
        opts = BinarySearchOptions(max_iterations=5)
        assert opts.max_iterations == 5

    def test_max_iterations_below_minimum_fails(self):
        """Test max_iterations < 5 fails validation."""
        with pytest.raises(ValidationError):
            BinarySearchOptions(max_iterations=4)

    def test_max_iterations_maximum(self):
        """Test max_iterations maximum is 100."""
        opts = BinarySearchOptions(max_iterations=100)
        assert opts.max_iterations == 100

    def test_max_iterations_above_maximum_fails(self):
        """Test max_iterations > 100 fails validation."""
        with pytest.raises(ValidationError):
            BinarySearchOptions(max_iterations=101)

    def test_tolerance_must_be_positive(self):
        """Test tolerance_mg_l must be > 0."""
        with pytest.raises(ValidationError):
            BinarySearchOptions(tolerance_mg_l=0.0)

    def test_dose_multiplier_must_exceed_one(self):
        """Test initial_dose_multiplier must be > 1."""
        with pytest.raises(ValidationError):
            BinarySearchOptions(initial_dose_multiplier=1.0)


# =============================================================================
# MAIN INPUT SCHEMA TESTS
# =============================================================================

class TestCalculateFerricDoseInput:
    """Tests for CalculateFerricDoseInput validation."""

    def test_minimal_valid_input(self):
        """Test minimal valid input is accepted."""
        input_data = CalculateFerricDoseInput(
            initial_solution=WaterAnalysisInput(
                ph=7.0,
                analysis={"P": 5.0},
                units="mg/L"
            ),
            target_residual_p_mg_l=0.5
        )
        assert input_data.target_residual_p_mg_l == 0.5

    def test_target_must_be_less_than_initial(self):
        """Test target P must be less than initial P."""
        with pytest.raises(ValidationError) as exc_info:
            CalculateFerricDoseInput(
                initial_solution=WaterAnalysisInput(
                    ph=7.0,
                    analysis={"P": 5.0},
                    units="mg/L"
                ),
                target_residual_p_mg_l=10.0  # > initial 5.0
            )
        assert "target" in str(exc_info.value).lower() or "less than" in str(exc_info.value).lower()

    def test_target_equals_initial_fails(self):
        """Test target P equal to initial P fails."""
        with pytest.raises(ValidationError):
            CalculateFerricDoseInput(
                initial_solution=WaterAnalysisInput(
                    ph=7.0,
                    analysis={"P": 5.0},
                    units="mg/L"
                ),
                target_residual_p_mg_l=5.0  # == initial
            )

    def test_default_iron_source(self):
        """Test default iron source is FeCl3."""
        input_data = CalculateFerricDoseInput(
            initial_solution=WaterAnalysisInput(
                ph=7.0,
                analysis={"P": 5.0},
                units="mg/L"
            ),
            target_residual_p_mg_l=0.5
        )
        assert input_data.iron_source == "FeCl3"

    def test_default_max_dose(self):
        """Test default max_dose_mg_l is 500."""
        input_data = CalculateFerricDoseInput(
            initial_solution=WaterAnalysisInput(
                ph=7.0,
                analysis={"P": 5.0},
                units="mg/L"
            ),
            target_residual_p_mg_l=0.5
        )
        assert input_data.max_dose_mg_l == 500.0

    def test_max_dose_minimum(self):
        """Test max_dose_mg_l minimum is 10."""
        with pytest.raises(ValidationError):
            CalculateFerricDoseInput(
                initial_solution=WaterAnalysisInput(
                    ph=7.0,
                    analysis={"P": 5.0},
                    units="mg/L"
                ),
                target_residual_p_mg_l=0.5,
                max_dose_mg_l=5.0  # < 10
            )

    def test_hfo_site_multiplier_range(self):
        """Test hfo_site_multiplier range is 0.1 to 5.0."""
        # Valid at boundaries
        input_low = CalculateFerricDoseInput(
            initial_solution=WaterAnalysisInput(ph=7.0, analysis={"P": 5.0}, units="mg/L"),
            target_residual_p_mg_l=0.5,
            hfo_site_multiplier=0.1
        )
        assert input_low.hfo_site_multiplier == 0.1

        input_high = CalculateFerricDoseInput(
            initial_solution=WaterAnalysisInput(ph=7.0, analysis={"P": 5.0}, units="mg/L"),
            target_residual_p_mg_l=0.5,
            hfo_site_multiplier=5.0
        )
        assert input_high.hfo_site_multiplier == 5.0

    def test_hfo_site_multiplier_below_range_fails(self):
        """Test hfo_site_multiplier < 0.1 fails."""
        with pytest.raises(ValidationError):
            CalculateFerricDoseInput(
                initial_solution=WaterAnalysisInput(ph=7.0, analysis={"P": 5.0}, units="mg/L"),
                target_residual_p_mg_l=0.5,
                hfo_site_multiplier=0.05
            )

    def test_hfo_site_multiplier_above_range_fails(self):
        """Test hfo_site_multiplier > 5.0 fails."""
        with pytest.raises(ValidationError):
            CalculateFerricDoseInput(
                initial_solution=WaterAnalysisInput(ph=7.0, analysis={"P": 5.0}, units="mg/L"),
                target_residual_p_mg_l=0.5,
                hfo_site_multiplier=6.0
            )

    def test_p_inert_must_be_non_negative(self):
        """Test p_inert_soluble_mg_l must be >= 0."""
        # Valid at 0
        input_data = CalculateFerricDoseInput(
            initial_solution=WaterAnalysisInput(ph=7.0, analysis={"P": 5.0}, units="mg/L"),
            target_residual_p_mg_l=0.5,
            p_inert_soluble_mg_l=0.0
        )
        assert input_data.p_inert_soluble_mg_l == 0.0


# =============================================================================
# COAGULANT CONVERSION TESTS
# =============================================================================

from tools.schemas_ferric import (
    COAGULANT_DEFINITIONS,
    get_coagulant_metal,
    get_metal_atoms_per_formula,
    metal_dose_to_product_dose,
    product_dose_to_metal_dose,
    is_iron_coagulant,
    is_aluminum_coagulant,
)


class TestCoagulantDefinitions:
    """Tests for coagulant definition mappings."""

    def test_fecl3_is_single_metal(self):
        """Test FeCl3 has 1 Fe atom per formula."""
        assert COAGULANT_DEFINITIONS["FeCl3"]["metal_atoms"] == 1

    def test_fe2so4_3_is_double_metal(self):
        """Test Fe2(SO4)3 has 2 Fe atoms per formula."""
        assert COAGULANT_DEFINITIONS["Fe2(SO4)3"]["metal_atoms"] == 2

    def test_al2so4_3_is_double_metal(self):
        """Test Al2(SO4)3 has 2 Al atoms per formula."""
        assert COAGULANT_DEFINITIONS["Al2(SO4)3"]["metal_atoms"] == 2

    def test_alcl3_is_single_metal(self):
        """Test AlCl3 has 1 Al atom per formula."""
        assert COAGULANT_DEFINITIONS["AlCl3"]["metal_atoms"] == 1


class TestGetCoagulantMetal:
    """Tests for get_coagulant_metal function."""

    def test_fecl3_metal_is_fe(self):
        """Test FeCl3 returns Fe."""
        assert get_coagulant_metal("FeCl3") == "Fe"

    def test_feso4_metal_is_fe(self):
        """Test FeSO4 returns Fe."""
        assert get_coagulant_metal("FeSO4") == "Fe"

    def test_fe2so4_3_metal_is_fe(self):
        """Test Fe2(SO4)3 returns Fe."""
        assert get_coagulant_metal("Fe2(SO4)3") == "Fe"

    def test_alcl3_metal_is_al(self):
        """Test AlCl3 returns Al."""
        assert get_coagulant_metal("AlCl3") == "Al"

    def test_al2so4_3_metal_is_al(self):
        """Test Al2(SO4)3 returns Al."""
        assert get_coagulant_metal("Al2(SO4)3") == "Al"

    def test_unknown_formula_raises(self):
        """Test unknown formula raises ValueError."""
        with pytest.raises(ValueError, match="Unknown coagulant formula"):
            get_coagulant_metal("UnknownCoag")


class TestGetMetalAtomsPerFormula:
    """Tests for get_metal_atoms_per_formula function."""

    def test_fecl3_single_atom(self):
        """Test FeCl3 returns 1."""
        assert get_metal_atoms_per_formula("FeCl3") == 1

    def test_fe2so4_3_double_atom(self):
        """Test Fe2(SO4)3 returns 2."""
        assert get_metal_atoms_per_formula("Fe2(SO4)3") == 2

    def test_al2so4_3_double_atom(self):
        """Test Al2(SO4)3 returns 2."""
        assert get_metal_atoms_per_formula("Al2(SO4)3") == 2

    def test_unknown_formula_raises(self):
        """Test unknown formula raises ValueError."""
        with pytest.raises(ValueError, match="Unknown coagulant formula"):
            get_metal_atoms_per_formula("UnknownCoag")


class TestMetalDoseToProductDose:
    """Tests for metal_dose_to_product_dose conversion."""

    def test_fecl3_1mmol_fe_equals_1mmol_product(self):
        """Test 1 mmol Fe with FeCl3 = 1 mmol FeCl3."""
        result = metal_dose_to_product_dose(1.0, "FeCl3")
        assert abs(result - 1.0) < 0.001

    def test_fe2so4_3_2mmol_fe_equals_1mmol_product(self):
        """Test 2 mmol Fe with Fe2(SO4)3 = 1 mmol Fe2(SO4)3."""
        result = metal_dose_to_product_dose(2.0, "Fe2(SO4)3")
        assert abs(result - 1.0) < 0.001

    def test_fe2so4_3_1mmol_fe_equals_half_mmol_product(self):
        """Test 1 mmol Fe with Fe2(SO4)3 = 0.5 mmol Fe2(SO4)3."""
        result = metal_dose_to_product_dose(1.0, "Fe2(SO4)3")
        assert abs(result - 0.5) < 0.001

    def test_al2so4_3_2mmol_al_equals_1mmol_product(self):
        """Test 2 mmol Al with Al2(SO4)3 = 1 mmol Al2(SO4)3."""
        result = metal_dose_to_product_dose(2.0, "Al2(SO4)3")
        assert abs(result - 1.0) < 0.001


class TestProductDoseToMetalDose:
    """Tests for product_dose_to_metal_dose conversion."""

    def test_fecl3_1mmol_product_equals_1mmol_fe(self):
        """Test 1 mmol FeCl3 = 1 mmol Fe."""
        result = product_dose_to_metal_dose(1.0, "FeCl3")
        assert abs(result - 1.0) < 0.001

    def test_fe2so4_3_1mmol_product_equals_2mmol_fe(self):
        """Test 1 mmol Fe2(SO4)3 = 2 mmol Fe."""
        result = product_dose_to_metal_dose(1.0, "Fe2(SO4)3")
        assert abs(result - 2.0) < 0.001

    def test_al2so4_3_1mmol_product_equals_2mmol_al(self):
        """Test 1 mmol Al2(SO4)3 = 2 mmol Al."""
        result = product_dose_to_metal_dose(1.0, "Al2(SO4)3")
        assert abs(result - 2.0) < 0.001


class TestIsIronCoagulant:
    """Tests for is_iron_coagulant function."""

    def test_fecl3_is_iron(self):
        """Test FeCl3 is iron."""
        assert is_iron_coagulant("FeCl3") is True

    def test_fe2so4_3_is_iron(self):
        """Test Fe2(SO4)3 is iron."""
        assert is_iron_coagulant("Fe2(SO4)3") is True

    def test_alcl3_is_not_iron(self):
        """Test AlCl3 is not iron."""
        assert is_iron_coagulant("AlCl3") is False

    def test_unknown_is_not_iron(self):
        """Test unknown formula is not iron."""
        assert is_iron_coagulant("Unknown") is False


class TestIsAluminumCoagulant:
    """Tests for is_aluminum_coagulant function."""

    def test_alcl3_is_aluminum(self):
        """Test AlCl3 is aluminum."""
        assert is_aluminum_coagulant("AlCl3") is True

    def test_al2so4_3_is_aluminum(self):
        """Test Al2(SO4)3 is aluminum."""
        assert is_aluminum_coagulant("Al2(SO4)3") is True

    def test_fecl3_is_not_aluminum(self):
        """Test FeCl3 is not aluminum."""
        assert is_aluminum_coagulant("FeCl3") is False

    def test_unknown_is_not_aluminum(self):
        """Test unknown formula is not aluminum."""
        assert is_aluminum_coagulant("Unknown") is False


class TestMetalDoseConversionRoundTrip:
    """Tests for round-trip conversion between metal and product doses."""

    def test_fecl3_round_trip(self):
        """Test FeCl3 conversion round-trip."""
        metal_dose = 5.0
        product_dose = metal_dose_to_product_dose(metal_dose, "FeCl3")
        recovered = product_dose_to_metal_dose(product_dose, "FeCl3")
        assert abs(recovered - metal_dose) < 0.001

    def test_fe2so4_3_round_trip(self):
        """Test Fe2(SO4)3 conversion round-trip."""
        metal_dose = 5.0
        product_dose = metal_dose_to_product_dose(metal_dose, "Fe2(SO4)3")
        recovered = product_dose_to_metal_dose(product_dose, "Fe2(SO4)3")
        assert abs(recovered - metal_dose) < 0.001

    def test_al2so4_3_round_trip(self):
        """Test Al2(SO4)3 conversion round-trip."""
        metal_dose = 5.0
        product_dose = metal_dose_to_product_dose(metal_dose, "Al2(SO4)3")
        recovered = product_dose_to_metal_dose(product_dose, "Al2(SO4)3")
        assert abs(recovered - metal_dose) < 0.001


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
