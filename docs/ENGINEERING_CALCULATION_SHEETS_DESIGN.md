# Engineering Calculation Sheets Design Document

## Executive Summary

This document outlines the implementation of auditable engineering calculation sheets for the Water Chemistry MCP Server. The solution uses papermill with Jupyter notebook templates to automatically generate professional calculation documents that meet engineering standards for documentation, review, and archival.

## Design Principles

### 1. Auditability Requirements
- Every calculation must show inputs, methods, and outputs
- Intermediate steps must be verifiable by hand calculation
- All assumptions and limitations clearly stated
- References to standards and literature provided
- Version control and revision tracking

### 2. Engineering Standards Compliance
- Follow standard engineering calculation format
- Include project information and metadata
- Prepared/Checked/Approved signature blocks
- Professional formatting and presentation
- Clear equation presentation with units

### 3. Integration Requirements
- Seamless integration with existing MCP tools
- Automatic generation from tool outputs
- No additional user burden
- Support for complex multi-step calculations

## Implementation Architecture

### Tool Structure
```python
@mcp.tool()
async def generate_calculation_sheet(
    calculation_type: str,
    project_info: Dict[str, Any],
    calculation_data: Dict[str, Any],
    include_raw_outputs: bool = True,
    output_format: List[str] = ["html", "pdf"]
) -> Dict[str, Any]:
    """
    Generates professional engineering calculation sheets from MCP tool outputs.
    
    Args:
        calculation_type: Type of calculation (e.g., "lime_softening", "pH_adjustment")
        project_info: Project metadata (name, number, date, engineer, reviewer)
        calculation_data: All inputs, outputs, and intermediate results
        include_raw_outputs: Whether to append raw PHREEQC outputs
        output_format: Desired output formats
        
    Returns:
        Paths to generated calculation sheets and success status
    """
```

### Template Directory Structure
```
water-chemistry-mcp/
├── templates/
│   ├── calculations/
│   │   ├── base_calculation.ipynb          # Base template with common elements
│   │   ├── lime_softening_calc.ipynb       # Lime softening calculations
│   │   ├── pH_adjustment_calc.ipynb        # pH adjustment calculations
│   │   ├── phosphate_removal_calc.ipynb    # Phosphate removal calculations
│   │   ├── metal_precipitation_calc.ipynb  # Heavy metal removal
│   │   ├── scaling_assessment_calc.ipynb   # Scaling potential evaluation
│   │   ├── kinetic_design_calc.ipynb       # Kinetic reactor design
│   │   └── treatment_train_calc.ipynb      # Multi-step treatment
│   └── styles/
│       ├── engineering_calc.css            # Professional styling
│       └── company_logo.png               # Optional branding
```

## Template Components

### 1. Header Section
```markdown
# CALCULATION SHEET

**Project:** {{project_name}}  
**Project No:** {{project_number}}  
**Subject:** {{calculation_subject}}  
**Calculation No:** {{calc_id}}  
**Revision:** {{revision}}  
**Date:** {{date}}  

| Prepared By | Checked By | Approved By |
|------------|------------|-------------|
| {{preparer}} | {{checker}} | {{approver}} |
| {{prep_date}} | {{check_date}} | {{approve_date}} |
```

### 2. Objective and Scope
```markdown
## 1. OBJECTIVE
{{objective_text}}

## 2. SCOPE
This calculation covers:
- {{scope_item_1}}
- {{scope_item_2}}
```

### 3. References and Standards
```markdown
## 3. REFERENCES
1. AWWA Water Quality & Treatment, 6th Edition
2. PHREEQC Version 3.8.6 User Manual
3. {{additional_references}}

## 4. DESIGN CRITERIA
- {{design_criteria}}
```

### 4. Input Parameters
```python
# Structured display of all inputs with units
input_df = pd.DataFrame({
    'Parameter': parameter_names,
    'Value': values,
    'Unit': units,
    'Source': sources
})
display(input_df.style.set_caption("Table 1: Input Parameters"))
```

