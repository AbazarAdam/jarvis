"""
core/cortex.py — JARVIS Goal Interpreter and Capability Selector.

This module provides the decision layer for JARVIS. Instead of relying on
the LLM alone to choose the correct tool, Cortex builds a live capability
manifest and resolves conflicts before execution.

Cortex:
  1. Registers available capabilities (built-in tools + plugins).
  2. Ranks capabilities for a given user query.
  3. Detects conflicts when multiple capabilities could match.
  4. Validates parameters before dispatching.
  5. Assigns a risk level using core/safety.py.
  6. Returns a structured decision that main.py can execute.

This module does NOT execute tools. It only decides which tool is appropriate.
"""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.safety import classify_path, validate_command_tokens


BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Capability dataclass
# ---------------------------------------------------------------------------
@dataclass
class Capability:
    name: str
    description: str
    source: str                      # "builtin" | "plugin" | "skill"
    parameters: dict = field(default_factory=dict)
    risk_level: int = 0              # 0=read only, 4=system dangerous
    side_effects: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    confidence: float = 1.0          # confidence for built-in/plugin, lower for new skills

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "parameters": self.parameters,
            "risk_level": self.risk_level,
            "side_effects": self.side_effects,
            "keywords": self.keywords,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Risk and side-effect classification
# ---------------------------------------------------------------------------
RISK_RULES = {
    "open_app": 1,
    "browser_control": 1,
    "file_controller": 2,
    "desktop_control": 2,
    "file_processor": 2,
    "computer_settings": 2,
    "computer_control": 2,
    "code_helper": 3,
    "dev_agent": 3,
    "agent_task": 2,
    "security_mode": 4,
    "security_tool_manager": 3,
    "self_heal": 4,
    "reminder": 1,
    "web_search": 0,
    "news": 0,
    "morning_brief": 0,
    "screen_process": 0,
    "camera_stream": 0,
    "tell_time": 0,
    "email_plugin": 2,
    "git_plugin": 3,
    "learning_mode": 1,
    "system_management": 3,
}


SIDE_EFFECTS = {
    "web_search": ["network_read"],
    "news": ["network_read"],
    "browser_control": ["network_read", "browser_state"],
    "file_controller": ["filesystem_write", "filesystem_read"],
    "file_processor": ["filesystem_read", "filesystem_write"],
    "computer_settings": ["system_control"],
    "computer_control": ["system_control"],
    "security_mode": ["network_scan", "network_active"],
    "security_tool_manager": ["filesystem_write", "network_download"],
    "self_heal": ["filesystem_write", "project_modify"],
    "code_helper": ["filesystem_write", "code_execution"],
    "dev_agent": ["filesystem_write", "code_execution"],
    "agent_task": ["multi_step", "tool_delegation"],
}


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------
STOP_WORDS = {
    "a", "an", "the", "for", "and", "or", "to", "of", "in", "on", "with",
    "is", "are", "was", "were", "be", "been", "do", "does", "did", "can",
    "could", "should", "would", "please", "sir", "jarvis", "my", "me", "you",
    "i", "it", "its", "this", "that", "what", "how", "when", "where", "which",
}


def _normalise_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.translate(str.maketrans("", "", string.punctuation))
    return text


def _tokenize(text: str) -> list[str]:
    tokens = _normalise_text(text).split()
    return [t for t in tokens if t and t not in STOP_WORDS]


