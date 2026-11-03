# CGT calculator

UK Capital Gains Tax on shares, from broker CSVs.
It converts everything to GBP, matches sales
same-day, then within 30 days, then from the
Section 104 pool, and writes HTML reports per
tax year (6 April – 5 April).

It calculates disposals, not tax yet: no annual
exempt amount, losses, or 18%/24% split.

Standard library only, plus `curl` for FX rates.

## Disclaimer

This is not financial or tax advice, and it is not
an official product. It may contain mistakes. Use
it at your own risk, and check the results yourself
before filing anything.

## Run

    python main.py --data-dir fixtures --clean-output

Reports go to `fixtures/output/`. For real data,
put files in `data/` and run `python main.py
--clean-output` — no arguments defaults to `data/`.

