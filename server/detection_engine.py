import os
from collections import defaultdict
from datetime import datetime, timedelta


class DetectionEngine:
    def __init__(self):
        self.file_activity_window = defaultdict(list)

    def process_event(
        self,
        agent_id: str,
        event_type: str,
        details: dict,
        severity: str | None = None,
    ) -> dict | None:
        now = datetime.utcnow()

        # ── PII detected by file watcher ──────────────────────────────────────
        if event_type == "pii_detected":
            file_path  = details.get("file", "unknown")
            confidence = details.get("confidence", 0)
            pii_types  = details.get("pii_types", "")
            alert_sev  = severity if severity in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
            return {
                "agent_id":   agent_id,
                "alert_type": "PII_DETECTED",
                "severity":   alert_sev,
                "description": (
                    f"PII found in file: {os.path.basename(file_path)} | "
                    f"types: {pii_types} | confidence: {confidence:.2f}"
                ),
            }

        # ── USB copy of PII file ──────────────────────────────────────────────
        if event_type in ("usb_pii_copy_blocked", "file_usb_copy_blocked"):
            file_path = details.get("file", "unknown")
            drive     = details.get("drive", details.get("usb_drive", "unknown"))
            return {
                "agent_id":   agent_id,
                "alert_type": "USB_PII_COPY_BLOCKED",
                "severity":   "HIGH",
                "description": (
                    f"Заблокирована попытка копирования ПД на USB: "
                    f"{os.path.basename(file_path)} → USB {drive}"
                ),
            }

        # ── Messenger / email exfiltration ────────────────────────────────────
        if event_type == "messenger_pii_send_blocked":
            file_path = details.get("file", "unknown")
            app       = details.get("app", "unknown app")
            return {
                "agent_id":   agent_id,
                "alert_type": "MESSENGER_PII_BLOCKED",
                "severity":   "HIGH",
                "description": (
                    f"PII file send via {app} blocked: "
                    f"{os.path.basename(file_path)}"
                ),
            }

        # ── Suspicious process ────────────────────────────────────────────────
        if event_type == "suspicious_process":
            process_name = details.get("process_name", "unknown")
            return {
                "agent_id":   agent_id,
                "alert_type": "SUSPICIOUS_PROCESS",
                "severity":   "HIGH",
                "description": f"Suspicious process detected: {process_name}",
            }

        # ── Registry interceptor: access GRANTED (no alert needed) ───────────
        if event_type in ("file_access_granted", "file_opened_no_pii"):
            return None

        # ── Registry interceptor: UNAUTHORIZED access attempt ─────────────────
        if event_type == "unauthorized_access_attempt":
            file_path = details.get("file", "unknown")
            pii_types = details.get("pii_types", "")
            attempts  = details.get("attempts", 1)
            reason    = details.get("reason", "")
            return {
                "agent_id":   agent_id,
                "alert_type": "UNAUTHORIZED_ACCESS_ATTEMPT",
                "severity":   "HIGH",
                "description": (
                    f"Unauthorized access to PII file: "
                    f"{os.path.basename(file_path)} | "
                    f"types: {pii_types} | attempt #{attempts} | {reason}"
                ),
            }

        # ── Registry interceptor: BRUTE FORCE ────────────────────────────────
        if event_type == "brute_force_attempt":
            file_path = details.get("file", "unknown")
            attempts  = details.get("attempts", 0)
            pii_types = details.get("pii_types", "")
            return {
                "agent_id":   agent_id,
                "alert_type": "BRUTE_FORCE_ATTEMPT",
                "severity":   "HIGH",
                "description": (
                    f"BRUTE FORCE on PII file: "
                    f"{os.path.basename(file_path)} | "
                    f"{attempts} failed attempts | types: {pii_types}"
                ),
            }

        # ── Mass file activity tracking ───────────────────────────────────────
        # Track first; if threshold reached — return mass alert (skip specific handler)
        if event_type in {"file_created", "file_modified", "file_moved", "file_read"}:
            self.file_activity_window[agent_id].append(now)
            self.file_activity_window[agent_id] = [
                t for t in self.file_activity_window[agent_id]
                if t > now - timedelta(minutes=1)
            ]
            if len(self.file_activity_window[agent_id]) >= 50:
                return {
                    "agent_id":   agent_id,
                    "alert_type": "MASS_FILE_ACTIVITY",
                    "severity":   "MEDIUM",
                    "description": "Mass file activity: >50 operations in 1 minute",
                }

        # ── File opened (read) with PII ───────────────────────────────────────
        if event_type == "file_read":
            file_path = details.get("file", "unknown")
            alert_sev = severity if severity in ("HIGH", "MEDIUM", "LOW") else "LOW"
            return {
                "agent_id":   agent_id,
                "alert_type": "FILE_READ",
                "severity":   alert_sev,
                "description": (
                    f"Открытие файла с персональными данными: "
                    f"{os.path.basename(file_path)}"
                ),
            }

        # ── File modified with PII ────────────────────────────────────────────
        if event_type == "file_modified":
            file_path = details.get("file", "unknown")
            alert_sev = severity if severity in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
            return {
                "agent_id":   agent_id,
                "alert_type": "FILE_MODIFIED",
                "severity":   alert_sev,
                "description": (
                    f"Изменение файла с персональными данными: "
                    f"{os.path.basename(file_path)}"
                ),
            }

        # ── File moved / renamed with PII ─────────────────────────────────────
        if event_type == "file_moved":
            file_path = details.get("file", "unknown")
            alert_sev = severity if severity in ("HIGH", "MEDIUM", "LOW") else "MEDIUM"
            return {
                "agent_id":   agent_id,
                "alert_type": "FILE_MOVED",
                "severity":   alert_sev,
                "description": (
                    f"Перемещение файла с персональными данными: "
                    f"{os.path.basename(file_path)}"
                ),
            }

        # ── File deleted with PII ─────────────────────────────────────────────
        if event_type == "file_deleted_with_pii":
            file_path = details.get("file", "unknown")
            pii_types = details.get("pii_types", "")
            return {
                "agent_id":   agent_id,
                "alert_type": "FILE_DELETED_WITH_PII",
                "severity":   "MEDIUM",
                "description": (
                    f"PII file deleted: {os.path.basename(file_path)} | "
                    f"types: {pii_types}"
                ),
            }

        # ── Generic HIGH-risk PII event ───────────────────────────────────────
        if severity == "HIGH" and event_type == "file_pii_high_risk":
            file_path = details.get("file", "unknown")
            reason    = details.get("reason", "")
            return {
                "agent_id":   agent_id,
                "alert_type": "PII_HIGH_RISK",
                "severity":   "HIGH",
                "description": (
                    f"High-risk PII event: {os.path.basename(file_path)} — {reason}"
                ),
            }

        return None
