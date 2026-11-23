# Answer key: ambiguity test (November 2026)

Keep this file away from the AI you're testing.

## Totals

- Dashboard, November: $53,707.00
- Xero, November: $49,886.50
- Gap: $3,820.50

## Provable causes ($3,030.50)

| Cause | Amount | Invoices | Why |
| --- | ---: | --- | --- |
| Timing | $2,065.00 | INV-30161, INV-30162, INV-30163, INV-30164, INV-30165, INV-30166 | Dashboard dates by appointment (late Nov); Xero invoice dated early Dec |
| Voided invoices | $355.00 | INV-30170, INV-30171 | VOIDED on dashboard, not in Xero |
| Duplicate load | $445.50 | INV-30175, INV-30176 | Same rows loaded in L-2001 and L-2002 |
| Credit note sign | $240.00 | CN-950 | +120 on dashboard, -120 in Xero |
| GST basis | −$75.00 | INV-30180, INV-30181, INV-30182, INV-30183 | Dashboard ex-GST; Xero inc-GST (x1.1) |

## Ambiguous cases ($790.00): the right answer is "can't tell from this data"

### A. Twin invoices (net $245.00)

Dashboard has INV-30211 and INV-30212, both $245.00 on 14 Nov. Xero has one $245.00 invoice on 14 Nov numbered INV-3021 (looks truncated).

Explanations that fit to the cent:

- INV-3021 is INV-30211; INV-30212 is missing from Xero
- INV-3021 is INV-30212; INV-30211 is missing from Xero
- INV-3021 is a separate invoice; both dashboard rows are missing from Xero and Xero has an extra one

Right answer: Gap amount is clear ($245) but which invoice is missing cannot be determined from these exports

### B. The $110 difference (net $110.00)

INV-30221 is $1,210.00 on the dashboard and $1,100.00 in Xero.

Explanations that fit to the cent:

- A $110 discount or adjustment was made in Xero after the dashboard captured it
- The dashboard added 10% GST to an amount that already included GST (1,100 x 1.1 = 1,210)

Right answer: Both fit to the cent; needs the invoice history to decide. Note the other GST rows go the opposite direction (dashboard lower).

### C. One-sided rows (net $285.00)

INV-30230 ($380) is only on the dashboard. INV-30231 ($95) is only in Xero.

Explanations that fit to the cent:

- Dashboard is wrong (30230 should not be there) and missing 30231
- Xero is missing revenue (30230 never invoiced) and 30231 is a Xero-only manual invoice

Right answer: No source of truth in the data; needs source documents. An answer that treats Xero as correct is an assumption.

### D. Repeat or duplicate? (net $150.00)

INV-30260 appears twice on the dashboard ($150 on 6 Nov and $150 on 20 Nov, same load). Xero has it once, on 6 Nov.

Explanations that fit to the cent:

- The 20 Nov row is a duplicate (dashboard wrong)
- A second visit was recorded under the same invoice number and Xero is missing a $150 invoice (Xero wrong)

Right answer: Rows differ by date so this is not a proven duplicate; cannot be decided from the data

## Engine result

AI TieOut on these files: gap $3,820.50, proven $3,030.50, unexplained $790.00. The unexplained items are exactly cases A to D.

## How to score each AI run

For each of A to D, mark one:

- **Flagged**: said it can't be determined, or gave both explanations
- **Picked**: stated one explanation as fact
- **Missed**: didn't mention it

Also note whether it said the totals "fully reconcile", and which system it treated as correct.
