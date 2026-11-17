"""Certificate -> a single self-contained HTML page a CFO can read and print.

Everything shown comes from the certificate itself, and every value is escaped.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import TYPE_CHECKING

from tieout_ai.models import (
    Assumption,
    Certificate,
    CertificateStatus,
    Extract,
    ResidualItem,
    key_label,
    source_names,
)

if TYPE_CHECKING:
    from tieout_ai.certify import SignOff

MAX_RESIDUAL_ROWS = 200
MINUS = chr(0x2212)  # a true minus sign, not a hyphen


_BADGE = {
    CertificateStatus.RECONCILED: "ok",
    CertificateStatus.PROVISIONAL: "prov",
    CertificateStatus.OPEN: "open",
}

_CSS = """
:root { --ink:#1d2430; --muted:#5b6575; --line:#dde2ea; --panel:#f6f8fb;
  --ok:#17663a; --ok-bg:#e6f4ec; --warn:#8a4b00; --warn-bg:#fff4e0; --bad:#a3261b; --bad-bg:#fdecea; }
* { box-sizing: border-box; }
body { margin: 0; background: #fff; color: var(--ink);
  font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 32px 20px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 32px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
.meta { color: var(--muted); font-size: 13px; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 999px; font-weight: 700;
  letter-spacing: .04em; font-size: 13px; margin-left: 8px; vertical-align: middle; }
.badge.ok { background: var(--ok-bg); color: var(--ok); }
.badge.open { background: var(--bad-bg); color: var(--bad); }
.badge.prov { background: var(--warn-bg); color: var(--warn); }
.quiet { color: var(--muted); font-size: 13px; margin: 10px 0 0; }
.banner { margin: 20px 0; padding: 12px 16px; border-radius: 8px; }
.banner.warn { background: var(--warn-bg); color: var(--warn); border: 1px solid #f0c98a; }
.banner.ok { background: var(--ok-bg); color: var(--ok); border: 1px solid #b7dcc5; }
.banner.bad { background: var(--bad-bg); color: var(--bad); border: 1px solid #f1b8b2; }
.fingerprint { margin: 18px 0 0; padding: 12px 16px; border: 2px solid var(--ink); border-radius: 8px; }
.fingerprint .key { font: 600 17px ui-monospace, "Cascadia Mono", Consolas, monospace; word-break: break-all; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-top: 20px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }
.tile .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
.tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile.residual.nonzero .value { color: var(--bad); }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.total td { font-weight: 700; border-top: 2px solid var(--ink); }
tr.unexplained td { color: var(--bad); }
.state.unconfirmed { color: var(--warn); font-weight: 600; }
.elevated { border: 1px solid #f0c98a; border-radius: 8px; padding: 4px 14px 12px; margin: 20px 0; }
.elevated h2 { color: var(--warn); border-bottom-color: #f0c98a; }
.reason { color: var(--warn); font-weight: 600; }
.state.confirmed { color: var(--ok); }
code, pre { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 12.5px; }
pre { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 10px;
  white-space: pre-wrap; word-break: break-word; margin: 6px 0; }
.hash { word-break: break-all; color: var(--muted); }
.source { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; margin: 6px 0 0; font-size: 13px; }
dt { color: var(--muted); }
dd { margin: 0; }
footer { margin-top: 36px; color: var(--muted); font-size: 12px; }
@media print { main { padding: 0; } .tile, .source { break-inside: avoid; } }
"""


def money(value: Decimal) -> str:
    sign = MINUS if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _names(cert: Certificate) -> tuple[str, str]:
    return source_names(cert.extracts[0].connection_id, cert.extracts[1].connection_id)


def _assumption_state(a: Assumption, cert: Certificate) -> str:
    problem = cert.unverified_confirmations.get(a.id)
    if problem:
        return (
            '<span class="state unconfirmed">Confirmation could not be verified</span>'
            f'<div class="meta">{_e(problem)}</div>'
        )
    if a.confirmed and a.id not in cert.unconfirmed_assumptions:
        when = a.confirmed_at.strftime("%d %b %Y %H:%M UTC") if a.confirmed_at else ""
        return f'<span class="state confirmed">Confirmed by {_e(a.confirmed_by)} {_e(when)}</span>'
    return '<span class="state unconfirmed">Unconfirmed</span>'


def _reason(a: Assumption) -> str:
    if not a.elevated:
        return ""
    return f'<div class="reason">Elevated: {_e(a.elevated_reason)}</div>'


def _where(item: ResidualItem, names: tuple[str, str]) -> str:
    label = {
        "only_a": f"Only in {names[0]}",
        "only_b": f"Only in {names[1]}",
        "difference": "Amounts differ",
        "data_quality": "Data quality",
    }[item.kind]
    return f"{_e(label)}: {_e(item.reason)}" if item.reason else _e(label)


def _params(parameters: dict) -> str:
    def show(value: object) -> str:
        return ", ".join(map(str, value)) if isinstance(value, list) else str(value)

    return ", ".join(f"{k}={show(v)}" for k, v in parameters.items() if v is not None)


def _no_findings(cert: Certificate) -> str:
    if not cert.checks_without_findings:
        return ""
    runs = "; ".join(
        f"<code>{_e(run.check_name)}</code> v{_e(run.check_version)} ({_e(_params(run.parameters))})"
        for run in cert.checks_without_findings
    )
    return f'<p class="quiet"><strong>Checks with no findings:</strong> {runs}</p>'


def _source(name: str, extract: Extract) -> str:
    totals = ", ".join(f"{_e(k)} = {_e(money(v))}" for k, v in extract.column_totals.items())
    return f"""
<div class="source">
  <strong>{_e(name)}</strong> · connection <code>{_e(extract.connection_id)}</code>
  <pre>{_e(extract.query_text)}</pre>
  <dl>
    <dt>Rows</dt><dd>{extract.row_count:,}</dd>
    <dt>Column totals</dt><dd>{totals or "none"}</dd>
    <dt>Fetched</dt><dd>{_e(extract.fetched_at.isoformat())}</dd>
    <dt>Query hash</dt><dd class="hash">{_e(extract.query_hash)}</dd>
    <dt>Data hash</dt><dd class="hash">{_e(extract.data_hash)}</dd>
  </dl>
</div>"""


def _fingerprint_panel(cert: Certificate) -> str:
    """The key confirmations were checked against, for the reader to check against the
    confirmer's own. A certificate checked against some other key is not theirs."""
    if not cert.confirmer_key_fingerprint:
        return (
            '<div class="fingerprint"><strong>No confirmer key is recorded</strong> on this '
            "certificate, so its confirmations cannot be verified.</div>"
        )
    return f"""<div class="fingerprint">
  <div class="meta">Expected confirmer key ({_e(cert.confirmer)})</div>
  <div class="key">{_e(cert.confirmer_key_fingerprint)}</div>
  <div class="meta">Check it against what <code>tieout keys fingerprint</code> prints on the
  confirmer's own machine, or against the fingerprint the signer publishes. If it does not
  match, these confirmations were not made with their key.</div>
</div>"""


def _sign_off_banner(cert: Certificate, sign_off: SignOff | None) -> str:
    """Only a RECONCILED certificate can be final, and only once a person signs it off."""
    if cert.status != CertificateStatus.RECONCILED:
        return ""
    if sign_off is not None and sign_off.signed:
        return (
            f'<div class="banner ok"><strong>Signed off by {_e(sign_off.by)}</strong> on '
            f'{_e(sign_off.at)} with key <span class="hash">{_e(sign_off.fingerprint)}</span>. '
            "This certificate is final.</div>"
        )
    if sign_off is not None and sign_off.problem:
        return (
            '<div class="banner bad"><strong>Sign-off does not verify.</strong> '
            f"{_e(sign_off.problem)}. This certificate is not final.</div>"
        )
    return (
        '<div class="banner warn"><strong>Not signed off.</strong> Every item is explained and '
        "every assumption confirmed, but this certificate is not final until a person signs it "
        f"off with <code>tieout cert sign {_e(cert.id)}</code>.</div>"
    )


def render_html(cert: Certificate, sign_off: SignOff | None = None) -> str:
    """The certificate as a page. Pass its sign-off (Workspace.sign_off) to show it."""
    spec = cert.match_spec
    period = (
        f"{spec.period.start.isoformat()} to {spec.period.end.isoformat()}"
        if spec.period
        else "no period filter"
    )
    by_id = {a.id: a for a in cert.assumptions}
    name_a, name_b = _names(cert)
    unconfirmed = len(cert.unconfirmed_assumptions)

    if unconfirmed:
        plural = "assumption has" if unconfirmed == 1 else "assumptions have"
        provisional = (
            "The gap is fully explained, so this certificate is <strong>provisional</strong>: "
            if cert.status == CertificateStatus.PROVISIONAL
            else ""
        )
        banner = (
            '<div class="banner warn"><strong>Unconfirmed assumptions.</strong> '
            f"{provisional}{unconfirmed} {plural} not been confirmed by a person. The "
            "explanations below depend on them. A person confirms each one with "
            "<code>tieout confirm &lt;id&gt;</code>, then the certificate is re-issued."
            "</div>"
        )
    elif cert.assumptions:
        banner = (
            '<div class="banner ok"><strong>All assumptions confirmed</strong> by a person.</div>'
        )
    else:
        banner = ""

    bucket_rows = "".join(
        f"""<tr>
  <td><strong>{_e(b.check_name)}</strong> <span class="meta">v{_e(b.check_version)}</span>
    <div class="meta">{"<br>".join(_e(by_id[i].description) for i in b.assumption_ids)}</div></td>
  <td class="num">{b.row_count:,}</td>
  <td class="num">{money(b.amount)}</td>
</tr>"""
        for b in cert.buckets
    )
    residual_class = "unexplained" if cert.residual_items else ""
    explanation = f"""
<table>
  <thead><tr><th>Explanation</th><th class="num">Rows</th><th class="num">Amount</th></tr></thead>
  <tbody>
    {bucket_rows or '<tr><td colspan="3" class="meta">No explanations were proven.</td></tr>'}
    <tr class="{residual_class}"><td>Unexplained (net residual), {len(cert.residual_items):,} items
      <div class="meta">Gross {money(cert.gross)}: the open items' amounts before they offset
      each other</div></td>
      <td class="num"></td><td class="num">{money(cert.residual)}</td></tr>
    <tr class="total"><td>Gap ({_e(name_a)} &minus; {_e(name_b)})</td><td class="num"></td>
      <td class="num">{money(cert.match.gap)}</td></tr>
  </tbody>
</table>{_no_findings(cert)}"""

    def assumption_table(rows: list[Assumption]) -> str:
        body = "".join(
            f"""<tr><td><code>{_e(a.id)}</code></td><td>{_reason(a)}{_e(a.description)}
  <div class="meta">{_e(a.parameter)} = <code>{_e(a.value)}</code></div></td>
  <td>{_e(a.proposed_by)}</td><td>{_assumption_state(a, cert)}</td></tr>"""
            for a in rows
        )
        return f"""<table><thead><tr><th>ID</th><th>Statement</th><th>Proposed by</th>
<th>Status</th></tr></thead><tbody>{body}</tbody></table>"""

    elevated_rows = [a for a in cert.assumptions if a.elevated]
    other_rows = [a for a in cert.assumptions if not a.elevated]
    elevated = (
        f"""<section class="elevated"><h2>Elevated assumptions</h2>
<p class="meta">These loosen a check's default, so they deserve the closest review.</p>
{assumption_table(elevated_rows)}</section>"""
        if elevated_rows
        else ""
    )
    assumptions = (
        f"""<h2>{"Other assumptions" if elevated_rows else "Assumptions"}</h2>
{assumption_table(other_rows)}"""
        if other_rows
        else ""
    )

    residual_section = ""
    if cert.residual_items:
        shown = cert.residual_items[:MAX_RESIDUAL_ROWS]
        rows = "".join(
            f"""<tr><td>{_e(key_label(i.key))}</td><td>{_where(i, (name_a, name_b))}</td>
  <td class="num">{"amount missing" if i.kind == "data_quality" else money(i.amount)}</td></tr>"""
            for i in shown
        )
        more = (
            f'<p class="meta">Showing {len(shown):,} of {len(cert.residual_items):,} items; '
            "the certificate JSON lists them all.</p>"
            if len(cert.residual_items) > len(shown)
            else ""
        )
        residual_section = f"""<h2>Unexplained items</h2>
<table><thead><tr><th>Key</th><th>Where</th><th class="num">Amount</th></tr></thead>
<tbody>{rows}</tbody></table>{more}"""

    confirmer_line = (
        f"Confirmations checked against {_e(cert.confirmer)}'s key "
        f'<span class="hash">{_e(cert.confirmer_key_fingerprint)}</span><br>'
        if cert.confirmer_key_fingerprint
        else ""
    )
    source_banner = "".join(
        f'<div class="banner bad"><strong>Source warning.</strong> {_e(w)}</div>'
        for w in cert.source_warnings
    )
    status_badge = f'<span class="badge {_BADGE[cert.status]}">{cert.status.value}</span>'
    supersedes = f" · supersedes {_e(cert.supersedes)}" if cert.supersedes else ""
    residual_tile = "residual" + ("" if cert.residual == 0 else " nonzero")
    gross_tile = "residual" + ("" if cert.gross == 0 else " nonzero")
    a, b = _e(name_a), _e(name_b)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reconciliation certificate {_e(cert.id)}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
  <h1>Reconciliation certificate {status_badge}</h1>
  <div class="meta">{_e(cert.id)} · issued {_e(cert.created_at.isoformat())} · period {_e(period)}{supersedes}</div>
  {_fingerprint_panel(cert)}
  {_sign_off_banner(cert, sign_off)}
  {source_banner}
  {banner}
  {elevated}
  <div class="tiles">
    <div class="tile"><div class="label">{_e(name_a)} total</div><div class="value">{money(cert.match.total_a)}</div></div>
    <div class="tile"><div class="label">{_e(name_b)} total</div><div class="value">{money(cert.match.total_b)}</div></div>
    <div class="tile"><div class="label">Gap</div><div class="value">{money(cert.match.gap)}</div></div>
    <div class="tile"><div class="label">Explained</div><div class="value">{money(cert.explained)}</div></div>
    <div class="tile {residual_tile}"><div class="label">Unexplained (net)</div><div class="value">{money(cert.residual)}</div></div>
    <div class="tile {gross_tile}"><div class="label">Unexplained (gross)</div><div class="value">{money(cert.gross)}</div></div>
  </div>

  <h2>How the gap is explained</h2>
  {explanation}
  {assumptions}
  {residual_section}

  <h2>Sources</h2>
  {_source(name_a, cert.extracts[0])}
  {_source(name_b, cert.extracts[1])}

  <h2>Matching</h2>
  <dl>
    <dt>Keys</dt><dd>{a}: {_e(", ".join(spec.key_columns))} · {b}: {_e(", ".join(spec.keys_b))}</dd>
    <dt>Amounts</dt><dd>{a}: {_e(spec.amount_column)} · {b}: {_e(spec.amount_b)}</dd>
    <dt>Dates</dt><dd>{a}: {_e(spec.date_column or "none")} · {b}: {_e(spec.date_b or "none")}</dd>
    <dt>Matched keys</dt><dd>{cert.match.matched_count:,}</dd>
    <dt>Outside period</dt><dd>{a}: {cert.match.excluded_a_count:,} · {b}: {cert.match.excluded_b_count:,} rows</dd>
    <dt>Unusable rows</dt><dd>{a}: {cert.match.invalid_a_count:,} · {b}: {cert.match.invalid_b_count:,} (listed as data quality)</dd>
  </dl>

  <footer>
    {confirmer_line}Engine version {_e(cert.engine_version)} · certificate hash
    <span class="hash">{_e(cert.certificate_hash)}</span><br>
    Check this certificate with <code>tieout cert show {_e(cert.id)}</code>.
  </footer>
</main>
</body>
</html>
"""
