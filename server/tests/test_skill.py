"""The agent skill bundle, its installers, and the bundled stdlib CLI run against the app."""

import importlib.util
import io
import json
import os
import tarfile
import zipfile

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from portofion_engine.metrics import summary
from portofion_server.settings import get_settings

FILES = {"portofion/SKILL.md", "portofion/reference.md", "portofion/scripts/portofion.py"}


def _zip(client, **headers) -> zipfile.ZipFile:
    r = client.get("/skill/portofion.zip", headers=headers)
    assert r.status_code == 200
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_bundle_contents(client):
    z = _zip(client)
    assert set(z.namelist()) == FILES
    skill = z.read("portofion/SKILL.md").decode()
    front = skill.split("---")[1].strip().splitlines()
    meta = dict(line.split(": ", 1) for line in front)
    assert meta["name"] == "portofion" and 0 < len(meta["description"]) <= 1024
    for name in FILES:
        text = z.read(name).decode()
        assert "{{BASE_URL}}" not in text and "{{SDK_WHEEL}}" not in text and "\r" not in text
        assert "http://testserver" in text
    with tarfile.open(fileobj=io.BytesIO(client.get("/skill/portofion.tar.gz").content)) as tgz:
        members = {m.name: m for m in tgz.getmembers() if m.isfile()}
        assert set(members) == FILES
        assert members["portofion/scripts/portofion.py"].mode & 0o111
        assert tgz.extractfile("portofion/SKILL.md").read().decode() == skill


def test_installers_and_base_url(client):
    sh = client.get("/skill/install.sh").text
    assert 'base="http://testserver"' in sh and "\r" not in sh and "{{" not in sh
    ps1 = client.get("/skill/install.ps1").text
    assert "$base = 'http://testserver'" in ps1 and "\r\n" in ps1
    assert client.get("/skill/install.bat").status_code == 404
    proxied = {"x-forwarded-host": "pf.lan:5173", "x-forwarded-proto": "https"}
    pinned = os.environ.pop("PORTOFION_BASE_URL")  # conftest pins it; the explicit setting always wins
    get_settings.cache_clear()
    try:
        # without PORTOFION_BASE_URL the URL the request came in on is used, also behind a proxy
        assert 'base="http://testserver"' in client.get("/skill/install.sh").text
        assert 'base="https://pf.lan:5173"' in client.get("/skill/install.sh", headers=proxied).text
        assert "https://pf.lan:5173/api/v1/ingest" in client.get("/llms.txt", headers=proxied).text
        os.environ["PORTOFION_BASE_URL"] = "http://pf.example:8000/"
        get_settings.cache_clear()
        assert 'base="http://pf.example:8000"' in client.get("/skill/install.sh", headers=proxied).text
        assert "at http://pf.example:8000 - daily" in _zip(client).read("portofion/SKILL.md").decode()
    finally:
        os.environ["PORTOFION_BASE_URL"] = pinned
        get_settings.cache_clear()


