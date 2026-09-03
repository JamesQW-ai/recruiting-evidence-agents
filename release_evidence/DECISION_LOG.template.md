# Decision Log

| Topic | Risk | Decision | Validation | Remaining limit |
|---|---|---|---|---|
| Runtime package | Test data or development residue reaches an installer | Deliver a lightweight runtime ZIP only; tests, fixtures, build tools and runtime output are excluded. | Archive inventory and checksum verification. | Clean-install remains a separate gate. |
| Identity freeze | A tested ZIP differs from the delivered plugin | Freeze version and SHA-256 before clean-install, Holdout evaluation or independent replay. | ZIP manifest and sidecar checksum. | Any correction starts a new version. |
| Holdout input | The plugin obtains the expected answer from control material | Accept only TA Lead released Source Pack; refuse restricted controls and creator notes. | Input-boundary test and evidence-package exclusion check. | TA Lead retains independent review authority. |
| Material status | Confirmed absence is presented as completed material | Retain `Confirmed Missing` as a disclosed limitation across the workflow. | Follow-up and formal-package regression tests. | Business owner must provide the linked explicit reply. |
| Decision authority | HRD opinion is treated as a CEO decision | Base retains HRD fields after explicit `SAVE`; the plugin never writes `CEO最终决策`. | Write/read-back and authority-boundary tests. | CEO decision remains `Not Provided` until separately recorded. |
