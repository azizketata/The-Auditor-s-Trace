"""Violation to hash-chained, tamper-evident evidence record.

Never "signed": no cryptographic signature ships (PLAN-REVIEW B3/B12) — the
integrity claim is exactly hash-chaining plus recomputable verification.
No LLM call may ever enter this package (invariant I2).
"""
