# market-signals

Stable India and US signal production.

The repository owns Chartink acquisition, translated US formulas, event gates, signal state, and
the `signals-bundle.v1.json` contract consumed by `market-screener-web`. It does not render HTML,
deploy Cloudflare Pages, or mutate canonical market stores.

```bash
export MARKET_DATA_ROOT=/Users/chiragpatnaik/Code/chartink-dashboard/data
export SIGNAL_STATE_ROOT=/Users/chiragpatnaik/Code/chartink-dashboard/data
export MARKET_CLASSIFICATION_PATH=/Users/chiragpatnaik/Code/chartink-dashboard/data.csv
./scripts/bundle
python3 -m pytest -q
```

The bundle uses an eight-week default for the weekly page. GitHub production emits an immutable
artifact identified by the producer commit. No workflow writes R2 or D1.

`classification.csv` is the four-column public classification snapshot used by hosted sector and
rotation rendering. The production schedule lives in `market-screener-web`; this repository's
producer workflow remains manual to prevent duplicate Chartink acquisition.

India and US composite shortlisting remain in `market-research` until their rule dependencies are
promoted as versioned, look-ahead-safe signal modules.
