"""
Common schemas for water chemistry calculations.
"""

import re
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, validator, root_validator

# --- Common Base Models ---


class WaterAnalysisInput(BaseModel):
    """Represents the input water composition."""

    analysis: Dict[str, Union[float, str, Dict]] = Field(
        ...,
        description="Dictionary of element/species concentrations. Keys are element/species names (e.g., 'Ca', 'Mg', 'Alkalinity'). Values can be numbers (concentration), strings ('Alkalinity as CaCO3 120'), or dicts for complex definitions.",
        examples=[{"Ca": 50, "Mg": 10, "Alkalinity": "as CaCO3 120", "S(6)": 96}],
    )
    ph: Optional[float] = Field(None, description="Initial pH (required if not charge balancing).")
    pe: Optional[float] = Field(4.0, description="Initial pe (electron activity). Default 4.")
    temperature_celsius: Optional[float] = Field(25.0, description="Temperature in Celsius. Default 25.")
    pressure_atm: Optional[float] = Field(1.0, description="Pressure in atmospheres. Default 1.")
    units: Optional[str] = Field(
        "mg/L", description="Units for concentration values (e.g., mg/L, mmol/L, ppm). Default mg/L."
    )
    charge_balance: Optional[str] = Field(
        None,
        description="Element/species to adjust for charge balance (e.g., 'Na' or 'Cl'). pH is ignored if this is set.",
    )
    density: Optional[float] = Field(None, description="Solution density in kg/L (or g/cm3).")
    redox: Optional[str] = Field(
        None, description="Redox couple to define pe (e.g., 'O(-2)/O(0)' or 'Fe(2)/Fe(3)'. Overrides pe if set."
    )

    # Validate analysis components
    @validator("analysis")
    def validate_analysis(cls, v):
        """Validate and standardize water analysis components."""
        if not v:
            raise ValueError("Analysis must contain at least one component")

        # Create standardized copy
        standardized = {}

        # Common element name mapping to handle variations
        element_map = {
            # Main cations
            "calcium": "Ca",
            "ca": "Ca",
            "ca+2": "Ca",
            "ca(2+)": "Ca",
            "ca2+": "Ca",
            "magnesium": "Mg",
            "mg": "Mg",
            "mg+2": "Mg",
            "mg(2+)": "Mg",
            "mg2+": "Mg",
            "sodium": "Na",
            "na": "Na",
            "na+": "Na",
            "na(+)": "Na",
            "na1+": "Na",
            "potassium": "K",
            "k": "K",
            "k+": "K",
            "k(+)": "K",
            "k1+": "K",
            # Main anions
            "chloride": "Cl",
            "cl": "Cl",
            "cl-": "Cl",
            "cl(-)": "Cl",
            "cl1-": "Cl",
            "sulfate": "S(6)",
            "so4": "S(6)",
            "so4-2": "S(6)",
            "so4(2-)": "S(6)",
            "so42-": "S(6)",
            "s(6)": "S(6)",
            "sulfur(6)": "S(6)",
            "bicarbonate": "Alkalinity",
            "hco3": "Alkalinity",
            "hco3-": "Alkalinity",
            "hco3(-)": "Alkalinity",
            "hco31-": "Alkalinity",
            "carbonate": "Alkalinity",
            "co3": "Alkalinity",
            "co3-2": "Alkalinity",
            "co3(2-)": "Alkalinity",
            "co32-": "Alkalinity",
            "alk": "Alkalinity",
            "alkalinity": "Alkalinity",
            # Other common elements
            "iron": "Fe",
            "fe": "Fe",
            "fe+2": "Fe(2)",
            "fe(2+)": "Fe(2)",
            "fe2+": "Fe(2)",
            "fe(ii)": "Fe(2)",
            "fe+3": "Fe(3)",
            "fe(3+)": "Fe(3)",
            "fe3+": "Fe(3)",
            "fe(iii)": "Fe(3)",
            "manganese": "Mn",
            "mn": "Mn",
            "aluminum": "Al",
            "al": "Al",
            "al+3": "Al",
            "al(3+)": "Al",
            "al3+": "Al",
            "zinc": "Zn",
            "zn": "Zn",
            "copper": "Cu",
            "cu": "Cu",
            "silicon": "Si",
            "si": "Si",
            "silica": "Si",
            "sio2": "Si",
            "phosphate": "P",
            "po4": "P",
            "p": "P",
            "p(5)": "P",
            "phosphorus": "P",
            "nitrate": "N(5)",
            "no3": "N(5)",
            "no3-": "N(5)",
            "n(5)": "N(5)",
            "nitrite": "N(3)",
            "no2": "N(3)",
            "no2-": "N(3)",
            "n(3)": "N(3)",
            "ammonium": "N(-3)",
            "nh4": "N(-3)",
            "nh4+": "N(-3)",
            "n(-3)": "N(-3)",
            "fluoride": "F",
            "f": "F",
            "f-": "F",
            "bromide": "Br",
            "br": "Br",
            "br-": "Br",
            "iodide": "I",
            "i": "I",
            "i-": "I",
            "barium": "Ba",
            "ba": "Ba",
            "strontium": "Sr",
            "sr": "Sr",
            "lithium": "Li",
            "li": "Li",
            "boron": "B",
            "b": "B",
            # Alkalinity special formats
            "alk as caco3": "Alkalinity",
            "alkalinity as caco3": "Alkalinity",
        }

        # Process each analysis component
        for key, value in v.items():
            # Standardize element/component name
            key_lower = key.lower().strip()

            # Handle different formats of input
            if isinstance(value, (int, float)):
                # Simple numeric value
                std_key = element_map.get(key_lower, key)
                standardized[std_key] = value

            elif isinstance(value, str):
                # String value like "as CaCO3 120" or similar
                std_key = element_map.get(key_lower, key)

                # Special handling for alkalinity
                if "alkalinity" in key_lower or key_lower == "alk":
                    # Check if value is numeric or a complex description
                    try:
                        value_float = float(value)
                        # It's a simple number
                        standardized[std_key] = f"as CaCO3 {value_float}"
                    except ValueError:
                        # It's a complex value like "as CaCO3 120"
                        if "as" not in value.lower():
                            # Add the "as CaCO3" if not present
                            try:
                                value_float = float(value)
                                standardized[std_key] = f"as CaCO3 {value_float}"
                            except ValueError:
                                # Not a simple number, keep as is
                                standardized[std_key] = value
                        else:
                            standardized[std_key] = value
                else:
                    # Other string values, keep as is
                    standardized[std_key] = value

            elif isinstance(value, dict):
                # Dictionary format with value and possibly other attributes
                std_key = element_map.get(key_lower, key)
                standardized[std_key] = value

            else:
                # Unexpected type, keep as is but warn
                logger.warning(f"Unexpected value type for analysis component {key}: {type(value)}")
                standardized[key] = value

        return standardized

    # Validate pH
    @validator("ph")
    def validate_ph(cls, v):
        """Validate pH is within reasonable range."""
        if v is not None:
            if v < 0 or v > 14:
                raise ValueError(f"pH value {v} is outside the reasonable range of 0-14")
        return v

    # Validate pe
    @validator("pe")
    def validate_pe(cls, v):
        """Validate pe is within reasonable range."""
        if v is not None:
            if v < -25 or v > 25:
                raise ValueError(f"pe value {v} is outside the reasonable range of -25 to +25")
        return v

    # Validate temperature
    @validator("temperature_celsius")
    def validate_temperature(cls, v):
        """Validate temperature is within reasonable range."""
        if v is not None:
            if v < -273.15:  # Absolute zero
                raise ValueError("Temperature cannot be below absolute zero (-273.15°C)")
            if v > 1000:  # Arbitrarily high but reasonable limit
                raise ValueError("Temperature is unreasonably high (>1000°C)")
        return v

    # Validate pressure
    @validator("pressure_atm")
    def validate_pressure(cls, v):
        """Validate pressure is positive."""
        if v is not None and v <= 0:
            raise ValueError("Pressure must be positive")
        return v

    # Validate units
    @validator("units")
    def validate_units(cls, v):
        """Validate and standardize concentration units."""
        if v is None:
            return "mg/L"

        v_lower = v.lower().strip()

        # Map common units to PHREEQC accepted units
        unit_map = {
            "mg/l": "mg/L",
            "mg/kg": "mg/kgw",
            "mg/kgw": "mg/kgw",
            "mg/kg h2o": "mg/kgw",
            "mg/kg water": "mg/kgw",
            "ppm": "mg/L",
            "mmol/l": "mmol/L",
            "mmol/kg": "mmol/kgw",
            "mmol/kgw": "mmol/kgw",
            "mol/l": "mol/L",
            "mol/kg": "mol/kgw",
            "mol/kgw": "mol/kgw",
            "mg/l as caco3": "mg/L as CaCO3",
            "ppm as caco3": "mg/L as CaCO3",
            "meq/l": "eq/L",
            "eq/l": "eq/L",
            "ug/l": "µg/L",
            "ppb": "µg/L",
        }

        # Check if the unit is in our mapping
        if v_lower in unit_map:
            return unit_map[v_lower]

        # Return as is if not in mapping
        return v

    @validator("ph", pre=True, always=True)
    def normalize_ph_input(cls, v, values):
        """Accept both 'pH' and 'ph' as input field names"""
        # This is a pre-validator, so we can't access other fields directly
        # Instead, this will be called before field assignment
        return v

    @root_validator(pre=True, skip_on_failure=True)
    def handle_ph_case(cls, values):
        """Handle both 'pH' and 'ph' input field names"""
        if "pH" in values and "ph" not in values:
            values["ph"] = values.pop("pH")
        return values

    @root_validator(pre=True, skip_on_failure=True)
    def handle_temperature_aliases(cls, values):
        """Accept 'temperature' or 'temp' as aliases for 'temperature_celsius'."""
        # Prefer explicit temperature_celsius if provided
        if "temperature_celsius" not in values:
            # Map common aliases
            for alias in ("temperature", "temp", "Temperature", "Temp"):
                if alias in values:
                    values["temperature_celsius"] = values.pop(alias)
                    break
        return values


