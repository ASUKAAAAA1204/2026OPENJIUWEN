#!/usr/bin/env python3
"""
SBOM 生成脚本 —— CycloneDX

PRD §9 纪律：SBOM 用 CycloneDX，W1 前生成第一次。
比赛硬约束：第三方代码 ≤30%（有效代码量计）。

用法：
    python scripts/generate_sbom.py
    → 输出 sbom.json（CycloneDX JSON 格式）
"""

import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    output = root / "sbom.json"

    try:
        # 方式 1：用 cyclonedx-python-lib 从 requirements.txt 生成
        # （需要 pip install cyclonedx-python-lib）
        result = subprocess.run(
            [
                sys.executable, "-m", "cyclonedx_py",
                "pip",
                "--requirements", str(root / "requirements.txt"),
                "--out", str(output),
                "--output-format", "json",
            ],
            capture_output=True, text=True, cwd=str(root),
        )
        if result.returncode == 0:
            print(f"[SBOM] Generated → {output}")
            return
        print(f"[SBOM] cyclonedx_py failed: {result.stderr}")

    except FileNotFoundError:
        print("[SBOM] cyclonedx_py not installed")

    # 方式 2：手拼一个最小 CycloneDX（以防万一）
    import json
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": [
            {
                "type": "library",
                "name": "pydantic",
                "version": "2.6+",
                "licenses": [{"license": {"id": "MIT"}}],
                "scope": "required",
            },
            {
                "type": "library",
                "name": "semgrep",
                "version": "1.80+",
                "licenses": [{"license": {"id": "LGPL-2.1-only"}}],
                "scope": "optional (CLI subprocess)",
                "notes": "called as independent CLI process, not linked",
            },
            {
                "type": "framework",
                "name": "Vue",
                "version": "3.4+",
                "licenses": [{"license": {"id": "MIT"}}],
                "scope": "required (UI)",
            },
            {
                "type": "library",
                "name": "uPlot",
                "version": "1.6+",
                "licenses": [{"license": {"id": "MIT"}}],
                "scope": "optional (curve)",
            },
        ],
        "metadata": {
            "component": {
                "type": "application",
                "name": "assurance-ledger",
                "version": "0.1.0",
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            },
        },
    }
    with open(output, "w", encoding="utf-8") as f:
        json.dump(sbom, f, indent=2, ensure_ascii=False)
    print(f"[SBOM] Manual fallback → {output}")
    print("[SBOM] Warning: manual SBOM — run 'pip install cyclonedx-python-lib' for auto-gen")


if __name__ == "__main__":
    main()
