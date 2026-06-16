#!/usr/bin/env python3
"""
Activity Monitor Diagnostic Tool
Run this to identify which component is failing
"""

import sys
import os
import ctypes
import ctypes.wintypes

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = "✓"
FAIL = "✗"
WARN = "⚠"

def test_window_detection():
    print("\n[TEST 1] Window Detection (GetForegroundWindow)")
    print("-" * 60)
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            print(f"{FAIL} GetForegroundWindow returned NULL")
            return False

        print(f"{PASS} GetForegroundWindow: hwnd={hwnd}")

        # Window title
        length = user32.GetWindowTextLengthW(hwnd) + 1
        if length > 1:
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            title = buf.value.strip()
        else:
            title = "(no title)"
        print(f"{PASS} Window title: {title[:70]!r}")

        # Process ID – THIS IS THE CRITICAL PART
        pid = ctypes.wintypes.DWORD()
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        print(f"{PASS} GetWindowThreadProcessId: tid={tid}, pid={pid.value}")

        if pid.value == 0:
            print(f"{FAIL} ERROR: PID is 0! This is why tracking fails.")
            print(f"         GetWindowThreadProcessId may not be initialized properly.")
            print(f"         Fix: Run 'python -m pip install --upgrade pywin32'")
            print(f"              Then: 'python Scripts/pywin32_postinstall.py -install'")
            return False

        # Get process info
        import psutil
        try:
            proc = psutil.Process(pid.value)
            print(f"{PASS} Process: {proc.name()} (PID={proc.pid})")
            print(f"{PASS} Exe: {proc.exe()}")
            print(f"{PASS} Create time: {proc.create_time()}")
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"{WARN} Cannot access process details: {e}")
            return True  # Still OK – we got the PID
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_idle_detection():
    print("\n[TEST 2] Idle Detection (GetLastInputInfo)")
    print("-" * 60)
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        
        user32 = ctypes.windll.user32
        result = user32.GetLastInputInfo(ctypes.byref(lii))
        
        if not result:
            print(f"{FAIL} GetLastInputInfo returned False")
            print(f"       System may not have initialized Win32 APIs properly")
            return False

        print(f"{PASS} GetLastInputInfo: success")
        print(f"{PASS} Input timestamp: {lii.dwTime}")

        kernel32 = ctypes.windll.kernel32
        tick = kernel32.GetTickCount()
        elapsed_ms = tick - lii.dwTime
        elapsed_sec = elapsed_ms / 1000.0

        print(f"{PASS} Current idle time: {elapsed_ms}ms ({elapsed_sec:.1f}s)")
        return True
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_pynput_listeners():
    print("\n[TEST 3] pynput Listeners (Keyboard/Mouse)")
    print("-" * 60)
    try:
        from pynput import keyboard, mouse
        print(f"{PASS} pynput imported")

        # Try to create listeners (don't start them)
        def noop(*args):
            pass

        kb_listener = keyboard.Listener(on_press=noop, daemon=True)
        ms_listener = mouse.Listener(on_move=noop, daemon=True)
        print(f"{PASS} Listeners created")
        print(f"{PASS} pynput is working")
        return True
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        return False


