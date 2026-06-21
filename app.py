import sys
import os
import subprocess
import threading
import time
import webview
import json
import ctypes
import struct
import socket
import winreg

from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def run_cmd(cmd, shell=True):
    try:
        # CREATE_NO_WINDOW evita que aparezca una ventana de cmd al ejecutar subprocesos
        CREATE_NO_WINDOW = 0x08000000
        result = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW
        )
        return {"ok": True, "out": result.stdout.strip(), "err": result.stderr.strip()}
    except Exception as e:
        return {"ok": False, "err": str(e)}

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

# ─────────────────────────────────────────────
#  DISPLAY / MONITOR  (user32 ChangeDisplaySettings)
# ─────────────────────────────────────────────

DEVMODE_FORMAT = "32sHHHHHHHHHHHH32sHHHHHHHHHHHH"

class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName",      ctypes.c_wchar * 32),
        ("dmSpecVersion",     ctypes.c_short),
        ("dmDriverVersion",   ctypes.c_short),
        ("dmSize",            ctypes.c_short),
        ("dmDriverExtra",     ctypes.c_short),
        ("dmFields",          ctypes.c_int),
        ("dmPositionX",       ctypes.c_int),
        ("dmPositionY",       ctypes.c_int),
        ("dmDisplayOrientation", ctypes.c_int),
        ("dmDisplayFixedOutput", ctypes.c_int),
        ("dmColor",           ctypes.c_short),
        ("dmDuplex",          ctypes.c_short),
        ("dmYResolution",     ctypes.c_short),
        ("dmTTOption",        ctypes.c_short),
        ("dmCollate",         ctypes.c_short),
        ("dmFormName",        ctypes.c_wchar * 32),
        ("dmLogPixels",       ctypes.c_short),
        ("dmBitsPerPel",      ctypes.c_int),
        ("dmPelsWidth",       ctypes.c_int),
        ("dmPelsHeight",      ctypes.c_int),
        ("dmDisplayFlags",    ctypes.c_int),
        ("dmDisplayFrequency",ctypes.c_int),
        ("dmICMMethod",       ctypes.c_int),
        ("dmICMIntent",       ctypes.c_int),
        ("dmMediaType",       ctypes.c_int),
        ("dmDitherType",      ctypes.c_int),
        ("dmReserved1",       ctypes.c_int),
        ("dmReserved2",       ctypes.c_int),
        ("dmPanningWidth",    ctypes.c_int),
        ("dmPanningHeight",   ctypes.c_int),
    ]

def get_current_display():
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
    return {
        "width": dm.dmPelsWidth,
        "height": dm.dmPelsHeight,
        "hz": dm.dmDisplayFrequency,
        "bits": dm.dmBitsPerPel
    }

def get_available_hz():
    hz_set = set()
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    current = get_current_display()
    i = 0
    while ctypes.windll.user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
        if dm.dmPelsWidth == current["width"] and dm.dmPelsHeight == current["height"]:
            hz_set.add(dm.dmDisplayFrequency)
        i += 1
    return sorted(list(hz_set))

def get_available_resolutions():
    res_set = set()
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    i = 0
    while ctypes.windll.user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
        res_set.add((dm.dmPelsWidth, dm.dmPelsHeight))
        i += 1
    return sorted([{"w": w, "h": h} for w, h in res_set], key=lambda x: x["w"])

def set_hz(hz):
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
    dm.dmDisplayFrequency = int(hz)
    dm.dmFields = 0x400000  # DM_DISPLAYFREQUENCY
    result = ctypes.windll.user32.ChangeDisplaySettingsW(ctypes.byref(dm), 1)
    return result == 0

def set_resolution(w, h):
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
    dm.dmPelsWidth  = int(w)
    dm.dmPelsHeight = int(h)
    dm.dmFields = 0x180000  # DM_PELSWIDTH | DM_PELSHEIGHT
    result = ctypes.windll.user32.ChangeDisplaySettingsW(ctypes.byref(dm), 1)
    return result == 0

# ─────────────────────────────────────────────
#  NIGHT MODE  (Windows registry)
# ─────────────────────────────────────────────

