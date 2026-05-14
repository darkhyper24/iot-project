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

    # Phase 3 — reported client attributes branch (HiveMQ attrs/reported → TB Gateway).
    reported_nodes = _build_reported_attrs_tab(floor, floor_str, hivemq_broker_id, tb_broker_id)

    # Phase 3 — desired-state branch (TB shared-attribute push → HiveMQ attrs/desired).
    desired_nodes = _build_desired_state_tab(floor_str, hivemq_broker_id, tb_broker_id)

    # Phase 3 B.1 — 30 s rolling-average of floor temperature → TB Floor device.
    floor_avg_nodes = _build_floor_avg_tab(floor_str, hivemq_broker_id, tb_broker_id)

    # Phase 3 B.5 — OTA tamper events → ThingsBoard security telemetry.
    tamper_nodes = _build_tamper_tab(floor_str, hivemq_broker_id, tb_broker_id) if floor == 1 else []

    # Credentials map for flows_cred.json (plain JSON, credentialSecret: false)
    credentials = {
        hivemq_broker_id: {"user": OBSERVER_USER, "password": observer_password},
        tb_broker_id: {"user": gw_token, "password": ""},
    }

    return (
        nodes + coap_nodes + downstream_nodes + reported_nodes
        + desired_nodes + floor_avg_nodes + tamper_nodes,
        credentials,
    )


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
                if (typeof d.temperature === 'number') {{
                    var bag = global.get('floor{floor_str}_temps') || {{}};
                    bag[room.pathname.split('/')[2]] = d.temperature;
                    global.set('floor{floor_str}_temps', bag);
                }}
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


def _build_reported_attrs_tab(
    floor: int, floor_str: str, hivemq_broker_id: str, tb_broker_id: str,
) -> list[dict]:
    """Phase 3 B.2 — forward simulator-reporter client attrs to TB Gateway.

    Subscribes to ``campus/b01/fXX/+/attrs/reported`` on HiveMQ; payload is the
    reported snapshot dict published by the central twin reporter. Translates
    the global MQTT room number to the TB-local device name (r0NN) and posts
    to ``v1/gateway/attributes``.
    """
    tab_id     = _nid()
    mqtt_in_id = _nid()
    func_id    = _nid()
    out_id     = _nid()

    func_code = f"""// HiveMQ attrs/reported → TB Gateway attributes (floor {floor_str})
// Topic: campus/b01/f{floor_str}/rNNN/attrs/reported  (NNN = floor*100 + room_on_floor)
var parts = msg.topic.split('/');
if (parts.length < 6) return null;
if (parts[4] !== 'attrs' || parts[5] !== 'reported') return null;

var rawNum      = parseInt(parts[3].slice(1));
var roomOnFloor = rawNum - {floor} * 100;
if (!(roomOnFloor >= 1 && roomOnFloor <= 20)) return null;
var roomStr     = 'r' + String(roomOnFloor).padStart(3, '0');
var device      = 'b01-f{floor_str}-' + roomStr;

var data;
try {{
    data = (typeof msg.payload === 'string') ? JSON.parse(msg.payload) : msg.payload;
}} catch(e) {{ return null; }}
if (!data || typeof data !== 'object') return null;

msg.payload = JSON.stringify({{ [device]: data }});
msg.topic   = 'v1/gateway/attributes';
return msg;""".strip()

    return [
        {"id": tab_id, "type": "tab", "label": f"Floor {floor_str} Attrs Reported",
         "disabled": False, "info": ""},
        {
            "id": mqtt_in_id, "type": "mqtt in",
            "name": f"HiveMQ f{floor_str} reported",
            "topic": f"campus/b01/f{floor_str}/+/attrs/reported", "qos": "0",
            "datatype": "auto",
            "broker": hivemq_broker_id,
            "x": 130, "y": 200, "z": tab_id,
            "wires": [[func_id]],
        },
        {
            "id": func_id, "type": "function",
            "name": "Reported → TB Gateway",
            "func": func_code,
            "outputs": 1,
            "x": 360, "y": 200, "z": tab_id,
            "wires": [[out_id]],
        },
        {
            "id": out_id, "type": "mqtt out",
            "name": "TB Attrs (reported)",
            "topic": "", "qos": "0", "retain": "false",
            "broker": tb_broker_id,
            "x": 620, "y": 200, "z": tab_id,
            "wires": [],
        },
    ]


