import os
import time
import json
import ipaddress
import requests
import subprocess
import yaml
import threading
from requests.auth import HTTPBasicAuth
from prometheus_client import Gauge
from prometheus_client import start_http_server

def load_config():
    """Load exporter configuration from a YAML file.

    The configuration file is expected to reside in the same directory as this
    script. Using an absolute path ensures the file can be located when the
    script is executed via systemd or from another working directory.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "exporter_config.yaml")
    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    return config

def expand_sensor_pattern(pattern):
    """展開目前 YAML 用的 {1~4}、{A,B}，可處理連續多組括號。"""
    start_idx = pattern.find("{")
    if start_idx == -1:
        return [pattern]

    end_idx = pattern.find("}", start_idx + 1)
    if end_idx == -1:
        return [pattern]
    
    expression = pattern[start_idx + 1 : end_idx]
    if "~" in expression:
        start_str, end_str = expression.split("~", 1)
        start, end = int(start_str.strip()),int(end_str.strip())
        choices = [str(number) for number in range(start, end + 1)]
    else:
        choices = [part.strip() for part in expression.split(",")]

    expanded = []
    for choice in choices:
        replaced = pattern[:start_idx] + choice + pattern[end_idx + 1:]
        expanded.extend(expand_sensor_pattern(replaced))
    return expanded

def build_sensor_to_gauge(metric_configs):
    """啟動時建一次反向表：Redfish Name -> Gauge 物件。"""
    sensor_to_gauge = {}
    for metric_name, metric_config in metric_configs.items():
        if metric_name not in metric_gauges:
            raise KeyError(f"YAML metric 沒有對應 Gauge: {metric_name}")

        gauge = metric_gauges[metric_name]
        for pattern in metric_config.get("sensors", []):
            for sensor_name in expand_sensor_pattern(pattern):
                if sensor_name in sensor_to_gauge and sensor_to_gauge[sensor_name] is not gauge:
                    raise ValueError(f"sensor 重複被分配到不同 metric: {sensor_name}")
                sensor_to_gauge[sensor_name] = gauge
    return sensor_to_gauge

# Prometheus metrics for mrack component temperatures
mrack_valve_setting = Gauge(
    "liquid_cooling_valve_setting_celsius",
    "coolong valve setting",
    ["sensor_name", "rack_name"]
)
mrack_environment_setting = Gauge(
    "chassis_ambient_environment_celsius",
    "ambient environment sensors",
    ["sensor_name", "rack_name"],
)
mrack_power_meter_watts = Gauge(
    "power_meter_watts",
    "power meter watts reading",
    ["sensor_name", "rack_name"],
)
mrack_power_meter_volts = Gauge(
    "power_meter_volts",
    "power meter volts reading",
    ["sensor_name", "rack_name"],
)

mrack_power_meter_amps = Gauge(
    "power_meter_amps",
    "power meter amps reading",
    ["sensor_name", "rack_name"],
)
mrack_power_meter_factors = Gauge(
    "power_meter_factors",
    "power meter factors reading",
    ["sensor_name", "rack_name"],
)
mrack_pump_PWM = Gauge(
    "pump_PWM_values",
    "pump PWM values reading",
    ["sensor_name", "rack_name"],
)
mrack_pump_RPM = Gauge(
    "pump_RPM_values",
    "pump RPM values reading",
    ["sensor_name", "rack_name"],
)

mrack_power_state_HAC = Gauge(
    "power_status_HAC",
    "power status of relay",
    ["sensor_name", "rack_name"]
)

mrack_tank = Gauge(
    "mrack_tank_level",
    "Water tank level status from mrack",
    ["sensor_name", "rack_name"]
)

mrack_leakage = Gauge(
    "mrack_leakage_sensor",
    "Leakage sensor readings from mrack",
    ["sensor_name", "rack_name"]
)

mrack_pump_fail = Gauge(
    "mrack_pump_fail",
    "pump failure status (1: fail, 0: ok)",
    ["sensor_name", "rack_name"]
)

mrack_BMC_status = Gauge(
    "mrack_BMC_status",
    "status reading from mrack BMC",
    ["sensor_name", "rack_name"]
)

# Prometheus metrics for tCDU component temperatures
tCDU_temperature = Gauge(
    "tCDU_sensors_temperatures",
    "sensors temperatures from tCDU",
    ["sensor_name", "rack_name"]
)

tCDU_pressure = Gauge(
    "tCDU_sensors_pressures",
    "sensors pressures from tCDU",
    ["sensor_name", "rack_name"]
)

tCDU_pump_metric = Gauge(
    "tCDU_pump_metrics",
    "sensors reading from tCDU pump",
    ["sensor_name", "rack_name"]
)

tCDU_leakage_metric = Gauge(
    "tCDU_leakage_metrics",
    "sensors reading from tCDU leakage",
    ["sensor_name", "rack_name"]
)

tCDU_sensor_metric = Gauge(
    "tCDU_sensor_metrics",
    "sensors reading from tCDU",
    ["sensor_name", "rack_name"]
)

tCDU_pump_fail = Gauge(
    "tCDU_pump_fail",
    "pump failure status (1: fail, 0: ok)",
    ["sensor_name", "rack_name"]
)

# Mapping sensor names to corresponding gauges
metric_gauges = {
    # mRack metrics
    "liquid_cooling_valve_setting_celsius": mrack_valve_setting,
    "chassis_ambient_environment_celsius": mrack_environment_setting,
    "power_meter_watts": mrack_power_meter_watts,
    "power_meter_volts": mrack_power_meter_volts,
    "power_meter_amps": mrack_power_meter_amps,
    "power_meter_factors": mrack_power_meter_factors,
    "pump_PWM_values": mrack_pump_PWM,
    "pump_RPM_values": mrack_pump_RPM,
    "power_status_HAC": mrack_power_state_HAC,
    "mrack_tank_level": mrack_tank,
    "mrack_leakage_sensor": mrack_leakage,
    "mrack_BMC_status": mrack_BMC_status,

    # tCDU metrics
    "tCDU_sensors_temperatures": tCDU_temperature,
    "tCDU_sensors_pressures": tCDU_pressure,
    "tCDU_pump_metrics": tCDU_pump_metric,
    "tCDU_leakage_metrics": tCDU_leakage_metric,
    "tCDU_sensor_metrics": tCDU_sensor_metric,
}

config = load_config()
mrack_device = config["devices"][0]
tCDU_device = config["devices"][1]

mrack_sensor_to_gauge = build_sensor_to_gauge(mrack_device["metrics"])
tCDU_sensor_to_gauge = build_sensor_to_gauge(tCDU_device["metrics"])

session = requests.Session()

# Track consecutive PSU and chassis status fetch failures to avoid immediately marking sensors as failed
# status_failures = {}

SERVER_POLL_INTERVAL_SECONDS = 60
SERVER_LEAKAGE_POLL_INTERVAL_SECONDS = 10
EXPORTER_TIMEOUT_SECONDS = 15
# SERVER_LEAKAGE_SENSORS = [
#     "Chassis_Leakage",
#     "Node_Leakage",
#     "GPU_Node_Leakage",
# ]

# server_leakage_state = {}
# server_leakage_lock = threading.Lock()

# _thread_local = threading.local()
# _server_executor = None
# _server_executor_lock = threading.Lock()
# _server_leakage_executor = None
# _server_leakage_executor_lock = threading.Lock()

def fetch_mrack_data():
    nodes_data = {}

    device_entry, logs = fetch_mrack_getall_data(mrack_device, mrack_sensor_to_gauge)

    for message in logs:
        print(message)

    nodes_data.update(device_entry.get("sensors", {}))

    return nodes_data

def fetch_mrack_getall_data(device, sensor_to_gauge):
    logs = []
    ip = device["BMC_ip"]
    base_url = f"https://{ip}/redfish/v1/Chassis/mRack/Sensors/getall"
    auth = HTTPBasicAuth("","")

    device_entry = {
        "bmc_ip": ip,
        "sensors": {},
    }

    try:
        response = session.get(
            base_url,
            headers={"Accept": "application/json"},
            auth=auth,
            verify=False,
            timeout=EXPORTER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        pump_rpm = {}
        pump_pwm = {}
    
    except Exception as exc:
        logs.append(f"[ERROR] {ip} mRack /getall API error: {exc}")
        return device_entry, logs

    members = data.get("Members") if isinstance(data, dict) else None
    if not isinstance(members, list):
        logs.append(f"[ERROR] {ip} mRack /getall response has no valid Members list")
        return device_entry, logs

    received_sensor_names = set()
    for item in members:
        if not isinstance(item, dict):
            logs.append(f"[SKIP] {ip} /getall invalid member: {item!r}")
            continue

        sensor_name = item.get("Id")
        gauge = sensor_to_gauge.get(sensor_name)
        if gauge is None:
            logs.append(f"[SKIP] {ip} {sensor_name} has no gauge")
            continue

        received_sensor_names.add(sensor_name)
        sensor_value = item.get("Reading")

        if sensor_name == "Peer_BMC_Status" :
            peer_status_values = {"00h": 0, "01h": 1, "02h": 2}
            if isinstance(sensor_value, str):
                sensor_value = peer_status_values.get(sensor_value.strip().lower())
            if sensor_value is None:
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(0)
                logs.append(f"[SKIP] {ip} {sensor_name} invalid status value")

            else :
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(sensor_value)
                logs.append(f"[OK] {ip} {sensor_name} = {sensor_value}")

        elif sensor_name.startswith("Sensor"):
            if sensor_value is None or not isinstance(sensor_value, (int, float)):
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(0)
                logs.append(f"[SKIP] {ip} {sensor_name} invalid status value")
            else :
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(sensor_value)
                logs.append(f"[OK] {ip} {sensor_name} = {sensor_value}")

        elif sensor_name.startswith("RPM_P") or sensor_name.startswith("POW_P") or sensor_name.startswith("PWM_P"):
            if sensor_value is None or not isinstance(sensor_value, (int, float)):
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(0)
                logs.append(f"[SKIP] {ip} {sensor_name} invalid status value")
            else :
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(sensor_value)
                logs.append(f"[OK] {ip} {sensor_name} = {sensor_value}")
                if sensor_name.startswith("RPM_P"):
                    pump_rpm[sensor_name.replace("RPM_P", "")] = sensor_value
                elif sensor_name.startswith("PWM_P"):
                    pump_pwm[sensor_name.replace("PWM_P", "")] = sensor_value

        else :
            if sensor_value is None or not isinstance(sensor_value, (int, float)):
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(0)
                logs.append(f"[SKIP] {ip} {sensor_name} invalid status value")

            else :
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(sensor_value)
                logs.append(f"[OK] {ip} {sensor_name} = {sensor_value}")

    for idx in set(pump_rpm) | set(pump_pwm):
        rpm = pump_rpm.get(idx)
        pwm = pump_pwm.get(idx)
        fail = 1 if rpm is not None and pwm is not None and rpm < 100 and pwm != 0 else 0
        mrack_pump_fail.labels(sensor_name=f"Pump_{idx}", rack_name=device["rack_name"]).set(fail)

#        device_entry["sensors"][sensor_name] = {"value": sensor_value}

    # 找出本次成功回應中完全沒有出現的已設定 sensor；不呼叫 set()，Gauge 保留現值。
    for sensor_name in sensor_to_gauge:
        if sensor_name not in received_sensor_names:
            logs.append(f"[KEEP] {ip} {sensor_name} missing from /getall response; Gauge not updated")

    return device_entry, logs

def fetch_tCDU_data():
    tCDU_data = {}

    device_entry, logs = fetch_tCDU_getall_data(tCDU_device, tCDU_sensor_to_gauge)

    for message in logs:
        print(message)

    tCDU_data.update(device_entry.get("sensors", {}))

    return tCDU_data

def fetch_tCDU_getall_data(device, sensor_to_gauge):
    logs = []
    ip = device["BMC_ip"]
    base_url = f"https://{ip}/redfish/v1/Chassis/CDU/Sensors/GetAll"
    auth = HTTPBasicAuth("","")

    device_entry = {
        "bmc_ip": ip,
        "sensors": {},
    }

    try:
        response = session.get(
            base_url,
            headers={"Accept": "application/json"},
            auth=auth,
            verify=False,
            timeout=EXPORTER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        pump_rpm = {}
        pump_pwm = {}
    except Exception as exc:
        logs.append(f"[ERROR] {ip} tCDU /getall API error: {exc}")
        return device_entry, logs

    members = data.get("Members") if isinstance(data, dict) else None
    if not isinstance(members, list):
        logs.append(f"[ERROR] {ip} tCDU /getall response has no valid Members list")
        return device_entry, logs

    received_sensor_names = set()
    for item in members:
        if not isinstance(item, dict):
            logs.append(f"[SKIP] {ip} /getall invalid member: {item!r}")
            continue

        sensor_name = item.get("Id")
        gauge = sensor_to_gauge.get(sensor_name)
        if gauge is None:
            logs.append(f"[SKIP] {ip} {sensor_name} has no gauge")
            continue

        received_sensor_names.add(sensor_name)
        sensor_value = item.get("Reading")

        if sensor_name.startswith("Sensor"):
            if sensor_value is None or not isinstance(sensor_value, (int, float)):
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(0)
                logs.append(f"[SKIP] {ip} {sensor_name} invalid status value")
                
            else :
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(sensor_value)
                logs.append(f"[OK] {ip} {sensor_name} = {sensor_value}")

        elif sensor_name.startswith("RPM_P") or sensor_name.startswith("POW_P") or sensor_name.startswith("PWM_P"):
            if sensor_value is None or not isinstance(sensor_value, (int, float)):
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(0)
                logs.append(f"[SKIP] {ip} {sensor_name} invalid status value")
            else :
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(sensor_value)
                logs.append(f"[OK] {ip} {sensor_name} = {sensor_value}")
                if sensor_name.startswith("RPM_P"):
                    pump_rpm[sensor_name.replace("RPM_P", "")] = sensor_value
                elif sensor_name.startswith("PWM_P"):
                    pump_pwm[sensor_name.replace("PWM_P", "")] = sensor_value


        else :
            if sensor_value is None or not isinstance(sensor_value, (int, float)):
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(0)
                logs.append(f"[SKIP] {ip} {sensor_name} invalid status value")

            else :
                gauge.labels(sensor_name=sensor_name, rack_name=device["rack_name"]).set(sensor_value)
                logs.append(f"[OK] {ip} {sensor_name} = {sensor_value}")

    for idx in set(pump_rpm) | set(pump_pwm):
        rpm = pump_rpm.get(idx)
        pwm = pump_pwm.get(idx)
        fail = 1 if rpm is not None and pwm is not None and rpm < 100 and pwm != 0 else 0
        tCDU_pump_fail.labels(sensor_name=f"Pump_{idx}", rack_name=device["rack_name"]).set(fail)

    # 找出本次成功回應中完全沒有出現的已設定 sensor；不呼叫 set()，Gauge 保留現值。
    for sensor_name in sensor_to_gauge:
        if sensor_name not in received_sensor_names:
            logs.append(f"[KEEP] {ip} {sensor_name} missing from /getall response; Gauge not updated")

    return device_entry, logs

def write_sensor_snapshot(nodes_data):
    """Persist the latest sensor readings to a JSON file without altering exporter behavior."""

    snapshot = {
        "nodes": nodes_data,
        "meta": {
            "exporter_version": "",  # 預留未來寫入
            "rack_id": "",  # 預留未來寫入
        },
    }

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensors_snapshot.json")

    try:
        with open(output_path, "w", encoding="utf-8") as outfile:
            json.dump(snapshot, outfile, ensure_ascii=False, indent=2)
        print(f"[OK] Sensor snapshot written to {output_path}")
    except Exception as exc:
        print(f"[ERROR] Failed to write sensor snapshot: {exc}")

if __name__ == '__main__':
    start_http_server(5000, addr="0.0.0.0")  

    while True:
        mrack_snapshot = fetch_mrack_data()
        tCDU_snapshot = fetch_tCDU_data()
#       write_sensor_snapshot(alldata_snapshot)
        time.sleep(SERVER_POLL_INTERVAL_SECONDS)

