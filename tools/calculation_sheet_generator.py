"""
Engineering Calculation Sheet Generator Tool

This tool generates professional, auditable calculation sheets from water chemistry
calculations performed by other MCP tools.
"""

import os
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import papermill as pm
import nbformat
from nbconvert import HTMLExporter, PDFExporter
from nbconvert.preprocessors import ExecutePreprocessor
import logging

from tools.schemas import GenerateCalculationSheetInput, GenerateCalculationSheetOutput, ProjectInfo

logger = logging.getLogger(__name__)

# Template mapping
CALCULATION_TEMPLATES = {
    "lime_softening": "lime_softening_calc.ipynb",
    "pH_adjustment": "pH_adjustment_calc.ipynb",
    "phosphate_removal": "phosphate_removal_calc.ipynb",
    "metal_precipitation": "metal_precipitation_calc.ipynb",
    "scaling_assessment": "scaling_assessment_calc.ipynb",
    "kinetic_design": "kinetic_design_calc.ipynb",
    "treatment_train": "treatment_train_calc.ipynb"
}

class CalculationSheetGenerator:
    """Generates engineering calculation sheets from MCP tool outputs"""
    
    def __init__(self, template_dir: str = None, output_dir: str = None):
        """
        Initialize the calculation sheet generator
        
        Args:
            template_dir: Directory containing calculation templates
            output_dir: Directory for generated calculation sheets
        """
        base_dir = Path(__file__).parent.parent
        self.template_dir = Path(template_dir or base_dir / "templates" / "calculations")
        self.output_dir = Path(output_dir or base_dir / "generated_calculations")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Configure exporters
        self.html_exporter = HTMLExporter()
        self.html_exporter.template_name = 'lab'
        
        # Load custom CSS
        css_path = base_dir / "templates" / "styles" / "engineering_calc.css"
        if css_path.exists():
            with open(css_path, 'r') as f:
                self.custom_css = f.read()
        else:
            self.custom_css = ""
    
    def validate_calculation_type(self, calc_type: str) -> str:
        """Validate and return the template filename"""
        if calc_type not in CALCULATION_TEMPLATES:
            raise ValueError(f"Unknown calculation type: {calc_type}. "
                           f"Available types: {list(CALCULATION_TEMPLATES.keys())}")
        return CALCULATION_TEMPLATES[calc_type]
    
    def prepare_calculation_data(self, 
                                calculation_data: Dict[str, Any],
                                project_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare and validate calculation data for template injection
        
        Args:
            calculation_data: Raw calculation data from MCP tools
            project_info: Project metadata
            
        Returns:
            Formatted parameters for papermill
        """
        # Set defaults for project info
        project_defaults = {
            "project_name": "Water Treatment Design",
            "project_number": f"WTP-{datetime.now().strftime('%Y%m%d')}",
            "calc_id": f"CALC-{datetime.now().strftime('%Y%m%d-%H%M')}",
            "revision": "0",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "preparer": "MCP Water Chemistry Tools",
            "checker": "-",
            "approver": "-",
            "prep_date": datetime.now().strftime("%Y-%m-%d"),
            "check_date": "-",
            "approve_date": "-"
        }
        
        # Merge with provided project info
        for key, default_value in project_defaults.items():
            if key not in project_info:
                project_info[key] = default_value
        
        # Combine all parameters
        parameters = {
            **project_info,
            "calculation_data": calculation_data
        }
        
        return parameters
    
    async def generate_calculation_sheet(self,
                                       calculation_type: str,
                                       project_info: Dict[str, Any],
                                       calculation_data: Dict[str, Any],
                                       include_raw_outputs: bool = True,
                                       output_formats: List[str] = None) -> Dict[str, Any]:
        """
        Generate a professional calculation sheet
        
        Args:
            calculation_type: Type of calculation (e.g., "lime_softening")
            project_info: Project metadata
            calculation_data: All calculation inputs, outputs, and results
            include_raw_outputs: Whether to include raw PHREEQC outputs
            output_formats: List of output formats (default: ["html", "pdf"])
            
        Returns:
            Dictionary with paths to generated files and status
        """
        if output_formats is None:
            output_formats = ["html", "pdf"]
        
        try:
            # Validate calculation type
            template_name = self.validate_calculation_type(calculation_type)
            template_path = self.template_dir / template_name
            
            if not template_path.exists():
                raise FileNotFoundError(f"Template not found: {template_path}")
            
            # Prepare parameters
            parameters = self.prepare_calculation_data(calculation_data, project_info)
            parameters["include_raw_outputs"] = include_raw_outputs
            
            # Generate output filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_filename = f"{calculation_type}_{project_info.get('project_number', 'CALC')}_{timestamp}"
            
            # Execute notebook with papermill
            executed_notebook_path = self.output_dir / f"{base_filename}.ipynb"
            
            logger.info(f"Executing template: {template_path}")
            pm.execute_notebook(
                str(template_path),
                str(executed_notebook_path),
                parameters=parameters,
                kernel_name='python3'
            )
            
            # Read executed notebook
            with open(executed_notebook_path, 'r', encoding='utf-8') as f:
                notebook = nbformat.read(f, as_version=4)
            
            # Generate outputs
            generated_files = {
                "notebook": str(executed_notebook_path)
            }
            
            # HTML output
            if "html" in output_formats:
                html_path = self.output_dir / f"{base_filename}.html"
                html_body, resources = self.html_exporter.from_notebook_node(notebook)
                
                # Inject custom CSS
                if self.custom_css:
                    css_tag = f"<style>\n{self.custom_css}\n</style>"
                    html_body = html_body.replace("</head>", f"{css_tag}\n</head>")
                
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_body)
                
                generated_files["html"] = str(html_path)
                logger.info(f"Generated HTML: {html_path}")
            
            # PDF output (requires additional setup)
            if "pdf" in output_formats:
                try:
                    pdf_exporter = PDFExporter()
                    pdf_path = self.output_dir / f"{base_filename}.pdf"
                    pdf_body, resources = pdf_exporter.from_notebook_node(notebook)
                    
                    with open(pdf_path, 'wb') as f:
                        f.write(pdf_body)
                    
                    generated_files["pdf"] = str(pdf_path)
                    logger.info(f"Generated PDF: {pdf_path}")
                except Exception as e:
                    logger.warning(f"PDF generation failed: {e}. "
                                 "Ensure LaTeX is installed for PDF export.")
            
            return {
                "success": True,
                "message": f"Successfully generated {calculation_type} calculation sheet",
                "files": generated_files,
                "calculation_id": parameters["calc_id"]
            }
            
        except Exception as e:
            logger.error(f"Error generating calculation sheet: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Failed to generate calculation sheet: {e}"
            }
    
    def list_available_templates(self) -> List[str]:
        """List all available calculation templates"""
        return list(CALCULATION_TEMPLATES.keys())
    
    def get_template_info(self, calculation_type: str) -> Dict[str, Any]:
        """Get information about a specific template"""
        template_name = self.validate_calculation_type(calculation_type)
        template_path = self.template_dir / template_name
        
        if template_path.exists():
            # Could extract more info from the notebook if needed
            return {
                "type": calculation_type,
                "template": template_name,
                "path": str(template_path),
                "exists": True
            }
        else:
            return {
                "type": calculation_type,
                "template": template_name,
                "path": str(template_path),
                "exists": False,
                "error": "Template file not found"
            }


# Async wrapper for MCP tool
async def generate_calculation_sheet(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    MCP tool wrapper for generating calculation sheets
    
    Args:
        input_data: Dictionary containing calculation type, project info, and data
    
    Returns:
        Dictionary with generation results
    """
    # Validate input
    try:
        input_model = GenerateCalculationSheetInput(**input_data)
    except Exception as e:
        return {
            "success": False,
            "error": f"Input validation error: {e}",
            "message": f"Failed to validate input: {e}"
        }
    
    generator = CalculationSheetGenerator()
    
    # Convert ProjectInfo to dict for the generator
    project_info_dict = input_model.project_info.model_dump()
    
    result = await generator.generate_calculation_sheet(
        calculation_type=input_model.calculation_type,
        project_info=project_info_dict,
        calculation_data=input_model.calculation_data,
        include_raw_outputs=input_model.include_raw_outputs,
        output_formats=input_model.output_formats
    )
    
    # Return as dictionary
    return result


# Example usage
if __name__ == "__main__":
    # Example calculation data from lime softening
    example_data = {
        "calculation_type": "lime_softening",
        "project_info": {
            "project_name": "Municipal WTP Upgrade",
            "project_number": "2024-WTP-001",
            "preparer": "John Engineer"
        },
        "calculation_data": {
            "inputs": {
                "analysis": {
                    "Ca": 5.0,
                    "Mg": 2.0,
                    "Alkalinity": 3.0,
                    "pH": 7.2,
                    "S(6)": 1.0,
                    "Cl": 2.0
                },
                "temperature_celsius": 15
            },
            "treatment_results": {
                "reagent_added_mmol": 6.5,
                "solution_summary": {
                    "pH": 10.3,
                    "analysis": {
                        "Ca": 0.3,
                        "Mg": 0.1
                    }
                },
                "precipitated_phases": {
                    "Calcite": 0.0045,
                    "Brucite": 0.0019
                },
                "total_precipitate_g_L": 0.57
            },
            "design_params": {
                "flow_rate": 100  # m³/h
            }
        }
    }
    
    # Run async function
    async def test():
        result = await generate_calculation_sheet(example_data)
        print(json.dumps(result, indent=2))
    
    asyncio.run(test())