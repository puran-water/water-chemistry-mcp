"""
Unit tests for PHREEQC output parser functions.

Tests:
- _parse_selected_output function
- _normalize_element_name helper
- _is_element_total_column helper
- Various header format handling
- Edge cases in parsing
"""

import pytest
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.phreeqc_wrapper import (
    _parse_selected_output,
    _normalize_element_name,
    _is_element_total_column,
)


# =============================================================================
# ELEMENT NAME NORMALIZATION TESTS
# =============================================================================

class TestNormalizeElementName:
    """Tests for _normalize_element_name function."""

    def test_single_letter_element(self):
        """Test single letter elements are unchanged."""
        assert _normalize_element_name("P") == "P"
        assert _normalize_element_name("N") == "N"
        assert _normalize_element_name("S") == "S"
        assert _normalize_element_name("C") == "C"

    def test_two_letter_element(self):
        """Test two letter elements are unchanged."""
        assert _normalize_element_name("Ca") == "Ca"
        assert _normalize_element_name("Fe") == "Fe"
        assert _normalize_element_name("Mg") == "Mg"
        assert _normalize_element_name("Na") == "Na"
        assert _normalize_element_name("Cl") == "Cl"

    def test_element_with_positive_valence(self):
        """Test elements with positive valence notation are normalized."""
        assert _normalize_element_name("P(5)") == "P"
        assert _normalize_element_name("Fe(3)") == "Fe"
        assert _normalize_element_name("Fe(2)") == "Fe"
        assert _normalize_element_name("N(5)") == "N"

    def test_element_with_negative_valence(self):
        """Test elements with negative valence notation are normalized."""
        assert _normalize_element_name("S(-2)") == "S"
        assert _normalize_element_name("N(-3)") == "N"
        assert _normalize_element_name("C(-4)") == "C"

    def test_element_with_signed_positive_valence(self):
        """Test elements with explicit + sign in valence."""
        assert _normalize_element_name("Fe(+3)") == "Fe"
        assert _normalize_element_name("Fe(+2)") == "Fe"

    def test_whitespace_handling(self):
        """Test whitespace is stripped."""
        assert _normalize_element_name(" P ") == "P"
        assert _normalize_element_name("  Fe(2)  ") == "Fe"

    def test_invalid_format_unchanged(self):
        """Test invalid formats are returned unchanged."""
        assert _normalize_element_name("si_Calcite") == "si_Calcite"
        assert _normalize_element_name("tot_P") == "tot_P"
        assert _normalize_element_name("m_Ca+2") == "m_Ca+2"


# =============================================================================
# IS ELEMENT TOTAL COLUMN TESTS
# =============================================================================

class TestIsElementTotalColumn:
    """Tests for _is_element_total_column function."""

    def test_single_letter_element(self):
        """Test single letter elements are recognized."""
        assert _is_element_total_column("P") is True
        assert _is_element_total_column("N") is True
        assert _is_element_total_column("S") is True
        assert _is_element_total_column("C") is True

    def test_two_letter_element(self):
        """Test two letter elements are recognized."""
        assert _is_element_total_column("Ca") is True
        assert _is_element_total_column("Fe") is True
        assert _is_element_total_column("Mg") is True
        assert _is_element_total_column("Na") is True

    def test_element_with_valence(self):
        """Test elements with valence notation are recognized."""
        assert _is_element_total_column("P(5)") is True
        assert _is_element_total_column("Fe(2)") is True
        assert _is_element_total_column("S(-2)") is True
        assert _is_element_total_column("Fe(+3)") is True

    def test_prefixed_headers_rejected(self):
        """Test prefixed headers are not recognized as element columns."""
        assert _is_element_total_column("si_Calcite") is False
        assert _is_element_total_column("tot_P") is False
        assert _is_element_total_column("m_Ca+2") is False
        assert _is_element_total_column("equi_Strengite") is False
        assert _is_element_total_column("surf_P") is False

    def test_numeric_headers_rejected(self):
        """Test numeric headers are rejected."""
        assert _is_element_total_column("123") is False
        assert _is_element_total_column("1.5e-05") is False

    def test_special_headers_rejected(self):
        """Test special PHREEQC headers are rejected."""
        assert _is_element_total_column("pH") is False
        assert _is_element_total_column("pe") is False
        assert _is_element_total_column("sim") is False
        assert _is_element_total_column("state") is False

    def test_whitespace_handling(self):
        """Test whitespace is handled."""
        assert _is_element_total_column(" P ") is True
        assert _is_element_total_column("  Fe  ") is True


