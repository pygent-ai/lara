from __future__ import annotations

import hashlib
import os
from pathlib import Path



def project_lara_root(workspace_root: str | Path, user_lara_root: str | Path | None = None) -> Path:
    """Return the user-owned data directory for a canonical workspace path."""
    workspace = os.path.normcase(str(Path(workspace_root).expanduser().resolve()))
    project_id = hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:24]
    user_root = Path(user_lara_root) if user_lara_root is not None else Path.home() / ".lara"
    return user_root.expanduser().resolve() / "projects" / project_id
