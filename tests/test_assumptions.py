from datetime import UTC, datetime

import pytest

from recon_engine.assumptions import AssumptionError, AssumptionStore
from recon_engine.models import Assumption

WHEN = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def proposal(value="VOIDED"):
    return Assumption.propose("status_filter.statuses", [value], f"Side A includes {value}.")


def test_proposing_twice_returns_the_stored_assumption():
    store = AssumptionStore()
    first = store.propose(proposal())
    again = store.propose(proposal())

    assert again is first
    assert len(store) == 1


def test_confirm_records_who_and_when():
    store = AssumptionStore()
    assumption = store.propose(proposal())
    confirmed = store.confirm(assumption.id, "Jane CFO", at=WHEN)

    assert confirmed.confirmed is True
    assert confirmed.confirmed_by == "Jane CFO"
    assert confirmed.confirmed_at == WHEN
    assert store.get(assumption.id) == confirmed


def test_reproposing_a_confirmed_assumption_keeps_the_confirmation():
    store = AssumptionStore()
    assumption_id = store.propose(proposal()).id
    store.confirm(assumption_id, "Jane CFO", at=WHEN)

    assert store.propose(proposal()).confirmed is True


def test_confirming_twice_keeps_the_first_confirmation():
    store = AssumptionStore()
    assumption_id = store.propose(proposal()).id
    store.confirm(assumption_id, "Jane CFO", at=WHEN)

    assert store.confirm(assumption_id, "Someone Else").confirmed_by == "Jane CFO"


def test_the_store_persists_and_reloads(tmp_path):
    path = tmp_path / "assumptions.json"
    store = AssumptionStore(path)
    assumption_id = store.propose(proposal()).id
    store.confirm(assumption_id, "Jane CFO", at=WHEN)

    reloaded = AssumptionStore(path).get(assumption_id)
    assert reloaded.confirmed_by == "Jane CFO"
    assert reloaded.confirmed_at == WHEN
    assert reloaded.value == ["VOIDED"]


def test_unknown_assumption_is_a_clear_error():
    with pytest.raises(AssumptionError, match="No assumption with id 'asm_missing'"):
        AssumptionStore().confirm("asm_missing", "Jane CFO")


def test_a_confirmation_needs_a_name():
    store = AssumptionStore()
    assumption_id = store.propose(proposal()).id
    with pytest.raises(AssumptionError, match="needs the name"):
        store.confirm(assumption_id, "   ")


def test_different_values_are_different_assumptions():
    store = AssumptionStore()
    assert store.propose(proposal("VOIDED")).id != store.propose(proposal("DRAFT")).id
