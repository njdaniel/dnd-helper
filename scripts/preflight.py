#!/usr/bin/env python3
"""Check that the configured model provider is ready before starting the bot."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import dotenv_values

DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_HOST": "http://localhost:11434",
    "OLLAMA_MODEL_DIALOGUE": "qwen3.6:27b",
    "OLLAMA_MODEL_UTILITY": "qwen3.6:27b",
    "OLLAMA_MODEL_EPIC": "qwen3.6:27b",
    "ANTHROPIC_MODEL_DIALOGUE": "claude-sonnet-5",
    "ANTHROPIC_MODEL_UTILITY": "claude-haiku-4-5",
    "ANTHROPIC_MODEL_EPIC": "claude-opus-5",
}
TIERS = ("DIALOGUE", "UTILITY", "EPIC")


def load_config(
    environ: Mapping[str, str] | None = None, env_file: Path = Path(".env")
) -> dict[str, str]:
    """Load provider configuration with the same environment-over-file priority."""
    environment = os.environ if environ is None else environ
    file_values = dotenv_values(env_file) if env_file.is_file() else {}
    config: dict[str, str] = {}
    for name, default in DEFAULTS.items():
        config[name] = environment.get(name) or file_values.get(name) or default
    config["ANTHROPIC_API_KEY"] = (
        environment.get("ANTHROPIC_API_KEY")
        or file_values.get("ANTHROPIC_API_KEY")
        or ""
    )
    return config


def model_mapping(config: Mapping[str, str], provider: str) -> dict[str, str]:
    """Return the configured tier-to-model mapping for a provider."""
    prefix = provider.upper()
    return {tier.lower(): config[f"{prefix}_MODEL_{tier}"] for tier in TIERS}


def fetch_ollama_models(host: str, timeout: float = 5.0) -> set[str]:
    """Fetch the model names exposed by the configured Ollama server."""
    request = Request(
        f"{host.rstrip('/')}/api/tags", headers={"Accept": "application/json"}
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured host
        payload: Any = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Ollama returned an unexpected response from /api/tags")
    return {
        model["name"]
        for model in payload["models"]
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    }


def free_vram() -> list[tuple[str, int]] | None:
    """Return free VRAM in MiB per NVIDIA GPU, or None when it is not detectable."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    devices: list[tuple[str, int]] = []
    for line in result.stdout.splitlines():
        try:
            name, free_mib = line.rsplit(",", maxsplit=1)
            devices.append((name.strip(), int(free_mib.strip())))
        except ValueError:
            return None
    return devices or None


def check_ollama(config: Mapping[str, str]) -> list[str]:
    """Run Ollama availability checks and return actionable failures."""
    host = config["OLLAMA_HOST"]
    try:
        installed = fetch_ollama_models(host)
    except (
        HTTPError,
        URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"FAIL Ollama is not reachable at {host}: {error}")
        print(
            "     Fix: start it with `systemctl --user start ollama` or `ollama serve`,"
        )
        print("     then correct OLLAMA_HOST if Ollama is running elsewhere.")
        return [f"Ollama unreachable at {host}"]

    print(f"PASS Ollama reachable at {host}")
    failures: list[str] = []
    for model in dict.fromkeys(model_mapping(config, "ollama").values()):
        if model in installed:
            print(f"PASS model installed: {model}")
        else:
            print(f"FAIL model not installed: {model}")
            print(f"     Fix: `ollama pull {model}`")
            failures.append(f"missing Ollama model {model}")

    detected_vram = free_vram()
    if detected_vram is None:
        print("INFO free VRAM unavailable (install/run `nvidia-smi` for this check)")
    else:
        for name, free_mib in detected_vram:
            print(f"INFO free VRAM: {name}: {free_mib / 1024:.1f} GiB")
    return failures


def check_anthropic(config: Mapping[str, str]) -> list[str]:
    """Check Anthropic credentials without making a metered API request."""
    if config["ANTHROPIC_API_KEY"]:
        print("PASS ANTHROPIC_API_KEY is set")
        print("INFO Ollama checks skipped (LLM_PROVIDER=anthropic)")
        return []
    print("FAIL ANTHROPIC_API_KEY is not set")
    print("     Fix: add `ANTHROPIC_API_KEY=...` to .env")
    print("INFO Ollama checks skipped (LLM_PROVIDER=anthropic)")
    return ["missing ANTHROPIC_API_KEY"]


def main() -> int:
    """Print the provider readiness report and return a gate-friendly status."""
    config = load_config()
    provider = config["LLM_PROVIDER"].lower()
    print(f"Configured provider: {provider}")
    if provider not in {"ollama", "anthropic"}:
        print(f"FAIL unsupported LLM_PROVIDER: {provider}")
        print("     Fix: set `LLM_PROVIDER=ollama` or `LLM_PROVIDER=anthropic`")
        return 1

    print("Tier → model mapping:")
    for tier, model in model_mapping(config, provider).items():
        print(f"  {tier}: {model}")

    failures = check_ollama(config) if provider == "ollama" else check_anthropic(config)
    if failures:
        print(f"Preflight failed ({len(failures)} problem(s)).")
        return 1
    print("Preflight passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
