from datetime import UTC, datetime

import pytest
from support import signer

from tieout_ai.assumptions import AssumptionError, AssumptionStore
from tieout_ai.models import Assumption
from tieout_ai.signing import Signer, SigningError

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
    confirmed = store.confirm(assumption.id, signer(), at=WHEN)

    assert confirmed.confirmed is True
    assert confirmed.confirmed_by == "Jane CFO"
    assert confirmed.confirmed_at == WHEN
    assert store.get(assumption.id) == confirmed


def test_reproposing_a_confirmed_assumption_keeps_the_confirmation():
    store = AssumptionStore()
    assumption_id = store.propose(proposal()).id
    store.confirm(assumption_id, signer(), at=WHEN)

    assert store.propose(proposal()).confirmed is True


def test_confirming_twice_keeps_the_first_confirmation():
    store = AssumptionStore()
    assumption_id = store.propose(proposal()).id
    first = store.confirm(assumption_id, signer(), at=WHEN)

    assert store.confirm(assumption_id, signer()) == first


def test_the_store_persists_and_reloads(tmp_path):
    path = tmp_path / "assumptions.json"
    store = AssumptionStore(path)
    assumption_id = store.propose(proposal()).id
    store.confirm(assumption_id, signer(), at=WHEN)

    reloaded = AssumptionStore(path).get(assumption_id)
    assert reloaded.confirmed_by == "Jane CFO"
    assert reloaded.confirmed_at == WHEN
    assert reloaded.value == ["VOIDED"]


def test_unknown_assumption_is_a_clear_error():
    with pytest.raises(AssumptionError, match="No assumption with id 'asm_missing'"):
        AssumptionStore().confirm("asm_missing", signer())


def test_a_confirmer_key_needs_a_name():
    with pytest.raises(SigningError, match="needs the name"):
        Signer.generate("   ")


def test_different_values_are_different_assumptions():
    store = AssumptionStore()
    assert store.propose(proposal("VOIDED")).id != store.propose(proposal("DRAFT")).id


def test_a_store_sees_changes_made_by_another_process(tmp_path):
    # The MCP server holds one store open; `tieout confirm` writes through another.
    path = tmp_path / "assumptions.json"
    server_view = AssumptionStore(path)
    assumption_id = server_view.propose(proposal()).id

    AssumptionStore(path).confirm(assumption_id, signer(), at=WHEN)

    assert server_view.get(assumption_id).confirmed is True
    assert server_view.all()[0].confirmed_by == "Jane CFO"


def test_a_store_never_writes_back_a_stale_copy(tmp_path):
    path = tmp_path / "assumptions.json"
    server_view = AssumptionStore(path)
    first = server_view.propose(proposal("VOIDED")).id

    AssumptionStore(path).confirm(first, signer(), at=WHEN)
    server_view.propose(proposal("DRAFT"))  # a later write by the server

    assert AssumptionStore(path).get(first).confirmed is True
    assert len(AssumptionStore(path)) == 2
