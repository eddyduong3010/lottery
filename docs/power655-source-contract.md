# Power 6/55 source recovery contract

The automatic and `--sync-missing` paths fetch missing draw IDs incrementally. They preserve valid partial results and leave inaccessible or conflicting IDs pending for a later attempt. They never silently download the entire archive to fill a small gap.

## Provider order and trust

1. Vietlott public history is preferred. A missing older ID is requested from its detail page, then the normal public draw-selector Ajax endpoint using the selector value published in the history page. Ajax render context is reused within that attempt.
2. If official history is unavailable or stale, public CVWork and XSMM history pages provide **unofficial** backup observations. For an individual unresolved gap, their published ID routes are tried. A page redirecting to the latest result cannot fill a different requested ID.
3. Every accepted observation requires a numeric ID, an actual draw date (Tuesday/Thursday/Saturday, from 2017-08-01 through the latest available cutoff), six distinct sorted main balls in 01–55, and one explicitly present different bonus ball. The cutoff remains **18:30 Vietnam time**. No missing ID is invented from the calendar when all latest-result providers fail.
4. Secondary variable prize amounts and winner counts remain unknown. Their historical prize tables are not sufficiently trustworthy; fixed payouts follow the existing game rules. Existing official prize facts are never downgraded. A later matching official detailed result can fill previously unknown prize fields through the manual recent-results update.

CVWork: <https://cvwork.vn/ket-qua-xo-so/vietlott/power-655>, individual `/01399`.
XSMM: <https://xsmm.vn/xo-so-vietlott-power-655>, individual `/ky-01399`.
These are public publishers, not official Vietlott endpoints. Availability from local probes does not prove availability from Streamlit or GitHub runners.

## Validation and storage

Malformed or incomplete result cards are quarantined with source warnings. This includes XSMM cards whose bonus is `**`; it is never inferred from prize counts. Valid complete cards on the same page remain usable. Different outcomes observed for one ID are quarantined rather than choosing the last provider. An unexpected requested ID is rejected.

SQLite writes use `BEGIN IMMEDIATE` to protect the read/check/write boundary. An established ID cannot change date, main balls, or bonus; another ID cannot claim its date or violate the chronology of adjacent stored IDs. Known prize amounts/counts cannot be changed or replaced with unknowns. Unknown values may only be enriched. Conflicts roll back that transaction. Ingestion saves each independent valid observation separately, so an unresolved older gap does not discard a newly available result. Repeat ingestion inserts zero duplicate draws. Corrections to established facts require a separate explicitly reviewed correction flow.

The report separates newly stored draws, known pending IDs, quarantined IDs, actual source URLs, provider errors, request count, and status. A parsed but stale provider is not healthy. A complete/current fallback result may be `degraded` with provider warnings and exit 0. Unavailable/latest-unknown, stale, partial, or conflict reports exit 1 while keeping validated partial writes. A packaged current snapshot alone is not proof of current source reachability.

## Request bounds and failure behavior

Each automatic/recent attempt shares a maximum of 12 HTTP attempts and a **cooperative** 20-second deadline across retries, selector setup, redirects, and fallback calls. Each network operation has at most a four-second connect/read timeout, with remaining deadline applied before requests. Streaming checks deadline after every chunk and caps each response at 1 MiB. An active network operation may finish after the cooperative deadline; this is not a hard real-time wall-clock guarantee. Explicit full-archive download remains a separately selected long-running operation.

HTTP 401/403 fails fast and opens a route-specific circuit only for that attempt. A blocked detail route does not disable history or Ajax. Transient network/5xx responses have at most one retry. HTTP 429 honors a short `Retry-After`; waits over two seconds defer that route until the next attempt instead of retrying early. Redirects are manual: at most two, HTTPS only, same provider host, and every hop consumes the shared budget. Ordinary certificate verification remains enabled. No proxies, security-warning bypasses, challenge solving or network-policy workarounds are used.

Manual Power completion replaces only the Power status for the normal sync TTL and refreshes tables. It does not clear the combined startup cache, refetch XSMN immediately, or hide a separate XSMN failure. A recent-only check still reports known gaps below the observed latest ID. Unresolved gaps/conflicts in the current browser session remain visible until a newer accepted check explicitly includes those IDs; an existing DB row alone cannot clear a source conflict, and an older cached positive observation cannot clear a newer conflict. Transport-only startup errors clear after confirmed recovery. The structured report lists the IDs actually checked.

## Operation

```bash
python src/fetch_vietlott_power655.py --sync-missing --database /path/to/scratch.sqlite3
python -m pytest -q tests/test_power_source_reliability.py tests/test_app.py
```

The CLI prints a structured report and uses the same guarded ingestion as the app. The daily workflow retains its 19:00 Vietnam schedule, serializes data writers, and explicitly persists `data/vietlott_power655.sqlite3`. It commits valid partial data, then marks the run failed if the Power sync was incomplete; `continue-on-error` on that step only allows preserving the partial results and does not make the final job green.

Local evidence on 2026-10-15: official history/details and CVWork/XSMM returned HTTP 200 under ordinary verified HTTPS GETs. CVWork's 14 overlapping complete outcomes matched the verified local official snapshot. XSMM included incomplete and misidentified older cards; adapters do not import these. A CVWork request for #01371 redirected to latest #01410, while XSMM's published #01371 route returned the requested complete outcome. Public result-only fixture markup and source hashes are in `tests/fixtures/power_sources/provenance.json`. Local outbound filtering of other candidate hosts is not evidence that those publishers themselves were unavailable.
