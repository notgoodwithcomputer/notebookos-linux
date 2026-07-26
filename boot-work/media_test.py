import subprocess, os
print("=== AUDIO CARD ===")
try:
    print(open("/proc/asound/cards").read().strip()[:200] or "(no cards)")
except Exception as e:
    print("no /proc/asound/cards:", e)
print("=== GSTREAMER ===")
try:
    import gi; gi.require_version("Gst","1.0")
    from gi.repository import Gst
    Gst.init(None)
    pb = Gst.ElementFactory.make("playbin","p")
    al = Gst.ElementFactory.make("alsasink","a")
    mp = Gst.ElementFactory.make("mpg123audiodec","m")
    print("playbin:", pb is not None, "| alsasink:", al is not None, "| mp3dec:", mp is not None)
except Exception as e:
    print("GST FAIL:", e)
print("=== POPPLER ===")
try:
    import gi; gi.require_version("Poppler","0.18")
    from gi.repository import Poppler
    print("poppler OK")
except Exception as e:
    print("POPPLER FAIL:", e)
print("=== FFMPEG ===")
import shutil
print("ffmpeg:", shutil.which("ffmpeg"))
print("bluetoothctl:", shutil.which("bluetoothctl"), "| bluealsa:", shutil.which("bluealsa"))
