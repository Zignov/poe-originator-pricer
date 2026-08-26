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

Two more are not analysis at all, but the packaging path:

- `launch.py` — pick a free port, start Streamlit, wait for its health endpoint
  and only then open the browser. Called by the double-click launchers
  (`Start Originator Pricer.bat`, `start.sh`), never by hand — the by-hand form
  is still `streamlit run app.py`.
- `package_release.py` — build `dist/poe-originator-pricer-<version>.zip`, the
  file attached to a GitHub release. Run it on a machine that has trained a
  model: the model is bundled when it is present, and that is the difference
  between a download that prices a stash tab immediately and one that first
  wants a 20-minute collection run.

To cut a release: bump `version` in `pyproject.toml` (the zip takes its name
from there), run `python scripts/package_release.py`, read the summary it
prints to confirm the model went in, then attach the zip to a tag with
`gh release create`.