class SolutionOutput(BaseModel):
    """Represents the calculated state of a solution."""

    solution_summary: Optional[Dict[str, Any]] = Field(
        None, description="Summary properties like pH, pe, alkalinity, TDS, density."
    )
    saturation_indices: Optional[Dict[str, float]] = Field(
        None, description="Calculated saturation indices for relevant minerals."
    )
    phases: Optional[List[Dict[str, Any]]] = Field(
        None, description="Information on phases at equilibrium (precipitated/dissolved amounts)."
    )
    element_totals_molality: Optional[Dict[str, float]] = Field(None, description="Total molality of elements.")
    species_molality: Optional[Dict[str, float]] = Field(None, description="Molality of individual aqueous species.")
    gas_phase_composition: Optional[Dict[str, Any]] = Field(
        None, description="Composition of the equilibrium gas phase (partial pressures, moles)."
    )
    surface_composition: Optional[List[Dict[str, Any]]] = Field(None, description="Composition of surface sites.")
    exchange_composition: Optional[List[Dict[str, Any]]] = Field(None, description="Composition of exchange sites.")
    kinetic_reactants: Optional[List[Dict[str, Any]]] = Field(None, description="State of kinetic reactants.")
    precipitated_phases: Optional[Dict[str, float]] = Field(
        None, description="Minerals that precipitated and their amounts in moles."
    )
    precipitation_occurred: Optional[bool] = Field(None, description="Whether any precipitation occurred.")
    precipitation_estimated: Optional[bool] = Field(
        None, description="Whether precipitation amounts are estimated rather than calculated."
    )
    total_precipitate_g_L: Optional[float] = Field(None, description="Total mass of precipitated solids in g/L.")
    precipitate_details: Optional[Dict[str, Dict[str, float]]] = Field(
        None, description="Detailed information about each precipitate including moles, mass, and MW."
    )
    error: Optional[str] = Field(None, description="Error message if calculation failed.")


