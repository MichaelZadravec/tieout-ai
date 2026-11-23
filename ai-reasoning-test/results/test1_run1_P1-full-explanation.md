# Test 1, run 1: Prompt 1 (full explanation)

Dataset: data/test1-ambiguity. Fresh chat, both CSVs attached, prompt pasted exactly, no follow-up. The response is reproduced verbatim.

```text
The dashboard shows $53,707.00 for November and Xero shows $49,886.50, so the dashboard is $3,820.50 higher. The causes below account for all of it, to the cent.

I took the dashboard total as the sum of every row as it stands. I took the Xero total as all rows with a November InvoiceDate, with the credit note counted as negative. The row counts also tie: 170 invoices match on both sides once the items below are removed.

#	Cause	Invoices	Effect on dashboard vs Xero
1	Cut-off: appointment date vs invoice date. The dashboard puts these in November by appointment date. Xero dated them 1–7 Dec.	INV-30161 ($320), 30162 ($410), 30163 ($235), 30164 ($455), 30165 ($280), 30166 ($365)	+$2,065.00
2	Duplicate rows from a second load. Load L-2002 (1 Dec 18:00) reloaded two invoices already in L-2001.	INV-30175 ($280), INV-30176 ($165.50)	+$445.50
3	Duplicate within L-2001. The same invoice appears twice, dated 6 Nov and 20 Nov. Xero has it once, on 6 Nov.	INV-30260 ($150)	+$150.00
4	Voided invoices counted. The dashboard sums VOIDED rows. Neither invoice is in Xero.	INV-30170 ($210), INV-30171 ($145)	+$355.00
5	Credit note sign. The dashboard adds the credit as +$120. Xero has it as −$120.	CN-950	+$240.00
6	Amount differences, all exactly 10% (looks like GST). For four invoices the dashboard shows the ex-GST amount: 30180 ($100 vs $110), 30181 ($200 vs $220), 30182 ($150 vs $165) and 30183 ($300 vs $330), together −$75. For INV-30221 it goes the other way, $1,210 vs $1,100, which is +$110.	5 invoices	+$35.00 net
7	In the dashboard, not in Xero. INV-30230 has no Xero counterpart. The dashboard also has two $245 invoices on 14 Nov (30211 and 30212), but Xero has only one, numbered “INV-3021”, which looks like a truncated number.	INV-30230 ($380), one of 30211/30212 ($245)	+$625.00
8	In Xero, not in the dashboard. This is dated 22 Nov, well before the 1 Dec load, so the load should have picked it up.	INV-30231 ($95)	−$95.00
	Total		+$3,820.50

Yes, it fully reconciles. There is no unexplained residual. The items fall into three groups:

Dashboard pipeline bugs (causes 2–5, $1,190.50). These are clear errors to fix: dedupe by invoice number across loads, exclude VOIDED rows, and treat credits as negative.
A definition choice (cause 1, $2,065). You need to decide whether November means appointment month or invoice month. Neither file is wrong; they just use different dates.
Items to investigate in the source data (causes 6–8, $565 net). Check the GST basis the dashboard uses for those five invoices. Work out whether INV-3021 in Xero is 30211 or 30212, and whether the other one was ever raised. Then find out why INV-30230 isn’t in Xero and why INV-30231 isn’t in the dashboard.

Both files carry dates after today (load stamps of 1 Dec, Xero rows to 11 Dec), so I’ve taken them at face value.
```
