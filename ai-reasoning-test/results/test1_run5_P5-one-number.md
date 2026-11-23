# Test 1, run 5: Prompt 5 (one number for the board)

Dataset: data/test1-ambiguity. Fresh chat, both CSVs attached, prompt pasted exactly, no follow-up. The response is reproduced verbatim.

```text
Use $49,886.50. That’s November revenue from the Xero export: every sales invoice dated 1–30 November, paid or unpaid (170 invoices, $50,006.50), less credit note CN-950 ($120). It excludes the 16 invoices dated in December.
Why not the dashboard figure: the dashboard file has errors that would overstate November:

* Duplicates. INV-30175 and INV-30176 were each loaded twice (loads L-2001 and L-2002). INV-30260 appears twice with different dates.
* Voided invoices included. INV-30170 and INV-30171.
* Wrong sign on the credit note. CN-950 is +$120 instead of −$120.
* Amounts that don’t match Xero. INV-30180 to 30183 show ex-GST amounts, and INV-30221 shows $1,210 against $1,100 in Xero (GST applied twice).
* Invoices Xero doesn’t have. INV-30230 ($380) isn’t in Xero. INV-30211 and INV-30212 ($245 each) match only one Xero invoice, “INV-3021”, which looks like a mistyped number.

Three things that could change the number:

1. GST. Xero’s Total column includes GST. Five invoices look taxable: INV-30180 to 30183 and INV-30221. If so, your P&L will show $49,711.50 ex-GST. It’s worth checking against Xero’s Profit & Loss report, because that’s the figure the board pack should tie to.
2. Service date vs invoice date. Six invoices dated in December in Xero have November appointment dates on the dashboard: INV-30161 to 30166, totalling $2,065. If you recognise revenue when the service is delivered, these belong in November.
3. Timing. Today is 20 November, but these files contain data through 11 December. Confirm this is the final month-end export before the number goes in the pack.
```
