"""Optional semantic enrichment beside the C/C++ CST front-ends.

A second parser may contribute edges only between nodes the primary parser already
 grounded. USRs are identities to check, never authority to invent PKG nodes. This
module deliberately declines templates, anonymous scopes and signature-qualified
identities that the existing name-keyed graph cannot distinguish.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_IDENTIFIER = r"[A-Za-z_][A-Za-z_0-9]*"
_CPP_FUNCTION = re.compile(rf"c:((?:@(?:N|S)@{_IDENTIFIER})*)@F@({_IDENTIFIER})#?")
_C_FUNCTION = re.compile(rf"c:@F@({_IDENTIFIER})")
_C_STATIC = re.compile(rf"c:([^@]+)@F@({_IDENTIFIER})")


def usr_to_id(usr: str, *, language: str, rel: str) -> str | None:
    """Map only recognised, name-keyed function USRs; reject everything else.

    ``rel`` is the declaration's repository-relative path, not the caller's TU.
    C statics use that full path (the USR itself contains only the basename).
    Function parameter encodings are deliberately refused: stripping arbitrary USR
    suffixes could turn a template/overload identity into a plausible wrong name.
    The caller must still verify that the result is a grounded function.
    """
    path = PurePosixPath(rel)
    if not rel or path.is_absolute() or ".." in path.parts or "\\" in rel or ":" in rel:
        return None
    if language == "c":
        match = _C_FUNCTION.fullmatch(usr)
        if match:
            return f"c:{match[1]}"
        match = _C_STATIC.fullmatch(usr)
        if match and match[1] == path.name:
            return f"c:{path.as_posix()}::{match[2]}"
    elif language == "cpp":
        match = _CPP_FUNCTION.fullmatch(usr)
        if match:
            parents = re.findall(r"@(?:N|S)@([^@]+)", match[1])
            return "cpp:" + "::".join([*parents, match[2]])
    return None
