# pyUSProbe

A reverse-engineered Python interface for real-time video streaming and TCP control of Konted C10 / WirelessUSG pocket ultrasound probes.

![Live ultrasound recording](recording.gif)

---

## Requirements

```bash
pip install opencv-python numpy pillow
```

---

## Files

| File | Description |
|---|---|
| `us.py` | Core `USProbe` class — TCP connection, frame parsing, probe commands |
| `live_stream.py` | Launch a live OpenCV viewer window |
| `record.py` | Live viewer **+** record 5 seconds to an animated GIF |

---

## Quick Start

### Live stream

```bash
python3 live_stream.py
```

### Record to GIF

```bash
python3 record.py              # saves → recording.gif
python3 record.py my_scan.gif  # custom filename
```

The window opens immediately. A **5-second preview** countdown is shown before recording starts, then a **● REC** indicator counts down the 5-second recording window. The GIF is saved in the background and the window stays live afterwards.

---

## Keyboard Controls (live window & recorder)

| Key | Action |
|---|---|
| `s` | Unfreeze / start stream |
| `f` | Freeze / stop stream |
| `m` | Toggle mode: **Curved ↔ Linear** |
| `6` | Depth level 1 (shallowest) |
| `7` | Depth level 2 |
| `8` | Depth level 3 |
| `9` | Depth level 4 (deepest) |
| `[` | Decrease gain (min 30) |
| `]` | Increase gain (max 105) |
| `q` | Dynamic range 40 dB |
| `w` | Dynamic range 50 dB |
| `e` | Dynamic range 60 dB |
| `r` | Dynamic range 70 dB |
| `t` | Dynamic range 80 dB |
| `y` | Dynamic range 90 dB |
| `u` | Dynamic range 100 dB |
| `i` | Dynamic range 110 dB |
| `c` | Frequency low (Curved: 3.2 MHz / Linear: 7.5 MHz) |
| `v` | Frequency high (Curved: H5.0 MHz / Linear: H10.0 MHz) |
| `x` | Quit |

---

## Using `USProbe` Programmatically

```python
from us import USProbe
import time

probe = USProbe(ip='192.168.1.1')
probe.initiate()
time.sleep(2)  # wait for connection

# Read frames
frame = probe.get_latest_frame()   # numpy uint8 array, or None

# Probe controls
probe.set_depth(2)        # levels 1–4
probe.set_gain(70)        # 30–105
probe.set_dynamic_range(60)   # 40/50/60/70/80/90/100/110
probe.set_frequency(3.2)  # curved: 3.2 / 5.0 — linear: 7.5 / 10.0
probe.toggle_mode()       # curved ↔ linear
probe.freeze()
probe.unfreeze()

probe.disconnect()
```

---

## Probe Connection

The probe creates its own Wi-Fi access point. Connect your computer to it, then run any of the scripts above. Default IP is `192.168.1.1`.

| Port | Purpose |
|---|---|
| `5002` | Data (scanline stream) |
| `5003` | Info / command channel |