# --- Tool-specific Input/Output Models ---


# Tool 1: calculate_solution_speciation
class CalculateSolutionSpeciationInput(WaterAnalysisInput):
    database: Optional[str] = Field(None, description="Path or name of the PHREEQC database file to use.")


class CalculateSolutionSpeciationOutput(SolutionOutput):
    pass


# Tool 2: simulate_chemical_addition
class ReactantInput(BaseModel):
    formula: str = Field(..., description="Chemical formula of the reactant (e.g., 'NaOH', 'FeCl3').")
    amount: float = Field(..., description="Amount of reactant to add.")
    units: Optional[str] = Field(
        "mmol",
        description="Units for the amount (e.g., 'mmol', 'g', 'mg'). Assumed per liter unless specified otherwise.",
    )

    # Custom validation to sanitize formula and handle formula format issues
    @validator("formula")
    def sanitize_formula(cls, v):
        """Sanitize chemical formula to prevent PHREEQC parsing errors."""
        # Remove any unexpected characters
        sanitized = re.sub(r"[^A-Za-z0-9\(\)\+\-\.:]", "", v)

        # Add a workaround for common formula misspellings or format issues
        formula_map = {
            # Map common misspellings/variations to correct PHREEQC formulas
            "CO2(g)": "CO2(g)",
            "CO2_g": "CO2(g)",
            "CO2": "CO2",
            "NaOH(s)": "NaOH",
            "CaOH2": "Ca(OH)2",
            "Ca(OH)2(s)": "Ca(OH)2",
            "CaCO3(s)": "CaCO3",
            "H+": "H+",
            "OH-": "OH-",
            "HCl": "HCl",
            "H2SO4": "H2SO4",
            "NaHCO3": "NaHCO3",
            "NaHCO3(s)": "NaHCO3",
        }

        # Check if the sanitized formula is in our mapping
        if sanitized in formula_map:
            return formula_map[sanitized]

        # Return the sanitized formula if no mapping is found
        return sanitized

    # Validate amount is positive
    @validator("amount")
    def positive_amount(cls, v):
        """Ensure amount is positive."""
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v

    # Validate and standardize units
    @validator("units")
    def standardize_units(cls, v):
        """Standardize units to those that PHREEQC recognizes."""
        if v is None:
            return "mmol"

        v = v.lower().strip()
        # Map similar units to PHREEQC accepted units
        unit_map = {
            # Mass units
            "g": "g",
            "gram": "g",
            "grams": "g",
            "mg": "mg",
            "milligram": "mg",
            "milligrams": "mg",
            "ug": "ug",
            "microgram": "ug",
            "micrograms": "ug",
            # Molar units
            "mol": "mol",
            "mole": "mol",
            "moles": "mol",
            "mmol": "mmol",
            "millimol": "mmol",
            "millimole": "mmol",
            "millimoles": "mmol",
            "umol": "umol",
            "micromol": "umol",
            "micromole": "umol",
            "micromoles": "umol",
            # Concentration units (converted to moles by PHREEQC)
            # These are accepted with /L qualifier
            "mg/l": "mg/L",
            "mg/kgw": "mg/kgw",
            "mmol/l": "mmol/L",
            "mmol/kgw": "mmol/kgw",
        }

        # Check if the unit is in our mapping
        if v in unit_map:
            return unit_map[v]

        # Handle common unit patterns with volume/mass denominators
        if "/" in v:
            parts = v.split("/")
            if len(parts) == 2:
                # Handle numerator (e.g., mg, g, mol)
                num = parts[0].strip()
                # Handle denominator (e.g., L, l, kgw)
                denom = parts[1].strip()

                # Standardize numerator
                if num in unit_map:
                    num = unit_map[num]

                # Standardize denominator
                denom_map = {
                    "l": "L",
                    "liter": "L",
                    "liters": "L",
                    "lt": "L",
                    "kg": "kgw",
                    "kgwater": "kgw",
                    "kgw": "kgw",
                    "kg water": "kgw",
                }

                if denom in denom_map:
                    denom = denom_map[denom]

                # Build standardized unit
                return f"{num}/{denom}"

        # If we can't standardize, return the original (PHREEQC will handle errors)
        return v


