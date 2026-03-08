from __future__ import annotations


def online_update(state: dict, features: list[float], target: float, lr: float = 0.01) -> dict:
    w = state.get("weights", [0.0] * len(features))
    if len(w) != len(features):
        w = [0.0] * len(features)
    pred = sum(a * b for a, b in zip(w, features))
    err = target - pred
    w = [wi + lr * err * xi for wi, xi in zip(w, features)]
    return {"weights": w, "last_error": err, "steps": state.get("steps", 0) + 1}


def simple_sentiment(text: str) -> float:
    pos = ["bull", "pump", "breakout", "surge", "strong"]
    neg = ["bear", "dump", "crash", "weak", "risk"]
    t = text.lower()
    score = sum(1 for w in pos if w in t) - sum(1 for w in neg if w in t)
    return float(score)