def test_database():
    print("\n[TEST 4] Database (SQLite)")
    print("-" * 60)
    try:
        from config.settings import DB_PATH
        from core.database import get_db

        print(f"{PASS} DB path: {DB_PATH}")
        
        db = get_db()
        health = db.get_health()

        print(f"{PASS} Database initialized")
        print(f"    Total records: {health['total_records']}")
        print(f"    DB size: {health['db_size_mb']} MB")
        print(f"    Tracker alive: {health['alive']}")
        print(f"    Pending sync: {health['pending_sync']}")
        
        return True
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_settings():
    print("\n[TEST 5] Settings Configuration")
    print("-" * 60)
    try:
        from config.settings import settings, SETTINGS_FILE

        print(f"{PASS} Settings file: {SETTINGS_FILE}")
        
        privacy = settings.get("privacy_mode")
        polling = settings.get("polling_interval")
        idle = settings.get("idle_timeout")
        excluded = settings.get("excluded_apps")

        print(f"{PASS} Privacy mode: {privacy}")
        print(f"{PASS} Polling interval: {polling}s")
        print(f"{PASS} Idle timeout: {idle}s")
        print(f"{PASS} Excluded apps: {excluded}")

        if privacy == "off":
            print(f"{WARN} WARNING: Privacy mode is 'off' – tracking is disabled!")
            return False

        return True
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_privacy_manager():
    print("\n[TEST 6] Privacy Manager")
    print("-" * 60)
    try:
        from privacy.manager import privacy_manager

        print(f"{PASS} Privacy manager loaded")
        print(f"    Mode: {privacy_manager.mode}")
        print(f"    Should track 'chrome.exe': {privacy_manager.should_track('chrome.exe')}")
        print(f"    Should track 'outlook.exe': {privacy_manager.should_track('outlook.exe')}")

        safe = privacy_manager.safe_title("chrome.exe", "Sensitive Document - John's Salary")
        print(f"    Safe title: {safe}")
        
        return True
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_tracker_init():
    print("\n[TEST 7] Activity Tracker Initialization")
    print("-" * 60)
    try:
        from core.tracker import ActivityTracker

        tracker = ActivityTracker()
        print(f"{PASS} Tracker initialized")
        print(f"    Device name: {tracker._device_name}")
        print(f"    Device ID: {tracker._device_id}")
        print(f"    Session manager: {tracker._session_mgr is not None}")
        print(f"    Idle detector: {tracker._idle_detector is not None}")

        return True
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_logs():
    print("\n[TEST 8] Log Files")
    print("-" * 60)
    try:
        from utils.logger import LOG_DIR
        import glob

        print(f"{PASS} Log directory: {LOG_DIR}")
        
        logs = glob.glob(str(LOG_DIR / "*.log"))
        if logs:
            print(f"{PASS} Found {len(logs)} log files:")
            for log in sorted(logs)[:5]:
                import os as os_module
                size = os_module.path.getsize(log)
                print(f"    - {os_module.path.basename(log)} ({size} bytes)")
        else:
            print(f"{WARN} No log files found yet (normal on first run)")

        return True
    except Exception as e:
        print(f"{FAIL} Exception: {e}")
        return False


def main():
    print("=" * 60)
    print("  ACTIVITY MONITOR DIAGNOSTIC TOOL")
    print("=" * 60)

    results = []
    results.append(("Window Detection", test_window_detection()))
    results.append(("Idle Detection", test_idle_detection()))
    results.append(("pynput Listeners", test_pynput_listeners()))
    results.append(("Database", test_database()))
    results.append(("Settings", test_settings()))
    results.append(("Privacy Manager", test_privacy_manager()))
    results.append(("Tracker Init", test_tracker_init()))
    results.append(("Log Files", test_logs()))

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\nPassed: {passed}/{total}")
    
    for name, ok in results:
        status = f"{PASS} PASS" if ok else f"{FAIL} FAIL"
        print(f"  {status:12} – {name}")

    if passed == total:
        print("\n✓ All tests passed! If tracking still doesn't work:")
        print("  1. Check logs in %APPDATA%\\ActivityMonitor\\logs\\")
        print("  2. Make sure app window is in focus")
        print("  3. Try switching between applications")
    else:
        print("\n✗ Some tests failed. See details above.")
        if not results[0][1]:  # Window detection failed
            print("\n  CRITICAL: Window detection is broken!")
            print("  This is likely due to missing pywin32 post-install.")
            print("  Fix with:")
            print("    python -m pip install --upgrade pywin32")
            print("    python Scripts/pywin32_postinstall.py -install")

    print("\n" + "=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())