class PHREEQCKineticParameters(BaseModel):
    """Parameters for PHREEQC-native kinetic modeling of a specific mineral."""

    m0: float = Field(..., description="Initial moles of the mineral.")
    m: Optional[float] = Field(None, description="Current moles (defaults to m0).")
    parms: List[float] = Field(..., description="Parameters for the rate equation (e.g., surface area, exponents).")
    tol: Optional[float] = Field(1e-8, description="Tolerance for kinetic integration.")


class KineticParameters(BaseModel):
    """Parameters for kinetic precipitation modeling using PHREEQC's native RATES."""

    enable_kinetics: bool = Field(False, description="Enable kinetic modeling instead of equilibrium.")
    time_steps: List[float] = Field(None, description="Time points in seconds for kinetic evolution.")
    use_phreeqc_rates: bool = Field(True, description="Use PHREEQC's native RATES database (recommended).")
    minerals_kinetic: Dict[str, PHREEQCKineticParameters] = Field(
        None,
        description="PHREEQC kinetic parameters for each mineral.",
        example={
            "Calcite": {
                "m0": 0.0,  # Starting with no solid
                "parms": [1.67e5, 0.6],  # cm²/mol calcite, exponent for M/M0
                "tol": 1e-8,
            }
        },
    )


# Keep the old schema for backward compatibility
class CustomKineticMineralParameters(BaseModel):
    """Parameters for custom kinetic precipitation/dissolution (deprecated)."""

    rate_constant: float = Field(..., description="Rate constant at 25°C in mol/m²/s.")
    surface_area: float = Field(1.0, description="Initial surface area to volume ratio in m²/L.")
    activation_energy: Optional[float] = Field(
        48000, description="Activation energy in J/mol for temperature correction."
    )
    surface_area_exponent: Optional[float] = Field(
        0.67, description="Exponent for surface area evolution (typically 0.67 for spheres)."
    )
    nucleation_si_threshold: Optional[float] = Field(0.0, description="Minimum SI required for nucleation to begin.")


class SimulateChemicalAdditionInput(BaseModel):
    initial_solution: WaterAnalysisInput = Field(..., description="The starting water composition.")
    reactants: List[ReactantInput] = Field(..., description="List of chemicals to add.")
    allow_precipitation: Optional[bool] = Field(
        True, description="Whether to allow mineral precipitation/dissolution. Default True."
    )
    equilibrium_minerals: Optional[List[str]] = Field(
        None,
        description="List of potential minerals to consider for equilibrium (if allow_precipitation is True). Defaults to full database mineral list for comprehensive precipitation modeling.",
    )
    kinetic_parameters: Optional[KineticParameters] = Field(
        None, description="Optional parameters for kinetic precipitation modeling."
    )
    database: Optional[str] = Field(None, description="Path or name of the PHREEQC database file to use.")
    temperature_celsius: Optional[float] = Field(
        None, description="Override temperature for reaction step (defaults to initial_solution temp)."
    )
    pressure_atm: Optional[float] = Field(
        None, description="Override pressure for reaction step (defaults to initial_solution press)."
    )


class KineticPrecipitationProfile(BaseModel):
    """Time-series data for kinetic precipitation of a mineral."""

    mineral: str = Field(..., description="Mineral name.")
    time_seconds: List[float] = Field(..., description="Time points in seconds.")
    amount_precipitated_mol: List[float] = Field(..., description="Cumulative moles precipitated at each time point.")
    saturation_index: List[float] = Field(..., description="Saturation index at each time point.")
    precipitation_rate_mol_s: List[float] = Field(
        ..., description="Instantaneous precipitation rate at each time point."
    )


class SimulateChemicalAdditionOutput(SolutionOutput):
    kinetic_profiles: Optional[List[KineticPrecipitationProfile]] = Field(
        None, description="Time-series precipitation data when kinetic modeling is used."
    )
    kinetic_modeling_used: Optional[bool] = Field(
        False, description="Whether kinetic modeling was used instead of equilibrium."
    )


# Tool 3: calculate_dosing_requirement
class TargetConditionInput(BaseModel):
    parameter: str = Field(
        ..., description="Parameter to target (e.g., 'pH', 'SI', 'Alkalinity', 'pe', 'Concentration')."
    )
    value: float = Field(..., description="Target value for the parameter.")
    mineral: Optional[str] = Field(None, description="Mineral name required if parameter is 'SI'.")
    element_or_species: Optional[str] = Field(
        None, description="Element or species name if parameter is 'Concentration'."
    )
    units: Optional[str] = Field(
        None,
        description="Units for target value if parameter is 'Alkalinity' or 'Concentration' (e.g., 'mmol/kgw', 'mg/L').",
    )


