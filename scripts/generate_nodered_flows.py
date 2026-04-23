#!/usr/bin/env python3
"""
Generate Node-RED flows.json for all 10 floor gateways.

What it does:
  1. Creates one ThingsBoard gateway device per floor (gw-floor-01 … gw-floor-10)
     with isGateway=true so ThingsBoard tracks connected devices automatically.
  2. Fetches each gateway device's access token.
  3. Writes a flows.json to gateways/floor-XX/ for each floor.

Each flow:
  - Subscribes to campus/b01/fXX/# on HiveMQ (campus_observer read-only account)
  - Routes /telemetry messages to ThingsBoard via v1/gateway/telemetry
  - Routes /heartbeat messages to ThingsBoard via v1/gateway/attributes
  - Routes /lwt (last-will) messages as a "status=offline" attribute update

Run from repo root:
    python scripts/generate_nodered_flows.py --url http://localhost:9090
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

import requests

TB_URL_DEFAULT = "http://localhost:9090"
HIVEMQ_HOST = "hivemq"
HIVEMQ_PORT = 1883
TB_MQTT_HOST = "thingsboard"
TB_MQTT_PORT = 1883
OBSERVER_USER = "campus_observer"


def _jwt(base_url: str, username: str, password: str) -> str:
    r = requests.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def _headers(token: str) -> dict:
    return {"X-Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _upsert_gateway_device(base_url: str, token: str, name: str) -> str:
    """Create or find a gateway device, return its entity ID."""
    # search first
    r = requests.get(
        f"{base_url}/api/tenant/devices?pageSize=10&page=0&textSearch={name}",
        headers=_headers(token),
        timeout=15,
    )
    r.raise_for_status()
    for dev in r.json().get("data", []):
        if dev["name"] == name:
            return dev["id"]["id"]

    # create
    payload = {
        "name": name,
        "label": name,
        "additionalInfo": {"gateway": True, "description": f"Node-RED floor gateway for {name}"},
    }
    r = requests.post(f"{base_url}/api/device", headers=_headers(token), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()["id"]["id"]


def _get_access_token(base_url: str, token: str, device_id: str) -> str:
    r = requests.get(
        f"{base_url}/api/device/{device_id}/credentials",
        headers=_headers(token),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["credentialsId"]


def _nid() -> str:
    """Short deterministic-ish node ID for Node-RED."""
    return uuid.uuid4().hex[:16]


def _build_flow(floor: int, gw_token: str, observer_password: str) -> tuple[list[dict], dict]:
    """Returns (nodes, credentials_map) where credentials_map is for flows_cred.json."""
    floor_str = f"{floor:02d}"
    topic_sub = f"campus/b01/f{floor_str}/#"
    hivemq_broker_id = _nid()
    tb_broker_id = _nid()
    mqtt_in_id = _nid()
    func_id = _nid()
    out_telemetry_id = _nid()
    out_attr_id = _nid()
    tab_id = _nid()
    inject_connect_id = _nid()
    func_connect_id = _nid()
    out_connect_id = _nid()

    func_code = f"""
// topic: campus/b01/f{floor_str}/rNNN/telemetry|heartbeat|lwt
var parts = msg.topic.split('/');
// parts: ['campus','b01','f{floor_str}','rNNN','<type>']
if (parts.length < 5) return null;

var roomNum     = parts[3];                                        // e.g. r109
var msgType     = parts[4];                                        // telemetry | heartbeat | lwt
var rawNum      = parseInt(roomNum.slice(1));                      // 109
var roomOnFloor = rawNum - {floor} * 100;                         // 9
var roomStr     = 'r' + String(roomOnFloor).padStart(3, '0');     // "r009"
var device      = 'b01-f{floor_str}-' + roomStr;                  // e.g. b01-f01-r009

var data;
try {{
    data = (typeof msg.payload === 'string') ? JSON.parse(msg.payload) : msg.payload;
}} catch(e) {{
    return null;
}}

if (msgType === 'telemetry') {{
    // TB gateway telemetry format: {{ "deviceName": [{{ "ts": ms, "values": {{...}} }}] }}
    var ts = data.timestamp ? data.timestamp * 1000 : Date.now();
    var values = {{
        temperature:      data.temperature,
        humidity:         data.humidity,
        occupancy:        data.occupancy,
        light_level:      data.light_level,
        hvac_mode:        data.hvac_mode,
        lighting_dimmer:  data.lighting_dimmer,
        target_temp:      data.target_temp,
        fault:            data.fault
    }};
    msg.payload = JSON.stringify({{ [device]: [{{ ts: ts, values: values }}] }});
    msg.topic   = 'v1/gateway/telemetry';
    msg.route   = 'telemetry';
    return msg;
}}

