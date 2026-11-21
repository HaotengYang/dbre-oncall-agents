"""Diagnosis side: reads evidence from the lab and says what is wrong.

Isolation rule (CLAUDE.md): nothing in this package may import the fault
injector or the eval runner, or read var/ or evals/. It sees only what an
on-call engineer would see: the alert, metrics, and a read-only database
account. A unit test enforces this.
"""