class ReagentInput(BaseModel):
    formula: str = Field(..., description="Chemical formula of the reagent (e.g., 'Ca(OH)2', 'HCl', 'Na2CO3').")


class CalculateDosingRequirementInput(BaseModel):
    initial_solution: WaterAnalysisInput = Field(..., description="The starting water composition.")
    target_condition: TargetConditionInput = Field(..., description="The desired final state.")
    reagent: ReagentInput = Field(..., description="The chemical to dose.")
    max_iterations: Optional[int] = Field(30, description="Maximum iterations for the search. Default 30.")
    tolerance: Optional[float] = Field(
        0.05, description="Acceptable tolerance for reaching the target (e.g., pH units, SI units). Default 0.05."
    )
    initial_guess_mmol: Optional[float] = Field(
        1.0, description="Initial guess for the dose in mmol (per liter). Default 1.0."
    )
    database: Optional[str] = Field(None, description="Path or name of the PHREEQC database file to use.")
    allow_precipitation: Optional[bool] = Field(
        True, description="Whether to allow mineral precipitation/dissolution during iteration. Default True."
    )
    equilibrium_minerals: Optional[List[str]] = Field(
        None,
        description="List of potential minerals to allow for equilibrium during iteration. Defaults to full database mineral list for comprehensive precipitation modeling.",
    )


class CalculateDosingRequirementOutput(BaseModel):
    required_dose_mmol_per_L: Optional[float] = Field(
        None, description="Calculated dose of the reagent in mmol per liter of initial solution."
    )
    final_state: SolutionOutput = Field(
        ..., description="The calculated solution state after adding the required dose."
    )
    iterations_taken: Optional[int] = Field(None, description="Number of iterations performed.")
    convergence_status: str = Field(
        ..., description="Status message (e.g., 'Converged', 'Max iterations reached', 'Error')."
    )
    error: Optional[str] = Field(None, description="Error message if calculation failed.")


# Tool 4: simulate_solution_mixing
class SolutionToMix(BaseModel):
    solution: WaterAnalysisInput = Field(..., description="Definition of the solution to mix.")
    # Accept either 'fraction' or 'volume_L', plus backward-compatible aliases
    fraction: Optional[float] = Field(None, description="Mixing fraction (0-1). Will be normalized across solutions.")
    volume_L: Optional[float] = Field(None, description="Mixing volume in liters.")
    # Backward-compatibility with older schema and external clients
    fraction_or_volume: Optional[float] = Field(None, description="Deprecated: use 'fraction' or 'volume_L'.")
    volume_fraction: Optional[float] = Field(
        None, description="Alias for 'fraction' for compatibility with some clients."
    )

    @root_validator(pre=True, skip_on_failure=True)
    def normalize_fraction_volume_fields(cls, values):
        """Normalize input to prefer explicit 'fraction' or 'volume_L'."""
        # If volume_fraction provided, map to fraction
        if "volume_fraction" in values and "fraction" not in values:
            values["fraction"] = values.get("volume_fraction")

        # If only fraction_or_volume is provided, keep it as either fraction or volume
        if "fraction_or_volume" in values and "fraction" not in values and "volume_L" not in values:
            values["fraction"] = values.get("fraction_or_volume")

        return values

    @root_validator(skip_on_failure=True)
    def validate_fraction_or_volume(cls, values):
        """Ensure exactly one of fraction or volume_L is provided; allow explicit zero for fraction."""
        frac = values.get("fraction")
        vol = values.get("volume_L")

        # If both provided, that's ambiguous
        if frac is not None and vol is not None:
            raise ValueError("Provide either 'fraction' or 'volume_L', not both")

        # If neither provided, allow later defaulting (e.g., equal fractions) by leaving as None
        # This will be handled by the mixing tool logic.
        return values


class SimulateSolutionMixingInput(BaseModel):
    solutions_to_mix: List[SolutionToMix] = Field(
        ..., min_items=2, description="List of solutions and their mixing fractions/volumes."
    )
    database: Optional[str] = Field(None, description="Path or name of the PHREEQC database file to use.")
    allow_precipitation: Optional[bool] = Field(
        True, description="Whether to allow mineral precipitation/dissolution during mixing. Default True."
    )

    @root_validator(pre=True, skip_on_failure=True)
    def allow_simplified_solutions_schema(cls, values):
        """Support simplified input {'solutions': [{analysis..., volume_fraction/volume_L/...}, ...]}.
        Transforms to structured solutions_to_mix list automatically.
        """
        if "solutions_to_mix" not in values and "solutions" in values:
            simplified = values.get("solutions") or []
            structured = []
            for item in simplified:
                # Split into solution and mixing key
                sol_fields = {}
                mix_kwargs = {}
                # Map temperature aliases here too
                item_copy = dict(item)
                if "temperature" in item_copy and "temperature_celsius" not in item_copy:
                    item_copy["temperature_celsius"] = item_copy.pop("temperature")

                # volume_fraction -> fraction; volume_L kept; fraction also honored
                if "volume_fraction" in item_copy:
                    mix_kwargs["fraction"] = item_copy.pop("volume_fraction")
                elif "fraction" in item_copy:
                    mix_kwargs["fraction"] = item_copy.pop("fraction")
                if "volume_L" in item_copy:
                    mix_kwargs["volume_L"] = item_copy.pop("volume_L")

                # Remaining fields become WaterAnalysisInput
                sol_fields = item_copy
                structured.append({"solution": sol_fields, **mix_kwargs})

            if structured:
                values["solutions_to_mix"] = structured
        return values


