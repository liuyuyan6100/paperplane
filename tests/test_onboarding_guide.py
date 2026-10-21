"""The standalone visual guide opens actual editor modules without starting work."""

import json
import subprocess
from pathlib import Path

from studio.preview_runtime import PreviewService

ROOT = Path(__file__).resolve().parents[1]


def test_onboarding_guide_navigation(tmp_path):
    service = PreviewService(tmp_path / "onboarding")
    service.start()
    try:
        fixture = tmp_path / "onboarding.json"
        fixture.write_text(json.dumps({
            "url": service.origin,
            "screenshot": str(ROOT / "test-results/m4.3.2-onboarding-guide.png"),
            "mobileScreenshot": str(ROOT / "test-results/m4.3.2-onboarding-mobile.png"),
        }), encoding="utf-8")
        result = subprocess.run(
            ["node", str(ROOT / "tests/verify_onboarding_guide.cjs"), str(fixture)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert service.app.db.one("SELECT COUNT(*) n FROM posts")["n"] == 0
    finally:
        service.stop()
