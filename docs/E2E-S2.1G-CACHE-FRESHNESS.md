# E2E-S2.1G — Cache, Freshness & Refresh Policy

## Decision

Exchange-symbol acquisition is cached through `CachedExchangeSymbolProvider`, a decorator implementing the existing `ExchangeSymbolProvider` contract.

The S2.1F provider composer remains unchanged.

## Cache identity

One entry is identified by:

* cache schema version;
* provider ID;
* provider version;
* exchange code.

A provider-version change creates a different cache key and cannot silently reuse an older provider representation.

The default location is `data/cache/scanner/exchange_symbols/`.

The parent directory `data/cache/` is excluded from Git.

## Persisted representation

Entries use deterministic UTF-8 JSON and contain:

* schema version;
* provider and exchange identity;
* `cached_at` and `expires_at`;
* original provider `fetched_at`;
* source provenance;
* provider status;
* raw listings;
* diagnostics;
* JSON-safe metadata;
* SHA-256 digest of the canonical payload.

`FAILED` provider results are never cached.

Deserialized values are reconstructed through the existing domain dataclasses, reapplying their validation rules and invariants.

## Atomic writes

Persistence uses:

1. a temporary file in the target directory;
2. UTF-8 JSON serialization;
3. flush and `fsync`;
4. atomic `os.replace`.

Temporary files are removed after failure.

A cache-write failure does not discard valid live listings. The result is returned as `PARTIAL` with a `CACHE_WRITE_FAILED` diagnostic.

## Freshness

Freshness is determined using the explicit, timezone-aware UTC timestamps:

* `cached_at`;
* `expires_at`.

Filesystem creation or modification time is not used.

The default TTL is 24 hours and can be configured by the caller.

At the exact expiry boundary, the entry is considered stale.

## Refresh modes

### `PREFER_CACHE`

A fresh entry is returned without calling the live exchange-symbol provider.

Missing, stale or invalid entries trigger a live refresh. A valid live result atomically replaces the existing entry.

This is the default operational mode.

### `FORCE_REFRESH`

The cache is bypassed and the live exchange-symbol provider is called.

A valid live result atomically replaces the cache entry.

This mode supports operator-requested refresh and live acceptance testing.

### `CACHE_ONLY`

No exchange-symbol provider fetch is performed.

* A fresh entry is returned.
* A missing entry produces `FAILED / CACHE_MISS`.
* A stale entry produces `FAILED / CACHE_STALE`.
* An invalid entry produces `FAILED / CACHE_INVALID`.

## Stale fallback

Stale data is rejected by default.

The caller can explicitly enable stale fallback after a live-provider failure using:

* `allow_stale_on_error=True`;
* a positive `max_stale_age`.

When stale fallback is accepted:

* the result status is `PARTIAL`;
* `STALE_CACHE_FALLBACK` is emitted;
* stale age is recorded in metadata;
* live failure codes are recorded in metadata;
* the original provider `fetched_at` timestamp is preserved.

Entries older than the permitted stale interval remain unusable.

## Failure boundaries

The implementation handles explicitly:

* cache miss;
* stale entry;
* malformed JSON;
* digest mismatch;
* unsupported cache schema;
* provider identity mismatch;
* provider-version mismatch;
* exchange mismatch;
* invalid reconstructed domain data;
* unexpected provider exception;
* invalid live provider result;
* cache-write failure;
* stale fallback beyond the permitted interval.

Unexpected provider exception messages are not exposed through diagnostics.

## Security

The cache rejects:

* API tokens;
* authorization data;
* secret-like metadata keys;
* authenticated source URLs;
* unsupported non-JSON metadata.

The live tool reads `EODHD_API_TOKEN` from the environment and never prints or persists it.

A case-insensitive scan of the generated cache files found no token, authorization, bearer or secret markers.

## Composition integration

The existing S2.1F composer requires no modification.

The execution path is:

Enabled exchange policy → provider composition → cached provider decorator → fresh cache or live provider.

Cache provenance is reported in `metadata["cache"]["disposition"]`.

Supported dispositions include:

