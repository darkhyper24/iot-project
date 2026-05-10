import json
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_nodered_flows", ROOT / "scripts" / "generate_nodered_flows.py"
)
generate_nodered_flows = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(generate_nodered_flows)


def test_gateway_attribute_request_matches_thingsboard_431_parser():
    flow, _ = generate_nodered_flows._build_flow(1, "gw-token", "observer-password")
    node = next(n for n in flow if n.get("name") == "Build attr requests")
    func = node["func"]

    assert "client: false" in func
    assert "keys: keys" in func
    assert "shared: keys" not in func

    assert any(
        n.get("topic") == "v1/gateway/attributes/request"
        for n in flow
    ) or "v1/gateway/attributes/request" in json.dumps(flow)
