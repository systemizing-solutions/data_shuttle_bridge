"""TypedDict definitions for data_shuttle_bridge."""

from typing import TypedDict, Literal, Dict, Any

Op = Literal["I", "U", "D"]


class ChangePayload(TypedDict):
    id: int
    table: str
    pk: int
    op: Op
    version: int
    data: Dict[str, Any] | None
    at: str | None
