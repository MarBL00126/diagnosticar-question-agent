"""Question selection by expected information gain."""

from diagnosticar_preguntas.selection.askability import AskabilityRules
from diagnosticar_preguntas.selection.selector import InformationGainSelector, SelectionConfig

__all__ = ["AskabilityRules", "InformationGainSelector", "SelectionConfig"]
