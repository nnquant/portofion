"""The agent skill: SKILL.md + reference.md + scripts/portofion.py, rendered for this server's URL.

Served as /skill/portofion.zip and /skill/portofion.tar.gz, with one-line installers at
/skill/install.sh and /skill/install.ps1.
"""

from __future__ import annotations

import io
import tarfile
import time
import zipfile
from functools import lru_cache
from pathlib import Path

DOCS = Path(__file__).parent / "agent_docs"
SKILL_SRC = DOCS / "skill" / "portofion"
NAME = "portofion"
EXECUTABLE = {"scripts/portofion.py"}


def render(text: str, base: str, wheel_url: str) -> str:
    return text.replace("\r\n", "\n").replace("{{BASE_URL}}", base).replace("{{SDK_WHEEL}}", wheel_url)


def llms_text(base: str, wheel_url: str) -> str:
    return render((DOCS / "llms.md").read_text(encoding="utf-8"), base, wheel_url)


def skill_files(base: str, wheel_url: str) -> dict[str, str]:
    """Relative path inside the skill folder -> rendered text."""
    out = {p.relative_to(SKILL_SRC).as_posix(): render(p.read_text(encoding="utf-8"), base, wheel_url)
           for p in sorted(SKILL_SRC.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    out["reference.md"] = llms_text(base, wheel_url)
    return out


@lru_cache(maxsize=16)
def bundle(fmt: str, base: str, wheel_url: str) -> bytes:
    files = skill_files(base, wheel_url)
    buf = io.BytesIO()
    now = time.time()
    if fmt == "zip":
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, text in files.items():
                info = zipfile.ZipInfo(f"{NAME}/{rel}", date_time=time.localtime(now)[:6])
                info.external_attr = (0o755 if rel in EXECUTABLE else 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, text.encode("utf-8"))
    else:
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            parents = {Path(rel).parent.as_posix() for rel in files} - {"."}
            for d in [NAME, *sorted(f"{NAME}/{p}" for p in parents)]:
                info = tarfile.TarInfo(d)
                info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, int(now)
                t.addfile(info)
            for rel, text in files.items():
                data = text.encode("utf-8")
                info = tarfile.TarInfo(f"{NAME}/{rel}")
                info.size, info.mtime = len(data), int(now)
                info.mode = 0o755 if rel in EXECUTABLE else 0o644
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def installer(kind: str, base: str) -> str:
    text = render((DOCS / "skill" / f"install.{kind}").read_text(encoding="utf-8"), base, "")
    return text.replace("\n", "\r\n") if kind == "ps1" else text
