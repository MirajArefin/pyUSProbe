"""
record.py — Show the live ultrasound window AND record 5 seconds to a GIF.

Usage:
    python record.py [output.gif]

Controls (same as live_stream.py):
    's' / 'f'   Unfreeze / Freeze
    'm'         Toggle probe mode (Curved ↔ Linear)
    '6'–'9'     Depth levels 1–4
    '[' / ']'   Gain down / up
    'q'–'i'     Dynamic range 40–110
    'c' / 'v'   Frequency low / high
    'x'         Quit

Requirements:
    pip install pillow
"""

import sys
import time
import threading
import numpy as np
import cv2
from PIL import Image
from us import USProbe

# ── Configuration ─────────────────────────────────────────────────────────────
PREVIEW_SECONDS = 5          # live preview before recording starts
RECORD_SECONDS  = 5
TARGET_FPS      = 10
OUTPUT_PATH     = sys.argv[1] if len(sys.argv) > 1 else "recording1.gif"
GIF_FRAME_DELAY = int(1000 / TARGET_FPS)    # ms per GIF frame


def save_gif(frames: list[Image.Image], path: str) -> None:
    """Write frames to an animated GIF (runs in its own thread so the UI stays live)."""
    if not frames:
        print("No frames captured — nothing to save.")
        return
    print(f"\nSaving {len(frames)} frames → {path} …")
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=GIF_FRAME_DELAY,
        loop=0,
        optimize=False,
    )
    print(f"Saved → {path}")


def main():
    probe = USProbe()
    probe.initiate()

    # ── Wait for the first real frame before starting any timers ──────────────
    print("Connecting to probe — waiting for first frame (up to 15 s) …")
    cv2.namedWindow("Live Ultrasound", cv2.WINDOW_NORMAL)
    cached_frame = np.zeros((600, 800), dtype=np.uint8)

    timeout = time.time() + 15.0
    while time.time() < timeout:
        f = probe.get_latest_frame()
        if f is not None:
            cached_frame = f
            print("Stream live — starting preview countdown.")
            break
        # Keep the window alive and responsive while waiting
        cv2.putText(cached_frame.copy(), "Connecting …", (30, 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255,), 2)
        cv2.imshow("Live Ultrasound", cached_frame)
        if cv2.waitKey(100) & 0xFF == ord('x'):
            probe.disconnect()
            cv2.destroyAllWindows()
            return
    else:
        print("Timeout: no frames received. Check probe connection.")
        probe.disconnect()
        cv2.destroyAllWindows()
        return

    # ── UI setup ──────────────────────────────────────────────────────────────
    print(f"\nRecording {RECORD_SECONDS} s at ~{TARGET_FPS} fps → {OUTPUT_PATH}")
    print("Controls: 's' unfreeze | 'f' freeze | 'm' mode | '6-9' depth | "
          "'[/]' gain | 'q-i' DR | 'c/v' freq | 'x' quit\n")

    dr_keys = {
        ord('q'): 40, ord('w'): 50, ord('e'): 60, ord('r'): 70,
        ord('t'): 80, ord('y'): 90, ord('u'): 100, ord('i'): 110,
    }

    # ── Recording state — timers start NOW (stream is confirmed live) ──────────
    gif_frames: list[Image.Image] = []
    frame_interval   = 1.0 / TARGET_FPS
    preview_deadline = time.time() + PREVIEW_SECONDS
    record_deadline  = preview_deadline + RECORD_SECONDS
    next_capture     = preview_deadline
    recording_done   = False

    # ── Main loop ─────────────────────────────────────────────────────────────
    while True:
        now = time.time()

        # Pull the latest frame from the probe
        new_frame = probe.get_latest_frame()
        if new_frame is not None:
            cached_frame = new_frame

        display = cached_frame.copy()

        # ── Recording / preview indicator ─────────────────────────────────────
        if not recording_done:
            if now < preview_deadline:
                # Preview phase — countdown to recording
                remaining_preview = preview_deadline - now
                cv2.putText(display, f"Starting in {remaining_preview:.1f}s …", (30, 38),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,), 2)

            elif now < record_deadline:
                # Recording phase
                remaining_rec = max(0.0, record_deadline - now)
                if now >= next_capture:
                    pil = Image.fromarray(cached_frame.astype(np.uint8), mode="L").convert("P")
                    gif_frames.append(pil)
                    next_capture += frame_interval

                # Red dot + countdown
                cv2.circle(display, (30, 30), 10, (255,), -1)
                cv2.putText(display, f"REC  {remaining_rec:.1f}s", (48, 38),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,), 2)

            else:
                # Recording finished — save in background so the window stays live
                recording_done = True
                threading.Thread(
                    target=save_gif,
                    args=(list(gif_frames), OUTPUT_PATH),
                    daemon=True,
                ).start()

        # ── Status overlay ────────────────────────────────────────────────────
        if probe.is_frozen:
            cv2.putText(display, "FROZEN — press 's' to unfreeze", (50, 580),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,), 2)
        else:
            freq_str = {10.0: "H10.0", 5.0: "H5.0"}.get(probe.current_frequency,
                                                           str(probe.current_frequency))
            info = (f"Mode: {probe.mode.upper()} | Depth: L{probe.current_depth_level} | "
                    f"Gain: {probe.current_gain} | DR: {probe.current_dr} | Freq: {freq_str} MHz")
            cv2.putText(display, info, (20, 580),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,), 2)

        cv2.imshow("Live Ultrasound", display)

        # ── Key handling ──────────────────────────────────────────────────────
        key = cv2.waitKey(30) & 0xFF
        if key == ord('x'):
            break
        elif key == ord('s'):
            probe.unfreeze()
        elif key == ord('f'):
            probe.freeze()
        elif key == ord('m'):
            probe.toggle_mode()
            probe.current_frequency = 3.2 if probe.mode == 'curved' else 7.5
        elif ord('6') <= key <= ord('9'):
            probe.set_depth(key - ord('6') + 1)
        elif key in dr_keys:
            probe.set_dynamic_range(dr_keys[key])
        elif key == ord('c'):
            probe.set_frequency(3.2 if probe.mode == 'curved' else 7.5)
        elif key == ord('v'):
            probe.set_frequency(5.0 if probe.mode == 'curved' else 10.0)
        elif key == ord('['):
            probe.set_gain(max(30, probe.current_gain - 1))
        elif key == ord(']'):
            probe.set_gain(min(105, probe.current_gain + 1))

    probe.disconnect()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
