"""Discrepancy checks. Importing this package registers every built-in check."""

from tieout_ai.checks import (
    duplicates,
    rounding,
    sign_credit,
    status_filter,
    tax_basis,
    timing,
)
from tieout_ai.checks.base import (
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
    "rounding",
    "run_checks",
    "sign_credit",
    "status_filter",
    "tax_basis",
    "timing",
]
