# The results store

Where finished runs are kept, per user, so a dashboard can show them. In the long run this is hosted: a database for the run documents and file storage for films and screenshots, beside the existing verifier service (a Cloudflare Worker with a D1 database, users known by a bearer token). This folder simulates that on one machine with the same shape: a SQLite file stands for the database, a folder of files stands for file storage, and one HTTP service stands for the API. Moving to the hosted version should change where the data lives, not what the API looks like.

## Who is asking

Every request acts as one user, and every query is scoped by that user's id.

- Uploads carry `Authorization: Bearer <token>`. Only a hash of the token is stored.
- The local store has one user, `local`. Its token is written to `<data folder>/token` when the store first starts.
- On the local store, reads from a browser need no login, because there is only one user.

### Hosted mode

`GT_HOSTED=1` (see `store/hosting.py` for `GT_HOST`, `GT_DATA`, `GT_PUBLIC_URL`, `GT_SERVICE_URL`). Then nothing is ever allowed because a request looks local; the Host header and the peer address are never used to decide who is asking.

- No `local` user and no token file are made. Users are managed on the server with `groundtruth.py users add|list|limit|remove|token` (each new token is printed once; only its hash is kept).
- Every read needs a bearer token or the sign-in cookie. An unauthenticated API request answers `401 {"error", "sign_in": "/signin.html"}`; a page (`*.html`, `/`) redirects (`302`) to `/signin.html?next=<where it was>`; any other file answers `401`. Only `signin.html`, `style.css`, `gt-mark.svg` and `js/signin.js` are served to anyone.
- Uploads (`POST /v1/runs`, `PUT .../files/...`, `POST .../complete`) take only the bearer token, never the cookie.
- A bearer token that is present but not valid is refused (`401`); it never falls back to the cookie.
- Feedback writes (`POST`/`DELETE`) made with the cookie must carry an `Origin` equal to this site's origin (`GT_PUBLIC_URL` when set, else the request's own `Host`) and no `Sec-Fetch-Site` other than `same-origin`; otherwise `403`. With the bearer token no `Origin` is needed.
- The verifier service's public routes are forwarded from `/verifier<path>` to the service on this machine (`GT_SERVICE_URL`), so people have one address: `GET /verifier/health`, `POST /verifier/v1/runs`, `POST /verifier/v1/runs/<id>/results`, `DELETE /verifier/v1/runs/<id>`, `POST /verifier/v1/drafts`, `GET /verifier/v1/drafts/<id>`. Exactly these (the path is matched as sent, ids are letters, digits and `_`); anything else under `/verifier` answers `404`, so `/internal/*` is never reachable through it. Only `Authorization`, `Content-Type`, `User-Agent` and the body are passed on (never a cookie); the answer is relayed in chunks; the service has 60 seconds to answer, which every forwarded route does (results long-poll about 20 s; drafts are polled). A service that can't be reached answers `502 {"error", "message"}`.

### Signing in a page (hosted)

| Route | What it does |
|---|---|
| `POST /v1/session` | Body `{"token"}`. Answers `200 {"user"}` and sets `gt_session=<random id>; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax; Secure` (30 days; only a hash of the id is stored, in `sessions`) and `gt_signed_in=1` with the same terms but readable by the page, which only tells the pages to show "Sign out". A wrong token answers `401`. An `Origin` from another site is refused. Local store: `404`. |
| `DELETE /v1/session` | Signs out: the session is deleted and both cookies are cleared (`Max-Age=0`). Needs this site's `Origin` when a session cookie is sent. Answers `204`. |
| `GET /v1/session` | Who is asking: `{"user", "via": "token"|"cookie"|"local"}`, or `401`. `login` uses it to check a token against a store that is not on this machine. |

Issuing a new token (`users token`) or removing a user ends all of their sessions.

## What is stored

