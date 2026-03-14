"""Subprocess sandbox for executing user-supplied Python code safely.

The user code is written to a temporary file and executed as a subprocess.
Input data is passed via stdin as JSON; the result is read from stdout as JSON.

The subprocess has a hard time limit (timeout_secs). If the process exceeds the
limit, it is terminated and an error is returned.

PERFORMANCE WARNING — do NOT use in the per-bar backtesting event loop.
    run_sandboxed() spawns a new Python subprocess per call (~50-200ms startup
    overhead). At 50,000 bars that is 2,500-10,000 seconds of overhead per run.

    Use ONLY for one-shot validation endpoints (POST /api/strategies/{id}/validate).
    For per-bar backtesting use CodeStrategy from code_strategy.py instead.

Usage
-----
    result = run_sandboxed(code, {"bar": {...}, "features": {...}})
    if result["success"]:
        output = result["output"]
    else:
        print(result["error"])
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Harness template uses __USER_CODE__ as a sentinel so dict literals with
# {braces} do not interfere with str.format().
_HARNESS_PREFIX = """\
import sys
import json
import traceback

def _run():
    input_data = json.loads(sys.stdin.read())
    result = {"success": False, "output": None, "error": None, "stdout": "", "stderr": ""}
    try:
"""

_HARNESS_SUFFIX = """
        result["success"] = True
        result["output"] = locals().get("output", None)
    except Exception as exc:
        result["error"] = traceback.format_exc()
    print(json.dumps(result))

_run()
"""


def _build_harness(code: str) -> str:
    """Wrap user code in the sandbox harness, indented into the try-block."""
    indented = "\n".join("        " + line for line in code.splitlines())
    return _HARNESS_PREFIX + indented + _HARNESS_SUFFIX


def run_sandboxed(
    code: str,
    input_data: dict,
    timeout_secs: float = 5.0,
) -> dict:
    """Run user code in a subprocess with a time limit.

    The user code receives ``input_data`` as a JSON-parsed variable named
    ``input_data`` in its local scope. To return a value the user code should
    assign it to a variable named ``output``.

    Parameters
    ----------
    code:
        Arbitrary Python source code string. Must use only stdlib — no
        imports from the backend package.
    input_data:
        Data to pass to the subprocess via stdin (JSON-serialised).
    timeout_secs:
        Maximum wall-clock seconds allowed for the subprocess to complete.

    Returns
    -------
    dict with keys:
        success (bool), output (any), error (str|None), stdout (str), stderr (str)
    """
    harness = _build_harness(code)

    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, prefix="sandbox_"
    ) as f:
        f.write(harness)
        tmp_path = f.name

    stdin_payload = json.dumps(input_data)

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if not stdout:
            return {
                "success": False,
                "output": None,
                "error": f"No output from subprocess. stderr: {stderr}",
                "stdout": stdout,
                "stderr": stderr,
            }

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "success": False,
                "output": None,
                "error": f"Could not parse subprocess output as JSON: {stdout[:500]}",
                "stdout": stdout,
                "stderr": stderr,
            }

        result.setdefault("stdout", stdout)
        result.setdefault("stderr", stderr)
        return result

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": None,
            "error": f"Sandbox timed out after {timeout_secs}s",
            "stdout": "",
            "stderr": "",
        }
    except Exception as exc:
        return {
            "success": False,
            "output": None,
            "error": f"Sandbox execution error: {exc}",
            "stdout": "",
            "stderr": "",
        }
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
