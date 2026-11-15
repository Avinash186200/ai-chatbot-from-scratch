# Methodology

Reference notes for the assistant's tools. The models are placeholders on
synthetic data; these notes describe how answers are produced, not what
the numbers mean.

## Point-in-time data and lookahead {#point-in-time}

Every observation has an observation date and a publication date. A
question asked as of a date only sees values published by then, and of
several revisions the latest one published by then. This prevents
lookahead.

## Snapshot id {#snapshot-id}

A snapshot id is a hash of what was known for a series on the as-of date;
equal snapshot ids mean equal inputs, so an answer can be cached and
reproduced.

## Placeholder forecast method {#forecast}

forecast_series fits an AR(1) model to daily changes over the last 250
observations known on the as-of date and reports 90% intervals. It is
computed on demand, so it never sees later data.

## Placeholder regime rule {#regime}

detect_regime labels a rate series (POLICY_RATE by default) tightening,
easing or on hold from its net change over a trailing window against a
threshold.

## Model versions {#model-versions}

Each model is its own installed package; the model version in an answer's
provenance is the version of the package that produced it.

## Vendor drop feed and late files {#vendor-feed}

Files from the vendor's daily drop are published on the day they arrive,
never earlier, so a file that arrives late cannot change an earlier
answer. Restated values become new revisions; nothing is overwritten.
Malformed files are quarantined with a reason.
