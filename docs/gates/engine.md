# Gate Engine

The engine evaluates `G0_ENVIRONMENT`, `G1_WORKSPACE`, `G2_TASK`, `G3_EXECUTION`,
`G4_TEST`, `G5_GIT`, and `G6_EVIDENCE`.

- `true`: verified evidence passed.
- `false`: verified evidence failed, producing Gate FAIL.
- `null`: required evidence was unavailable, producing BLOCKED.

PASS requires every gate to have verified evidence. A missing runtime, task
criteria, workspace, test suite, or evidence record cannot be treated as PASS.
The agent cannot set the decision. Passing these process gates does not replace
product-owner/business acceptance.
