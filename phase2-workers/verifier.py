"""LLM-as-a-Verifier — probabilistic fact verification.

Implements the method from "LLM-as-a-Verifier: A General-Purpose Verification
Framework" (Stanford / UC Berkeley, arXiv:2607.05391), adapted to company-fact
verification and layered on top of our source-tier rules.

The paper's three scaling axes, all implemented here:

  1. SCORE GRANULARITY — instead of asking the model for a discrete verdict
     ("SAME"/"DIFFERENT"), we ask for a single scoring token and compute the
     EXPECTATION over the distribution of that token's logits. That turns a
     yes/no into a continuous 0..1 score carrying far more signal.
     (Falls back to repeated sampling automatically if the endpoint doesn't
     return logprobs.)

  2. REPEATED EVALUATION — the same criterion can be scored N times and averaged,
     reducing variance. Controlled by VERIFIER_SAMPLES.

  3. CRITERIA DECOMPOSITION — "do these two values agree?" is a compound question.
     We decompose it into independent sub-criteria and score each separately:
        entity  — same legal entity / subject?
        period  — same time period / as-of date?
        unit    — same unit, currency, scale?
        value   — same magnitude / content?
     Decomposition is what catches the failure modes plain judging misses
     (e.g. two different Razorpay legal entities, or FY24 vs FY25 revenue).

Cost control: this runs ONLY on facts that survive the tier fast-path, i.e. real
candidate conflicts — not on every write.
"""
import math
import os
import re

# how many independent samples per criterion (paper axis 2)
SAMPLES = int(os.environ.get("VERIFIER_SAMPLES", "1"))
# "full" = 4 decomposed criteria, "fast" = single combined criterion
MODE = os.environ.get("VERIFIER_MODE", "full").lower()

CRITERIA = {
    "entity": (
        "Do both statements describe the SAME legal entity / subject? "
        "Different subsidiaries, group companies, or unrelated firms score 0."
    ),
    "period": (
        "Do both statements refer to the SAME time period or as-of date? "
        "Different fiscal years, quarters, or vintages score 0. "
        "If neither statement is time-bound, score 9."
    ),
    "unit": (
        "Do both statements use a COMPATIBLE unit, currency and scale "
        "(e.g. INR crore vs INR crore, or convertible without ambiguity)? "
        "If units are irrelevant to this field, score 9."
    ),
    "value": (
        "Do both statements assert the SAME underlying value or content, "
        "allowing for rewording and differing levels of detail?"
    ),
}

COMBINED_CRITERION = (
    "Do these two statements assert the same underlying fact about the same "
    "entity and period, allowing for rewording and differing detail?"
)

SYSTEM = """You are a strict verification scorer for an investment-intelligence database.
You are given a data field, two recorded statements (A and B), and ONE criterion.
Reply with a SINGLE DIGIT 0-9 and nothing else.
  0 = the criterion is clearly NOT satisfied
  9 = the criterion is clearly satisfied
Use the middle of the range for partial or uncertain agreement."""

_DIGIT = re.compile(r"[0-9]")


def _expected_score(client, model, field_label, value_a, value_b, criterion) -> float:
    """One scored call. Returns 0..1.

    Score granularity (paper axis 1): read the top logprobs of the single output
    token, keep the digit tokens, softmax-normalise them and take the expectation
    E[digit]/9 instead of trusting the argmax digit alone.
    """
    user = (
        f"FIELD: {field_label}\n"
        f"STATEMENT A: {value_a[:700]}\n"
        f"STATEMENT B: {value_b[:700]}\n"
        f"CRITERION: {criterion}\n"
        "Score (0-9):"
    )
    resp = client.chat.completions.create(
        model=model, temperature=0.0, max_tokens=2,
        logprobs=True, top_logprobs=20,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": user}],
    )
    choice = resp.choices[0]
    text = (choice.message.content or "").strip()

    # --- axis 1: expectation over the scoring-token distribution -------------
    try:
        top = choice.logprobs.content[0].top_logprobs
        weights, total = {}, 0.0
        for cand in top:
            m = _DIGIT.search(cand.token.strip())
            if not m:
                continue
            d = int(m.group())
            p = math.exp(cand.logprob)
            weights[d] = weights.get(d, 0.0) + p
            total += p
        if total > 0:
            expectation = sum(d * p for d, p in weights.items()) / total
            return max(0.0, min(1.0, expectation / 9.0))
    except Exception:
        pass  # endpoint didn't return usable logprobs — fall through

    # --- fallback: plain digit read (repeated sampling supplies the variance) -
    m = _DIGIT.search(text)
    return (int(m.group()) / 9.0) if m else 0.0


def _score_criterion(client, model, field_label, a, b, criterion) -> float:
    """Axis 2: repeated evaluation — average N independent scorings."""
    scores = []
    for _ in range(max(1, SAMPLES)):
        try:
            scores.append(_expected_score(client, model, field_label, a, b, criterion))
        except Exception:
            continue
    return sum(scores) / len(scores) if scores else -1.0  # -1 = could not score


def verify(client, model, field_label: str, value_a: str, value_b: str) -> dict:
    """Verify whether A and B state the same fact.

    Returns {verdict, score, criteria} where verdict is one of
    SAME | A_MORE_PRECISE | B_MORE_PRECISE | DIFFERENT and score is 0..1.
    Fails closed: any error → DIFFERENT (never silently merges facts).
    """
    result = {"verdict": "DIFFERENT", "score": 0.0, "criteria": {}}

    if MODE == "fast":
        s = _score_criterion(client, model, field_label, value_a, value_b, COMBINED_CRITERION)
        if s < 0:
            return result
        result["criteria"] = {"combined": round(s, 3)}
        result["score"] = round(s, 3)
        result["verdict"] = _verdict_from(s, {}, value_a, value_b)
        return result

    # axis 3: criteria decomposition
    scores = {}
    for name, criterion in CRITERIA.items():
        s = _score_criterion(client, model, field_label, value_a, value_b, criterion)
        if s < 0:
            return result                      # scoring failed → fail closed
        scores[name] = round(s, 3)
        # short-circuit: a hard entity/period mismatch settles it — skip the rest
        if name in ("entity", "period") and s < 0.25:
            result["criteria"] = scores
            result["score"] = round(s, 3)
            return result                      # verdict stays DIFFERENT

    # aggregate: the weakest criterion caps agreement (a conjunction, not an average)
    agreement = min(scores.values())
    result["criteria"] = scores
    result["score"] = round(agreement, 3)
    result["verdict"] = _verdict_from(agreement, scores, value_a, value_b)
    return result


def _verdict_from(agreement: float, scores: dict, a: str, b: str) -> str:
    if agreement < 0.55:
        return "DIFFERENT"
    # they agree — decide which wording is richer (more specific wins)
    la, lb = len(a.strip()), len(b.strip())
    if lb > la * 1.35:
        return "B_MORE_PRECISE"
    if la > lb * 1.35:
        return "A_MORE_PRECISE"
    return "SAME"