if (msgType === 'heartbeat') {{
    msg.payload = JSON.stringify({{ [device]: {{ status: data.status || 'alive', last_seen: data.timestamp }} }});
    msg.topic   = 'v1/gateway/attributes';
    msg.route   = 'attr';
    return msg;
}}

if (msgType === 'lwt') {{
    msg.payload = JSON.stringify({{ [device]: {{ status: 'offline' }} }});
    msg.topic   = 'v1/gateway/attributes';
    msg.route   = 'attr';
    return msg;
}}

return null;
""".strip()

    nodes = [
        # Tab
        {"id": tab_id, "type": "tab", "label": f"Floor {floor_str} Gateway", "disabled": False, "info": ""},

        # HiveMQ broker config
        {
            "id": hivemq_broker_id, "type": "mqtt-broker",
            "name": f"HiveMQ-f{floor_str}",
            "broker": HIVEMQ_HOST, "port": str(HIVEMQ_PORT),
            "clientid": f"nodered-gw-f{floor_str}",
            "autoConnect": True, "usetls": False, "protocolVersion": "4",
            "keepalive": "60", "cleansession": True,
            "birthTopic": "", "birthQos": "0", "birthPayload": "",
            "closeTopic": "", "closeQos": "0", "closePayload": "",
            "willTopic": "", "willQos": "0", "willPayload": "",
            "credentials": {"user": OBSERVER_USER, "password": observer_password},
        },

        # ThingsBoard broker config (gateway token as username)
        {
            "id": tb_broker_id, "type": "mqtt-broker",
            "name": f"ThingsBoard-gw-f{floor_str}",
            "broker": TB_MQTT_HOST, "port": str(TB_MQTT_PORT),
            "clientid": f"tb-gw-f{floor_str}",
            "autoConnect": True, "usetls": False, "protocolVersion": "4",
            "keepalive": "60", "cleansession": True,
            "credentials": {"user": gw_token, "password": ""},
        },

        # MQTT In — subscribe to all floor topics on HiveMQ
        {
            "id": mqtt_in_id, "type": "mqtt in",
            "name": f"HiveMQ f{floor_str}",
            "topic": topic_sub, "qos": "0",
            "datatype": "auto",
            "broker": hivemq_broker_id,
            "x": 120, "y": 100, "z": tab_id,
            "wires": [[func_id]],
        },

        # Function — parse and route
        {
            "id": func_id, "type": "function",
            "name": "Format → TB Gateway",
            "func": func_code,
            "outputs": 1,
            "x": 350, "y": 100, "z": tab_id,
            "wires": [[out_telemetry_id, out_attr_id]],
        },

        # MQTT Out — telemetry
        {
            "id": out_telemetry_id, "type": "mqtt out",
            "name": "TB Telemetry",
            "topic": "", "qos": "0", "retain": "false",
            "broker": tb_broker_id,
            "x": 600, "y": 80, "z": tab_id,
            "wires": [],
        },

        # MQTT Out — attributes (heartbeat / lwt)
        {
            "id": out_attr_id, "type": "mqtt out",
            "name": "TB Attributes",
            "topic": "", "qos": "0", "retain": "false",
            "broker": tb_broker_id,
            "x": 600, "y": 140, "z": tab_id,
            "wires": [],
        },

        # Inject once on startup — announce all 20 devices as connected to ThingsBoard
        {
            "id": inject_connect_id, "type": "inject",
            "name": "Announce devices connected",
            "props": [{"p": "payload"}],
            "repeat": "", "crontab": "",
            "once": True, "onceDelay": 5,
            "topic": "", "payload": "", "payloadType": "date",
            "x": 120, "y": 220, "z": tab_id,
            "wires": [[func_connect_id]],
        },

        # Function — build v1/gateway/connect messages for all 20 rooms on this floor
        {
            "id": func_connect_id, "type": "function",
            "name": "Connect announcements",
            "func": "\n".join([
                "var msgs = [];",
                f"for (var i = 1; i <= {ROOMS_PER_FLOOR}; i++) {{",
                "    var roomStr = 'r' + String(i).padStart(3,'0');",
                f"    var device  = 'b01-f{floor_str}-' + roomStr;",
                f"    var profile = (i <= {MQTT_ROOMS_PER_FLOOR}) ? 'MQTT_Room_Device' : 'CoAP_Room_Device';",
                "    msgs.push({ topic: 'v1/gateway/connect',",
                "                payload: JSON.stringify({ device: device, type: profile }) });",
                "}",
                "return [msgs];",
            ]),
            "outputs": 1,
            "x": 360, "y": 220, "z": tab_id,
            "wires": [[out_connect_id]],
        },

        # MQTT Out — publish connect announcements to ThingsBoard
        {
            "id": out_connect_id, "type": "mqtt out",
            "name": "TB Connect",
            "topic": "", "qos": "1", "retain": "false",
            "broker": tb_broker_id,
            "x": 600, "y": 220, "z": tab_id,
            "wires": [],
        },
    ]

    # CoAP tab — one Observe node per CoAP room (rooms 11-20 on this floor)
    coap_nodes = _build_coap_tab(floor, floor_str, tb_broker_id)

    # Downstream tab — ThingsBoard RPC → MQTT cmd (rooms 01-10) or CoAP PUT (rooms 11-20)
    downstream_nodes = _build_downstream_tab(floor_str, hivemq_broker_id, tb_broker_id)

    # Credentials map for flows_cred.json (plain JSON, credentialSecret: false)
    credentials = {
        hivemq_broker_id: {"user": OBSERVER_USER, "password": observer_password},
        tb_broker_id: {"user": gw_token, "password": ""},
    }

    return nodes + coap_nodes + downstream_nodes, credentials


COAP_SIMULATOR_HOST = "simulator"
COAP_PLAIN_PORT = 5683
ROOMS_PER_FLOOR = 20
MQTT_ROOMS_PER_FLOOR = 10  # rooms 1-10 are MQTT; rooms 11-20 are CoAP


def _build_coap_tab(floor: int, floor_str: str, tb_broker_id: str) -> list[dict]:
    """
    Builds a Node-RED tab: inject → function (CoAP observe all 10 rooms) → mqtt out.

    The function node uses the coap npm module directly via require() to start
    10 persistent Observe subscriptions and forwards each update via node.send().

    CoAP rooms on each floor: room_on_floor 11-20
      room_number = floor * 100 + room_on_floor   (e.g. floor 1, room 11 → 111)
      CoAP path  : /fXX/rNNN/telemetry             (e.g. /f01/r111/telemetry)
      TB device  : b01-fXX-r011 … b01-fXX-r020
    """
    tab_id     = _nid()
    inject_id  = _nid()
    func_id    = _nid()
    mqtt_out_id = _nid()

    # Build the list of (pathname, device_name) pairs for this floor
    room_entries = []
    for room_on_floor in range(MQTT_ROOMS_PER_FLOOR + 1, ROOMS_PER_FLOOR + 1):  # 11..20
        room_number = floor * 100 + room_on_floor
        pathname    = f"/f{floor_str}/r{room_number:03d}/telemetry"
        device_name = f"b01-f{floor_str}-r{room_on_floor:03d}"
        room_entries.append((pathname, device_name))

    # Build JS rooms array literal
    rooms_js = ",\n    ".join(
        f"{{pathname:{repr(p)}, device:{repr(d)}}}"
        for p, d in room_entries
    )

    func_code = f"""// CoAP Observe → ThingsBoard — Floor {floor_str} (rooms 011-020)