# =============================================================================
# PARSE SELECTED OUTPUT TESTS
# =============================================================================

class TestParseSelectedOutput:
    """Tests for _parse_selected_output function."""

    def _create_temp_file(self, content):
        """Helper to create temporary file with content."""
        fd, path = tempfile.mkstemp(suffix=".sel")
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(content)
            return path
        except Exception:
            os.close(fd)
            raise

    def test_parse_empty_file(self):
        """Test parsing file with no data lines."""
        content = "header1\theader2\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            # Should return empty dicts but not fail
            assert "saturation_indices" in result
            assert "element_totals_molality" in result
        finally:
            os.unlink(path)

    def test_parse_minimal_file(self):
        """Test parsing file with single header line."""
        content = "pH\tpe\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert isinstance(result, dict)
        finally:
            os.unlink(path)

    def test_parse_ph_value(self):
        """Test parsing pH from selected output."""
        content = "pH\tpe\n7.5\t8.0\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "solution_summary" in result
            assert result["solution_summary"]["pH"] == 7.5
        finally:
            os.unlink(path)

    def test_parse_pe_value(self):
        """Test parsing pe from selected output."""
        content = "pH\tpe\n7.5\t-4.5\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "solution_summary" in result
            assert result["solution_summary"]["pe"] == -4.5
        finally:
            os.unlink(path)

    def test_parse_si_underscore_format(self):
        """Test parsing saturation index with underscore format (si_Calcite)."""
        content = "si_Calcite\tsi_Strengite\n-0.5\t0.2\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "saturation_indices" in result
            assert "Calcite" in result["saturation_indices"]
            assert result["saturation_indices"]["Calcite"] == -0.5
            assert result["saturation_indices"]["Strengite"] == 0.2
        finally:
            os.unlink(path)

    def test_parse_si_parentheses_format(self):
        """Test parsing saturation index with parentheses format (si(Calcite))."""
        content = "si(Calcite)\tsi(Strengite)\n-0.3\t0.1\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "saturation_indices" in result
            assert "Calcite" in result["saturation_indices"]
            assert result["saturation_indices"]["Calcite"] == -0.3
        finally:
            os.unlink(path)

    def test_parse_tot_underscore_format(self):
        """Test parsing element total with underscore format (tot_P)."""
        content = "tot_P\ttot_Fe\n1.5e-05\t2.3e-07\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "element_totals_molality" in result
            assert "P" in result["element_totals_molality"]
            assert abs(result["element_totals_molality"]["P"] - 1.5e-05) < 1e-10
        finally:
            os.unlink(path)

    def test_parse_tot_parentheses_format(self):
        """Test parsing element total with parentheses format (tot(P))."""
        content = "tot(P)\ttot(Fe)\n1.2e-05\t3.1e-07\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "element_totals_molality" in result
            assert "P" in result["element_totals_molality"]
            assert abs(result["element_totals_molality"]["P"] - 1.2e-05) < 1e-10
        finally:
            os.unlink(path)

    def test_parse_quoted_headers(self):
        """Test parsing headers with surrounding quotes."""
        content = '"tot_P"\t"tot_Fe"\n1.5e-05\t2.3e-07\n'
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "element_totals_molality" in result
            assert "P" in result["element_totals_molality"]
        finally:
            os.unlink(path)

    def test_parse_bare_element_header(self):
        """Test parsing bare element name as header (P, Fe, Ca)."""
        content = "P\tFe\tCa\n1.5e-05\t2.3e-07\t1.0e-03\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "element_totals_molality" in result
            assert "P" in result["element_totals_molality"]
            assert "Fe" in result["element_totals_molality"]
            assert "Ca" in result["element_totals_molality"]
        finally:
            os.unlink(path)

    def test_parse_element_with_valence_header(self):
        """Test parsing element with valence notation as header (P(5), Fe(2))."""
        content = "P(5)\tFe(2)\n1.5e-05\t2.3e-07\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "element_totals_molality" in result
            # Should be normalized to base element
            assert "P" in result["element_totals_molality"]
            assert "Fe" in result["element_totals_molality"]
        finally:
            os.unlink(path)

    def test_parse_molality_underscore_format(self):
        """Test parsing species molality with underscore format (m_Ca+2)."""
        content = "m_Ca+2\tm_PO4-3\n1.0e-03\t5.0e-06\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "species_molalities" in result
            assert "Ca+2" in result["species_molalities"]
        finally:
            os.unlink(path)

    def test_parse_equi_phase_format(self):
        """Test parsing equilibrium phase moles (equi_Strengite)."""
        content = "equi_Strengite\tequi_Ferrihydrite\n0.0001\t0.0005\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "equilibrium_phase_moles" in result
            assert "Strengite" in result["equilibrium_phase_moles"]
            assert result["equilibrium_phase_moles"]["Strengite"] == 0.0001
        finally:
            os.unlink(path)

    def test_parse_surf_format(self):
        """Test parsing surface-adsorbed moles (surf_P_Hfo)."""
        content = "surf_P_Hfo\tsurf_Fe_Hfo\n0.00005\t0.00001\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "surface_adsorbed_moles" in result
            assert "P_Hfo" in result["surface_adsorbed_moles"]
        finally:
            os.unlink(path)

    def test_parse_scientific_notation(self):
        """Test parsing values in scientific notation."""
        content = "P\tFe\n1.23e-05\t4.56e-10\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert abs(result["element_totals_molality"]["P"] - 1.23e-05) < 1e-12
            assert abs(result["element_totals_molality"]["Fe"] - 4.56e-10) < 1e-17
        finally:
            os.unlink(path)

    def test_parse_missing_value_dash(self):
        """Test parsing handles dash (-) as missing value."""
        content = "P\tFe\n1.5e-05\t-\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "P" in result["element_totals_molality"]
            assert "Fe" not in result["element_totals_molality"]  # Dash skipped
        finally:
            os.unlink(path)

    def test_parse_empty_value(self):
        """Test parsing handles empty values."""
        content = "P\tFe\n1.5e-05\t\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "P" in result["element_totals_molality"]
            # Empty value should be skipped
        finally:
            os.unlink(path)

    def test_parse_multiple_data_rows_uses_last(self):
        """Test that parser uses the last data row (final state)."""
        content = "P\tFe\n1.0e-05\t1.0e-07\n2.0e-05\t2.0e-07\n3.0e-05\t3.0e-07\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            # Should use last row values
            assert abs(result["element_totals_molality"]["P"] - 3.0e-05) < 1e-12
            assert abs(result["element_totals_molality"]["Fe"] - 3.0e-07) < 1e-14
        finally:
            os.unlink(path)

    def test_parse_mismatched_columns(self):
        """Test parsing handles more headers than values gracefully."""
        content = "P\tFe\tCa\tMg\n1.5e-05\t2.3e-07\n"  # Only 2 values, 4 headers
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            # Should parse what's available
            assert "P" in result["element_totals_molality"]
            assert "Fe" in result["element_totals_molality"]
            # Ca and Mg should not be present (no values)
        finally:
            os.unlink(path)

    def test_parse_temperature_header(self):
        """Test parsing temperature header variations."""
        content = "temp\tpH\n25.0\t7.0\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "solution_summary" in result
            assert result["solution_summary"].get("temperature_celsius") == 25.0
        finally:
            os.unlink(path)

    def test_parse_ionic_strength_header(self):
        """Test parsing ionic strength header (mu)."""
        content = "mu\tpH\n0.005\t7.0\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "solution_summary" in result
            assert result["solution_summary"].get("ionic_strength_molal") == 0.005
        finally:
            os.unlink(path)

    def test_parse_alkalinity_header(self):
        """Test parsing alkalinity header (alk)."""
        content = "alk\tpH\n0.002\t7.0\n"
        path = self._create_temp_file(content)
        try:
            result = _parse_selected_output(path)
            assert "solution_summary" in result
            assert result["solution_summary"].get("alkalinity_eq_L") == 0.002
        finally:
            os.unlink(path)


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
