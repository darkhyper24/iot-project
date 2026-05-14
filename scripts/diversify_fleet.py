"""One-shot helper: diversify shared desired attrs across the 200 rooms so the
fleet shows visible dynamics on the dashboard.

Pattern: cycles HVAC mode through (ON, OFF, ECO) and target_temp across 18-27 °C
so the temperature column splits into three observable equilibrium clusters.
"""
import re
import requests

BASE = "http://localhost:9090"
USER = "admin@gmail.com"
PWD = "admins"
HVAC_CYCLE = ("ON", "OFF", "ECO")
ROOM_RE = re.compile(r"^b01-f\d{2}-r\d{3}$")


def main() -> None:
    jwt = requests.post(
        f"{BASE}/api/auth/login",
        json={"username": USER, "password": PWD},
        timeout=10,
    ).json()["token"]
    headers = {"X-Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}

    rooms = []
    page = 0
    while True:
        body = requests.get(
            f"{BASE}/api/tenant/devices?pageSize=200&page={page}",
            headers=headers,
            timeout=15,
        ).json()
        for d in body["data"]:
            if ROOM_RE.match(d["name"]):
                rooms.append((d["name"], d["id"]["id"]))
        if not body.get("hasNext"):
            break
        page += 1

    print(f"found {len(rooms)} rooms")

    fail = 0
    for i, (name, did) in enumerate(rooms):
        mode = HVAC_CYCLE[i % 3]
        target = 18 + (i % 10)
        r = requests.post(
            f"{BASE}/api/plugins/telemetry/DEVICE/{did}/attributes/SHARED_SCOPE",
            headers=headers,
            json={
                "hvac_mode_desired": mode,
                "target_temp_desired": target,
                "lighting_dimmer_desired": 50 + (i % 5) * 10,
            },
            timeout=10,
        )
        if not r.ok:
            fail += 1

    print(f"diversified; failures={fail}")
    print("wait ~30s, then refresh dashboard to see HVAC clusters")


if __name__ == "__main__":
    main()