// Triggered once on startup; node.send() fires for every CoAP notification.
// 'coap' is injected via the node's libs declaration (functionExternalModules: true).

var rooms = [
    {rooms_js}
];

rooms.forEach(function(room) {{
    var req = coap.request({{
        hostname: '{COAP_SIMULATOR_HOST}',
        port: {COAP_PLAIN_PORT},
        pathname: room.pathname,
        method: 'GET',
        observe: true
    }});
    req.on('response', function(res) {{
        res.on('data', function(data) {{
            try {{
                var d = JSON.parse(data.toString());
                var ts = d.timestamp ? d.timestamp * 1000 : Date.now();
                node.send({{
                    topic: 'v1/gateway/telemetry',
                    payload: JSON.stringify({{
                        [room.device]: [{{
                            ts: ts,
                            values: {{
                                temperature:     d.temperature,
                                humidity:        d.humidity,
                                occupancy:       d.occupancy,
                                light_level:     d.light_level,
                                hvac_mode:       d.hvac_mode,
                                lighting_dimmer: d.lighting_dimmer,
                                target_temp:     d.target_temp,
                                fault:           d.fault
                            }}
                        }}]
                    }})
                }});
            }} catch(e) {{}}
        }});
    }});
    req.on('error', function(e) {{
        node.error('CoAP ' + room.device + ': ' + e.message);
    }});
    req.end();
}});
return null;""".strip()

    return [
        # Tab
        {
            "id": tab_id, "type": "tab",
            "label": f"Floor {floor_str} CoAP Gateway",
            "disabled": False, "info": "",
        },
        # Inject once on startup
        {
            "id": inject_id, "type": "inject",
            "name": "Start CoAP Observe",
            "props": [{"p": "payload"}],
            "repeat": "", "crontab": "",
            "once": True, "onceDelay": 3,
            "topic": "", "payload": "", "payloadType": "date",
            "x": 120, "y": 200, "z": tab_id,
            "wires": [[func_id]],
        },
        # Function: one node sets up all 10 CoAP Observe subscriptions
        # libs declares 'coap' so it's available as a global (functionExternalModules must be true)
        {
            "id": func_id, "type": "function",
            "name": f"CoAP Observe f{floor_str} (rooms 011-020)",
            "func": func_code,
            "libs": [{"var": "coap", "module": "coap"}],
            "outputs": 1,
            "x": 380, "y": 200, "z": tab_id,
            "wires": [[mqtt_out_id]],
        },
        # MQTT Out → ThingsBoard (reuses broker config from tab 1)
        {
            "id": mqtt_out_id, "type": "mqtt out",
            "name": "TB Telemetry (CoAP)",
            "topic": "", "qos": "0", "retain": "false",
            "broker": tb_broker_id,
            "x": 620, "y": 200, "z": tab_id,
            "wires": [],
        },
    ]


MQTT_TOPIC_PREFIX = "campus/b01"


def _build_downstream_tab(
    floor_str: str, hivemq_broker_id: str, tb_broker_id: str
) -> list[dict]:
    """
    Downstream tab: ThingsBoard RPC → device command.

    ThingsBoard publishes to v1/gateway/rpc:
      {"device": "b01-fXX-rYYY", "id": <rpcId>, "data": {"method": "...", "params": {...}}}

    For MQTT rooms (room_on_floor 01-10):
      Publishes params JSON to HiveMQ: campus/b01/fXX/rNNN/cmd
      (NNN = floor*100 + room_on_floor, e.g. floor 1 room 9 → r109)

    For CoAP rooms (room_on_floor 11-20):
      CON PUT to simulator:5683/fXX/rNNN/actuators/hvac

    Sends RPC response back to ThingsBoard on v1/gateway/rpc/response.
    """
    tab_id       = _nid()
    mqtt_in_id   = _nid()
    func_id      = _nid()
    hivemq_out_id = _nid()
    tb_resp_id   = _nid()

    func_code = f"""// Downstream: ThingsBoard RPC → device command (floor {floor_str})