def _build_desired_state_tab(
    floor_str: str, hivemq_broker_id: str, tb_broker_id: str,
) -> list[dict]:
    """Phase 3 B.3 — TB shared-attribute push → HiveMQ attrs/desired.

    Subscribes to ``v1/gateway/attributes`` on TB. Defensively parses inbound
    shared-attribute updates (shape ``{"device":"...","data":{...}}``) and
    republishes to ``campus/b01/f##/r###/attrs/desired`` so the simulator's
    central desired-state subscriber picks them up.

    Self-published gateway client-attribute messages reach this same topic;
    the parser ignores them (no ``data`` map / no ``device`` string).
    """
    tab_id     = _nid()
    mqtt_in_id = _nid()
    resp_in_id = _nid()
    func_id    = _nid()
    out_id     = _nid()
    request_inject_id = _nid()
    request_func_id = _nid()
    request_out_id = _nid()

    func_code = """// TB shared-attribute push → HiveMQ attrs/desired
// Inbound push shape: { "device": "b01-fXX-rYYY", "data": { key: value, ... } }
// Request response shape is accepted defensively if it also carries device + data.
// Outbound HiveMQ: campus/b01/fXX/rNNN/attrs/desired   (NNN = floor*100 + room_on_floor)
var data;
try { data = (typeof msg.payload === 'string') ? JSON.parse(msg.payload) : msg.payload; }
catch(e) { return null; }
if (!data || typeof data !== 'object') return null;

// Defensive: ignore gateway-self publishes (no `data` key, or no `device` field).
if (typeof data.device !== 'string') return null;
if (!data.data || typeof data.data !== 'object') return null;

var parts = data.device.split('-');                       // ["b01","f01","r009"]
if (parts.length < 3) return null;
var floorNum    = parseInt(parts[1].slice(1));
var roomOnFloor = parseInt(parts[2].slice(1));
if (isNaN(floorNum) || isNaN(roomOnFloor)) return null;

var roomNumber  = floorNum * 100 + roomOnFloor;
var floorPad    = String(floorNum).padStart(2,'0');
var roomPad     = String(roomNumber).padStart(3,'0');

msg.topic   = 'campus/b01/f' + floorPad + '/r' + roomPad + '/attrs/desired';
msg.payload = JSON.stringify(data.data);
return msg;""".strip()

    request_code = f"""// Request shared attrs for all 20 floor devices on gateway startup.
// TB Gateway MQTT API: v1/gateway/attributes/request
var keys = [
    'hvac_mode_desired',
    'lighting_dimmer_desired',
    'target_temp_desired',
    'target_version',
    'alpha',
    'beta'
];
var msgs = [];
for (var i = 1; i <= {ROOMS_PER_FLOOR}; i++) {{
    var device = 'b01-f{floor_str}-r' + String(i).padStart(3, '0');
    msgs.push({{
        topic: 'v1/gateway/attributes/request',
        payload: JSON.stringify({{
            id: ({int(floor_str)} * 1000) + i,
            device: device,
            client: false,
            keys: keys
        }})
    }});
}}
return [msgs];""".strip()

    return [
        {"id": tab_id, "type": "tab", "label": f"Floor {floor_str} Desired State",
         "disabled": False, "info": ""},
        {
            "id": mqtt_in_id, "type": "mqtt in",
            "name": "TB attrs (shared push)",
            "topic": "v1/gateway/attributes", "qos": "1",
            "datatype": "auto",
            "broker": tb_broker_id,
            "x": 130, "y": 200, "z": tab_id,
            "wires": [[func_id]],
        },
        {
            "id": resp_in_id, "type": "mqtt in",
            "name": "TB attrs response",
            "topic": "v1/gateway/attributes/response", "qos": "1",
            "datatype": "auto",
            "broker": tb_broker_id,
            "x": 130, "y": 260, "z": tab_id,
            "wires": [[func_id]],
        },
        {
            "id": func_id, "type": "function",
            "name": "Shared → attrs/desired",
            "func": func_code,
            "outputs": 1,
            "x": 380, "y": 230, "z": tab_id,
            "wires": [[out_id]],
        },
        {
            "id": out_id, "type": "mqtt out",
            "name": "HiveMQ attrs/desired",
            "topic": "", "qos": "1", "retain": "false",
            "broker": hivemq_broker_id,
            "x": 650, "y": 230, "z": tab_id,
            "wires": [],
        },
        {
            "id": request_inject_id, "type": "inject",
            "name": "Request shared attrs",
            "props": [{"p": "payload"}],
            "repeat": "", "crontab": "",
            "once": True, "onceDelay": 8,
            "topic": "", "payload": "", "payloadType": "date",
            "x": 130, "y": 340, "z": tab_id,
            "wires": [[request_func_id]],
        },
        {
            "id": request_func_id, "type": "function",
            "name": "Build attr requests",
            "func": request_code,
            "outputs": 1,
            "x": 380, "y": 340, "z": tab_id,
            "wires": [[request_out_id]],
        },
        {
            "id": request_out_id, "type": "mqtt out",
            "name": "TB Attr Requests",
            "topic": "", "qos": "1", "retain": "false",
            "broker": tb_broker_id,
            "x": 650, "y": 340, "z": tab_id,
            "wires": [],
        },
    ]


