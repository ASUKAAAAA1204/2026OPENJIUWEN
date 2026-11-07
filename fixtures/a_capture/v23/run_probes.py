"""Offline A-side openJiuwen v2.3 probes. Each execution has its own working directory."""
import argparse
import asyncio
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys
from tempfile import mkdtemp


ROOT = Path(__file__).resolve().parents[3]


async def run(output: Path) -> bool:
    output.mkdir(parents=True, exist_ok=True)
    work = Path(mkdtemp(prefix="run-", dir=output)).resolve()
    os.chdir(work)  # Isolate SDK logs and the two MCP tools' data per invocation.
    sys.path.insert(0, str(ROOT / "src"))
    report = {"format": "a-v23-probes-v1", "runtime": {"python": platform.python_version(),
              "openjiuwen": version("openjiuwen"), "mcp": version("mcp")}, "status": "failed"}
    try:
        if report["runtime"]["openjiuwen"] != "0.1.20.post1":
            raise RuntimeError("Run these probes with openjiuwen==0.1.20.post1 (PRD v2.3).")
        from resume_probe import observe_resume
        from tool_probe import observe_tools

        async with asyncio.timeout(45):
            report["N4"] = await observe_resume(work)
        async with asyncio.timeout(75):
            report["tools"] = await observe_tools(work)
        tools = report["tools"]
        if (report["N4"]["lifecycle_verified"] and tools["approval_verified"]
                and tools["mcp_writes_verified"] and tools["unanswered_window_measured"]):
            report["status"] = "observations_complete"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        destination = work / "report.json"
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"{report['status']}  {destination}", flush=True)
    return report["status"] == "observations_complete"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/a-v23-probes")
    sys.exit(0 if asyncio.run(run(parser.parse_args().output.resolve())) else 1)
