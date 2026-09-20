"""Compact working memory for long-horizon computer use."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    phase: str = "observe"
    landmarks: dict[str, Any] = field(default_factory=dict)
    last_actions: list[str] = field(default_factory=list)
    last_hashes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stuck: int = 0

    def remember_action(self, act: str, result: str | None = None) -> None:
        line = act if not result else f"{act} → {result}"
        self.last_actions.append(line[:160])
        self.last_actions = self.last_actions[-8:]

    def remember_hash(self, h: str) -> None:
        self.last_hashes.append(h)
        self.last_hashes = self.last_hashes[-6:]

    def unchanged_streak(self) -> int:
        if len(self.last_hashes) < 2:
            return 0
        last = self.last_hashes[-1]
        n = 0
        for prev in reversed(self.last_hashes):
            if prev != last:
                break
            n += 1
        return n

    def prompt_block(self) -> str:
        parts = [f"Phase: {self.phase}"]
        if self.landmarks:
            bits = [f"{k}={v}" for k, v in list(self.landmarks.items())[:8]]
            parts.append("Landmarks: " + ", ".join(bits))
        if self.last_actions:
            parts.append("Recent actions: " + " | ".join(self.last_actions[-5:]))
        if self.notes:
            parts.append("Notes: " + "; ".join(self.notes[-3:]))
        return "\n".join(parts)