class SimulateSolutionMixingOutput(SolutionOutput):
    pass


# Tool 5: predict_scaling_potential
class PredictScalingPotentialInput(WaterAnalysisInput):
    force_equilibrium_minerals: Optional[List[str]] = Field(
        None, description="Optional list of mineral names to force equilibrium with (calculates mass precipitated)."
    )
    database: Optional[str] = Field(None, description="Path or name of the PHREEQC database file to use.")


class PredictScalingPotentialOutput(SolutionOutput):
    pass


# Tool 6: simulate_gas_phase_interaction
class GasPhaseDefinition(BaseModel):
    type: str = Field(
        "fixed_pressure",
        description="Type of gas phase ('fixed_pressure' or 'fixed_volume'). Default 'fixed_pressure'.",
    )
    initial_components: Dict[str, float] = Field(
        ...,
        description="Dictionary of gas components and their initial partial pressures (atm) for fixed_pressure, or moles for fixed_volume.",
    )
    fixed_pressure_atm: Optional[float] = Field(1.0, description="Total pressure for fixed_pressure type. Default 1.0.")
    initial_volume_liters: Optional[float] = Field(
        1.0, description="Initial volume for fixed_volume type or initial bubble size for fixed_pressure. Default 1.0."
    )
    temperature_celsius: Optional[float] = Field(25.0, description="Initial temperature of gas phase. Default 25.0.")

    # Validate gas phase type
    @validator("type")
    def validate_gas_phase_type(cls, v):
        """Validate and standardize the gas phase type."""
        v_lower = v.lower().strip()

        if v_lower in ["fixed_pressure", "fixed-pressure", "pressure", "constant_pressure", "constant pressure"]:
            return "fixed_pressure"
        elif v_lower in ["fixed_volume", "fixed-volume", "volume", "constant_volume", "constant volume"]:
            return "fixed_volume"
        else:
            raise ValueError("Gas phase type must be 'fixed_pressure' or 'fixed_volume'")

    # Validate gas components
    @validator("initial_components")
    def validate_gas_components(cls, v):
        """Validate and standardize gas component formulas."""
        if not v:
            raise ValueError("At least one gas component must be specified")

        standardized_components = {}

        # Mapping for common gas formulas and variations
        gas_formula_map = {
            "co2": "CO2(g)",
            "co2(g)": "CO2(g)",
            "co2_g": "CO2(g)",
            "carbon dioxide": "CO2(g)",
            "o2": "O2(g)",
            "o2(g)": "O2(g)",
            "o2_g": "O2(g)",
            "oxygen": "O2(g)",
            "n2": "N2(g)",
            "n2(g)": "N2(g)",
            "n2_g": "N2(g)",
            "nitrogen": "N2(g)",
            "ch4": "CH4(g)",
            "ch4(g)": "CH4(g)",
            "ch4_g": "CH4(g)",
            "methane": "CH4(g)",
            "h2": "H2(g)",
            "h2(g)": "H2(g)",
            "h2_g": "H2(g)",
            "hydrogen": "H2(g)",
            "h2s": "H2S(g)",
            "h2s(g)": "H2S(g)",
            "h2s_g": "H2S(g)",
            "hydrogen sulfide": "H2S(g)",
            "nh3": "NH3(g)",
            "nh3(g)": "NH3(g)",
            "nh3_g": "NH3(g)",
            "ammonia": "NH3(g)",
            "so2": "SO2(g)",
            "so2(g)": "SO2(g)",
            "so2_g": "SO2(g)",
            "sulfur dioxide": "SO2(g)",
            "no2": "NO2(g)",
            "no2(g)": "NO2(g)",
            "no2_g": "NO2(g)",
            "nitrogen dioxide": "NO2(g)",
            "co": "CO(g)",
            "co(g)": "CO(g)",
            "co_g": "CO(g)",
            "carbon monoxide": "CO(g)",
        }

        for gas, value in v.items():
            # Check if the value is valid
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"Gas component value for {gas} must be a positive number")

            # Check and standardize gas formula
            gas_lower = gas.lower().strip()
            if gas_lower in gas_formula_map:
                # Use standardized formula
                formula = gas_formula_map[gas_lower]
                standardized_components[formula] = value
            elif "(g)" in gas or "_g" in gas.lower():
                # It has gas designation, but not in our map; keep as is but ensure (g) format
                # Convert _g to (g) if needed
                if "_g" in gas.lower():
                    clean_formula = gas.lower().replace("_g", "")
                    formula = f"{clean_formula.upper()}(g)"
                else:
                    formula = gas  # Keep as is, assuming proper (g) format
                standardized_components[formula] = value
            else:
                # No gas designation, add (g)
                formula = f"{gas}(g)"
                standardized_components[formula] = value

        return standardized_components

    # Validate pressure
    @validator("fixed_pressure_atm")
    def validate_pressure(cls, v):
        """Validate pressure is positive."""
        if v is not None and v <= 0:
            raise ValueError("Pressure must be positive")
        return v

    # Validate volume
    @validator("initial_volume_liters")
    def validate_volume(cls, v):
        """Validate volume is positive."""
        if v is not None and v <= 0:
            raise ValueError("Volume must be positive")
        return v

    # Validate temperature
    @validator("temperature_celsius")
    def validate_temperature(cls, v):
        """Validate temperature is within a reasonable range."""
        if v is not None:
            if v < -273.15:  # Absolute zero in Celsius
                raise ValueError("Temperature cannot be below absolute zero (-273.15°C)")
            if v > 1000:  # Arbitrarily high but reasonable limit
                raise ValueError("Temperature is unreasonably high (>1000°C)")
        return v


