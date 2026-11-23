# Test 1, run 2: Prompt 2 (short answer for the CEO)

Dataset: data/test1-ambiguity. Fresh chat, both CSVs attached, prompt pasted exactly, no follow-up. The response is reproduced verbatim.

```text
No, they don’t reconcile. The dashboard shows $53,707.00 for November and Xero shows $49,886.50, a gap of $3,820.50. The whole gap is explained below. About half is a timing difference. The rest is mostly dashboard errors, plus a few invoices where the two systems disagree.

Note for the CEO:

November revenue doesn’t tie between the dashboard and Xero: $53,707 vs $49,886.50. $2,065 is timing: six late-November appointments were invoiced in early December. Roughly $1,190 is dashboard error: duplicate rows, voided invoices still counted, and a credit note added instead of subtracted. The remaining ~$565 is a handful of invoices where the two systems disagree on amount or existence, which finance is chasing.

Where the $3,820.50 goes (dashboard minus Xero):

Item	Impact
6 November appointments invoiced in December in Xero (INV-30161 to 30166)	+$2,065.00
Duplicate rows in dashboard (INV-30260, 30175, 30176)	+$595.50
Voided invoices still counted (INV-30170, 30171)	+$355.00
Credit note CN-950 counted as +$120 instead of −$120	+$240.00
INV-30230 is in the dashboard only	+$380.00
INV-30231 is in Xero only	−$95.00
INV-30211 and 30212 in the dashboard vs one “INV-3021” in Xero (likely a typo)	+$245.00
Amount mismatches on INV-30221, 30183, 30181, 30182, 30180	+$35.00
Total	+$3,820.50

Things to check:

GST handling looks inconsistent. Four of the amount mismatches are exactly 10% lower in the dashboard, so the dashboard seems to be ex-GST there. INV-30221 goes the other way: $1,210 in the dashboard vs $1,100 in Xero.
The Xero export isn’t November-only. It includes 10 December invoices (INV-30301 to 30310, $3,395). I left those out, so Xero’s full file total is $55,346.50.
The dates don’t line up with today. Today is 20 November, but both files contain December dates. The dashboard was loaded on 1 December, and the Xero invoices run to 11 December. It’s worth confirming which extract this actually is before it goes to the CEO.
```
