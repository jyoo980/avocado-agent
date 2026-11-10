"""Write a copy of a C file that defines `__CPROVER` and `__CPROVER__` before its first line.

`cbmc file.c` defines `__CPROVER__`, so specs that an agent wrapped in `#ifdef __CPROVER__` (or
`#ifdef __CPROVER`) are visible when the agent runs CBMC on the C file. The kill-score pipeline
compiles with `goto-cc` (CBMC 6.9.0), which defines neither macro, so those specs disappear when
the file is scored. This script makes the scored copy see what the agent saw, without changing
`tools/run_cbmc.py`.

The defines are guarded so a file that already defines a macro is unchanged, and `#line 1`
after them keeps the line numbers of the original file (for CBMC output and `__LINE__`).
Files that mention neither macro are copied unchanged.

Usage: python3 define_cprover_macros.py <in.c> <out.c>
"""
import re
import sys
from pathlib import Path

PREFIX = (
    "#ifndef __CPROVER\n#define __CPROVER 1\n#endif\n"
    "#ifndef __CPROVER__\n#define __CPROVER__ 1\n#endif\n"
)

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
text = src.read_text(encoding="utf-8")
if re.search(r"\b__CPROVER(__)?\b", text):
    text = f'{PREFIX}#line 1 "{src.name}"\n{text}'
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(text, encoding="utf-8")
