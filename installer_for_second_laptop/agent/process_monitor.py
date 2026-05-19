import psutil

BLACKLIST = {
    "mimikatz.exe",
    "nc.exe",
    "netcat.exe",
    "procdump.exe",
}


def scan_processes():
    suspicious = []

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"]
            if name and name.lower() in BLACKLIST:
                suspicious.append({
                    "pid": proc.info["pid"],
                    "process_name": name
                })
        except Exception:
            continue

    return suspicious