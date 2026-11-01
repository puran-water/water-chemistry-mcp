"""Pydantic contracts for water chemistry tool surfaces."""

from .common import (
    AnalyteAmount,
    CanonicalWaterAnalysis,
    ConcentrationUnit,
    ModelWarning,
    RunDiagnostics,
    SaturationState,
    SolutionState,
    StrictFiniteBaseModel,
)
from .speciation import SpeciationRequest, SpeciationResult
from .scaling import ScalingRequest, ScalingResult
from .reaction import ChemicalAdditionRequest, ChemicalAdditionResult, Reactant
from .mixing import SolutionMixItem, SolutionMixingRequest, SolutionMixingResult
from .gas import GasPhaseDefinition, GasPhaseRequest, GasPhaseResult
from .redox import RedoxAdjustmentRequest, RedoxAdjustmentResult, TargetRedoxCondition
from .surface import SurfaceDefinition, SurfaceInteractionRequest, SurfaceInteractionResult
from .kinetics import (
    KineticRateDefinition,
    KineticReaction,
    KineticReactionDefinition,
    KineticReactionRequest,
    KineticReactionResult,
    KineticTimeSeriesResult,
    TimeStepDefinition,
)
from .batch import BatchScenario, BatchScenariosRequest, BatchScenariosResult
from .audit import EndpointThermodynamicAuditRequest
from .dosing import (
    DosingReagent,
    DosingRequirementRequest,
    DosingRequirementResult,
    EnhancedDosingRequest,
    EnhancedDosingResult,
    LimeSofteningCurveRequest,
    LimeSofteningCurveResult,
    LimeSofteningDoseRequest,
    LimeSofteningDoseResult,
    TargetCondition,
)
from .optimization import MultiReagentOptimizationRequest, MultiReagentOptimizationResult
from .phosphorus import (
    ParameterContactObservation,
    PhosphorusConvergenceFlags,
    PhosphorusMassPartitionPoint,
    PhosphorusPhaseRole,
    PhosphorusRemovalRequest,
    PhosphorusRemovalResult,
    PhosphorusRemovalStrategyRequest,
    PhosphorusSITrigger,
)

__all__ = [
    "AnalyteAmount",
    "CanonicalWaterAnalysis",
    "ConcentrationUnit",
    "ModelWarning",
    "RunDiagnostics",
    "SaturationState",
    "SolutionState",
    "StrictFiniteBaseModel",
    "SpeciationRequest",
    "SpeciationResult",
    "ScalingRequest",
    "ScalingResult",
    "Reactant",
    "ChemicalAdditionRequest",
    "ChemicalAdditionResult",
    "SolutionMixItem",
    "SolutionMixingRequest",
    "SolutionMixingResult",
    "GasPhaseDefinition",
    "GasPhaseRequest",
    "GasPhaseResult",
    "TargetRedoxCondition",
    "RedoxAdjustmentRequest",
    "RedoxAdjustmentResult",
    "SurfaceDefinition",
    "SurfaceInteractionRequest",
    "SurfaceInteractionResult",
    "KineticReaction",
    "KineticRateDefinition",
    "KineticReactionDefinition",
    "KineticReactionRequest",
    "KineticReactionResult",
    "KineticTimeSeriesResult",
    "TimeStepDefinition",
    "BatchScenario",
    "BatchScenariosRequest",
    "BatchScenariosResult",
    "EndpointThermodynamicAuditRequest",
    "TargetCondition",
    "DosingReagent",
    "DosingRequirementRequest",
    "DosingRequirementResult",
    "EnhancedDosingRequest",
    "EnhancedDosingResult",
    "LimeSofteningCurveRequest",
    "LimeSofteningCurveResult",
    "LimeSofteningDoseRequest",
    "LimeSofteningDoseResult",
    "MultiReagentOptimizationRequest",
    "MultiReagentOptimizationResult",
    "PhosphorusRemovalStrategyRequest",
    "ParameterContactObservation",
    "PhosphorusRemovalRequest",
    "PhosphorusPhaseRole",
    "PhosphorusConvergenceFlags",
    "PhosphorusMassPartitionPoint",
    "PhosphorusSITrigger",
    "PhosphorusRemovalResult",
]
