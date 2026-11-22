One-off helper scripts. Everything here is also reachable through
`python -m src.cli`; these exist for convenience when running long jobs.

- `collect_market.py [limit]` — refresh the divine/chaos rate, then collect
  listings from the official trade API. Equivalent to
  `python -m src.cli update-rate && python -m src.cli collect-market --limit N`,
  but writes progress to stdout in a form that is easy to `tail -f`.
- `recollect.py` — discard everything collected by the trade-API collector and
  collect again. Use after changing the sampling design; a biased sample cannot
  be repaired by reweighting, so it is removed rather than kept.

  **It deletes every `official_trade_api` row in the database, across all
  leagues**, not just the current one. If you only want to re-read what is
  already stored because the parser learned something, use
  `reparse_listings.py` below, which costs no requests and deletes nothing.
- `collect_loop.py [--limit N] [--rest MIN] [--retrain-at N]` — collect
  continuously until stopped, with a rest between cycles.

  This exists because the collector stratifies its search into a fixed set of
  slices and stops when they run dry: asking for 2,500 listings in one go
  returned 1,067, which was every *new* listing the slices could see, not a
  budget being hit. More data comes from asking again later, once the trade
  site has indexed more. Rate limiting is still the collector's own, read from
  GGG's response headers; the rest between cycles is on top of that.

  Stop it with Ctrl-C, with `--cycles N`, or by creating
  `data/collect_loop.stop`. It writes one line per cycle and keeps a JSON
  summary at `data/collect_loop.json`. `--retrain-at` retrains once the league
  passes a given number of listings and on meaningful growth after that,
  backing the previous model up to `models/backups/<timestamp>/` first.
- `reparse_listings.py [--league L] [--dry-run]` — re-derive the stored feature
  columns from each listing's saved trade payload. Run it after the parser
  learns something it did not know when the rows were collected: a renamed
  reward, a new property id, a new modifier phrasing. Because the full payload
  is stored with every listing, this spends no rate limit and loses nothing —
  it is always preferable to deleting rows and collecting them again.

  Covens is why it exists. `Item Quantity` became `Atlas Drops` with a new
  property id, so every listing collected in that league was written with
  `quantity = 0` — while the real number sat in `raw_data` the whole time,
  simply unread.

  **Always `--dry-run` first.** It only rewrites rows whose payload actually
  carries a `properties` array, and refuses to clear a feature that currently
  has a value; both guards exist because the first version of this script would
  have overwritten 15,266 CSV-imported listings with zeroes, those rows having
  taken their features from the CSV's own columns and having no `properties` to
  re-read.
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
  file attached to a GitHub release. Since 0.2.0 the trained model is tracked,
  so any checkout builds an identical zip; the script refuses to build one
  without the model rather than quietly shipping a download that prices
  nothing.

To cut a release: bump `version` in `pyproject.toml` (the zip takes its name
from there), run `python scripts/package_release.py`, read the summary it
prints to confirm the model went in, then attach the zip to a tag with
`gh release create`.
