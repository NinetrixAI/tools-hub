"""Ninetrix Hub Runtime — search and install tools/skills dynamically at runtime.

Allows agents to self-extend by discovering and installing new capabilities
from the Ninetrix Hub without rebuilding the container.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ninetrix import Tool

# ── Constants ────────────────────────────────────────────────────────────────

_TOOLS_HUB_BASE = "https://raw.githubusercontent.com/Ninetrix-ai/tools-hub/main"
_TOOLS_REGISTRY_URL = f"{_TOOLS_HUB_BASE}/registry.json"
_SKILLS_HUB_BASE = "https://raw.githubusercontent.com/Ninetrix-ai/skills-hub/main"

# Runtime state — tracks what was installed in this session
_installed_tools: dict[str, dict[str, Any]] = {}
_installed_skills: dict[str, str] = {}
_registry_cache: dict | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fetch(url: str) -> str | None:
    """Fetch URL content. Returns None on failure."""
    import httpx
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def _get_registry() -> dict:
    """Fetch and cache the Tool Hub registry."""
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache

    raw = _fetch(_TOOLS_REGISTRY_URL)
    if raw:
        try:
            _registry_cache = json.loads(raw)
            return _registry_cache
        except json.JSONDecodeError:
            pass

    return {"version": 2, "tools": {}}


def _score_match(query: str, name: str, desc: str, tags: list[str]) -> int:
    """Score a tool/skill match against a search query."""
    q = query.lower()
    score = 0
    if q in name.lower():
        score += 10
    if q == name.lower():
        score += 5
    if q in desc.lower():
        score += 5
    if any(q in t.lower() for t in tags):
        score += 3
    return score


def _run_install(cmd: str) -> tuple[bool, str]:
    """Run an install command. Returns (success, output)."""
    try:
        result = subprocess.run(
            cmd.split(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as exc:
        return False, str(exc)


# ── @Tool functions ──────────────────────────────────────────────────────────

@Tool
def hub_search(query: str) -> str:
    """Search the Ninetrix Hub for tools and skills matching a query.

    Args:
        query: Search term (e.g. "github", "email", "database", "browser").
    """
    registry = _get_registry()
    tools = registry.get("tools", {})

    results: list[dict[str, Any]] = []
    for name, raw in tools.items():
        score = _score_match(
            query, name, raw.get("description", ""), raw.get("tags", [])
        )
        if score > 0:
            source = raw.get("source", {})
            deps = raw.get("dependencies", {})
            creds = raw.get("credentials", {})
            results.append({
                "name": name,
                "description": raw.get("description", ""),
                "type": source.get("type", "unknown"),
                "tags": raw.get("tags", []),
                "verified": raw.get("verified", False),
                "has_companion_skill": bool(raw.get("skill_set")),
                "needs_credentials": list(creds.keys()) if creds else [],
                "installed": name in _installed_tools,
                "_score": score,
            })

    results.sort(key=lambda x: -x["_score"])
    for r in results:
        del r["_score"]

    if not results:
        return f"No tools found for '{query}'. Try broader terms like 'search', 'database', 'email', 'browser'."

    lines = [f"Found {len(results)} result(s) for '{query}':\n"]
    for r in results:
        status = " [INSTALLED]" if r["installed"] else ""
        creds = f" (needs: {', '.join(r['needs_credentials'])})" if r["needs_credentials"] else ""
        skill = " +skill" if r["has_companion_skill"] else ""
        lines.append(
            f"  {r['name']}{status} — {r['description']}\n"
            f"    type: {r['type']} | tags: {', '.join(r['tags'])}{creds}{skill}"
        )

    lines.append("\nUse hub_tool_info(name) for details. Use hub_install_tool(name) to install.")
    return "\n".join(lines)


@Tool
def hub_tool_info(name: str) -> str:
    """Get detailed information about a specific tool from the Hub.

    Args:
        name: Tool name (e.g. "github", "slack", "ocr").
    """
    registry = _get_registry()
    tools = registry.get("tools", {})
    raw = tools.get(name)

    if raw is None:
        return f"Tool '{name}' not found in the Hub. Use hub_search() to find available tools."

    source = raw.get("source", {})
    deps = raw.get("dependencies", {})
    creds = raw.get("credentials", {})
    aliases = raw.get("credential_aliases", {})
    skills = raw.get("skill_set", [])

    lines = [
        f"# {name} v{raw.get('latest', '1.0.0')}",
        f"  {raw.get('description', '')}",
        f"  Verified: {'yes' if raw.get('verified') else 'no'}",
        f"  Tags: {', '.join(raw.get('tags', []))}",
        f"\n  Source type: {source.get('type', 'unknown')}",
    ]

    if source.get("type") == "mcp":
        lines.append(f"  Package: {source.get('package', '')}")
        lines.append(f"  Runner: {source.get('runner', '')}")
        if source.get("args"):
            lines.append(f"  Args: {source['args']}")
    elif source.get("type") == "openapi":
        lines.append(f"  Spec URL: {source.get('spec_url', '')}")
        lines.append(f"  Base URL: {source.get('base_url', '')}")
    elif source.get("type") == "local":
        lines.append(f"  Files: {', '.join(source.get('files', []))}")

    if deps:
        lines.append("\n  Dependencies:")
        if deps.get("pip"):
            lines.append(f"    pip: {', '.join(deps['pip'])}")
        if deps.get("apt"):
            lines.append(f"    apt: {', '.join(deps['apt'])}")
        if deps.get("npm"):
            lines.append(f"    npm: {', '.join(deps['npm'])}")

    if creds:
        lines.append("\n  Credentials required:")
        for var, spec in creds.items():
            label = spec.get("label", var) if isinstance(spec, dict) else spec
            required = spec.get("required", True) if isinstance(spec, dict) else True
            present = bool(os.environ.get(var))
            # Check aliases too
            if not present and aliases:
                for alias, canon in aliases.items():
                    if canon == var and os.environ.get(alias):
                        present = True
                        break
            status = "SET" if present else "MISSING"
            lines.append(f"    {var}: {label} [{status}]")

    if skills:
        lines.append(f"\n  Companion skills: {', '.join(skills)}")

    installed = name in _installed_tools
    lines.append(f"\n  Status: {'INSTALLED' if installed else 'NOT INSTALLED'}")

    return "\n".join(lines)


@Tool
def hub_install_tool(name: str, skip_deps: bool = False) -> str:
    """Install a tool from the Hub into the running agent.

    Downloads the tool, installs dependencies, and makes it available.
    MCP tools become available via the MCP gateway.
    Local @Tool functions are loaded into the runtime registry.

    Args:
        name: Tool name to install (e.g. "github", "ocr", "slack").
        skip_deps: Skip dependency installation if True.
    """
    if name in _installed_tools:
        return f"Tool '{name}' is already installed."

    registry = _get_registry()
    tools = registry.get("tools", {})
    raw = tools.get(name)

    if raw is None:
        return f"Tool '{name}' not found. Use hub_search() to find tools."

    source = raw.get("source", {})
    source_type = source.get("type", "unknown")
    deps = raw.get("dependencies", {})
    creds = raw.get("credentials", {})
    aliases = raw.get("credential_aliases", {})

    messages: list[str] = []

    # Check credentials (warn, don't block)
    missing_creds = []
    for var in creds:
        present = bool(os.environ.get(var))
        if not present and aliases:
            for alias, canon in aliases.items():
                if canon == var and os.environ.get(alias):
                    present = True
                    break
        if not present:
            missing_creds.append(var)

    if missing_creds:
        cred_lines = "\n".join(
            f"  {v}: {creds[v].get('label', v) if isinstance(creds[v], dict) else v}"
            for v in missing_creds
        )
        messages.append(
            f"Warning: missing credentials (some features may not work):\n"
            f"{cred_lines}\n"
            f"Set these environment variables when needed."
        )

    # Install dependencies
    if not skip_deps and deps:
        if deps.get("apt"):
            ok, out = _run_install("apt-get update -qq")
            if ok:
                ok, out = _run_install(
                    "apt-get install -y -qq " + " ".join(deps["apt"])
                )
            if not ok:
                messages.append(f"Warning: apt install failed: {out[:200]}")

        if deps.get("pip"):
            ok, out = _run_install(
                sys.executable + " -m pip install -q " + " ".join(deps["pip"])
            )
            if not ok:
                messages.append(f"Warning: pip install failed: {out[:200]}")

        if deps.get("npm"):
            ok, out = _run_install(
                "npm install -g " + " ".join(deps["npm"])
            )
            if not ok:
                messages.append(f"Warning: npm install failed: {out[:200]}")

    # Source-specific installation
    if source_type == "local":
        # Download and load @Tool Python files
        files = source.get("files", [])
        versions = raw.get("versions", {})
        latest_ver = raw.get("latest", "1.0.0")
        ver_entry = versions.get(latest_ver, {})
        file_hashes = ver_entry.get("file_hashes", {})

        tools_dir = Path("/app/tools/hub")
        tools_dir.mkdir(parents=True, exist_ok=True)

        for filename in files:
            url = f"{_TOOLS_HUB_BASE}/tools/{name}/{filename}"
            content = _fetch(url)
            if content is None:
                return f"Failed to download {filename} from Hub."

            # Verify SHA256
            expected = file_hashes.get(filename)
            if expected:
                actual = hashlib.sha256(content.encode()).hexdigest()
                if actual != expected:
                    return (
                        f"SHA256 mismatch for {filename}! File may be tampered.\n"
                        f"  Expected: {expected}\n"
                        f"  Actual:   {actual}"
                    )

            dest = tools_dir / filename
            dest.write_text(content)

            # Load into runtime registry
            if filename.endswith(".py"):
                try:
                    from ninetrix.discover import load_local_tools
                    load_local_tools([str(dest)])
                    messages.append(f"Loaded {filename} into tool registry.")
                except Exception as exc:
                    messages.append(f"Warning: failed to load {filename}: {exc}")

    elif source_type == "mcp":
        # MCP tools route through the gateway — just record it
        messages.append(
            f"MCP tool '{name}' registered. "
            f"Calls will route through the MCP gateway.\n"
            f"  Package: {source.get('package', 'unknown')}\n"
            f"  Note: The MCP worker must have this server configured."
        )

    elif source_type == "openapi":
        messages.append(
            f"OpenAPI tool '{name}' registered.\n"
            f"  Spec: {source.get('spec_url', '')}\n"
            f"  Base: {source.get('base_url', '')}"
        )

    elif source_type == "cli":
        install_cmd = source.get("install", "")
        if install_cmd and not skip_deps:
            ok, out = _run_install(install_cmd)
            if ok:
                messages.append(f"CLI tool '{name}' installed.")
            else:
                messages.append(f"Warning: install failed: {out[:200]}")

    # Record installation
    _installed_tools[name] = {
        "source_type": source_type,
        "description": raw.get("description", ""),
        "skill_set": raw.get("skill_set", []),
    }

    # Suggest companion skills
    skill_set = raw.get("skill_set", [])
    if skill_set:
        messages.append(
            f"\nCompanion skill(s) available: {', '.join(skill_set)}\n"
            f"Use hub_install_skill() to load them for better usage patterns."
        )

    header = f"Installed '{name}' ({source_type})"
    return header + "\n" + "\n".join(messages) if messages else header


@Tool
def hub_install_skill(name: str) -> str:
    """Install a skill from the Skills Hub into the running agent.

    Downloads the skill markdown and appends it to the system prompt,
    giving the agent new knowledge and patterns.

    Args:
        name: Skill name (e.g. "gh-master", "code-review", "error-handling-playbook").
    """
    if name in _installed_skills:
        return f"Skill '{name}' is already installed."

    # Fetch SKILL.md from GitHub
    url = f"{_SKILLS_HUB_BASE}/skills/{name}/SKILL.md"
    content = _fetch(url)
    if content is None:
        return (
            f"Skill '{name}' not found in the Skills Hub.\n"
            f"Available skills can be found alongside tools via hub_search()."
        )

    # Strip YAML frontmatter — keep only the markdown body
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()

    if not body:
        return f"Skill '{name}' has no content."

    # Store it
    _installed_skills[name] = body

    return (
        f"Skill '{name}' installed ({len(body.split())} words).\n"
        f"The skill instructions are now available. "
        f"Key sections have been loaded into your context."
        f"\n\n--- SKILL: {name} ---\n{body}"
    )


@Tool
def hub_list_installed() -> str:
    """List all tools and skills installed in this runtime session."""
    lines: list[str] = []

    if _installed_tools:
        lines.append("Installed tools:")
        for name, info in _installed_tools.items():
            lines.append(f"  {name} ({info['source_type']}) — {info['description']}")
    else:
        lines.append("No tools installed at runtime.")

    lines.append("")

    if _installed_skills:
        lines.append("Installed skills:")
        for name in _installed_skills:
            lines.append(f"  {name}")
    else:
        lines.append("No skills installed at runtime.")

    return "\n".join(lines)


@Tool
def hub_browse_categories(category: str = "") -> str:
    """Browse tools in the Hub by category or list all categories.

    Args:
        category: Filter by tag/category (e.g. "search", "database", "developer"). Leave empty to see all categories.
    """
    registry = _get_registry()
    tools = registry.get("tools", {})

    if not category:
        # Collect all unique tags
        tag_counts: dict[str, int] = {}
        for raw in tools.values():
            for tag in raw.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        sorted_tags = sorted(tag_counts.items(), key=lambda x: -x[1])
        lines = ["Available categories:\n"]
        for tag, count in sorted_tags:
            lines.append(f"  {tag} ({count} tool{'s' if count != 1 else ''})")
        lines.append("\nUse hub_browse_categories(category='search') to see tools in a category.")
        return "\n".join(lines)

    # Filter by category
    matches = []
    for name, raw in sorted(tools.items()):
        if category.lower() in [t.lower() for t in raw.get("tags", [])]:
            source = raw.get("source", {})
            installed = name in _installed_tools
            status = " [INSTALLED]" if installed else ""
            matches.append(
                f"  {name}{status} ({source.get('type', '?')}) — {raw.get('description', '')}"
            )

    if not matches:
        return f"No tools found in category '{category}'. Use hub_browse_categories() to see all categories."

    return f"Tools in '{category}' ({len(matches)}):\n\n" + "\n".join(matches)