- `users`: id, name, token hash, created, `monthly_limit` (US dollars per calendar month, UTC; empty means no limit).
- `sessions`: hash of a page's sign-in id, user, created, expires (hosted).
- `spend`: user, job (the verifier's run or draft id), kind (`run` or `draft`), `cost_usd`, at (UTC). One row per model session, written by the verifier service as each session ends; this month's spend is the sum of rows since the first of the month. Removing a user removes their rows.
- `projects`: id, user, name. One per app a user verifies.
- `runs`: id, user, project, feature, the verifier's run id, when it ran, when it was uploaded, whether all its files arrived, the verdict and case counts, each claim in brief (`claims`, below) and the commit it ran against, and the run document (see `RUNDOC.md`) as JSON. Uploading the same project, feature and run id again replaces the earlier upload. The brief claims and the commit are taken from the document when it is uploaded; a store made before they existed fills them in from its stored documents the next time it starts, so earlier uploads need not be sent again.
- `files`: run, path (as the document names it), kind (`clip` or `shot`), content type, size, hash. The bytes live in the file folder under their hash.
- `feedback`: id, user, run, property, case (or empty), kind (`not_what_i_meant`, `wrong_result` or `right`), note, when. What a person thought of one claim, or one variation of it, in one run.

## Routes

| Route | What it does |
|---|---|
| `POST /v1/runs` | Body: `{"document": <run document>, "files": [{"path", "kind", "size"}]}`. Creates or replaces the run. Answers `201 {"id", "missing": [paths still to send]}`. Needs the token. |
| `PUT /v1/runs/<id>/files/<path>` | The file's bytes. Only a path the upload declared, only under the declared size, at most 50 MB. Answers `204`. Needs the token. |
| `POST /v1/runs/<id>/complete` | Says every file was sent. Answers `200 {"missing": []}` or `409` with what is missing. Needs the token. |
| `GET /v1/projects` | The user's projects, each with its number of runs and its latest run's summary. |
| `GET /v1/runs?project=&feature=&limit=` | Run summaries, newest first: `id`, `project`, `feature`, `run`, `ran_at`, `uploaded_at`, `complete`, `properties` (count per verdict), `cases` (count per outcome), `commit` (`ran_against.commit`, or null), and `claims`: every property of the document, in its order, as `{"id", "title", "verdict", "variations"}`, where `title` is the property's title (`""` when it has none) and `variations` is how many variations (cases) it tried in that run. `limit` is 1 to 500, 50 when not given. |
| `GET /v1/runs/<id>` | `{"id", "uploaded_at", "complete", "files_base": "/v1/runs/<id>/files/", "document": {...}}`. |
| `GET /v1/runs/<id>/files/<path>` | The file. Honours `Range` requests (answers `206`), which a film's timeline needs to seek. |
| `GET /v1/runs/<id>/feedback` | `{"feedback": [{"id", "property", "case" (or null), "kind", "note", "at"}]}`, oldest first. |
| `POST /v1/runs/<id>/feedback` | Body: `{"property", "case" (optional), "kind", "note" (optional, at most 1,000 characters)}`. `kind` is `not_what_i_meant`, `wrong_result` or `right`; `property` must be a property of that run and `case`, when given, a case of that property. Answers `201` with the saved entry. |
| `DELETE /v1/runs/<id>/feedback/<feedback id>` | Removes one. Answers `204`. |
| `GET /health` | `{"ok": true, "store": 1}`. |
| anything else | A static file from `dashboard/`, so the dashboard and the API share one origin, as they will when hosted. |

Answer shapes, exactly:

- `GET /v1/runs` answers `{"runs": [...]}` and `GET /v1/projects` answers `{"projects": [{"name", "runs", "latest"}]}`. Lists are always wrapped in an object, never bare.
- A run's id is made by the store and means nothing; it stays the same when the run is uploaded again.
- Uploading a run again means sending every file again; the run reads as not complete until they have all arrived.
- A file must be exactly its declared size. A document may be at most 5 MB as JSON.
- Local store: a read without a token is accepted only when the request names this machine (127.0.0.1, localhost or ::1) as its host. Feedback may be posted and deleted without a token on the same terms, and only when the request carries no `Origin` or one on this machine, so a page on another site can't write to the store through the user's browser. Anything else needs the token. Hosted: see "Hosted mode" above; none of this applies.
- Feedback is scoped by user like runs: feedback on another user's run answers `404`.
- Hidden files (any path part starting with a dot) are never served from `dashboard/`.

Errors are JSON: `{"error": "<plain sentence>"}` with a fitting status. A run or file that belongs to another user answers `404`, the same as one that does not exist.

## Rules

- Only the command line starts an upload, after the run is saved, and only to the store it was told about. No tool the verifier service can call uploads anything.
- A failed upload never changes a verdict or an exit code.
- A path is never followed outside the run: no `..`, no leading `/`, only the paths the upload declared.