* `CACHE_HIT`;
* `LIVE_REFRESH`;
* `LIVE_FAILED`;
* `LIVE_CACHE_WRITE_FAILED`;
* `CACHE_ONLY_FAILED`;
* `STALE_FALLBACK`.

## Dynamic exchange resolution

The live validation tool accepts any nonblank EODHD exchange code through `--eodhd-exchange`.

The corresponding `ExchangeDescriptor` is resolved dynamically through `EODHDExchangeDiscoveryProvider`.

No exchange catalog is hardcoded in the tool.

The provider registry and temporary live-validation policy are constructed dynamically using the requested exchange code.

Exchange discovery remains a separate lightweight configuration operation and is not cached by S2.1G.

## LU live validation

A forced refresh of the LU + BIT pilot produced:

* discovery status `SUCCESS`;
* composition status `SUCCESS`;
* 45 canonical listings;
* BIT listings: 40;
* LU listings: 5;
* diagnostics: 0;
* both providers reported `LIVE_REFRESH`.

The next `PREFER_CACHE` execution produced:

* composition status `SUCCESS`;
* the same 45-listing universe;
* unchanged provider timestamps;
* both providers reported `CACHE_HIT`;
* diagnostics: 0.

A subsequent `CACHE_ONLY` execution also produced:

* composition status `SUCCESS`;
* the same 45-listing universe;
* unchanged provider timestamps;
* both providers reported `CACHE_HIT`;
* no exchange-symbol provider fetch.

## XETRA stress validation

Dynamic discovery of XETRA succeeded without adding an exchange-specific descriptor to the tool.

The forced-refresh execution produced:

* discovery status `SUCCESS`;
* composition status `PARTIAL`;
* 4,231 canonical listings;
* BIT listings: 40;
* XETRA listings: 4,191;
* both providers reported `LIVE_REFRESH`;
* one explicit `ROW_SKIPPED` diagnostic.

EODHD row 3836 was rejected because its `Type` value was not a string.

The provider correctly preserved the remaining 4,191 valid XETRA rows instead of silently repairing, inventing or accepting malformed identity metadata.

The next `PREFER_CACHE` execution produced:

* discovery status `SUCCESS`;
* composition status `PARTIAL`;
* the same 4,231-listing canonical universe;
* BIT reported `CACHE_HIT`;
* XETRA reported `CACHE_HIT`;
* provider timestamps remained unchanged;
* the same `ROW_SKIPPED` diagnostic was reconstructed from cache.

This demonstrates that a large partial provider result, including its audit diagnostic, survives deterministic serialization and reconstruction without silent data loss.

## Test coverage

S2.1G tests cover:

* deterministic cache paths;
* fresh round trips;
* partial-result round trips;
* cache misses;
* TTL expiration;
* provider-version isolation;
* exchange isolation;
* malformed JSON;
* payload tampering and digest validation;
* rejection of failed results;
* rejection of invalid TTL values;
* rejection of secret-like metadata;
* rejection of authenticated source URLs;
* atomic-write cleanup;
* timezone-aware clocks;
* `PREFER_CACHE`;
* `FORCE_REFRESH`;
* `CACHE_ONLY`;
* explicit stale fallback;
* stale-age limits;
* provider exception containment;
* invalid provider identity;
* cache-write failure;
* invalid-cache replacement;
* provider composition over cached LU and BIT results;
* dynamic exchange argument normalization;
* dynamic exchange-policy construction;
* descriptor resolution through EODHD discovery;
* rejection of unknown exchange codes.

The focused S2.1G and S2.1F integration suite completed with 55 passing tests.

## Explicitly deferred

This milestone does not:

* cache EODHD exchange discovery;
* implement background refresh;
* implement scheduled refresh;
* coordinate concurrent writers across multiple processes;
* retry failed providers;
* select the final production exchange set;
* filter instrument types;
* translate symbols to Yahoo syntax;
* verify Yahoo availability;
* apply liquidity or history thresholds;
* invoke Research or Opportunity Scoring.

Those concerns remain assigned to downstream scanner milestones.
