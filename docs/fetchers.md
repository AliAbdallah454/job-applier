# Job fetchers

Eight provider implementations are registered in `backend/jobs/job_fetchers/`.
They normalize public employer postings to `JobPost` and share filtering, HTTP
errors, request spacing, and JSON export. Tests use synthetic provider responses.
**Live API compatibility has not been verified in this environment because DNS
resolution fails.** An implementation is not a guarantee that every board in the
CSV remains accessible.

| Platform | Listing source | Pagination/details | Important limitations |
| --- | --- | --- | --- |
| Greenhouse | Public Job Board API | Complete board response | Missing publication dates remain null. |
| Lever | Public Postings API, global/EU | Offset pages, up to 100 pages | No guaranteed publication timestamp. |
| Ashby | Public Job Postings API | Complete jobs feed | Explicitly unlisted jobs are excluded; dotted board names are supported. |
| Workday | Career site's CXS endpoints | POST searches of 20 jobs, then GET details; up to 1,000 pages | Undocumented career-site interface; token must preserve `wdN/tenant/site`; changes or access restrictions can break it. |
| Workable | Public widget and job-detail endpoints | Follows widget next-page URLs; fetches details per job | Public website interfaces, not its authenticated customer API; format changes are possible. |
| Recruitee | Careers Site offers API | Complete offers feed | Some boards require a careers token. The announced unauthenticated-access deadline is 10 February 2027. No bypass is attempted. |
| Rippling | Public board jobs endpoint | Array or jobs envelope | Thin listings often omit descriptions and publication dates; these remain empty/null. Public interface is experimental. |
| Personio | Public XML feed | Whole feed, position/posting records | Feed must be enabled by employer. Supports redirects to the same board's `.com` host. `createdAt` is not treated as publication time. |

## Sources and verification

Documented APIs/feed formats:

- [Ashby public Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)
- [Personio XML format](https://developer.personio.de/docs/retrieving-open-job-positions)
- [Personio feed activation](https://support.personio.de/hc/en-us/articles/207576365)
- [Recruitee Careers Site authentication](https://docs.recruitee.com/reference/authentication-1)
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)
- [Lever Postings API](https://github.com/lever/postings-api)

Workday, Workable widget/detail, and Rippling implementations target the public
career-site routes represented in the supplied CSV. Their request/response
fixtures are implemented offline; there is no claim of an official stable API
contract or current live verification. Unknown/malformed payloads and access
refusals become board errors, not successful empty results. All fetchers collect
listings only; Workday's POST is a search request, not an application submission.

## Recruitee authorization

For a board where you have authorized careers API access, the Python interface
accepts a `careers_token` argument. Load it from a secret or environment variable;
do not put it in the CSV. It is sent only on that fetcher's requests. The batch
collector does not currently configure per-company tokens, so protected boards
are recorded as access errors.

## Limits and dates

All fetchers accept `fetch(query="", location=None, limit=None)` to collect all
available listings within their pagination bounds. Numeric limits are 1–100.
Query matching requires every word in the title or available description; location
matching is a case-insensitive substring. These are not semantic recommendations.
Thin descriptions can reduce keyword matches.

Exact publication datetimes require a timezone. Date-only and relative values,
where exposed, can be preserved as `published_text`; no timezone or publication
instant is invented. Missing descriptions are empty strings. The shared catalogue
preserves source/board/region identity and records failed boards separately.
