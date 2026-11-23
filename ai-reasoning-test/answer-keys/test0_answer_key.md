# Answer key: Test 0, the AI TieOut demo data (October 2026)

Keep this file away from the AI you're testing.

This is the "messy" pair from AI TieOut's built-in demo (`uvx tieout-ai demo`), renamed so the file names give no hints. It was built to test data-cleaning problems, not ambiguity, so use it as a warm-up. `test0_manifest.json` is the generator's own record of what it planted.

## Totals

- Gap (dashboard minus Xero, October): $6,882.34

## What the engine proves with the demo's fixed plan ($5,556.34)

| Cause | Amount | Invoices |
| --- | ---: | --- |
| Timing (appointment date vs invoice date) | $4,599.00 | INV-20161 to INV-20170 |
| Duplicate load | $612.00 | INV-20188, INV-20189, INV-20190 |
| Voided invoices (status exactly "VOIDED" or "Voided") | $220.00 | INV-20173, INV-20174 |
| Credit note sign flip | $190.00 | CN-901 |
| GST basis | −$64.65 | INV-20179 to INV-20186 |
| Rounding | −$0.01 | INV-20187 |

## What the engine leaves unexplained ($1,326.00 net)

| Item | Amount | Why the engine won't claim it |
| --- | ---: | --- |
| INV-20171, INV-20172 | $340.00, $470.00 | Dated in a different period and also a different amount, so timing and GST at once |
| INV-20175, INV-20176 | $130.00, $140.00 | Status is "voided " and " VOIDED" (stray spaces and casing), which the demo plan didn't name |
| INV-20177 | $75.00 | On the dashboard only, with no status |
| INV-20178 | $210.00 and −$210.00 | Trailing space in the invoice number, so the keys don't match |
| Row with no invoice number | $55.00 | Can't be matched to anything |
| INV-20191 | $180.00 | Same invoice on two different dates, so not a proven copy |
| INV-20192 | −$64.00 | Dashboard amount is blank |

## Being honest about this test

The AI did better than the engine's fixed demo plan on several of these. It recognised the messy "voided" statuses and matched INV-20178 despite the trailing space. In real use, the AI would propose those clean-ups and the engine would record each one as an assumption for a person to confirm. The demo plan doesn't include them.

Where the AI overreached was in labelling, not arithmetic. It presented INV-20177, the row with no invoice number and INV-20191 as explained causes, when the data only shows that they differ, not why. This test is what led to Test 1, which was built specifically to check that behaviour.
