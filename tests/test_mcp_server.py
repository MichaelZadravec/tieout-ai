"""Drive the MCP server over the real MCP protocol, as an AI client would."""

import asyncio
import json
import logging
from decimal import Decimal

import pytest
from mcp.client import Client
from support import CLEAN_PLAN, FIXTURE_MATCH_ARGS, fixture_workspace

from recon_engine.mcp_server import build_server

TOOLS = {
    "list_connections",
    "profile_source",
    "run_query",
    "get_rows",
    "match",
    "list_open_items",
    "list_checks",
    "run_check",
    "propose_assumption",
    "certify",
    "get_certificate",
}


@pytest.fixture(autouse=True)
def quiet_server_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def payload(result):
    """The JSON a tool returned, however the SDK packaged it."""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured.get("result", structured) if len(structured) == 1 else structured
    texts = [c.text for c in result.content]
    return json.loads(texts[0]) if len(texts) == 1 else [json.loads(t) for t in texts]


def run(server, script):
    async def go():
        async with Client(server) as client:
            return await script(client)

    return asyncio.run(go())


@pytest.fixture
def server(tmp_path):
    return build_server(fixture_workspace(tmp_path / "home"))


def test_the_server_exposes_exactly_the_brief_tools_and_no_confirm(server):
    async def script(client):
        return {t.name for t in (await client.list_tools()).tools}

    names = run(server, script)
    assert names == TOOLS
    assert not any("confirm" in n for n in names)


def test_calling_a_confirm_tool_fails(server):
    async def script(client):
        return await client.call_tool("confirm", {"assumption_id": "asm_x"})

    result = run(server, script)
    assert result.is_error


def test_end_to_end_reconciliation_over_mcp(server):
    async def script(client):
        async def call(name, **args):
            result = await client.call_tool(name, args)
            assert not result.is_error, result.content[0].text
            return payload(result)

        connections = await call("list_connections")
        a = (await call("run_query", connection_id="dashboard"))["extract_id"]
        b = (await call("run_query", connection_id="xero"))["extract_id"]
        started = await call("match", extract_a=a, extract_b=b, **FIXTURE_MATCH_ARGS)
        rec = started["reconciliation_id"]
        checks = await call("list_checks")
        runs = [
            await call("run_check", reconciliation_id=rec, check=name, params=params)
            for name, params in CLEAN_PLAN.values()
        ]
        cert = await call("certify", reconciliation_id=rec)
        loaded = await call("get_certificate", certificate_id=cert["certificate_id"])
        return connections, started, checks, runs, cert, loaded

    connections, started, checks, runs, cert, loaded = run(server, script)

    assert {c["id"] for c in connections} == {"dashboard", "xero"}
    assert started["gap"] == "29072.00"
    assert {c["name"] for c in checks} == {
        "timing",
        "status_filter",
        "tax_basis",
        "duplicates",
        "sign_credit",
    }
    # Every number in the explanation comes from the engine and adds up exactly.
    assert sum(Decimal(r["amount"]) for r in runs) == Decimal(started["gap"])
    assert cert["status"] == "PROVISIONAL"
    assert cert["residual"] == "0.00"
    assert sum(Decimal(b["amount"]) for b in cert["buckets"]) == Decimal(cert["gap"])
    assert loaded["hash_verified"] is True


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("run_query", {"connection_id": "nope"}, "No connection 'nope'"),
        ("get_certificate", {"certificate_id": "cert_missing"}, "No certificate 'cert_missing'"),
        (
            "run_check",
            {"reconciliation_id": "rec_missing", "check": "timing", "params": {}},
            "No reconciliation",
        ),
    ],
)
def test_engine_errors_reach_the_client_as_readable_messages(server, tool, args, message):
    async def script(client):
        return await client.call_tool(tool, args)

    result = run(server, script)
    assert result.is_error
    assert message in result.content[0].text


def test_guardrail_rejections_reach_the_client(server):
    async def script(client):
        a = payload(await client.call_tool("run_query", {"connection_id": "dashboard"}))
        b = payload(await client.call_tool("run_query", {"connection_id": "xero"}))
        rec = payload(
            await client.call_tool(
                "match",
                {"extract_a": a["extract_id"], "extract_b": b["extract_id"], **FIXTURE_MATCH_ARGS},
            )
        )["reconciliation_id"]
        return await client.call_tool(
            "run_check",
            {"reconciliation_id": rec, "check": "timing", "params": {"max_shift_days": 400}},
        )

    result = run(server, script)
    assert result.is_error
    assert "at most one period" in result.content[0].text