def _build_floor_avg_tab(
    floor_str: str, hivemq_broker_id: str, tb_broker_id: str,
) -> list[dict]:
    """Phase 3 B.1 — 30 s rolling-average floor temperature → TB Floor aggregate device.

    Subscribes to all telemetry on the floor, accumulates per-room latest
    temperatures in a JS Map, and every 30 s emits a single TB Gateway
    telemetry message for device ``b01-fXX-floor``. The seeder links that
    aggregate device to the Floor asset so the dashboard can visualize the
    Floor-level twin state.
    """
    tab_id    = _nid()
    mqtt_in   = _nid()
    fn_aggr   = _nid()
    inj_tick  = _nid()
    fn_emit   = _nid()
    out_id    = _nid()

    aggr_code = f"""// Per-room latest temperature collector for floor {floor_str}
var parts = msg.topic.split('/');
if (parts.length < 5 || parts[4] !== 'telemetry') return null;

var data;
try {{ data = (typeof msg.payload === 'string') ? JSON.parse(msg.payload) : msg.payload; }}
catch(e) {{ return null; }}
if (!data || typeof data.temperature !== 'number') return null;

var bag = global.get('floor{floor_str}_temps') || {{}};
bag[parts[3]] = data.temperature;
global.set('floor{floor_str}_temps', bag);
return null;""".strip()

    emit_code = f"""// Emit 30 s floor-average telemetry to TB
var bag = global.get('floor{floor_str}_temps') || {{}};
var keys = Object.keys(bag);
if (keys.length === 0) return null;
var sum = 0;
for (var i = 0; i < keys.length; i++) sum += bag[keys[i]];
var avg = sum / keys.length;
var device = 'b01-f{floor_str}-floor';
msg.topic   = 'v1/gateway/telemetry';
msg.payload = JSON.stringify({{
    [device]: [{{ ts: Date.now(), values: {{
        avg_temperature: Number(avg.toFixed(2)),
        room_sample_count: keys.length
    }} }}]
}});
return msg;""".strip()

    return [
        {"id": tab_id, "type": "tab", "label": f"Floor {floor_str} Avg Temp",
         "disabled": False, "info": ""},
        {
            "id": mqtt_in, "type": "mqtt in",
            "name": f"HiveMQ f{floor_str} telemetry",
            "topic": f"campus/b01/f{floor_str}/+/telemetry", "qos": "0",
            "datatype": "auto",
            "broker": hivemq_broker_id,
            "x": 130, "y": 200, "z": tab_id,
            "wires": [[fn_aggr]],
        },
        {
            "id": fn_aggr, "type": "function",
            "name": "Collect latest temps",
            "func": aggr_code,
            "outputs": 1,
            "x": 360, "y": 200, "z": tab_id,
            "wires": [[]],
        },
        {
            "id": inj_tick, "type": "inject",
            "name": "every 30 s",
            "props": [{"p": "payload"}],
            "repeat": "30", "crontab": "",
            "once": True, "onceDelay": 35,
            "topic": "", "payload": "", "payloadType": "date",
            "x": 130, "y": 280, "z": tab_id,
            "wires": [[fn_emit]],
        },
        {
            "id": fn_emit, "type": "function",
            "name": "Emit floor avg",
            "func": emit_code,
            "outputs": 1,
            "x": 360, "y": 280, "z": tab_id,
            "wires": [[out_id]],
        },
        {
            "id": out_id, "type": "mqtt out",
            "name": "TB Telemetry (floor avg)",
            "topic": "", "qos": "0", "retain": "false",
            "broker": tb_broker_id,
            "x": 620, "y": 280, "z": tab_id,
            "wires": [],
        },
    ]


