#!/usr/bin/env python3
"""Generate registry.json from all tools/*/TOOL.yaml files.

Run manually:  python generate_registry.py
Run in CI:     .github/workflows/build-registry.yml

Output: registry.json in the repo root (committed by CI).
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


# Patterns that indicate potentially dangerous code in @Tool files.
# These are checked during registry generation (CI) — not a replacement for
# human review, but catches obvious issues automatically.
_DANGEROUS_PATTERNS = [
    (r"\beval\s*\(", "eval() is not allowed in tool files"),
    (r"\bexec\s*\(", "exec() is not allowed in tool files"),
    (r"\b__import__\s*\(", "__import__() is not allowed — use regular imports"),
    (r"\bos\.system\s*\(", "os.system() is not allowed — use subprocess.run instead"),
    (r"\bsubprocess\.Popen\s*\(", "subprocess.Popen is not allowed — use subprocess.run instead"),
    (r"\bshell\s*=\s*True", "shell=True is not allowed (command injection risk)"),
    (r"\bcompile\s*\(.*\bexec\b", "compile()+exec is not allowed"),
    (r"\bctypes\b", "ctypes is not allowed in tool files"),
    (r"\bpickle\.loads?\s*\(", "pickle.load/loads is not allowed (deserialization risk)"),
]

_MAX_LINES = 500


def _check_code_safety(tool_name: str, filename: str, content: str, errors: list[str]) -> None:
    """Run static safety checks on a Python tool file."""
    import re

    lines = content.splitlines()

    # Max line count
    if len(lines) > _MAX_LINES:
        errors.append(f"{tool_name}/{filename}: exceeds {_MAX_LINES} line limit ({len(lines)} lines)")

    # Check for @Tool decorator
    if "@Tool" not in content:
        errors.append(f"{tool_name}/{filename}: no @Tool decorator found")

    # Dangerous pattern scan
    for pattern, message in _DANGEROUS_PATTERNS:
        if re.search(pattern, content):
            errors.append(f"{tool_name}/{filename}: {message}")


def main() -> None:
    tools_dir = Path(__file__).parent / "tools"
    registry: dict = {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tools": {},
    }

    errors: list[str] = []

    for tool_dir in sorted(tools_dir.iterdir()):
        if not tool_dir.is_dir() or tool_dir.name.startswith("_"):
            continue

        manifest_path = tool_dir / "TOOL.yaml"
        if not manifest_path.exists():
            errors.append(f"{tool_dir.name}: missing TOOL.yaml")
            continue

        raw = manifest_path.read_text()
        data = yaml.safe_load(raw)

        # Validate required fields
        name = data.get("name", "")
        if not name:
            errors.append(f"{tool_dir.name}: missing 'name' field")
            continue
        if name != tool_dir.name:
            errors.append(f"{tool_dir.name}: name '{name}' doesn't match directory")
            continue

        version = data.get("version", "")
        if not version:
            errors.append(f"{name}: missing 'version' field")
            continue

        source = data.get("source", {})
        if not source.get("type"):
            errors.append(f"{name}: missing 'source.type' field")
            continue

        # Compute SHA256 of the TOOL.yaml content
        sha256 = hashlib.sha256(raw.encode()).hexdigest()

        # For local tools (source.type == "local"), also hash each .py file
        file_hashes: dict[str, str] = {}
        if source.get("type") == "local":
            for filename in source.get("files", []):
                file_path = tool_dir / filename
                if not file_path.exists():
                    errors.append(f"{name}: source file '{filename}' not found")
                    continue
                file_content = file_path.read_text()
                file_hashes[filename] = hashlib.sha256(file_content.encode()).hexdigest()

                # Basic safety checks on code files
                if filename.endswith(".py"):
                    _check_code_safety(name, filename, file_content, errors)

        # Build registry entry
        entry: dict = {
            "latest": version,
            "description": data.get("description", ""),
            "tags": data.get("tags", []),
            "verified": data.get("verified", False),
            "source": source,
            "versions": {
                version: {"sha256": sha256, **({"file_hashes": file_hashes} if file_hashes else {})},
            },
        }

        if data.get("dependencies"):
            entry["dependencies"] = data["dependencies"]
        if data.get("credentials"):
            entry["credentials"] = data["credentials"]
        if data.get("credential_aliases"):
            entry["credential_aliases"] = data["credential_aliases"]
        if data.get("oauth"):
            entry["oauth"] = data["oauth"]

        registry["tools"][name] = entry

    if errors:
        print("Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    output = Path(__file__).parent / "registry.json"
    output.write_text(json.dumps(registry, indent=2) + "\n")
    print(f"Generated registry.json with {len(registry['tools'])} tools")


if __name__ == "__main__":
    main()