# ---------------------------------------------------------------------------
# Capability manifest builder
# ---------------------------------------------------------------------------
def build_capabilities(
    tool_declarations: list[dict],
    plugin_declarations: list[dict],
    skills: list[dict] | None = None,
) -> list[Capability]:
    """
    Convert tool/plugin/skill declarations into Capability objects.

    tool_declarations: list of TOOL_DECLARATIONS from main.py
    plugin_declarations: list of PLUGIN_DECLARATIONS from main.py
    skills: optional saved skill definitions
    """
    capabilities: list[Capability] = []

    for tool in tool_declarations or []:
        name = tool.get("name", "")
        desc = tool.get("description", "")
        params = tool.get("parameters", {})
        risk = RISK_RULES.get(name, 2)
        side_effects = SIDE_EFFECTS.get(name, [])
        keywords = _extract_keywords(name, desc, params)
        capabilities.append(
            Capability(
                name=name,
                description=desc,
                source="builtin",
                parameters=params,
                risk_level=risk,
                side_effects=side_effects,
                keywords=keywords,
            )
        )

    for plugin in plugin_declarations or []:
        name = plugin.get("name", "")
        desc = plugin.get("description", "")
        params = plugin.get("parameters", {})
        risk = RISK_RULES.get(name, 2)
        side_effects = SIDE_EFFECTS.get(name, [])
        keywords = _extract_keywords(name, desc, params)
        capabilities.append(
            Capability(
                name=name,
                description=desc,
                source="plugin",
                parameters=params,
                risk_level=risk,
                side_effects=side_effects,
                keywords=keywords,
            )
        )

    for skill in skills or []:
        capabilities.append(
            Capability(
                name=skill.get("name", ""),
                description=skill.get("description", ""),
                source="skill",
                parameters=skill.get("parameters", {}),
                risk_level=skill.get("risk_level", 2),
                side_effects=skill.get("side_effects", []),
                keywords=skill.get("keywords", []),
                confidence=skill.get("confidence", 0.6),
            )
        )

    return capabilities


def _extract_keywords(name: str, desc: str, params: dict) -> list[str]:
    """
    Build a keyword set from tool name, description, and parameter names.
    """
    keywords = set(_tokenize(name))
    keywords.update(_tokenize(desc))
    for prop_name in params.get("properties", {}):
        keywords.add(prop_name.lower())
    return sorted(keywords)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_capability(query: str, capability: Capability) -> float:
    """
    Score a capability against the user query.

    Returns a float between 0.0 and 1.0.
    """
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0

    keyword_hits = query_tokens.intersection(set(capability.keywords))
    desc_tokens = set(_tokenize(capability.description))
    desc_hits = query_tokens.intersection(desc_tokens)

    # Stronger weight for matching tool/plugin name or primary keywords
    name_tokens = set(_tokenize(capability.name))
    name_hits = query_tokens.intersection(name_tokens)

    score = (
        len(name_hits) * 0.50 +
        len(keyword_hits) * 0.30 +
        len(desc_hits) * 0.20
    )
    # Normalise by query length, but avoid dividing by zero
    normaliser = max(1.0, len(query_tokens) * 0.5)
    score = min(1.0, score / normaliser)

    # Apply confidence multiplier for learned skills
    score *= capability.confidence

    return score


