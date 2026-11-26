# State Machine

## Task

Task status is owned by DevRelay-X and validated against explicit transitions:
`created -> assigned -> in_progress -> implemented -> verifying`, then `pass`,
`fail`, or `blocked`. Optional review can transition `verifying -> reviewing ->
verifying`; it does not grant PASS. Transitions are checked by the Task model
and a SQLite trigger, including updates that bypass the ORM. `cancelled` is an explicit
terminal state. PASS can only be set by a Gate approval backed by verified
execution, test, and Git evidence; neither an Agent nor a direct status update
can set it.

## AgentRun

Normal execution:
`created -> preparing -> running -> changed -> testing -> test_pass -> evidence_collected
-> gate_pending -> completed`.

The test-failure branch uses `test_fail`; runtime or execution failure ends in
`failed`, and missing/unverifiable prerequisites end in `blocked`. Illegal
transitions raise an error. Each transition is persisted in the evidence record.

The Gate Engine alone chooses the final gate decision. PASS requires exactly the
registered G0-G6 gates, all passing, and a verifier-issued receipt bound to the
Task, AgentRun, Agent, test result, workspace commits, and persisted evidence.
Missing or unknown gates and unverified evidence block PASS.
