# State Machine

## Task

Task status is owned by DevRelay-X and validated against explicit transitions:
`created -> assigned -> in_progress -> implemented -> verifying`, then `pass`,
`fail`, or `blocked`. `cancelled` is an explicit terminal state. PASS is not
available before verification.

## AgentRun

Normal execution:
`created -> preparing -> running -> changed -> testing -> test_pass -> evidence_collected
-> gate_pending -> completed`.

The test-failure branch uses `test_fail`; runtime or execution failure ends in
`failed`, and missing/unverifiable prerequisites end in `blocked`. Illegal
transitions raise an error. Each transition is persisted in the evidence record.

The Gate Engine alone chooses the final gate decision. A completed run can only
reach Gate PASS if each required gate has verified evidence.