class SimulateGasPhaseInteractionInput(BaseModel):
    initial_solution: WaterAnalysisInput = Field(..., description="The starting water composition.")
    gas_phase: GasPhaseDefinition = Field(..., description="Definition of the gas phase to equilibrate with.")
    database: Optional[str] = Field(None, description="Path or name of the PHREEQC database file to use.")


class SimulateGasPhaseInteractionOutput(SolutionOutput):
    pass


# Tool 7: simulate_redox_adjustment
class TargetRedoxCondition(BaseModel):
    parameter: str = Field(
        ..., description="How to define the target redox state ('pe', 'Eh_mV', 'equilibrate_with_couple')."
    )
    value: Optional[float] = Field(None, description="Target value for 'pe' or 'Eh_mV'.")
    couple_name: Optional[str] = Field(
        None,
        description="Name of the redox couple (e.g., 'O2(g)', 'Fe(+3)/Fe(+2)') if parameter is 'equilibrate_with_couple'.",
    )
    couple_logK_or_pressure: Optional[float] = Field(
        None, description="LogK or partial pressure (atm) for the equilibrium couple."
    )


class SimulateRedoxAdjustmentInput(BaseModel):
    initial_solution: WaterAnalysisInput = Field(..., description="The starting water composition.")
    target_redox: TargetRedoxCondition = Field(..., description="The desired final redox state.")
    database: Optional[str] = Field(None, description="Path or name of the PHREEQC database file to use.")


class SimulateRedoxAdjustmentOutput(SolutionOutput):
    pass


# Tool 8: simulate_surface_interaction
class SurfaceDefinition(BaseModel):
    # Simplistic definition - PHREEQC SURFACE block is complex
    surface_block_string: Optional[str] = Field(None, description="Raw PHREEQC SURFACE block definition string.")
    sites_block_string: Optional[str] = Field(
        None, description="Raw PHREEQC SURFACE_MASTER_SPECIES and SURFACE_SPECIES blocks if not in main DB."
    )
    sites_info: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Structured list of sites (e.g., [{'name': 'Hfo_s', 'moles': 0.1, 'specific_area_m2_g': 600, 'mass_g': 0.5}]).",
    )
    equilibrate_with_solution_number: int = Field(1, description="Solution number to equilibrate with.")


class SimulateSurfaceInteractionInput(BaseModel):
    initial_solution: WaterAnalysisInput = Field(..., description="The starting water composition.")
    surface_definition: SurfaceDefinition = Field(..., description="Definition of the surface sites and their amounts.")
    database: Optional[str] = Field(
        None, description="Path or name of the PHREEQC database file to use (must contain surface species definitions)."
    )


class SimulateSurfaceInteractionOutput(SolutionOutput):
    pass


# Tool 9: simulate_kinetic_reaction
class KineticReactionParameter(BaseModel):
    """Parameters for kinetic reaction rates."""

    name: str = Field(..., description="Parameter name (e.g., 'm', 'm0', 'parm(1)').")
    value: Union[float, str] = Field(..., description="Parameter value.")


class KineticReaction(BaseModel):
    """Definition of a single kinetic reaction."""

    name: str = Field(
        ..., description="Reaction name that matches a RATES block (e.g., 'Calcite', 'Pyrite_oxidation')."
    )
    formula: Optional[Union[str, Dict[str, Union[float, int]]]] = Field(
        None,
        description="Optional chemical formula (e.g., 'CaCO3', 'FeS2') or formula dictionary (e.g., {'N(5)': -1, 'N(3)': 1}).",
    )
    parameters: Optional[Dict[str, Union[float, str]]] = Field(
        None, description="Rate parameters (e.g., {m: 1, m0: 1, parm(1): 0.1})."
    )
    custom_kinetics_line: Optional[str] = Field(None, description="Optional custom line for the KINETICS block.")


class KineticRateDefinition(BaseModel):
    """Definition of a rate law."""

    name: str = Field(..., description="Rate name (e.g., 'Calcite', 'Pyrite_oxidation').")
    rate_law: str = Field(..., description="BASIC rate law code.")


