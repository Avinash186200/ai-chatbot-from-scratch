# Tool guide

How to read the tools' answers and their refusals.

## The response envelope {#envelope}

Every tool returns an envelope with ok, payload and provenance. When ok is
true the payload holds a result; when ok is false it holds an error with a
stable code and a message. Clients map the code, never the message text.

## Error codes {#error-codes}

SNAPSHOT_UNAVAILABLE means no data was published by the requested date.
SCHEMA_VIOLATION means an argument was missing, unknown or of the wrong
type, or the series is not supported. UNSUPPORTED_HORIZON means the
forecast horizon is outside the allowed range. MODEL_FAILURE means a model
package raised an error; INTERNAL means the server itself failed.

## Provenance fields {#provenance}

Provenance names the model id, the model version, the as_of date, the
snapshot id and the computed_at timestamp. Quote them with any number so a
reader can reproduce it.

## Forecast horizon limits {#horizon}

The forecast horizon is counted in business days. Horizons from 1 to 20
are accepted; longer horizons are refused with UNSUPPORTED_HORIZON rather
than extrapolated.

## Available series {#series}

The synthetic history has RATE_LONG, POLICY_RATE and VOL_INDEX. The
synthetic vendor feed adds RATE_SHORT and SPREAD_A from the dates its files
arrive. The model_info tool lists the series and tools that are live.
