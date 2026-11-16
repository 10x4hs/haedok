"""hardening: Pillow 는 scripts/ 전용 — 앱을 띄울 때 필요하지 않다."""

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pillow_is_an_optional_scripts_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert not any(d.lower().startswith("pillow") for d in project["dependencies"])
    assert any(d.lower().startswith("pillow") for d in project["optional-dependencies"]["scripts"])
    # Vercel 이 설치하는 목록에도 없다
    assert "pillow" not in (ROOT / "requirements.txt").read_text().lower()


def test_app_starts_without_importing_pil():
    code = (
        "import sys, importlib.abc\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'PIL' or name.startswith('PIL.'):\n"
        "            raise ImportError('PIL blocked')\n"
        "sys.meta_path.insert(0, Block())\n"
        "import app.main\n"
        "assert 'PIL' not in sys.modules\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"DATABASE_URL": "sqlite+aiosqlite://", "SECRET_KEY": "x", "PATH": "/usr/bin:/bin"},
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
