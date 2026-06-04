"""
JSON Schema for `mark_6_rules_v1` parameters.

Defaults come from CLEv2's `clev2_settings.py` — the proven values
that have been trading for months.

The host validates PUT /api/plugins/mark_6_rules_v1/config bodies
against this schema before calling `plugin.configure(...)`. The CST
portal also reads this schema to render the form — no plugin-side
UI code, ever.

Schema is JSON Schema draft 2020-12. Use the `title` and
`description` fields liberally — the portal uses them as labels and
inline help text.
"""
from __future__ import annotations

PARAMETER_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://chimera/plugins/mark_6_rules_v1.schema.json",
    "title": "Mark 6 Rules — Horse Racing Lay Engine v1",
    "description": (
        "Parameters for the Mark 6 Rules horse-racing lay strategy. "
        "Defaults match the live engine at the time of port (CLEv2)."
    ),
    "type": "object",
    "additionalProperties": False,
    "properties": {
        # ── General ──────────────────────────────────────────────────
        "general": {
            "type": "object",
            "title": "General",
            "additionalProperties": False,
            "properties": {
                "point_value": {
                    "type": "number", "minimum": 0.1, "maximum": 100.0,
                    "default": 1.0,
                    "title": "Point Value (£/pt)",
                    "description": "Each 'point' in a rule's stake multiplies by this value.",
                },
                "countries": {
                    "type": "array", "items": {"type": "string"},
                    "default": ["GB", "IE"],
                    "title": "Countries",
                },
                "process_window_mins": {
                    "type": "integer", "minimum": 1, "maximum": 60,
                    "default": 5,
                    "title": "Process Window (minutes pre-off)",
                },
            },
        },

        # ── Core Rules ───────────────────────────────────────────────
        "rules": {
            "type": "object",
            "title": "Core Rules",
            "additionalProperties": False,
            "properties": {
                "rule1_enabled":  {"type": "boolean", "default": True, "title": "Rule 1 (favourite < split1)"},
                "rule1_stake":    {"type": "number",  "default": 3.0,  "minimum": 0, "title": "Rule 1 stake (pts)"},
                "rule2a_enabled": {"type": "boolean", "default": True, "title": "Rule 2A (split1 ≤ fav < split2)"},
                "rule2a_stake":   {"type": "number",  "default": 0.0,  "minimum": 0, "title": "Rule 2A stake (pts; 0 = skip band)"},
                "rule2b_enabled": {"type": "boolean", "default": True, "title": "Rule 2B (split2 ≤ fav < 5.0)"},
                "rule2b_stake":   {"type": "number",  "default": 1.0,  "minimum": 0, "title": "Rule 2B stake (pts)"},
                "rule2c_enabled": {"type": "boolean", "default": True, "title": "Rule 2C (fav between 5.0 and gap_threshold)"},
                "rule2c_stake":   {"type": "number",  "default": 2.0,  "minimum": 0, "title": "Rule 2C stake (pts)"},
                "rule3a_enabled": {"type": "boolean", "default": True, "title": "Rule 3A (fav > 5.0, gap < threshold — split)"},
                "rule3a_stake":   {"type": "number",  "default": 1.0,  "minimum": 0, "title": "Rule 3A stake per leg (pts)"},
                "rule3b_enabled": {"type": "boolean", "default": True, "title": "Rule 3B (fav > 5.0, gap ≥ threshold — fav only)"},
                "rule3b_stake":   {"type": "number",  "default": 1.0,  "minimum": 0, "title": "Rule 3B stake (pts)"},
                "rule2_split1":   {"type": "number",  "default": 3.0,  "title": "Rule 2 split 1 (2A/2B boundary)"},
                "rule2_split2":   {"type": "number",  "default": 4.0,  "title": "Rule 2 split 2 (2B/2C boundary)"},
                "rule3_gap_threshold": {"type": "number", "default": 2.0, "title": "Rule 3 fav→2nd gap threshold"},
            },
        },

        # ── Controls ─────────────────────────────────────────────────
        "controls": {
            "type": "object",
            "title": "Controls (risk modifiers)",
            "additionalProperties": False,
            "properties": {
                "spread_control_enabled": {"type": "boolean", "default": True,  "title": "Spread Control"},
                "jofs_enabled":           {"type": "boolean", "default": True,  "title": "JOFS (joint-favourite split)"},
                "mark_ceiling_enabled":   {"type": "boolean", "default": False, "title": "Mark Ceiling"},
                "mark_ceiling_value":     {"type": "number",  "default": 8.0,   "title": "Mark Ceiling odds cap"},
                "mark_floor_enabled":     {"type": "boolean", "default": False, "title": "Mark Floor"},
                "mark_floor_value":       {"type": "number",  "default": 1.5,   "title": "Mark Floor odds floor"},
                "mark_uplift_enabled":    {"type": "boolean", "default": False, "title": "Mark Uplift"},
                "mark_uplift_stake":      {"type": "number",  "default": 3.0,   "title": "Mark Uplift stake (pts)"},
            },
        },

        # ── Signal filters ───────────────────────────────────────────
        "signals": {
            "type": "object",
            "title": "Signal Filters",
            "additionalProperties": False,
            "properties": {
                "signal_overround_enabled":   {"type": "boolean", "default": False, "title": "Overround"},
                "overround_soft_threshold":   {"type": "number",  "default": 1.15},
                "overround_hard_threshold":   {"type": "number",  "default": 1.20},
                "signal_field_size_enabled":  {"type": "boolean", "default": False, "title": "Field Size"},
                "field_size_max_runners":     {"type": "integer", "default": 16},
                "field_size_odds_min":        {"type": "number",  "default": 1.5},
                "field_size_stake_cap":       {"type": "number",  "default": 2.0},
                "signal_steam_gate_enabled":  {"type": "boolean", "default": False, "title": "Steam Gate"},
                "steam_gate_odds_min":        {"type": "number",  "default": 2.0},
                "steam_shortening_pct":       {"type": "number",  "default": 10.0},
                "signal_band_perf_enabled":   {"type": "boolean", "default": False, "title": "Band Performance"},
                "band_perf_lookback_days":    {"type": "integer", "default": 30},
                "band_perf_min_win_rate":     {"type": "number",  "default": 0.55},
                "band_perf_min_sample":       {"type": "integer", "default": 50},
                "band_perf_reduced_stake":    {"type": "number",  "default": 0.5},
            },
        },

        # ── Risk overlays ────────────────────────────────────────────
        "risk": {
            "type": "object",
            "title": "Risk Overlays",
            "additionalProperties": False,
            "properties": {
                "top2_concentration_enabled": {
                    "type": "boolean", "default": False,
                    "title": "TOP2 Concentration",
                },
                "market_overlay_enabled": {
                    "type": "boolean", "default": False,
                    "title": "Market Overlay Modifier (MOM)",
                },
            },
        },
    },
}