class KineticReactionDefinition(BaseModel):
    """Definition of kinetic reactions and rates."""

    # Support both raw strings and structured definitions
    rates_block_string: Optional[str] = Field(None, description="Raw PHREEQC RATES block definition string.")
    kinetics_block_string: Optional[str] = Field(None, description="Raw PHREEQC KINETICS block definition string.")

    # Alternative structured input for auto-generation
    reactions: Optional[List[KineticReaction]] = Field(None, description="List of kinetic reactions with parameters.")
    rates: Optional[List[KineticRateDefinition]] = Field(None, description="List of rate laws.")


class TimeStepDefinition(BaseModel):
    """Definition of time steps for kinetic simulation."""

    # Support both raw values and structured time step definitions
    time_values: Optional[List[float]] = Field(None, description="List of time points for simulation steps.")
    units: Optional[str] = Field("seconds", description="Units for time values (e.g., 'seconds', 'hours', 'days').")

    # Alternative simplified inputs
    count: Optional[int] = Field(None, description="Number of equal time steps.")
    duration: Optional[float] = Field(None, description="Total duration of simulation.")
    duration_units: Optional[str] = Field("seconds", description="Units for duration.")


class SimulateKineticReactionInput(BaseModel):
    initial_solution: WaterAnalysisInput = Field(..., description="The starting water composition.")
    kinetic_reactions: KineticReactionDefinition = Field(
        ..., description="Definition of the kinetic reactions and rates."
    )
    time_steps: TimeStepDefinition = Field(..., description="Time steps for the kinetic simulation.")
    allow_precipitation: Optional[bool] = Field(
        True, description="Whether to allow mineral precipitation/dissolution during kinetics. Default True."
    )
    equilibrium_minerals: Optional[List[str]] = Field(
        None,
        description="List of potential minerals to allow for equilibrium during kinetics. Defaults to full database mineral list for comprehensive precipitation modeling.",
    )
    database: Optional[str] = Field(None, description="Path or name of the PHREEQC database file to use.")


class SimulateKineticReactionOutput(BaseModel):
    # Returns results for the *last* time step
    final_state: SolutionOutput = Field(..., description="The calculated solution state at the final time step.")
    error: Optional[str] = Field(None, description="Error message if calculation failed.")


# Tool 10: query_thermodynamic_database
class QueryThermoDatabaseInput(BaseModel):
    query_term: str = Field(
        ...,
        description="Element, species formula, mineral name, or keyword (e.g., 'Ca', 'HCO3-', 'Calcite', 'SOLUTION_SPECIES').",
    )
    query_type: str = Field(..., description="Type of query ('species', 'mineral', 'element_info', 'keyword_block').")
    database: Optional[str] = Field(
        None, description="Path or name of the PHREEQC database file to query. Default to phreeqpython default."
    )


class QueryThermoDatabaseOutput(BaseModel):
    query_term: str = Field(...)
    query_type: str = Field(...)
    database_used: str = Field(...)
    results: Optional[Union[Dict[str, Any], List[str], str]] = Field(
        None,
        description="Found database information (e.g., reaction, log_K, raw block text). Structure depends on query type.",
    )
    error: Optional[str] = Field(None, description="Error message if query failed or term not found.")


# Tool 11: generate_calculation_sheet
class ProjectInfo(BaseModel):
    """Project information for calculation header."""

    project_name: str = Field("Water Treatment Design", description="Name of the project.")
    project_number: Optional[str] = Field(None, description="Project number or code.")
    calculation_subject: Optional[str] = Field(None, description="Subject of the calculation.")
    preparer: Optional[str] = Field("MCP Water Chemistry Tools", description="Person/system preparing the calculation.")
    checker: Optional[str] = Field("-", description="Person checking the calculation.")
    approver: Optional[str] = Field("-", description="Person approving the calculation.")
    prep_date: Optional[str] = Field(None, description="Date prepared (auto-generated if not provided).")
    check_date: Optional[str] = Field("-", description="Date checked.")
    approve_date: Optional[str] = Field("-", description="Date approved.")


class GenerateCalculationSheetInput(BaseModel):
    """Input for generating engineering calculation sheets."""

    calculation_type: str = Field(
        ...,
        description="Type of calculation sheet to generate.",
        examples=[
            "lime_softening",
            "pH_adjustment",
            "phosphate_removal",
            "metal_precipitation",
            "scaling_assessment",
            "kinetic_design",
            "treatment_train",
        ],
    )
    project_info: ProjectInfo = Field(..., description="Project metadata for calculation header.")
    calculation_data: Dict[str, Any] = Field(
        ..., description="All calculation inputs, outputs, and intermediate results from tool executions."
    )
    include_raw_outputs: bool = Field(True, description="Whether to include raw PHREEQC outputs in appendices.")
    output_formats: List[str] = Field(["html", "pdf"], description="Desired output formats for the calculation sheet.")


class GenerateCalculationSheetOutput(BaseModel):
    """Output from calculation sheet generation."""

    success: bool = Field(..., description="Whether generation was successful.")
    message: str = Field(..., description="Status message.")
    files: Optional[Dict[str, str]] = Field(None, description="Paths to generated files by format.")
    calculation_id: Optional[str] = Field(None, description="Unique identifier for this calculation.")
    error: Optional[str] = Field(None, description="Error message if generation failed.")