// TB publishes: {{"device":"b01-fXX-rYYY","id":N,"data":{{"method":"...","params":{{...}}}}}}
// Output 1 → HiveMQ cmd topic  (MQTT rooms r001-r010)
// Output 2 → TB rpc/response   (all rooms, after command dispatched)
var rpc;
try {{ rpc = JSON.parse(msg.payload); }} catch(e) {{ return null; }}

var device = rpc.device;
if (!device) return null;
var parts = device.split('-');   // ["b01","f01","r009"]
if (parts.length < 3) return null;

var floorNum    = parseInt(parts[1].slice(1));    // 1
var roomOnFloor = parseInt(parts[2].slice(1));    // 9
var roomNumber  = floorNum * 100 + roomOnFloor;   // 109
var floorPad    = String(floorNum).padStart(2,'0');
var roomPad     = String(roomNumber).padStart(3,'0');

var params  = (rpc.data && rpc.data.params) ? rpc.data.params : {{}};
var cmdJson = JSON.stringify(params);
var rpcId   = (rpc.id !== undefined) ? rpc.id : 0;

function makeResp(success, errMsg) {{
    var d = {{ success: success }};
    if (errMsg) d.error = errMsg;
    return {{ topic: 'v1/gateway/rpc/response',
              payload: JSON.stringify({{ device: device, id: rpcId, data: d }}) }};
}}

