# c0c0n 2026 · Supply-chain incident readiness scorecard

Participant self-assessment for the c0c0n 2026 CISO working session **"Anatomy of a Software Supply Chain Incident"** (detection, response and recovery).

- **Start:** https://araviiindb.github.io/c0c0n-2026-scorecard/
- **Sample report:** https://araviiindb.github.io/c0c0n-2026-scorecard/?demo=1#report

## What it does

- Walks participants through **one unfolding supply-chain incident in 6 scenarios**: a poisoned npm package, a hijacked CI action, a compromised developer laptop, attackers publishing as you, the credential-containment race, and recovery. Each scenario ends with the questions it raises, and each question has a short explainer (for example, what an SBOM is).
- 40 controls, each asked once in the scenario where it matters, and scored in 4 weighted areas: Secure by default (30%), Preventive controls (25%), Detective capabilities (25%) and Incident-response readiness (20%).
- Each control is rated Implemented (1), Partial (0.5), Not implemented (0) or Unknown (0, reported as uncertainty).
- Shows area and overall scores out of 100, interpretation bands, critical gaps, uncertainty, the lowest-scoring area and a priority action plan.
- Builds a detailed PDF report **on the participant's device**. It contains the scores, findings, recommendations, suggested owners, roadmap timeframes, an action plan, the incident scenarios, and every question as asked with the participant's answer, grouped by area.

## Privacy

- Answers, owners and target dates are stored only in the browser's local storage on the participant's device. The page loads nothing from other sites (no external scripts, fonts, images or trackers). Its Content-Security-Policy also blocks fetch, XHR, WebSocket, EventSource, beacon and ping requests and form submissions from the page. No CSP can block every channel; `SECURITY.md` explains the limits and the other protections.
- Each participant gets a random code (for example `K7M-Q4X`) generated on the device. Participants can keep it or type their own, but should not use anything that identifies them.
- Nothing is sent unless the participant chooses **Open pre-filled form**. That link takes the participant code, the optional profile and the ratings to Microsoft Forms, and they reach the room dashboard only when the participant presses **Submit** there. Owners, dates and the PDF are never sent to Microsoft Forms or the room dashboard; the PDF goes only where the participant saves or shares it.

## Notes

The results are self-reported and are not an audit or certification. Controls, recommendations and timeframes draw on the "Breaking the Chain" research.

This is a single static page. It bundles jsPDF and jsPDF-AutoTable (both MIT). jsPDF in turn compiles in the npm packages fflate, fast-png and iobuffer (MIT) and pako (MIT and Zlib), plus a few smaller embedded components. See `THIRD-PARTY-NOTICES.md` for the licences and `sbom.cdx.json` for the CycloneDX software bill of materials, which includes the vulnerability (VEX) assessment. Security design and vulnerability reporting are described in `SECURITY.md`, which also shows how to check that the page you were served is the signed, released build.
