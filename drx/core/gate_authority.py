import hashlib
import hmac
import json
import secrets
from typing import cast

_APPROVAL_KEY = secrets.token_bytes(32)


def _issue_gate_approval(
    *,
    task_id: str,
    run_id: str,
    agent_id: str,
    run_status: str,
    initial_commit: str,
    final_commit: str,
    workspace: str,
    evidence_type: str,
    evidence_producer: str,
    evidence_result: str,
    verification_digest: str,
    metadata_json: str,
) -> str:
    payload = json.dumps(
        (
            task_id,
            run_id,
            agent_id,
            run_status,
            initial_commit,
            final_commit,
            workspace,
            evidence_type,
            evidence_producer,
            evidence_result,
            verification_digest,
            metadata_json,
        ),
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(_APPROVAL_KEY, payload, hashlib.sha256).hexdigest()


def verify_gate_approval(*values: object) -> int:
    if len(values) != 13 or not all(isinstance(value, str) for value in values):
        return 0
    fields = cast(tuple[str, ...], values)
    signature = fields[-1]
    expected = _issue_gate_approval(
        task_id=fields[0],
        run_id=fields[1],
        agent_id=fields[2],
        run_status=fields[3],
        initial_commit=fields[4],
        final_commit=fields[5],
        workspace=fields[6],
        evidence_type=fields[7],
        evidence_producer=fields[8],
        evidence_result=fields[9],
        verification_digest=fields[10],
        metadata_json=fields[11],
    )
    return int(hmac.compare_digest(expected, signature))
