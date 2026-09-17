"""Compatibility import for the unified semantic interpreter.

The old manually maintained ROUTES table and keyword-gated interpreter were
removed. Capabilities now come from ActionRegistry.semantic_capabilities().
"""
from core.semantic import SemanticInterpreter

ActionInterpreter = SemanticInterpreter
