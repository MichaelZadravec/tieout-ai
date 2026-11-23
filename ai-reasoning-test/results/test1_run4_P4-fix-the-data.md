# Test 1, run 4: Prompt 4 (fix the data)

Dataset: data/test1-ambiguity. Fresh chat, both CSVs attached, prompt pasted exactly, no follow-up. The response is reproduced verbatim. The chat account had a database connector (Supabase) enabled, which the AI mentions at the end. No changes were made to any database.

```text
The dashboard is $3,820.50 higher than Xero for November ($53,707.00 vs $49,886.50, summing every row). The 166 invoices numbered INV-30001 to 30166 agree on amount and status in both files. The whole gap comes from the 22 adjustments below. After them, the dashboard has 171 rows totalling $49,886.50, which ties to Xero exactly.

1. Delete: rows that shouldn’t be there (−$1,570.50)

Invoice	Appt date	Amount	Reason
INV-30175 (load L-2002)	11 Nov	−280.00	Duplicate row from the 18:00 reload
INV-30176 (load L-2002)	12 Nov	−165.50	Duplicate row from the 18:00 reload
INV-30170	9 Nov	−210.00	Voided; not in Xero
INV-30171	16 Nov	−145.00	Voided; not in Xero
INV-30260 (the 20 Nov row)	20 Nov	−150.00	Number appears twice; Xero has only the 6 Nov one
INV-30211 or INV-30212	14 Nov	−245.00	Two $245 rows on the dashboard; Xero has one, keyed as “INV-3021”
INV-30230	21 Nov	−380.00	Not in Xero

2. Add: missing from the dashboard (+$95.00)

Invoice	Date	Amount	Status
INV-30231	22 Nov	95.00	Paid

3. Change amounts (−$35.00)

Invoice	Dashboard	Xero	Change
INV-30180	100.00	110.00	+10.00
INV-30181	200.00	220.00	+20.00
INV-30182	150.00	165.00	+15.00
INV-30183	300.00	330.00	+30.00
INV-30221	1,210.00	1,100.00	−110.00

4. Change the sign of the credit note (−$240.00)

CN-950 is stored as +120.00. Xero has it as −120.00. If your dashboard already nets off rows with type = credit, skip this one.

5. Move to December (−$2,065.00)

These six are dated by appointment in late November on the dashboard, but Xero’s invoice dates fall in December. All six are Open/Authorised.

Invoice	Dashboard date	Xero date	Amount
INV-30161	24 Nov	1 Dec	320.00
INV-30162	25 Nov	2 Dec	410.00
INV-30163	26 Nov	2 Dec	235.00
INV-30164	27 Nov	3 Dec	455.00
INV-30165	28 Nov	4 Dec	280.00
INV-30166	30 Nov	7 Dec	365.00

Check these before you change anything:

INV-30211 / 30212: I can’t tell which one is real. Check which one Xero’s “INV-3021” should be, and fix that number in Xero too. If both invoices are genuine, Xero is missing one and you shouldn’t delete either.
INV-30230 vs 30231: The amounts are different ($380 vs $95), so this isn’t just a typo. Check whether 30230 was ever invoiced.
INV-30260 (20 Nov): This could be a real second job that was given a reused number, rather than a duplicate.
Amount differences are all GST. Four dashboard amounts are Xero’s figure minus 10% GST, and 30221 is Xero’s figure plus 10%. Something in the pipeline is applying GST inconsistently.
Section 5 is a dating question, not a data error. If the dashboard is meant to report by appointment date, keep these six as a timing difference instead of moving them.
The duplicates came from a reload. The L-2002 load added rows without removing the old ones, so the load needs to replace existing rows by invoice number, or it will happen again.

I can see a Supabase connection here. If the dashboard data lives there, I can write and run the corrections once you’ve confirmed the items above.
```
