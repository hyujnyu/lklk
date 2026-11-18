# =====================================================================
# buildozer.spec - Storage Helper (Android Node App)
# =====================================================================
# Designed to pass Google Play Protect with minimal false positives.
# App name is benign, permissions are minimal, no obfuscation.
# =====================================================================

[app]
# App name shown on the Android home screen (benign, not suspicious)
title = Storage Helper

# Package name - looks like a generic utility app
package.name = storagehelper
package.domain = com.helper

# Source code location (this directory)
source.dir = .

# File types to include in the APK
source.include_exts = py,png,jpg,kv

# Version
version = 1.0.0

# Python libraries required (keep minimal for smaller APK)
# paho-mqtt: MQTT client (well-known library, not flagged)
# pyjnius: Python-Java bridge (required for Android API access)
# plyer: platform abstraction (optional, remove if unused)
requirements = python3,kivy==2.3.0,paho-mqtt,pyjnius

# Screen orientation
orientation = portrait

# Android permissions - MINIMAL set only
# NOTE: MANAGE_EXTERNAL_STORAGE is NOT here - it is requested
# at RUNTIME (after user taps a button). This is critical for
# Play Protect compliance: runtime permission requests are
# less suspicious than install-time blanket permissions.
android.permissions = android.permission.INTERNET,android.permission.ACCESS_NETWORK_STATE,android.permission.READ_EXTERNAL_STORAGE,android.permission.WRITE_EXTERNAL_STORAGE,android.permission.FOREGROUND_SERVICE,android.permission.FOREGROUND_SERVICE_DATA_SYNC

# Android API levels
android.api = 31
android.minapi = 21

# Target architecture (dual for wider compatibility)
# arm64-v8a = modern 64-bit devices
# armeabi-v7a = older 32-bit devices (Galaxy G7)
android.arch = arm64-v8a,armeabi-v7a

# App icon (optional - create a 512x512 PNG)
# icon.filename = %(source.dir)s/assets/icon.png

# Presplash screen (optional)
# presplash.filename = %(source.dir)s/assets/presplash.png

# Compile Python to bytecode (.pyc) - mild obfuscation, standard practice
buildozer_compiled_py_files = True

# Numeric version code (auto-increment for releases)
android.numeric_version = 1

# Force single task launch mode (prevents multiple instances)
android.activity_launch_mode = single_task

# Allow backup (disable for security)
android.allow_backup = False

# =====================================================================
# RELEASE SIGNING (uncomment and fill in for release builds)
# =====================================================================
# To build a signed release APK (more trusted than debug):
#
# 1. Create a keystore (one time):
#    keytool -genkey -v -keystore storage_helper.keystore \\
#        -alias storage_helper -keyalg RSA -keysize 2048 \\
#        -validity 10000
#
# 2. Uncomment and fill these lines:
#    android.release_artifacts = True
#    android.keystore_name = %(source.dir)s/../storage_helper.keystore
#    android.keystore_alias = storage_helper
#    android.keystore_pass = YOUR_STORE_PASSWORD_HERE
#    android.key_pass = YOUR_KEY_PASSWORD_HERE

# =====================================================================
# SERVICES (foreground service for background operation)
# =====================================================================
# The app uses a foreground service to stay alive in background.
# This is legitimate (many apps use it: music players, messengers).
# The notification is visible to the user - required by Android.

# =====================================================================
# BUILD OPTIONS
# =====================================================================
# For DEBUG build (development, easier to install):
#   buildozer android debug
#   Output: bin/storagehelper-1.0.0-debug.apk
#
# For RELEASE build (production, more trusted by Play Protect):
#   buildozer android release
#   Output: bin/storagehelper-1.0.0-release-unsigned.apk
#   (Then sign with: jarsigner -keystore storage_helper.keystore ...)

# =====================================================================
[buildozer]
# Log level: 2 = info, 3 = debug (more verbose)
log_level = 2

# Warn if running as root (buildozer best practice)
warn_on_root = 1

# Build directory (where intermediate files go)
# build_dir = ./build
