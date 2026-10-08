# Session API

Source of the small Azure Functions app (Python 3.12) that the scorecard app talks to during the workshop. It counts how many participants are on each step, keeps the latest finished assessment for each device for the facilitators' room dashboard, and forwards each submission to the workshop's Microsoft Form, which feeds the backup Excel dashboard. It runs only for the workshop and is deleted, with its data, afterwards.

The security design is described in [`../SECURITY.md`](../SECURITY.md#session-api).

## Routes

| Route | Who calls it | What it does |
| --- | --- | --- |
| `POST /api/v1/progress` | the app | Records the step (0 to 7) a device is on. Body: `{"sid", "s"}`. |
| `POST /api/v1/submit` | the app | Stores the latest finished assessment for a device and queues it for the form. |
| `GET /api/v1/stats` | the dashboard | Room aggregates. Needs the facilitator key. |
| `GET /api/dash` | facilitators | The room dashboard page. The key is passed in the URL fragment, never sent to the server in the URL. |
| `GET /api/health` | anyone | Version and whether the session is open. No data. |

## Layout

- `src/function_app.py`: Azure Functions entry points (HTTP routes and a 30-second timer that retries forwarding to the form).
- `src/service.py`: request handlers, independent of the Functions runtime. Nothing from a request, and no IP address, is logged.
- `src/core.py`: strict request validation, scoring and room aggregates. No I/O. Scores exactly like the app.
- `src/guard.py`: rate limits, CORS and the security headers on every response.
- `src/store.py`: Azure Table Storage over REST with the app's managed identity. The storage account has shared-key access disabled, so there are no account keys.
- `src/relay.py`: forwards submissions to the configured Microsoft Form only, on Microsoft's Forms hosts only.
- `src/dash.html`: the facilitators' dashboard page (hash-pinned script and style, Trusted Types, `frame-ancestors 'none'`).
- `src/catalog.json`: controls, weights, areas, bands, profile options and scenarios, generated from the app's question catalogue.
- `src/requirements.txt`: runtime dependencies pinned by version and SHA-256.
- `build_api.py`: builds the deployment zip. It downloads exactly the pinned wheels with `pip download --require-hashes`, unpacks them next to the code so nothing is installed when the app starts, and writes a reproducible zip. It also regenerates `catalog.json` from the app's question catalogue, which is kept outside this repository, so it does not run stand-alone here.
- `tests/`: unit tests. Run `python -m unittest discover -s tests` in this folder. The tests that compare scores with the reference implementation run only where that implementation is available.

## Configuration

Set as app settings; no values are kept in this repository.

| Setting | Purpose |
| --- | --- |
| `AzureWebJobsStorage__accountName`, `TABLE_ACCOUNT`, `TABLE_PREFIX` | Storage account and table names, reached with the managed identity. |
| `ALLOWED_ORIGINS` | The two app origins allowed to send progress and submissions. |
| `INTAKE_FROM`, `INTAKE_UNTIL` | The session window (Unix time). Outside it, progress and submissions are refused. |
| `FACILITATOR_KEY_SHA256` | SHA-256 of the facilitator key. The key itself is not stored. |
| `FORM_ID`, `RELAY_ENABLED` | The Microsoft Form that receives a copy of each submission. |

The Function App runs on the Flex Consumption plan with HTTPS only, TLS 1.2 or later, FTP and basic-auth publishing disabled, remote debugging off and no Application Insights.