def set_night_mode(enable: bool):
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current\default$windows.data.bluelightreduction.bluelightreductionstate\windows.data.bluelightreduction.bluelightreductionstate"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            data, _ = winreg.QueryValueEx(key, "Data")
            data = bytearray(data)
            # byte 18 controls on/off: 0x15 = on, 0x13 = off
            if len(data) > 18:
                data[18] = 0x15 if enable else 0x13
            winreg.SetValueEx(key, "Data", 0, winreg.REG_BINARY, bytes(data))
            winreg.CloseKey(key)
            return True
        except:
            # fallback: PowerShell
            val = "Enabled" if enable else "Disabled"
            run_cmd(f'powershell -Command "Set-ItemProperty -Path HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CloudStore\\Store\\DefaultAccount\\Current\\default$windows.data.bluelightreduction.settings\\windows.data.bluelightreduction.settings -Name Data -Value ([byte[]](0x02,0x00,0x00,0x00))"')
            return True
    except Exception as e:
        return False

# ─────────────────────────────────────────────
#  PC TWEAKS
# ─────────────────────────────────────────────

def clean_temp():
    paths = [
        os.environ.get("TEMP", ""),
        os.environ.get("TMP", ""),
        r"C:\Windows\Temp",
        r"C:\Windows\Prefetch",
    ]
    deleted = 0
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        for f in os.listdir(p):
            fp = os.path.join(p, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                    deleted += 1
            except:
                pass
    return deleted

def free_ram():
    run_cmd("powershell -Command \"[System.GC]::Collect()\"")
    run_cmd("rundll32.exe advapi32.dll,ProcessIdleTasks")
    return True

def set_power_plan(high: bool):
    if high:
        run_cmd("powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
    else:
        run_cmd("powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e")
    return True

def set_visual_effects(minimal: bool):
    if minimal:
        run_cmd('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f')
    else:
        run_cmd('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 0 /f')
    return True

def set_telemetry(disable: bool):
    val = "0" if disable else "1"
    run_cmd(f'reg add "HKLM\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection" /v AllowTelemetry /t REG_DWORD /d {val} /f')
    return True

def set_game_bar(disable: bool):
    val = "0" if disable else "1"
    run_cmd(f'reg add "HKCU\\Software\\Microsoft\\GameBar" /v AutoGameModeEnabled /t REG_DWORD /d {val} /f')
    run_cmd(f'reg add "HKCU\\System\\GameConfigStore" /v GameDVR_Enabled /t REG_DWORD /d {val} /f')
    return True

def optimize_startup():
    run_cmd("sc config SysMain start= disabled")
    run_cmd("sc stop SysMain")
    return True

# ─────────────────────────────────────────────
#  INTERNET TWEAKS
# ─────────────────────────────────────────────

DNS_OPTIONS = {
    "cloudflare": ("1.1.1.1", "1.0.0.1"),
    "google":     ("8.8.8.8", "8.8.4.4"),
    "opendns":    ("208.67.222.222", "208.67.220.220"),
    "auto":       None
}

def get_active_adapter():
    result = run_cmd('powershell -Command "Get-NetAdapter | Where-Object {$_.Status -eq \'Up\'} | Select-Object -First 1 -ExpandProperty Name"')
    return result["out"].strip() if result["ok"] else None

def set_dns(provider: str):
    adapter = get_active_adapter()
    if not adapter:
        return False
    servers = DNS_OPTIONS.get(provider)
    if servers is None:
        run_cmd(f'netsh interface ip set dns name="{adapter}" dhcp')
    else:
        run_cmd(f'netsh interface ip set dns name="{adapter}" static {servers[0]}')
        run_cmd(f'netsh interface ip add dns name="{adapter}" {servers[1]} index=2')
    return True

def flush_dns():
    return run_cmd("ipconfig /flushdns")

def reset_winsock():
    run_cmd("netsh winsock reset")
    run_cmd("netsh int ip reset")
    return True

def optimize_tcp():
    run_cmd("netsh int tcp set global autotuninglevel=normal")
    run_cmd("netsh int tcp set global congestionprovider=ctcp")
    run_cmd("netsh int tcp set global ecncapability=disabled")
    run_cmd("netsh int tcp set global timestamps=disabled")
    run_cmd("netsh int tcp set global rss=enabled")
    # Nagle algorithm disable
    run_cmd('reg add "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces" /v TcpAckFrequency /t REG_DWORD /d 1 /f')
    run_cmd('reg add "HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters" /v TCPNoDelay /t REG_DWORD /d 1 /f')
    return True

def get_ping(host="8.8.8.8"):
    try:
        CREATE_NO_WINDOW = 0x08000000
        result = subprocess.run(
            ["ping", "-n", "1", "-w", "1000", host],
            capture_output=True, text=True, timeout=3,
            creationflags=CREATE_NO_WINDOW
        )
        for line in result.stdout.split("\n"):
            if "ms" in line and ("tiempo" in line.lower() or "time" in line.lower() or "=" in line):
                for part in line.split():
                    if "ms" in part:
                        return int(''.join(filter(str.isdigit, part)))
        return None
    except:
        return None

def get_net_speed():
    try:
        r1 = run_cmd('powershell -Command "Get-NetAdapterStatistics | Select-Object -First 1 | ConvertTo-Json"')
        if not r1["ok"] or not r1["out"]:
            return {"rx": 0, "tx": 0}
        s1 = json.loads(r1["out"])
        time.sleep(1)
        r2 = run_cmd('powershell -Command "Get-NetAdapterStatistics | Select-Object -First 1 | ConvertTo-Json"')
        s2 = json.loads(r2["out"])
        rx = max(0, s2.get("ReceivedBytes", 0) - s1.get("ReceivedBytes", 0))
        tx = max(0, s2.get("SentBytes", 0) - s1.get("SentBytes", 0))
        return {"rx": round(rx / 1024, 1), "tx": round(tx / 1024, 1)}
    except:
        return {"rx": 0, "tx": 0}

def run_diagnostic():
    lines = []
    # Ping
    ping = get_ping("8.8.8.8")
    lines.append(f"PING 8.8.8.8 → {ping}ms" if ping else "PING 8.8.8.8 → timeout")
    ping2 = get_ping("1.1.1.1")
    lines.append(f"PING 1.1.1.1 → {ping2}ms" if ping2 else "PING 1.1.1.1 → timeout")
    # DNS
    r = run_cmd("nslookup google.com")
    lines.append("DNS → OK" if r["ok"] and "Address" in r["out"] else "DNS → FAIL")
    # Adapter
    adapter = get_active_adapter()
    lines.append(f"ADAPTER → {adapter}" if adapter else "ADAPTER → not found")
    # TCP
    r2 = run_cmd("netsh int tcp show global")
    if r2["ok"]:
        for line in r2["out"].split("\n")[:6]:
            if line.strip():
                lines.append(line.strip())
    return "\n".join(lines)

# ─────────────────────────────────────────────
#  SYSTEM STATS
# ─────────────────────────────────────────────

def get_system_stats():
    try:
        # CPU
        cpu_r = run_cmd('powershell -Command "(Get-WmiObject Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"')
        cpu = float(cpu_r["out"]) if cpu_r["ok"] and cpu_r["out"] else 0.0

        # RAM
        ram_r = run_cmd('powershell -Command "$m=Get-WmiObject Win32_OperatingSystem; [math]::Round(($m.TotalVisibleMemorySize-$m.FreePhysicalMemory)/1MB,1).ToString()+\',\'+[math]::Round($m.TotalVisibleMemorySize/1MB,1).ToString()"')
        ram_used, ram_total = 0, 0
        if ram_r["ok"] and "," in ram_r["out"]:
            parts = ram_r["out"].strip().split(",")
            ram_used  = float(parts[0])
            ram_total = float(parts[1])

        # Temp (if available)
        temp_r = run_cmd('powershell -Command "try{(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi | Select-Object -First 1).CurrentTemperature/10-273.15}catch{\'N/A\'}"')
        temp = temp_r["out"].strip() if temp_r["ok"] else "N/A"
        try:
            temp = str(round(float(temp))) + "°C"
        except:
            temp = "N/A"

        # Uptime
        uptime_r = run_cmd('powershell -Command "(Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime | Select-Object -ExpandProperty TotalSeconds"')
        uptime = "N/A"
        if uptime_r["ok"] and uptime_r["out"]:
            secs = int(float(uptime_r["out"].strip()))
            h, m = divmod(secs // 60, 60)
            uptime = f"{h:02d}:{m:02d}"

        return {
            "cpu": round(cpu, 1),
            "ram_used": ram_used,
            "ram_total": ram_total,
            "temp": temp,
            "uptime": uptime
        }
    except Exception as e:
        return {"cpu": 0, "ram_used": 0, "ram_total": 0, "temp": "N/A", "uptime": "N/A"}

# ─────────────────────────────────────────────
#  ROUTES — MONITOR
# ─────────────────────────────────────────────

@app.route("/api/monitor/info")
def monitor_info():
    return jsonify({
        "current": get_current_display(),
        "hz_list": get_available_hz(),
        "res_list": get_available_resolutions()
    })

@app.route("/api/monitor/hz", methods=["POST"])
def api_set_hz():
    hz = request.json.get("hz")
    ok = set_hz(hz)
    return jsonify({"ok": ok})

@app.route("/api/monitor/resolution", methods=["POST"])
def api_set_resolution():
    w = request.json.get("w")
    h = request.json.get("h")
    ok = set_resolution(w, h)
    return jsonify({"ok": ok})

@app.route("/api/monitor/nightmode", methods=["POST"])
def api_night_mode():
    enable = request.json.get("enable", False)
    ok = set_night_mode(enable)
    return jsonify({"ok": ok})

# ─────────────────────────────────────────────
#  ROUTES — PC
# ─────────────────────────────────────────────

@app.route("/api/pc/cleantemp", methods=["POST"])
def api_clean_temp():
    count = clean_temp()
    return jsonify({"ok": True, "deleted": count})

@app.route("/api/pc/freeram", methods=["POST"])
def api_free_ram():
    ok = free_ram()
    return jsonify({"ok": ok})

@app.route("/api/pc/power", methods=["POST"])
def api_power():
    high = request.json.get("high", True)
    ok = set_power_plan(high)
    return jsonify({"ok": ok})

@app.route("/api/pc/visualfx", methods=["POST"])
def api_visual_fx():
    minimal = request.json.get("minimal", True)
    ok = set_visual_effects(minimal)
    return jsonify({"ok": ok})

@app.route("/api/pc/telemetry", methods=["POST"])
def api_telemetry():
    disable = request.json.get("disable", True)
    ok = set_telemetry(disable)
    return jsonify({"ok": ok})

@app.route("/api/pc/gamebar", methods=["POST"])
def api_game_bar():
    disable = request.json.get("disable", True)
    ok = set_game_bar(disable)
    return jsonify({"ok": ok})

@app.route("/api/pc/startup", methods=["POST"])
def api_startup():
    ok = optimize_startup()
    return jsonify({"ok": ok})

# ─────────────────────────────────────────────
#  ROUTES — INTERNET
# ─────────────────────────────────────────────

@app.route("/api/net/dns", methods=["POST"])
def api_dns():
    provider = request.json.get("provider", "cloudflare")
    ok = set_dns(provider)
    return jsonify({"ok": ok})

@app.route("/api/net/flushdns", methods=["POST"])
def api_flush_dns():
    r = flush_dns()
    return jsonify({"ok": r["ok"], "out": r["out"]})

@app.route("/api/net/resetwinsock", methods=["POST"])
def api_reset_winsock():
    ok = reset_winsock()
    return jsonify({"ok": ok, "msg": "Reinicia el PC para aplicar los cambios."})

@app.route("/api/net/tcpopt", methods=["POST"])
def api_tcp_opt():
    ok = optimize_tcp()
    return jsonify({"ok": ok})

@app.route("/api/net/ping")
def api_ping():
    host = request.args.get("host", "8.8.8.8")
    ping = get_ping(host)
    return jsonify({"ok": ping is not None, "ping": ping})

@app.route("/api/net/speed")
def api_speed():
    speed = get_net_speed()
    return jsonify(speed)

@app.route("/api/net/diagnostic")
def api_diagnostic():
    result = run_diagnostic()
    return jsonify({"ok": True, "out": result})

# ─────────────────────────────────────────────
#  ROUTES — SYSTEM STATS
# ─────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    return jsonify(get_system_stats())

# ─────────────────────────────────────────────
#  MAIN PAGE
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

def start_flask():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    if not is_admin():
        # Re-lanzar como admin solo UNA vez — si falla, igual continuar
        try:
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
        except Exception:
            pass
        sys.exit(0)
    # Flask en hilo secundario
    t = threading.Thread(target=start_flask, daemon=True)
    t.start()
    time.sleep(1.2)
    # Ventana nativa — sin barra de navegador
    webview.create_window(
        "Taz Tweaks",
        "http://127.0.0.1:5000",
        width=1200,
        height=750,
        resizable=True,
        frameless=False,
    )
    webview.start()
