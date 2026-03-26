import time
import json
import network
from machine import Pin, ADC
import dht
from umqtt.simple import MQTTClient

# -------- Config --------
WIFI_SSID = "Wokwi-GUEST"
WIFI_PASSWORD = ""
MQTT_BROKER = "broker.hivemq.com"   # good for Wokwi POC
MQTT_PORT = 1883

BUILDING_ID = "bldg_01"
FLOOR_ID = "floor_01"
ROOM_ID = "room_101"
SENSOR_ID = "b01-f01-r101"

TELEMETRY_TOPIC = "campus/{}/{}/{}/telemetry".format(BUILDING_ID, FLOOR_ID, ROOM_ID)
HEARTBEAT_TOPIC = "campus/{}/{}/{}/heartbeat".format(BUILDING_ID, FLOOR_ID, ROOM_ID)
COMMAND_TOPIC = "campus/{}/{}/{}/command".format(BUILDING_ID, FLOOR_ID, ROOM_ID)

PUBLISH_INTERVAL_SEC = 5
SENSOR_READ_INTERVAL_SEC = 1
OUTSIDE_TEMP = 30.0
THERMAL_LEAKAGE_ALPHA = 0.02
HVAC_BETA_ON = 0.25
HVAC_BETA_ECO = 0.12

# -------- Pins --------
dht_sensor = dht.DHT22(Pin(15))
pir_sensor = Pin(14, Pin.IN)
ldr_sensor = ADC(Pin(34))
ldr_sensor.atten(ADC.ATTN_11DB)
status_led = Pin(2, Pin.OUT)

# -------- State --------
hvac_mode = "ECO"
lighting_dimmer = 50
target_temp = 22.0

last_temp = 22.0
last_humidity = 50.0
last_light = 300
last_occupancy = False
last_publish = 0
temp_initialized = False

# -------- Helpers --------
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    while not wlan.isconnected():
        time.sleep(0.2)
    print("WiFi connected:", wlan.ifconfig())

def validate_command(cmd):
    if not isinstance(cmd, dict):
        return False
    if "hvac_mode" in cmd and cmd["hvac_mode"] not in ("ON", "OFF", "ECO"):
        return False
    if "lighting_dimmer" in cmd:
        if not isinstance(cmd["lighting_dimmer"], int):
            return False
        if cmd["lighting_dimmer"] < 0 or cmd["lighting_dimmer"] > 100:
            return False
    if "target_temp" in cmd:
        if not isinstance(cmd["target_temp"], (int, float)):
            return False
        if cmd["target_temp"] < 15 or cmd["target_temp"] > 30:
            return False
    return True

def validate_telemetry(payload):
    try:
        assert isinstance(payload["sensor_id"], str)
        assert isinstance(payload["timestamp"], int)
        assert 15.0 <= payload["temperature"] <= 50.0
        assert 0.0 <= payload["humidity"] <= 100.0
        assert isinstance(payload["occupancy"], bool)
        assert 0 <= payload["light_level"] <= 1000
        assert payload["hvac_mode"] in ("ON", "OFF", "ECO")
        assert 0 <= payload["lighting_dimmer"] <= 100
        return True
    except Exception:
        return False

def on_message(topic, msg):
    global hvac_mode, lighting_dimmer, target_temp
    print("Command received:", topic, msg)
    try:
        cmd = json.loads(msg.decode())
    except Exception:
        print("Invalid command JSON rejected")
        return

    if not validate_command(cmd):
        print("Malformed command rejected:", cmd)
        return

    if "hvac_mode" in cmd:
        hvac_mode = cmd["hvac_mode"]
    if "lighting_dimmer" in cmd:
        lighting_dimmer = cmd["lighting_dimmer"]
    if "target_temp" in cmd:
        target_temp = float(cmd["target_temp"])

def connect_mqtt():
    client = MQTTClient(SENSOR_ID, MQTT_BROKER, port=MQTT_PORT)
    client.set_callback(on_message)
    client.connect()
    client.subscribe(COMMAND_TOPIC)
    print("MQTT connected")
    print("Subscribed to:", COMMAND_TOPIC)
    return client

def read_sensors():
    global last_temp, last_humidity, last_light, last_occupancy, temp_initialized

    try:
        dht_sensor.measure()
        hum = float(dht_sensor.humidity())
        last_humidity = hum
        if not temp_initialized:
            last_temp = float(dht_sensor.temperature())
            temp_initialized = True
    except Exception as e:
        print("DHT read failed, using last value:", e)

    last_occupancy = bool(pir_sensor.value())

    leakage = THERMAL_LEAKAGE_ALPHA * (OUTSIDE_TEMP - last_temp)

    if hvac_mode == "ON":
        hvac_effect = HVAC_BETA_ON * (target_temp - last_temp)
    elif hvac_mode == "ECO":
        hvac_effect = HVAC_BETA_ECO * (target_temp - last_temp)
    else:
        hvac_effect = 0.0

    occupancy_heat = 0.05 if last_occupancy else 0.0
    simulated_temp = last_temp + leakage + hvac_effect + occupancy_heat
    last_temp = max(15.0, min(50.0, simulated_temp))

    raw = ldr_sensor.read()
    last_light = int((raw / 4095) * 1000)

    if last_occupancy and last_light < 200:
        last_light = 300

def publish_heartbeat(client):
    heartbeat = {
        "sensor_id": SENSOR_ID,
        "timestamp": int(time.time()),
        "status": "healthy"
    }
    client.publish(HEARTBEAT_TOPIC, json.dumps(heartbeat))
    print("Heartbeat:", heartbeat)

def publish_telemetry(client):
    status_led.value(1)

    payload = {
        "sensor_id": SENSOR_ID,
        "timestamp": int(time.time()),
        "temperature": round(last_temp, 2),
        "humidity": round(last_humidity, 2),
        "occupancy": last_occupancy,
        "light_level": last_light,
        "hvac_mode": hvac_mode,
        "lighting_dimmer": lighting_dimmer
    }

    if not validate_telemetry(payload):
        print("Telemetry validation failed, not publishing:", payload)
        status_led.value(0)
        return

    client.publish(TELEMETRY_TOPIC, json.dumps(payload))
    print("Telemetry:", payload)
    publish_heartbeat(client)

    status_led.value(0)

# -------- Main --------
connect_wifi()
mqtt = connect_mqtt()

while True:
    mqtt.check_msg()
    read_sensors()

    now = time.time()
    if now - last_publish >= PUBLISH_INTERVAL_SEC:
        publish_telemetry(mqtt)
        last_publish = now

    time.sleep(SENSOR_READ_INTERVAL_SEC)
