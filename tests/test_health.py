import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "src"))
import health

def test_health():
    r = health.handler({}, None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["status"] == "ok"