@pytest.fixture
def cli(client, app, tmp_path, monkeypatch, capsys):
    """The bundled script, loaded as a module, with its HTTP layer routed into the test app."""
    path = tmp_path / "portofion_skill.py"
    path.write_bytes(_zip(client).read("portofion/scripts/portofion.py"))
    spec = importlib.util.spec_from_file_location("portofion_skill", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tc = TestClient(app)

    def http(method, url, headers, body):
        r = tc.request(method, url, headers=headers, content=body)
        return r.status_code, r.content

    monkeypatch.setattr(mod, "http", http)
    monkeypatch.setenv("PORTOFION_CONFIG", str(tmp_path / "config.json"))
    for var in ("PORTOFION_URL", "PORTOFION_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    def run(*args):
        code = mod.main(list(args))
        out, err = capsys.readouterr()
        return code, (json.loads(out) if out.strip().startswith(("{", "[")) else out), (json.loads(err) if err else None)

    run.mod = mod
    return run


def test_configure_whoami(cli, setup, tmp_path):
    code, _, err = cli("whoami")
    assert code == 2 and "configure" in err["error"]["message"]
    code, out, _ = cli("configure", "--key", "pfn_bad00000_" + "x" * 32)
    assert code == 1  # verified before saving
    assert not (tmp_path / "config.json").exists()
    code, out, _ = cli("configure", "--key", setup["key"])
    assert code == 0 and out["workspace"] == setup["ws"].slug and out["url"] == "http://testserver"
    assert json.loads((tmp_path / "config.json").read_text())["api_key"] == setup["key"]
    code, out, _ = cli("whoami")
    assert code == 0 and out["workspace"] == setup["ws"].slug and "ingest" in out["scopes"]


def test_report_csv_roundtrip(cli, setup, tmp_path):
    cli("configure", "--key", setup["key"])
    idx = pd.bdate_range("2015-01-01", periods=2500)
    r = np.random.default_rng(5).normal(0.0004, 0.01, 2500)
    df = pd.DataFrame({"trade_date": idx.strftime("%Y%m%d"), "ret": r, "turnover": np.abs(r) * 10})
    df.loc[7, "ret"] = np.nan  # blank cell: skipped, not sent as 0
    p = tmp_path / "r.csv"
    df.to_csv(p, index=False)
    code, out, err = cli("report", "--strategy", "skl", "--version", "v1", "--params", '{"k": 1}',
                         "--csv", str(p), "--columns", "turnover", "--scalar", "capacity_usd=1e7",
                         "--tags", "cn,test", "--chunk", "1000")
    assert code == 0, err
    assert out["ref"] == "skl/main@v1" and out["calls"] == 6 and out["created"]["version"]
    assert out["url"] == "http://testserver/w/" + setup["ws"].slug + "/s/skl?b=main&v=v1"
    assert out["series"]["returns"]["inserted"] == 2499 and out["series"]["turnover"]["inserted"] == 2500
    exp = summary(pd.Series(np.delete(r, 7), index=idx.delete(7)))
    assert out["metrics"]["sharpe"] == pytest.approx(exp["sharpe"])
    # re-running is a no-op
    code, again, _ = cli("report", "--strategy", "skl", "--version", "v1", "--params", '{"k": 1}', "--csv", str(p))
    assert code == 0 and again["series"]["returns"]["unchanged"] == 2499 and again["series"]["returns"]["inserted"] == 0
    code, rows, _ = cli("status", "--strategy", "skl")
    assert rows == [{"ref": "skl/main@v1", "head": True, "stage": "research", "code_ref": None,
                     "first_date": "2015-01-01", "last_date": idx[-1].strftime("%Y-%m-%d"), "n": 2499,
                     "series": ["turnover"]}]
    back = tmp_path / "back.csv"
    code, out, _ = cli("series", "skl/main@v1", "--names", "returns", "--out", str(back))
    assert code == 0 and out["rows"] == 2499
    got = pd.read_csv(back, index_col=0)["returns"].to_numpy()
    np.testing.assert_allclose(got, np.delete(r, 7))
    # changed params under the same label -> VERSION_CONFLICT, nothing written
    code, _, err = cli("report", "--strategy", "skl", "--version", "v1", "--params", '{"k": 2}', "--csv", str(p))
    assert code == 1 and err["error"]["code"] == "VERSION_CONFLICT"


def test_report_percent_headerless_and_input_errors(cli, setup, tmp_path):
    cli("configure", "--key", setup["key"])
    p = tmp_path / "pct.csv"
    p.write_text("20240102,1.5\n20240103,-0.5%\n2024/01/04,0.25\n")
    code, _, err = cli("report", "--strategy", "pct", "--csv", str(p), "--percent")
    assert code == 0, err
    code, _, _ = cli("series", "pct", "--out", str(tmp_path / "o.csv"))
    np.testing.assert_allclose(pd.read_csv(tmp_path / "o.csv")["returns"], [0.015, -0.005, 0.0025])

    dup = tmp_path / "dup.csv"
    dup.write_text("date,r\n2024-01-02,0.01\n2024-01-02,0.02\n")
    code, _, err = cli("report", "--strategy", "pct", "--csv", str(dup))
    assert code == 2 and "twice" in err["error"]["message"]
    comma = tmp_path / "comma.csv"
    comma.write_text("date;r\n2024-01-05;0,01\n")
    code, _, err = cli("report", "--strategy", "pct", "--csv", str(comma))
    assert code == 2 and "decimal separator" in err["error"]["message"]
    code, _, err = cli("report", "--strategy", "pct", "--csv", str(p), "--value-col", "nope")
    assert code == 2


def test_weights(cli, setup, tmp_path):
    cli("configure", "--key", setup["key"])
    for s in ("wa", "wb"):
        cli("report", "--strategy", s, "--points", json.dumps([["2024-01-02", 0.01], ["2024-01-03", 0.02]]))
    setup["web"].post(f"/api/v1/w/{setup['ws'].slug}/portfolios", json={
        "slug": "alloc", "spec": {"scheme": "reported"}, "members": [{"ref": "wa"}, {"ref": "wb"}]}).raise_for_status()
    p = tmp_path / "w.csv"
    p.write_text("date,wa,wb\n2024-01-02,0.6,0.4\n")
    code, _, err = cli("weights", "--portfolio", "pf:alloc", "--csv", str(p))
    assert code == 0, err
    res = setup["web"].get(f"/api/v1/w/{setup['ws'].slug}/portfolios/alloc/result").json()
    assert res["returns"][0] == pytest.approx(0.01)
