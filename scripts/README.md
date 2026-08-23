One-off helper scripts. Everything here is also reachable through
`python -m src.cli`; these exist for convenience when running long jobs.

- `collect_market.py [limit]` — refresh the divine/chaos rate, then collect
  listings from the official trade API. Equivalent to
  `python -m src.cli update-rate && python -m src.cli collect-market --limit N`,
  but writes progress to stdout in a form that is easy to `tail -f`.
- `recollect.py` — discard everything collected by the trade-API collector and
  collect again. Use after changing the sampling design; a biased sample cannot
  be repaired by reweighting, so it is removed rather than kept.
- `prune_stale.py [days]` — remove listings older than N days (default 5). A
  long-unsold ask is evidence the price was wrong, not evidence of value.
- `export_sample.py [rows]` — write `data/sample_listings.csv`, an anonymised
  sample in the format `python -m src.cli import-market` accepts, so a fresh
  checkout has something to train on without collecting first.
