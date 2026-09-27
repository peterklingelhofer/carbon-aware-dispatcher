# Security Policy

## Reporting a vulnerability

Please report security issues privately via
[GitHub Security Advisories](https://github.com/peterklingelhofer/carbon-aware-dispatcher/security/advisories/new)
rather than opening a public issue. You should get an acknowledgement within a
few days.

## How this action handles secrets

- API keys and tokens are read only from the inputs/environment you provide.
  They're sent only to the corresponding upstream grid API over HTTPS, never
  to any third-party service.
- The action never logs full request URLs (which can carry tokens in query
  strings) and truncates upstream error bodies in its warnings.
- Most zones work with no key at all. Optional tokens (Electricity Maps,
  ENTSO-E, GridStatus, EIA, WattTime) add coverage, forecasts or accuracy.
- The action makes outbound HTTPS requests to public grid, weather and cloud-pricing APIs, to
  the GitHub REST API when you enable workflow dispatch, the PR comment, the
  digest issue, suggest-mode PRs or the gist ledger, and to `notify_webhook`
  when set. It doesn't execute remote code.

## Supported versions

Fixes land on the latest `v1` release. Pin `@v1` to receive them, or pin an
exact tag (e.g. `@v1.4.0`) for reproducibility.
