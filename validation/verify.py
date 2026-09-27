"""Validate synchronous executor behavior without contacting external services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

SOURCE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
CFG = json.loads(Path(__file__).with_name("case.json").read_text())
assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip() == CFG["source"]
print("TESTED SOURCE", CFG["source"], flush=True)
ENV = os.environ.copy()
ENV.update(AWS_EC2_METADATA_DISABLED="true", AWS_DEFAULT_REGION="us-west-2", AWS_ACCESS_KEY_ID="testing",
           AWS_SECRET_ACCESS_KEY="testing", AWS_SESSION_TOKEN="testing", MPLBACKEND="Agg", PYTHONDONTWRITEBYTECODE="1")
ENV.pop("PYTHONPATH", None)
PY = sys.executable
TEST = "tests/unit/util/test_dummy_executor.py"
PRODUCTION = SOURCE / "util/exec_util.py"
FIXED = PRODUCTION.read_bytes()
RESULTS = {}

def run(label, cmd, expected=0, cwd=SOURCE):
    p = subprocess.run(cmd, cwd=cwd, env=ENV, text=True, capture_output=True, timeout=180)
    output = p.stdout + p.stderr
    (OUT / (label + ".log")).write_text(output)
    print(label, "exit", p.returncode, output[-800:], flush=True)
    assert p.returncode == expected, (label, p.returncode, output[-6000:])
    return output

def test(label, failures=0, coverage=False, cwd=SOURCE, target=TEST):
    args = [PY, "-m", "pytest", target, "-q", "-o", "addopts=", "-o", "log_cli=false", "--junitxml", str(OUT / (label + ".xml"))]
    if coverage:
        args += ["--cov=util.exec_util", "--cov-branch", "--cov-report=term"]
    run(label, args, int(bool(failures)), cwd)
    tree = ET.parse(OUT / (label + ".xml"))
    counts = [sum(int(n.get(k, 0)) for n in tree.iter("testsuite")) for k in ["tests", "failures", "errors", "skipped"]]
    assert counts == [23, failures, 0, 0], counts
    RESULTS[label] = counts

run("dependencies", [PY, "-m", "pip", "freeze"])
run("dependency-check", [PY, "-m", "pip", "check"])
test("fixed", coverage=True)
run("coverage-json", [PY, "-m", "coverage", "json", "-o", str(OUT / "coverage.json")])
try:
    PRODUCTION.write_bytes(subprocess.check_output(["git", "show", CFG["base"] + ":util/exec_util.py"], cwd=SOURCE))
    test("original", 7)
finally:
    PRODUCTION.write_bytes(FIXED)
test("restored")
# The existing unittest fixture removes its temporary working directory. Run
# these unmodified cases in separate interpreters to keep their CWDs isolated.
for name in ["test_real_exit_1000_truncates_to_232_and_maps", "test_real_generic_failure_keeps_generic_error",
             "test_untruncated_1000_also_maps", "test_short_error_fits_figaro_elision"]:
    out = run(name, [PY, "-m", "unittest", "tests.unit.util.test_exec_util.TestCallNoerrExitCodes." + name])
    assert "Ran 1 test" in out and "OK" in out, out
run("test-lint", [PY, "-m", "ruff", "check", "--select", "E,F", "--line-length", "119", TEST])
run("source-restoration", ["git", "diff", "--exit-code"])
run("patch-check", ["git", "diff", "--check", CFG["base"], "HEAD"])
run("package-build", [PY, "-m", "build", "--outdir", str(OUT / "dist")])
wheel, = (OUT / "dist").glob("*.whl")
run("install-wheel", [PY, "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)])
with tempfile.TemporaryDirectory(prefix="opera-installed-") as td:
    outside = Path(td)
    shutil.copy2(SOURCE / TEST, outside / "test_dummy_executor.py")
    run("installed-import", [PY, "-c", "from pathlib import Path; import util.exec_util as m; p=Path(m.__file__).resolve(); print(p); assert 'site-packages' in p.parts"], cwd=outside)
    test("installed", cwd=outside, target="test_dummy_executor.py")
(OUT / "results.json").write_text(json.dumps(RESULTS, indent=2))
print("VALIDATION COMPLETE", CFG["source"], flush=True)
