"""
JSON Schema describing the shape of `EvaluationResult.evidence` and
`Instruction.decision` for this plugin.

Portal consumers can use this to render the per-evaluation trace view.
"""
from __future__ import annotations

RESULT_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://chimera/plugins/mark_6_rules_v1.result.json",
    "title": "Mark 6 Rules — EvaluationResult.evidence + Instruction.decision",
    "type": "object",
    "properties": {
        "pipeline": {
            "type": "array",
            "title": "Pipeline trace",
            "description": (
                "Ordered list of steps the evaluator ran. Each entry has "
                "`step` (string) and `detail` (string)."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "step":   {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["step", "detail"],
            },
        },
        "favourite": {
            "type": "object",
            "title": "Favourite at evaluation",
            "properties": {
                "name":         {"type": "string"},
                "odds":         {"type": "number"},
                "selection_id": {"type": "integer"},
            },
        },
        "second_favourite": {
            "type": "object",
            "title": "Second-favourite at evaluation",
            "properties": {
                "name":         {"type": "string"},
                "odds":         {"type": "number"},
                "selection_id": {"type": "integer"},
            },
        },
        "rule_applied": {
            "type": "string",
            "title": "Rule that fired",
            "description": "e.g. 'rule_1', 'rule_2b', 'rule_3a_split'",
        },
    },
}
