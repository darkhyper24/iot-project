import json

from simulator.config.system_clients import load_system_clients


def test_load_system_clients_from_configured_file(tmp_path):
    path = tmp_path / "system_clients.json"
    path.write_text(
        json.dumps(
            {
                "fanout": {"username": "simulator_fanout", "password": "secret"},
                "ignored": "not-a-dict",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_system_clients({"phase3": {"system_clients_file": str(path)}})

    assert loaded == {"fanout": {"username": "simulator_fanout", "password": "secret"}}
