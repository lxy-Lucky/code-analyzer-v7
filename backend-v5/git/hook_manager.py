from __future__ import annotations
from pathlib import Path

HOOK_MARKER = "# code-analyzer-hook"
HOOK_TEMPLATE = """{marker}
#!/bin/sh
curl -s -X POST http://{host}:{port}/api/git/hook-trigger \\
  -H "Content-Type: application/json" \\
  -d '{{"repo_path": "{repo_path}"}}' > /dev/null 2>&1 || true
"""


def install_hook(repo_path: str, host: str = "localhost", port: int = 8000) -> dict:
    hooks_dir = Path(repo_path) / ".git" / "hooks"
    if not hooks_dir.exists():
        return {"success": False, "error": "Not a git repository"}

    hook_file = hooks_dir / "post-commit"
    content = HOOK_TEMPLATE.format(
        marker=HOOK_MARKER,
        host=host,
        port=port,
        repo_path=repo_path.replace("\\", "/"),
    )

    if hook_file.exists():
        existing = hook_file.read_text()
        if HOOK_MARKER in existing:
            return {"success": True, "status": "already_installed"}
        # append to existing hook
        content = existing.rstrip() + "\n\n" + content

    hook_file.write_text(content)
    hook_file.chmod(0o755)
    return {"success": True, "status": "installed"}


def uninstall_hook(repo_path: str) -> dict:
    hook_file = Path(repo_path) / ".git" / "hooks" / "post-commit"
    if not hook_file.exists():
        return {"success": True, "status": "not_found"}

    content = hook_file.read_text()
    if HOOK_MARKER not in content:
        return {"success": True, "status": "not_installed"}

    # remove our block
    lines = content.splitlines(keepends=True)
    filtered = []
    skip = False
    for line in lines:
        if HOOK_MARKER in line:
            skip = True
        if not skip:
            filtered.append(line)
        # stop skipping after our block (next blank line after curl)
        if skip and line.strip() == "" and len(filtered) > 0:
            skip = False

    new_content = "".join(filtered).strip()
    if new_content:
        hook_file.write_text(new_content + "\n")
    else:
        hook_file.unlink()
    return {"success": True, "status": "uninstalled"}


def is_hook_installed(repo_path: str) -> bool:
    hook_file = Path(repo_path) / ".git" / "hooks" / "post-commit"
    if not hook_file.exists():
        return False
    return HOOK_MARKER in hook_file.read_text()
