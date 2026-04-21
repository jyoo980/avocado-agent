from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CFunction:
    name: str
    file_path: Path
    start_line: int
    end_line: int

    def get_source_lines(self) -> list[str]:
        text = self.file_path.read_text()
        lines = text.splitlines()
        return lines[self.start_line - 1 : self.end_line]

    def get_source_code(self, include_line_numbers: bool = False) -> str:
        lines = self.get_source_lines()
        if not include_line_numbers:
            return "\n".join(lines)
        return "\n".join(f"{self.start_line + i:4d}  {line}" for i, line in enumerate(lines))

    def __repr__(self) -> str:
        return f"CFunction({self.name!r}, {self.file_path.name}:{self.start_line}-{self.end_line})"
