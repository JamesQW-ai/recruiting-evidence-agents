# Boundary Matrix

| Scenario | Risk | Strong constraint | Validation layer | Conclusion |
|---|---|---|---|---|
| Mixed batch | Candidate scope crosses a business boundary | Exact table and batch selection; cross-scope reuse is rejected. | Offline fixture | Pass only when the rejection is observed. |
| Uncontrolled source | The process consumes prior output, chat files or unrelated folders | Read only the authorized input or selected Base scope. | Offline fixture | Blocker with no automatic repair. |
| Restricted control material | A Holdout result can be influenced by expected answers | Do not copy or process restricted controls or creator notes. | Archive and evidence-builder test | Blocker; TA Lead must release a compliant Source Pack. |
| Invalid output location | Business material is written inside a plugin or nested technical folder | Package is a direct child of the selected work directory. | Offline fixture | Blocker before output creation. |
| No new candidate | A same-batch rerun creates unnecessary output | Incremental preflight is read-only when no new stable ID exists. | Offline fixture | No output paths created. |
| Existing candidate as new | A known record is silently duplicated | Incremental scope compares stable candidate IDs and fingerprints. | Offline fixture | Existing identity is excluded. |
| Unconfirmed write or send | External state changes without explicit user approval | Require `SAVE`, `SEND`, or `SEND_AND_WATCH` as applicable. | Offline fixture | No write or send before confirmation. |
| CEO decision boundary | Workflow invents or writes an executive decision | HRD write field allowlist excludes `CEO最终决策`. | Offline fixture | CEO decision remains `Not Provided`. |
