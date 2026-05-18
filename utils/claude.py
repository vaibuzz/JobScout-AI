"""
Claude API wrapper — provides two main helpers:
    claude(prompt, system)         → str   (raw text response)
    claude_json(prompt, system, Model) → Model  (validated Pydantic model)

Uses tenacity for retry with exponential backoff.
TODO: Implement in Step 2
"""
import os
import json
from typing import Type, TypeVar
from dotenv import load_dotenv

load_dotenv()

T = TypeVar("T")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")


def claude(prompt: str, system: str = "") -> str:
    """
    Call Claude and return raw text response.
    Retries up to 3 times with exponential backoff on transient errors.
    """
    raise NotImplementedError("claude() not yet implemented — coming in Step 2")


def claude_json(prompt: str, system: str, model_class: Type[T]) -> T:
    """
    Call Claude, parse JSON from the response, and validate with a Pydantic model.
    Strips markdown fences (```json ... ```) before parsing.

    Args:
        prompt: User prompt
        system: System prompt (should instruct Claude to return JSON)
        model_class: Pydantic model class to validate against

    Returns:
        Validated instance of model_class
    """
    raise NotImplementedError("claude_json() not yet implemented — coming in Step 2")