if (roomOnFloor >= 1 && roomOnFloor <= {MQTT_ROOMS_PER_FLOOR}) {{
    // MQTT room — publish command to HiveMQ
    var cmdTopic = '{MQTT_TOPIC_PREFIX}/f' + floorPad + '/r' + roomPad + '/cmd';
    node.send([{{ topic: cmdTopic, payload: cmdJson }}, makeResp(true)]);
}} else {{
    // CoAP room — CON PUT to simulator
    var path = '/f' + floorPad + '/r' + roomPad + '/actuators/hvac';
    var req = coap.request({{
        hostname: '{COAP_SIMULATOR_HOST}',
        port: {COAP_PLAIN_PORT},
        pathname: path,
        method: 'PUT',
        confirmable: true
    }});
    req.on('response', function(res) {{
        node.send([null, makeResp(res.code === '2.04')]);
    }});
    req.on('error', function(e) {{
        node.error('CoAP PUT ' + device + ': ' + e.message);
        node.send([null, makeResp(false, e.message)]);
    }});
    req.write(Buffer.from(cmdJson));
    req.end();
}}
return null;""".strip()

    return [
        {"id": tab_id, "type": "tab", "label": f"Floor {floor_str} Downstream", "disabled": False, "info": ""},

        # MQTT In — subscribe to ThingsBoard gateway RPC topic
        {
            "id": mqtt_in_id, "type": "mqtt in",
            "name": "TB RPC In",
            "topic": "v1/gateway/rpc", "qos": "1",
            "datatype": "auto",
            "broker": tb_broker_id,
            "x": 120, "y": 200, "z": tab_id,
            "wires": [[func_id]],
        },

        # Function — route to MQTT cmd or CoAP PUT; builds RPC response
        {
            "id": func_id, "type": "function",
            "name": f"Route RPC f{floor_str}",
            "func": func_code,
            "libs": [{"var": "coap", "module": "coap"}],
            "outputs": 2,
            "x": 360, "y": 200, "z": tab_id,
            "wires": [[hivemq_out_id], [tb_resp_id]],
        },

        # MQTT Out — HiveMQ command topic (MQTT rooms)
        {
            "id": hivemq_out_id, "type": "mqtt out",
            "name": "HiveMQ CMD",
            "topic": "", "qos": "1", "retain": "false",
            "broker": hivemq_broker_id,
            "x": 600, "y": 160, "z": tab_id,
            "wires": [],
        },

        # MQTT Out — ThingsBoard RPC response
        {
            "id": tb_resp_id, "type": "mqtt out",
            "name": "TB RPC Response",
            "topic": "", "qos": "1", "retain": "false",
            "broker": tb_broker_id,
            "x": 600, "y": 240, "z": tab_id,
            "wires": [],
        },
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=TB_URL_DEFAULT)
    ap.add_argument("--username", default="admin@gmail.com")
    ap.add_argument("--password", default="admins")
    ap.add_argument("--observer-password", default=None,
                    help="HiveMQ campus_observer password (auto-read from credentials.xml if omitted)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]

    # Auto-read observer password from credentials.xml
    observer_pw = args.observer_password
    if not observer_pw:
        creds_xml = root / "config/hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml"
        if creds_xml.is_file():
            import xml.etree.ElementTree as ET
            tree = ET.parse(creds_xml)
            for user in tree.findall(".//user"):
                if user.find("name").text == "campus_observer":
                    observer_pw = user.find("password").text
                    break
        if not observer_pw:
            print("ERROR: could not find campus_observer password. Pass --observer-password.", file=sys.stderr)
            return 1

    print(f"Logging in to ThingsBoard at {args.url} …")
    token = _jwt(args.url, args.username, args.password)

    for floor in range(1, 11):
        floor_str = f"{floor:02d}"
        gw_name = f"gw-floor-{floor_str}"

        print(f"  [{floor_str}] Upsert gateway device '{gw_name}' …", end=" ", flush=True)
        dev_id = _upsert_gateway_device(args.url, token, gw_name)
        gw_token = _get_access_token(args.url, token, dev_id)
        print(f"token={gw_token[:12]}…")

        flow, creds = _build_flow(floor, gw_token, observer_pw)
        out_dir = root / f"gateways/floor-{floor_str}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "flows.json").write_text(json.dumps(flow, indent=2), encoding="utf-8")
        (out_dir / "flows_cred.json").write_text(json.dumps(creds, indent=2), encoding="utf-8")
        print(f"         Wrote {out_dir}/flows.json + flows_cred.json")

    print("\nDone. Restart the Node-RED gateway containers to load the new flows:")
    print("  docker compose restart gateway-floor-01 gateway-floor-02 ... (or all at once)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
