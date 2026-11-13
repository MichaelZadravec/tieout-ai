"""Discrepancy checks. Importing this package registers every built-in check."""

from recon_engine.checks import duplicates, sign_credit, status_filter, tax_basis, timing
from recon_engine.checks.base import (
    Check,
    CheckContext,
    CheckError,
    CheckParameterError,
    CheckParams,
    Claim,
    Item,
    Proposal,
    ProposedBy,
    discrepancy_items,
    get_check,
    list_checks,
    register,
    run_checks,
)

__all__ = [
    "Check",
    "CheckContext",
    "CheckError",
    "CheckParameterError",
    "CheckParams",
    "Claim",
    "Item",
    "Proposal",
    "ProposedBy",
    "discrepancy_items",
    "duplicates",
    "get_check",
    "list_checks",
    "register",
    "run_checks",
    "sign_credit",
    "status_filter",
    "tax_basis",
    "timing",
]
