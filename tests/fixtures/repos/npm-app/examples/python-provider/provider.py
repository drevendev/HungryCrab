"""Score a prompt with a local model."""

import torch


def score(prompt: str) -> float:
    return float(len(prompt)) / 100.0
