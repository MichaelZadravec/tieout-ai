"""Certificate -> a single self-contained HTML page a CFO can read and print.

Everything shown comes from the certificate itself, and every value is escaped.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape

from recon_engine.models import Assumption, Certificate, CertificateStatus, Extract

MAX_RESIDUAL_ROWS = 200
MINUS = chr(0x2212)  # a true minus sign, not a hyphen

_KIND = {
    "only_a": "Only in A",
    "only_b": "Only in B",
    "difference": "Amounts differ",
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
.banner { margin: 20px 0; padding: 12px 16px; border-radius: 8px; }
.banner.warn { background: var(--warn-bg); color: var(--warn); border: 1px solid #f0c98a; }
.banner.ok { background: var(--ok-bg); color: var(--ok); border: 1px solid #b7dcc5; }
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


def _key_part(part: object) -> str:
    if part is None:
        return "(blank)"
    text = str(part)
    # Quote values with edge whitespace, so a reader can see why they did not match.
    return f'"{text}"' if text != text.strip() else text


def _key(key: list) -> str:
    return " / ".join(_key_part(part) for part in key)


def _assumption_state(a: Assumption) -> str:
    if a.confirmed:
        when = a.confirmed_at.strftime("%d %b %Y %H:%M UTC") if a.confirmed_at else ""
        return f'<span class="state confirmed">Confirmed by {_e(a.confirmed_by)} {_e(when)}</span>'
    return '<span class="state unconfirmed">Unconfirmed</span>'


def _source(label: str, extract: Extract) -> str:
    totals = ", ".join(f"{_e(k)} = {_e(money(v))}" for k, v in extract.column_totals.items())
    return f"""
<div class="source">
  <strong>Side {label}</strong> · connection <code>{_e(extract.connection_id)}</code>
  <pre>{_e(extract.query_text)}</pre>
  <dl>
    <dt>Rows</dt><dd>{extract.row_count:,}</dd>
    <dt>Column totals</dt><dd>{totals or "none"}</dd>
    <dt>Fetched</dt><dd>{_e(extract.fetched_at.isoformat())}</dd>
    <dt>Query hash</dt><dd class="hash">{_e(extract.query_hash)}</dd>
    <dt>Data hash</dt><dd class="hash">{_e(extract.data_hash)}</dd>
  </dl>
</div>"""


def render_html(cert: Certificate) -> str:
    reconciled = cert.status == CertificateStatus.RECONCILED
    spec = cert.match_spec
    period = (
        f"{spec.period.start.isoformat()} to {spec.period.end.isoformat()}"
        if spec.period
        else "no period filter"
    )
    by_id = {a.id: a for a in cert.assumptions}
    unconfirmed = len(cert.unconfirmed_assumptions)

    if unconfirmed:
        plural = "assumption has" if unconfirmed == 1 else "assumptions have"
        banner = (
            '<div class="banner warn"><strong>Unconfirmed assumptions.</strong> '
            f"{unconfirmed} {plural} not been confirmed by a person. The explanations below "
            "depend on them. Confirm with <code>recon confirm &lt;id&gt;</code> and re-issue."
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
    <tr class="{residual_class}"><td>Unexplained (residual), {len(cert.residual_items):,} items</td>
      <td class="num"></td><td class="num">{money(cert.residual)}</td></tr>
    <tr class="total"><td>Gap (A &minus; B)</td><td class="num"></td>
      <td class="num">{money(cert.match.gap)}</td></tr>
  </tbody>
</table>"""

    assumption_rows = "".join(
        f"""<tr><td><code>{_e(a.id)}</code></td><td>{_e(a.description)}
  <div class="meta">{_e(a.parameter)} = <code>{_e(a.value)}</code></div></td>
  <td>{_e(a.proposed_by)}</td><td>{_assumption_state(a)}</td></tr>"""
        for a in cert.assumptions
    )
    assumptions = (
        f"""<h2>Assumptions</h2>
<table><thead><tr><th>ID</th><th>Statement</th><th>Proposed by</th><th>Status</th></tr></thead>
<tbody>{assumption_rows}</tbody></table>"""
        if cert.assumptions
        else ""
    )

    residual_section = ""
    if cert.residual_items:
        shown = cert.residual_items[:MAX_RESIDUAL_ROWS]
        rows = "".join(
            f"""<tr><td>{_e(_key(i.key))}</td><td>{_KIND[i.kind]}</td>
  <td class="num">{money(i.amount)}</td></tr>"""
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

    status_badge = (
        '<span class="badge ok">RECONCILED</span>'
        if reconciled
        else '<span class="badge open">OPEN</span>'
    )
    residual_tile = "residual" + ("" if cert.residual == 0 else " nonzero")
    keys_b = ", ".join(spec.keys_b)
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
  <div class="meta">{_e(cert.id)} · issued {_e(cert.created_at.isoformat())} · period {_e(period)}</div>
  {banner}
  <div class="tiles">
    <div class="tile"><div class="label">Side A total</div><div class="value">{money(cert.match.total_a)}</div></div>
    <div class="tile"><div class="label">Side B total</div><div class="value">{money(cert.match.total_b)}</div></div>
    <div class="tile"><div class="label">Gap</div><div class="value">{money(cert.match.gap)}</div></div>
    <div class="tile"><div class="label">Explained</div><div class="value">{money(cert.explained)}</div></div>
    <div class="tile {residual_tile}"><div class="label">Unexplained</div><div class="value">{money(cert.residual)}</div></div>
  </div>

  <h2>How the gap is explained</h2>
  {explanation}
  {assumptions}
  {residual_section}

  <h2>Sources</h2>
  {_source("A", cert.extracts[0])}
  {_source("B", cert.extracts[1])}

  <h2>Matching</h2>
  <dl>
    <dt>Keys</dt><dd>A: {_e(", ".join(spec.key_columns))} · B: {_e(keys_b)}</dd>
    <dt>Amounts</dt><dd>A: {_e(spec.amount_column)} · B: {_e(spec.amount_b)}</dd>
    <dt>Dates</dt><dd>A: {_e(spec.date_column or "none")} · B: {_e(spec.date_b or "none")}</dd>
    <dt>Matched keys</dt><dd>{cert.match.matched_count:,}</dd>
    <dt>Outside period</dt><dd>A: {cert.match.excluded_a_count:,} · B: {cert.match.excluded_b_count:,} rows</dd>
  </dl>

  <footer>
    Engine version {_e(cert.engine_version)} · certificate hash
    <span class="hash">{_e(cert.certificate_hash)}</span><br>
    Check this certificate with <code>recon cert show {_e(cert.id)}</code>.
  </footer>
</main>
</body>
</html>
"""
