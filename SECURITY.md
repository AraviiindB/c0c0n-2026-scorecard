# Security policy

## Reporting a vulnerability

Please report security problems **privately** through GitHub private vulnerability reporting:
[Report a vulnerability](https://github.com/AraviiindB/c0c0n-2026-scorecard/security/advisories/new) (Security tab → *Report a vulnerability*).
Please do not post security problems in public. We aim to acknowledge reports within 3 working days and will credit reporters who want to be named.

**In scope:** the web app at <https://araviiindb.github.io/c0c0n-2026-scorecard/> (`index.html` in this repository) and the configuration of this repository.
**Out of scope:** the Microsoft Forms, Microsoft 365/OneDrive and GitHub/GitHub Pages platforms themselves (report those to Microsoft or GitHub), volumetric denial of service, social engineering, and anything that needs an already-compromised device or browser.

## Security design

- **No back end, no accounts, no tracking.** The app is one static HTML file. Scoring and the PDF report run entirely on the participant's device. There are no cookies, analytics, fonts, CDNs or other third-party requests.
- **Strict Content-Security-Policy** (meta tag): `default-src 'none'`. Every script and style block is allowed only by its SHA-256 hash: there is no `'unsafe-inline'`, no `'unsafe-eval'` and no inline `style` attribute. `connect-src 'none'` means the page cannot fetch or post anything. `img-src` allows only `data:` images. `form-action 'none'`, `base-uri 'none'`, `object-src 'none'`, `frame-src 'none'` and `worker-src 'none'` are also set.
- **Trusted Types are enforced** (`require-trusted-types-for 'script'`). The only HTML sink is a single named policy, and every dynamic value passes through an HTML-escaping function before it reaches it.
- **Untrusted input is escaped and validated.** Everything typed by the participant, or read back from local storage, is HTML-escaped before it is shown. It is also checked against allow-lists (control codes, rating values, profile options) and length-limited. Lookup tables have no prototype, so crafted keys cannot reach built-in properties.
- **Random participant codes.** Each participant gets a code generated with `crypto.getRandomValues` (for example `K7M-Q4X`) instead of a name or initials.
- **Anti-framing and referrer protection.** The page stays blank inside a frame and sends no referrer.
- **Local data only.** Answers are kept in the browser's local storage on the participant's device until the participant deletes them in the app. The page sends nothing by itself. Data leaves the device only if the participant opens the pre-filled Microsoft Forms link, which carries the participant code, optional profile and ratings, and then presses *Submit* there. Owners, target dates and the PDF are never sent.
- **Pinned, verified dependencies.** jsPDF 4.2.1 and jsPDF-AutoTable 5.0.8 (MIT) are embedded at fixed versions with no known vulnerabilities at release. Each build fails unless both npm tarballs match their pinned registry `sha512` integrity values and the shipped files are byte-identical to the tarball copies. [`sbom.cdx.json`](sbom.cdx.json) (CycloneDX 1.6) lists the components, including those bundled inside jsPDF, with their hashes. It also records the SHA-256 of `index.html`, so you can check that the file served by GitHub Pages is the one that was built. Licences are in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).
- **Repository protection.** `main` is protected by a ruleset: no deletion, no force-push, linear history and signed commits only. Commits are GitHub-verified. GitHub Actions is limited to GitHub-owned actions with read-only tokens. Secret scanning, push protection, Dependabot alerts and private vulnerability reporting are on. Issues, wiki and discussions are off.

## Room dashboard

The scores and the PDF that participants see are calculated on their own devices, so other people cannot change them. The facilitators' room dashboard (an Excel workbook linked to Microsoft Forms) shows self-reported, combined results. It is a discussion aid, not a measurement. Microsoft Forms cannot limit who submits or check the submitted values, so facilitators open the form only during the session. They also review submissions before discussing the numbers.

## Known platform limitations

- GitHub Pages does not allow custom HTTP response headers. The protections above therefore use the meta-tag and script equivalents. `frame-ancestors` and `X-Frame-Options` cannot be set, so a script anti-framing guard is used instead.
- The page is served from the shared `araviiindb.github.io` origin. No other site is published from this account.