### 5. Methodology
```markdown
## 5. METHODOLOGY

### 5.1 Governing Equations

**Lime Softening Reactions:**
$$\text{Ca}^{2+} + \text{Ca(OH)}_2 + 2\text{HCO}_3^- \rightarrow 2\text{CaCO}_3 \downarrow + 2\text{H}_2\text{O}$$

$$\text{Mg}^{2+} + \text{Ca(OH)}_2 \rightarrow \text{Mg(OH)}_2 \downarrow + \text{Ca}^{2+}$$

### 5.2 Calculation Approach
1. Determine carbonate and non-carbonate hardness
2. Calculate theoretical lime requirement
3. Apply excess factor for kinetics
4. Model precipitation using PHREEQC
5. Verify residual hardness meets target
```

### 6. Calculations
```python
# Step-by-step calculations with intermediate results

# Step 1: Water Analysis
print("=== Step 1: Initial Water Analysis ===")
ca_mgL = ca_mmolL * 40.08  # Convert mmol/L to mg/L
print(f"Calcium: {ca_mmolL:.2f} mmol/L = {ca_mgL:.1f} mg/L")

# Step 2: Theoretical Lime Requirement
print("\n=== Step 2: Lime Requirement ===")
lime_theory = calculate_theoretical_lime(ca_mmolL, mg_mmolL, alk_mmolL)
print(f"Theoretical: {lime_theory:.2f} mmol/L")
print(f"With 10% excess: {lime_theory*1.1:.2f} mmol/L")

# Include actual PHREEQC calculation
result = await simulate_chemical_addition(calculation_input)
```

### 7. Results Summary
```python
# Professional results presentation
results_df = pd.DataFrame({
    'Parameter': ['pH', 'Total Hardness', 'Calcium', 'Magnesium', 'Sludge'],
    'Initial': [7.2, 250, 200, 50, 0],
    'Final': [10.3, 40, 30, 10, 2.5],
    'Unit': ['-', 'mg/L as CaCO3', 'mg/L', 'mg/L', 'g/L'],
    'Target': ['10-10.5', '<50', '<40', '<10', '-']
})

# Conditional formatting for pass/fail
```

### 8. Validation Checks
```python
# Engineering checks
hardness_removal = (initial_hardness - final_hardness) / initial_hardness * 100
assert hardness_removal > 80, "Insufficient hardness removal"

assert 10.0 <= final_pH <= 10.5, "pH outside target range"
```

### 9. Conclusions and Recommendations
```markdown
## 8. CONCLUSIONS
- Lime dose of {{dose}} mg/L achieves {{removal}}% hardness removal
- Final hardness of {{final}} mg/L meets target of <50 mg/L
- Estimated sludge production: {{sludge}} kg/m³

## 9. RECOMMENDATIONS
- Provide {{contact_time}} minutes contact time
- Consider two-stage addition for optimal removal
- Implement sludge handling for {{sludge_rate}} kg/day
```

### 10. Appendices
```python
# Appendix A: Raw PHREEQC Output
if include_raw_outputs:
    print("APPENDIX A: PHREEQC Simulation Output")
    print("="*60)
    print(phreeqc_raw_output)
```

## Styling and Output

### CSS Styling (engineering_calc.css)
```css
/* Professional engineering calculation styling */
body {
    font-family: 'Arial', sans-serif;
    line-height: 1.6;
    color: #333;
    max-width: 8.5in;
    margin: 0 auto;
    padding: 0.5in;
}

h1 {
    border-bottom: 2px solid #000;
    color: #000;
    font-size: 18pt;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}

th, td {
    border: 1px solid #000;
    padding: 0.5em;
    text-align: left;
}

.equation {
    margin: 1em 0;
    padding: 0.5em;
    background: #f5f5f5;
    border-left: 3px solid #666;
}

.result-box {
    border: 2px solid #000;
    padding: 1em;
    margin: 1em 0;
    background: #f0f0f0;
}

@media print {
    .pagebreak { page-break-before: always; }
}
```

