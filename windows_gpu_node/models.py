import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    model: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=64_000)
    system_prompt: str | None = Field(default=None, max_length=16_000)
    max_tokens: int = Field(default=2048, ge=1, le=8192)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"request_id"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
