"""
Camera thread: detecteert nieuwe producten in de bouwcontainer.

Aanpak voor iPhone Continuity Camera (sterke auto-belichting):
- Auto-exposure wordt uitgeschakeld via OpenCV.
- Elk frame wordt gehist-genormaliseerd zodat belichtingswisselingen geen rol spelen.
- Snapshots worden 1,5 sec uit elkaar vergeleken via contourenanalyse.
- Stilstaand beeld scoort 0–7.000; een echt object geeft 35.000+.
- Zodra de beweging stopt wordt vergeleken met de vorige referentie.
"""
import cv2
import threading
import time
import os
from datetime import datetime
from queue import Queue

PHOTOS_DIR = "photos"

MOTION_AREA_THRESHOLD  = 15_000   # contouroppervlak = beweging
SCENE_CHANGE_THRESHOLD = 12_000   # contouroppervlak = nieuw product t.o.v. referentie
SETTLE_SECONDS         = 3.0      # seconden stil = scene tot rust
MIN_CAPTURE_INTERVAL   = 8        # minimale tijd tussen captures
SNAPSHOT_INTERVAL      = 1.5      # interval tussen vergelijkende snapshots


def _normalize(frame):
    """Normaliseert helderheid via histogram-equalisatie op het L-kanaal (LAB)."""
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.equalizeHist(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def _contour_area(frame_a, frame_b):
    """Geeft totale oppervlakte van significante contouren tussen twee frames."""
    g1 = cv2.cvtColor(_normalize(frame_a), cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(_normalize(frame_b), cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(g1, g2)
    _, mask = cv2.threshold(diff, 35, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    cleaned = cv2.dilate(cleaned, kernel, iterations=2)
    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return sum(cv2.contourArea(c) for c in contours if cv2.contourArea(c) > 4_000)


class CameraThread(threading.Thread):
    def __init__(self, capture_queue: Queue, camera_index: int = 0):
        super().__init__(daemon=True)
        self.capture_queue = capture_queue
        self.camera_index = camera_index
        self._running = False
        self._current_frame = None
        self._frame_lock = threading.Lock()

    def run(self):
        self._running = True
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            print(f"[camera] Kon camera {self.camera_index} niet openen.")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)   # handmatige belichting
        cap.set(cv2.CAP_PROP_EXPOSURE, -4)

        # Camera stabiliseren
        print("[camera] Camera stabiliseren (3 sec)...")
        stab_end = time.time() + 3.0
        frame = None
        while time.time() < stab_end:
            ret, f = cap.read()
            if ret:
                frame = f
                with self._frame_lock:
                    self._current_frame = f.copy()
            time.sleep(0.05)

        if frame is None:
            print("[camera] Geen frames ontvangen.")
            cap.release()
            return

        print("[camera] Klaar — wacht op nieuwe producten...")

        reference_frame   = frame.copy()
        prev_snapshot     = frame.copy()
        last_snapshot_t   = time.time()
        last_motion_time  = 0.0
        in_motion         = False
        last_moving_frame = frame.copy()
        last_capture_time = 0.0

        while self._running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            with self._frame_lock:
                self._current_frame = frame.copy()

            now = time.time()

            if now - last_snapshot_t >= SNAPSHOT_INTERVAL:
                area = _contour_area(prev_snapshot, frame)

                if area > MOTION_AREA_THRESHOLD:
                    last_motion_time  = now
                    last_moving_frame = frame.copy()
                    if not in_motion:
                        in_motion = True
                        print(f"[camera] Beweging gedetecteerd (area={area:.0f})")

                elif in_motion and (now - last_motion_time >= SETTLE_SECONDS):
                    in_motion = False
                    settled   = last_moving_frame

                    change = _contour_area(reference_frame, settled)
                    print(f"[camera] Scene tot rust — scènewijziging: {change:.0f} (min: {SCENE_CHANGE_THRESHOLD})")

                    if change > SCENE_CHANGE_THRESHOLD and now - last_capture_time > MIN_CAPTURE_INTERVAL:
                        last_capture_time = now
                        reference_frame   = settled.copy()
                        path = self._save_photo(settled)
                        self.capture_queue.put(path)
                        print(f"[camera] Nieuw product — foto opgeslagen: {path}")
                    else:
                        reference_frame = settled.copy()

                prev_snapshot   = frame.copy()
                last_snapshot_t = now

            time.sleep(0.05)

        cap.release()
        print("[camera] Gestopt.")

    def stop(self):
        self._running = False

    def get_current_frame(self):
        with self._frame_lock:
            return self._current_frame.copy() if self._current_frame is not None else None

    def _save_photo(self, frame) -> str:
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"
        path = os.path.join(PHOTOS_DIR, filename)
        cv2.imwrite(path, frame)
        return path