# ---------------------------------------------------------------------------
# Conflict resolution and selection
# ---------------------------------------------------------------------------
def select_capability(
    query: str,
    capabilities: list[Capability],
    threshold: float = 0.2,
) -> dict:
    """
    Choose the best capability for a user query.

    Returns:
        {
            "selected": Capability | None,
            "rankings": list[(Capability, score)],
            "conflicts": list[Capability],
            "ambiguous": bool,
            "reason": str,
        }
    """
    if not query.strip():
        return {
            "selected": None,
            "rankings": [],
            "conflicts": [],
            "ambiguous": False,
            "reason": "Empty query.",
        }

    scored = []
    for cap in capabilities:
        s = score_capability(query, cap)
        if s > 0:
            scored.append((cap, s))

    if not scored:
        return {
            "selected": None,
            "rankings": [],
            "conflicts": [],
            "ambiguous": True,
            "reason": "No capability matched.",
        }

    scored.sort(key=lambda x: x[1], reverse=True)
    best_score = scored[0][1]
    top_candidates = [cap for cap, s in scored if abs(best_score - s) < 0.08]

    selected = scored[0][0] if scored else None
    conflicts = top_candidates[1:5] if len(top_candidates) > 1 else []
    ambiguous = len(top_candidates) > 1

    reason = f"Selected {selected.name} with score {best_score:.2f}"
    if ambiguous:
        reason += f"; conflicts: {[c.name for c in conflicts]}"

    return {
        "selected": selected,
        "rankings": scored,
        "conflicts": conflicts,
        "ambiguous": ambiguous,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------
def validate_parameters(capability: Capability, parameters: dict) -> tuple[bool, list[str]]:
    """
    Validate parameters against a capability's declared schema.

    Returns:
        (valid: bool, errors: list[str])
    """
    params_schema = capability.parameters or {}
    required = params_schema.get("required", [])
    properties = params_schema.get("properties", {})

    errors = []

    for req in required:
        if req not in parameters:
            errors.append(f"Missing required parameter: {req}")

    # Basic type checks
    for key, value in parameters.items():
        if key in properties:
            expected_type = properties[key].get("type", "").lower()
            if expected_type == "string" and not isinstance(value, str):
                errors.append(f"Parameter '{key}' should be a string.")
            elif expected_type == "integer" and not isinstance(value, int):
                errors.append(f"Parameter '{key}' should be an integer.")
            elif expected_type == "boolean" and not isinstance(value, bool):
                errors.append(f"Parameter '{key}' should be a boolean.")
            elif expected_type == "array" and not isinstance(value, list):
                errors.append(f"Parameter '{key}' should be an array.")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Risk check using core/safety
# ---------------------------------------------------------------------------
def assess_risk(capability: Capability, parameters: dict) -> tuple[bool, int, str]:
    """
    Determine if a capability+parameters should be blocked or flagged.

    Returns:
        (allowed: bool, risk_level: int, reason: str)
    """
    name = capability.name
    risk = RISK_RULES.get(name, capability.risk_level)
    action = str(parameters.get("action", "")).lower()
    params_risk = risk

    if name == "file_controller" and action in ("delete", "move", "rename"):
        params_risk = max(params_risk, 3)
    if name == "computer_settings" and action in ("restart", "shutdown"):
        params_risk = max(params_risk, 4)
    if name == "security_mode" and action not in ("list_tools", "update_tools"):
        params_risk = max(params_risk, 4)
    if name == "self_heal":
        params_risk = max(params_risk, 4)
    if name == "git_plugin" and action in ("status", "diff", "log", "branch"):
        params_risk = 0

    if params_risk < 3:
        return True, params_risk, "Allowed."

    confirmed = parameters.get("confirmed")
    if str(confirmed).lower() in ("yes", "true", "1", "confirm"):
        return True, params_risk, "Explicit confirmation received."

    return False, params_risk, f"Confirmation required for risk level {params_risk}."


# ---------------------------------------------------------------------------
# Main Cortex API
# ---------------------------------------------------------------------------
def analyse_query(
    query: str,
    tool_declarations: list[dict],
    plugin_declarations: list[dict],
    skills: list[dict] | None = None,
) -> dict:
    """
    Full Cortex decision for a user query.

    Returns:
        decision dict containing selected capability, conflicts, risk, and
        parameter validation if parameters were provided.
    """
    capabilities = build_capabilities(tool_declarations, plugin_declarations, skills)
    selection = select_capability(query, capabilities)

    decision = {
        "query": query,
        "selected": selection["selected"].to_dict() if selection["selected"] else None,
        "conflicts": [c.to_dict() for c in selection["conflicts"]],
        "ambiguous": selection["ambiguous"],
        "reason": selection["reason"],
        "rankings": [
            {"name": cap.name, "score": round(score, 3)}
            for cap, score in selection["rankings"][:5]
        ],
    }
    return decision


def analyse_tool_call(
    name: str,
    parameters: dict,
    tool_declarations: list[dict],
    plugin_declarations: list[dict],
    skills: list[dict] | None = None,
) -> dict:
    """
    Analyse an actual tool call before execution.

    Returns:
        {
            "allowed": bool,
            "risk_level": int,
            "reason": str,
            "valid_parameters": bool,
            "parameter_errors": list[str],
        }
    """
    capabilities = build_capabilities(tool_declarations, plugin_declarations, skills)
    cap = next((c for c in capabilities if c.name == name), None)

    if not cap:
        return {
            "allowed": False,
            "risk_level": 3,
            "reason": f"Unknown capability: {name}",
            "valid_parameters": False,
            "parameter_errors": ["Capability not found."],
        }

    valid, errors = validate_parameters(cap, parameters)
    allowed, risk, reason = assess_risk(cap, parameters)

    return {
        "allowed": allowed,
        "risk_level": risk,
        "reason": reason,
        "valid_parameters": valid,
        "parameter_errors": errors,
    }