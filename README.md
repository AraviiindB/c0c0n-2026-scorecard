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
- Submits the ratings to the **room dashboard automatically** when the participant finishes the last scenario, so the facilitators can show the room's combined results, and shows the facilitators how many people are on each step.

## Privacy

- **Calculated on the device.** Scores, the report and the PDF are produced in the browser. Answers, owners and target dates are kept in the browser's local storage on the participant's device until the participant chooses *Start over*. The page loads nothing from other sites: no external scripts, fonts, images or trackers.
- **One destination.** The page connects only to the workshop's session API at `https://func-c0c0n26-8ug1oc.azurewebsites.net`. Its Content-Security-Policy blocks fetch, XHR, WebSocket, EventSource, beacon and ping requests to anywhere else, and blocks form submissions. No CSP can block every channel; `SECURITY.md` explains the limits and the other protections.
- **What is sent.**
  - While the participant answers: a random device ID, generated on the device and not linked to the person, and the number of the step they are on (0 to 7), so the facilitators can pace the session.
  - When the participant finishes the last scenario: the participant code, the three optional profile answers (sector, size, role), the 40 ratings, the app version and the time spent. If an answer is changed later, the update is sent automatically and replaces the earlier entry.
  - Never sent: owners, target dates, the report or the PDF. The PDF goes only where the participant saves or shares it.
- **Participant codes.** Each participant gets a random code (for example `K7M-Q4X`) generated on the device. Participants can keep it or type their own, but should not use anything that identifies them. No name, email or company is asked for.
- **Room dashboard.** The session API keeps the latest submission for each device for the facilitators' dashboard and forwards it to the workshop's Microsoft Form, which feeds a backup Excel dashboard. Room results appear only once five participants have submitted. The API stores no IP addresses and keeps no request logs. It is deleted, with its data, after the workshop.
- **If the network fails.** The app keeps retrying. After three failed attempts it also offers **Open pre-filled form**: Microsoft Forms opens with the same answers filled in, and they are recorded when the participant presses **Submit** there.
- The sample report (`?demo=1`) sends nothing.

## Notes

The results are self-reported and are not an audit or certification. Controls, recommendations and timeframes draw on the "Breaking the Chain" research.

The app is a single static page. It bundles jsPDF and jsPDF-AutoTable (both MIT). jsPDF in turn compiles in the npm packages fflate, fast-png and iobuffer (MIT) and pako (MIT and Zlib), plus a few smaller embedded components. See `THIRD-PARTY-NOTICES.md` for the licences and `sbom.cdx.json` for the CycloneDX software bill of materials, which includes the vulnerability (VEX) assessment. The source of the session API is in [`api/`](api/). Security design and vulnerability reporting are described in `SECURITY.md`, which also shows how to check that the page you were served is the signed, released build.
