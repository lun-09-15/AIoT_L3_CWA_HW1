"""
Datasets package for CWA Open Data processors.
Provides dedicated parsers and database loaders for each dataset.
"""

from src.datasets.specs import DATASET_SPECS, DatasetSpec

__all__ = ["DATASET_SPECS", "DatasetSpec"]
