"""Fault injector for the lab MySQL.

Puts the lab into a known bad state on demand and takes it back out. It knows
the answer the diagnosis system is trying to find, so diagnosis-side code must
never import this package or read its records.
"""
