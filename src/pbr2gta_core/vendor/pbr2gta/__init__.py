"""PBR2GTA Lite public API."""

from .converter import ConversionResult, Spec2GtaResult, convert_material, convert_spec2gta
from .profile import DEFAULT_PROFILE, ConverterProfile

__all__ = [
    "ConversionResult",
    "ConverterProfile",
    "DEFAULT_PROFILE",
    "Spec2GtaResult",
    "convert_material",
    "convert_spec2gta",
]

__version__ = "1.0.8"
