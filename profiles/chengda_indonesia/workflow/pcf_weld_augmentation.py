"""Chengda-facing façade for the verified generic PCF weld augmenter.

The project-specific writeback entry imports this module so Chengda behavior
can be isolated without modifying the frozen Blue Star SHA parser.
"""

from bluestar_pcf_weld_augmentation import (
    AugmentDecision,
    augment_pcf_tree,
    validate_augmented_tree,
)

__all__ = ["AugmentDecision", "augment_pcf_tree", "validate_augmented_tree"]