## AI Agent Integration

### Updated Workflow Pattern
```python
# AI Agent pseudo-code for calculation generation
async def handle_engineering_calculation(user_request):
    # Step 1: Parse request and perform calculations
    water_data = parse_water_analysis(user_request)
    
    # Step 2: Execute required tools
    speciation = await calculate_solution_speciation(water_data)
    treatment = await simulate_chemical_addition(treatment_params)
    
    # Step 3: Automatically generate calculation sheet
    calc_data = {
        "inputs": water_data,
        "speciation_results": speciation,
        "treatment_results": treatment,
        "calculations": {
            "theoretical_dose": theoretical_calc,
            "actual_dose": treatment["reagent_added_mmol"],
            "removal_efficiency": calc_removal(speciation, treatment)
        }
    }
    
    project_info = {
        "project_name": extract_project_name(user_request),
        "calculation_subject": "Lime Softening Design",
        "preparer": "AI Assistant",
        "date": datetime.now().strftime("%Y-%m-%d")
    }
    
    # Step 4: Generate calculation sheet
    calc_sheet = await generate_calculation_sheet(
        calculation_type="lime_softening",
        project_info=project_info,
        calculation_data=calc_data
    )
    
    # Step 5: Return both answer and calculation sheet
    return {
        "answer": format_answer(treatment),
        "calculation_sheet": calc_sheet["file_paths"]["pdf"]
    }
```

### AI System Prompt Addition
```markdown
## Automatic Calculation Documentation

When performing engineering calculations, you MUST:

1. **Always generate calculation sheets** for:
   - Design calculations (dosing, sizing, etc.)
   - Treatment evaluation
   - Multi-step processes
   - Any calculation requiring review

2. **Include in calculation sheets**:
   - All input parameters with sources
   - Step-by-step calculations
   - Intermediate results for verification
   - Final results with appropriate significant figures
   - Recommendations based on results

3. **Use appropriate calculation template**:
   - `lime_softening_calc` - For hardness removal
   - `pH_adjustment_calc` - For simple pH changes
   - `phosphate_removal_calc` - For P removal design
   - `treatment_train_calc` - For multi-step processes

4. **Example workflow**:
```python
# After performing calculations
calc_sheet = await generate_calculation_sheet(
    calculation_type="lime_softening",
    project_info={
        "project_name": "ACME Industrial WTP",
        "calculation_subject": "Softener Design"
    },
    calculation_data={
        "inputs": initial_water,
        "results": treatment_results,
        "design_params": {"flow_rate": 100, "units": "m3/h"}
    }
)
```

## Implementation Benefits

### 1. For Engineers
- Standardized, professional calculations
- Easy review and checking process
- Complete documentation trail
- Reusable for similar projects

### 2. For AI Agents
- Structured output generation
- Clear workflow to follow
- Automatic documentation
- Improved user confidence

### 3. For Organizations
- Consistent calculation quality
- Regulatory compliance
- Knowledge preservation
- Reduced liability

## Quality Assurance

### Calculation Verification
1. All equations shown symbolically first
2. Unit consistency throughout
3. Intermediate results for checking
4. Mass balance verification where applicable
5. Boundary condition checks

### Template Validation
1. Peer review of template structure
2. Test with multiple scenarios
3. Verify against manual calculations
4. Update based on user feedback

## Future Enhancements

### Phase 1: Core Implementation
- Basic calculation templates
- Integration with existing tools
- HTML/PDF output generation

### Phase 2: Advanced Features
- Digital signature integration
- Revision tracking system
- Calculation database
- Auto-generated summary reports

### Phase 3: Enterprise Features
- Integration with engineering databases
- Custom company templates
- Workflow management
- API for external systems

## Conclusion

This papermill-based approach provides the optimal solution for generating auditable engineering calculation sheets. It balances automation with professional requirements, ensuring that AI-assisted calculations meet the same standards as manually prepared documents. The integration with the MCP server is seamless, adding value without complexity.