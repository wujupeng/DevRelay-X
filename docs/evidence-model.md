# Evidence Model

Evidence is typed, producer-attributed, and has a result. Commit, test, review,
policy, and artifact evidence are separate inputs. Evidence must be attributable
and reproducible before Gate Engine evaluation.

For Local Agent PASS, the execution record must belong to the same Task, Agent,
and AgentRun as the runtime result. The verifier checks the persisted record,
the passing pytest result, the managed workspace marker, the initial and final
Git commits, the current HEAD, the committed file list, and the actual Git diff.
The Gate Engine accepts only a verifier-issued receipt; Agent-provided PASS
claims or a manually assembled Gate check map are insufficient.
