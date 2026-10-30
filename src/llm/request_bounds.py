"""Validated optional bounds shared by real completion request paths."""
import math


def completion_options(max_output_tokens=None, timeout_seconds=None):
    options = {}
    if max_output_tokens is not None:
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        options["max_completion_tokens"] = max_output_tokens
    if timeout_seconds is not None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be a positive finite number")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        options["timeout"] = float(timeout_seconds)
    return options