def _build_tamper_tab(
    floor_str: str, hivemq_broker_id: str, tb_broker_id: str,
) -> list[dict]:
    """Phase 3 B.5 — relay OTA tamper events into ThingsBoard telemetry.

    Every floor gateway can see the shared tamper topic through the observer
    account. The security device is provisioned by the seeder as ``b01-security``.
    """
    tab_id = _nid()
    mqtt_in = _nid()
    func_id = _nid()
    out_id = _nid()

    func_code = """// OTA tamper event → TB security telemetry
var data;
try { data = (typeof msg.payload === 'string') ? JSON.parse(msg.payload) : msg.payload; }
catch(e) { return null; }
if (!data || typeof data !== 'object') return null;

msg.topic = 'v1/gateway/telemetry';
msg.payload = JSON.stringify({
    'b01-security': [{
        ts: Date.now(),
        values: {
            ota_tamper: true,
            source_topic: data.source_topic || msg.topic,
            actual_sha256: data.actual_sha256 || '',
            expected_sha256: data.expected_sha256 || '',
            client_id: data.client_id || '',
            tamper_ts: data.ts || Math.floor(Date.now() / 1000)
        }
    }]
});
return msg;""".strip()

    return [
        {"id": tab_id, "type": "tab", "label": f"Floor {floor_str} Tamper Relay",
         "disabled": False, "info": ""},
        {
            "id": mqtt_in, "type": "mqtt in",
            "name": "HiveMQ OTA tamper",
            "topic": "campus/b01/security/tamper", "qos": "1",
            "datatype": "auto",
            "broker": hivemq_broker_id,
            "x": 130, "y": 220, "z": tab_id,
            "wires": [[func_id]],
        },
        {
            "id": func_id, "type": "function",
            "name": "Tamper → TB telemetry",
            "func": func_code,
            "outputs": 1,
            "x": 380, "y": 220, "z": tab_id,
            "wires": [[out_id]],
        },
        {
            "id": out_id, "type": "mqtt out",
            "name": "TB Security Telemetry",
            "topic": "", "qos": "1", "retain": "false",
            "broker": tb_broker_id,
            "x": 650, "y": 220, "z": tab_id,
            "wires": [],
        },
    ]


def _load_existing_gateway_credentials(root: Path, floor_str: str) -> tuple[str, str]:
    """Read observer password and TB gateway token from an existing flows_cred.json."""
    p = root / f"gateways/floor-{floor_str}/flows_cred.json"
    if not p.is_file():
        raise FileNotFoundError(f"Missing existing credentials file: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    observer_password = ""
    gateway_token = ""
    for entry in data.values():
        if entry.get("user") == OBSERVER_USER:
            observer_password = entry.get("password", "")
        elif entry.get("user"):
            gateway_token = entry["user"]
    if not observer_password or not gateway_token:
        raise RuntimeError(f"Could not infer observer password + gateway token from {p}")
    return gateway_token, observer_password


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=TB_URL_DEFAULT)
    ap.add_argument("--username", default="admin@gmail.com")
    ap.add_argument("--password", default="admins")
    ap.add_argument("--observer-password", default=None,
                    help="HiveMQ campus_observer password (auto-read from credentials.xml if omitted)")
    ap.add_argument(
        "--offline-existing-creds",
        action="store_true",
        help="Regenerate flows using existing gateways/floor-XX/flows_cred.json tokens; no TB API calls.",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]

    # Auto-read observer password from credentials.xml
    observer_pw = args.observer_password
    if not observer_pw and not args.offline_existing_creds:
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

    token = None
    if not args.offline_existing_creds:
        print(f"Logging in to ThingsBoard at {args.url} …")
        token = _jwt(args.url, args.username, args.password)

    for floor in range(1, 11):
        floor_str = f"{floor:02d}"
        gw_name = f"gw-floor-{floor_str}"

        if args.offline_existing_creds:
            gw_token, observer_pw_for_floor = _load_existing_gateway_credentials(root, floor_str)
            print(f"  [{floor_str}] Reusing existing gateway token {gw_token[:12]}…")
        else:
            print(f"  [{floor_str}] Upsert gateway device '{gw_name}' …", end=" ", flush=True)
            dev_id = _upsert_gateway_device(args.url, token, gw_name)
            gw_token = _get_access_token(args.url, token, dev_id)
            observer_pw_for_floor = observer_pw
            print(f"token={gw_token[:12]}…")

        flow, creds = _build_flow(floor, gw_token, observer_pw_for_floor)
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
