import cv2, time, os, subprocess, threading, joblib, ctypes, pyautogui
from datetime import datetime
import numpy as np
from collections import deque
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from cvzone.HandTrackingModule import HandDetector
from math import acos, degrees, hypot, isfinite


def distance(a, b):
    return hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))

def angle(a, b, c):
    ab = (a[0] - b[0], a[1] - b[1])
    cb = (c[0] - b[0], c[1] - b[1])
    length = hypot(*ab) * hypot(*cb)
    if length < 1e-6:
        return 0.0
    cosine = (ab[0] * cb[0] + ab[1] * cb[1]) / length
    return degrees(acos(max(-1.0, min(1.0, cosine))))

class MouseMode:
    def __init__(self, pointer_area=0.75):
        self.area = pointer_area
        self.screen = pyautogui.size()
        pyautogui.PAUSE = 0
        self.pointer = None
        self.aim = None
        self.touch = None
        self.touch_start = 0.0
        self.touch_frames = 0
        self.touch_fired = False
        self.touch_location = None
        self.open_since = None
        self.rearm_at = 0.0
        self.armed_until = 0.0
        self.dragging = False
        self.missing_since = None
        self.status = "MOUSE READY"

    def _call(self, function):

        previous = pyautogui.FAILSAFE
        pyautogui.FAILSAFE = False
        try:
            return function()
        finally:
            pyautogui.FAILSAFE = previous

    def _drop(self):
        if self.dragging:
            self._call(lambda: pyautogui.mouseUp(button="left"))
            self.dragging = False
            self.status = "DROP"

    def reset(self, now=0.0):
        self._drop()
        self.pointer = self.aim = self.touch = self.touch_location = None
        self.open_since = self.missing_since = None
        self.touch_frames = 0
        self.touch_fired = False
        self.rearm_at = self.armed_until = 0.0

    def _position(self, tip, shape):
        height, width = shape[:2]
        margin = (1.0 - self.area) / 2.0
        x = max(0.0, min(1.0, (tip[0] / (width - 1) - margin) / self.area))
        y = max(0.0, min(1.0, (tip[1] / (height - 1) - margin) / self.area))
        return round(x * (self.screen[0] - 1)), round(y * (self.screen[1] - 1))

    def _move(self, target):
        if self.pointer is None:
            self.pointer = tuple(map(float, target))
        else:
            speed = distance(target, self.pointer)
            alpha = min(0.92, max(0.42, 0.42 + speed / 140.0))
            self.pointer = tuple(target[j] if target[j] in (0, self.screen[j] - 1)
                                 else alpha * target[j] + (1 - alpha) * self.pointer[j]
                                 for j in (0, 1))
        self.aim = tuple(round(value) for value in self.pointer)
        self._call(lambda: pyautogui.moveTo(*self.aim))

    def _finish_touch(self, now):
        if self.touch == "drag":
            self._drop()
            self.rearm_at = now + 0.18
        elif self.touch:
            self.rearm_at = now + 0.03
        self.touch = self.touch_location = self.open_since = None
        self.touch_frames = 0
        self.touch_fired = False

    def process(self, hand, shape, now):
        lm = hand.get("lmList", []) if hand else []
        if (len(lm) < 21 or shape[0] < 2 or shape[1] < 2
                or any(len(p) < 2 or not all(isfinite(float(v)) for v in p[:2])
                       for p in lm[:21])):
            if self.missing_since is None:
                self.missing_since = now
            elif now - self.missing_since >= 0.30:
                self.reset(now)
            if not self.dragging:
                self.status = "MOUSE READY"
            return self.status
        self.missing_since = None
        scale = max(15.0, distance(lm[5], lm[17]))
        gaps = [distance(lm[4], lm[n]) for n in (8, 12, 16)]
        extended = []
        for n, (mcp, pip, dip, tip) in enumerate(
                ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20))):
            bend = 128 if n >= 2 else 140
            end = 125 if n >= 2 else 135
            extended.append(angle(lm[mcp], lm[pip], lm[dip]) > bend
                            and angle(lm[pip], lm[dip], lm[tip]) > end
                            and distance(lm[tip], lm[mcp]) >
                            (1.06 if n >= 2 else 1.12) * distance(lm[pip], lm[mcp]))
        index, middle, ring, little = extended
        tip = lm[8]
        if not (0 <= tip[0] < shape[1] and 0 <= tip[1] < shape[0]):
            return self.process(None, shape, now)
        target = self._position(tip, shape)

        if self.touch:
            contact = gaps[2] < 0.72 * scale if self.touch == "drag" else (
                gaps[0 if self.touch == "left" else 1] <= 0.48 * scale)
            if contact:
                self.open_since = None
            elif self.open_since is None:
                self.open_since = now
            clear_open = (self.touch != "drag" and
                          gaps[0 if self.touch == "left" else 1] >= 0.62 * scale)
            release_wait = 0.16 if self.touch == "drag" else 0.05
            if not contact and (clear_open or now - self.open_since >= release_wait):
                self._finish_touch(now)
            elif not contact:
                return self.status

        if self.touch == "drag":
            if not self.dragging and now - self.touch_start >= 0.13:
                self._call(lambda: pyautogui.mouseDown(button="left"))
                self.dragging = True
                self.status = "SELECT / DRAG"
            if self.dragging:
                self._move(target)
            return self.status
        if self.touch in ("left", "right"):
            self.touch_frames += 1
            if self.touch_frames >= 2 and not self.touch_fired:
                self.touch_fired = True
                kind = self.touch
                self._call(lambda: pyautogui.click(*self.touch_location, button=kind))
                self.status = f"{kind.upper()} CLICK"
            return self.status

        if index and not middle and not ring and not little:
            self.armed_until = now + 1.5
        if index and middle and not ring:
            return self.status
        if now >= self.rearm_at and now <= self.armed_until and not self.dragging:
            reach = distance(lm[8], lm[5]) / max(1.0, distance(lm[6], lm[5]))
            if reach > 0.85:
                kind = None
                if (index and not middle and gaps[2] <= 0.48 * scale
                        and gaps[2] < 0.80 * min(gaps[0], gaps[1])):
                    kind = "drag"
                elif gaps[0] <= 0.34 * scale and gaps[0] < 0.90 * gaps[1]:
                    kind = "left"
                elif gaps[1] <= 0.34 * scale and gaps[1] < 0.90 * gaps[0]:
                    kind = "right"
                if kind:
                    self.touch = kind
                    self.touch_start = now
                    self.touch_frames = 1
                    self.touch_fired = False
                    self.touch_location = self.aim or target
                    return self.status
        if index and not middle and not ring and not little:
            self._move(target)
            self.status = "POINTER"
        else:
            self.pointer = self.aim = None
        return self.status


VK_MENU = 0x12
VK_TAB = 0x09
VK_SHIFT = 0x10
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
MOUSEEVENTF_WHEEL = 0x0800

user32 = ctypes.windll.user32

class WindowsBrightnessController:

    def __init__(self):
        self._current_brightness = 100
        self._target_brightness = 100
        self._lock = threading.Lock()
        self._has_work = threading.Event()
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def get_brightness(self) -> int:

        with self._lock:
            return self._target_brightness

    def set_brightness(self, percent: int) -> int:

        percent = max(0, min(100, int(percent)))
        with self._lock:
            self._target_brightness = percent
            self._has_work.set()
            return self._target_brightness

    def adjust_brightness(self, delta: int) -> int:

        with self._lock:
            new_val = max(0, min(100, self._target_brightness + delta))
            self._target_brightness = new_val
            self._has_work.set()
            return self._target_brightness

    def _worker(self):

        use_com = False
        mons = []
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()
            loc = comtypes.client.CreateObject("WbemScripting.SWbemLocator")
            svc = loc.ConnectServer(".", "root\\wmi")
            mons = [m for m in svc.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods")]
            cur_q = [q for q in svc.ExecQuery("SELECT * FROM WmiMonitorBrightness")]
            if cur_q:
                initial_val = int(cur_q[0].Properties_("CurrentBrightness").Value)
                with self._lock:
                    self._current_brightness = initial_val
                    self._target_brightness = initial_val
            if mons:
                use_com = True
        except Exception:
            use_com = False

        if not use_com:
            self._query_initial_powershell()
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        while self._running:
            self._has_work.wait(timeout=0.015)
            if not self._running:
                break
            with self._lock:
                target = self._target_brightness
                current = self._current_brightness
                self._has_work.clear()

            if target != current:
                if use_com:
                    try:
                        for m in mons:
                            in_p = m.Methods_("WmiSetBrightness").InParameters.SpawnInstance_()
                            in_p.Properties_("Timeout").Value = 1
                            in_p.Properties_("Brightness").Value = target
                            m.ExecMethod_("WmiSetBrightness", in_p)
                        with self._lock:
                            self._current_brightness = target
                    except Exception:
                        self._exec_powershell_set(target, creation_flags)
                else:
                    self._exec_powershell_set(target, creation_flags)

        if use_com:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass

    def _query_initial_powershell(self):
        try:
            cmd = (
                "(Get-CimInstance -Namespace root/WMI "
                "-ClassName WmiMonitorBrightness | "
                "Select-Object -First 1).CurrentBrightness"
            )
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                check=True,
                timeout=4,
                creationflags=creation_flags
            )
            val = int(result.stdout.strip())
            with self._lock:
                self._current_brightness = val
                self._target_brightness = val
        except Exception:
            pass

    def _exec_powershell_set(self, target: int, creation_flags: int):
        try:
            command = (
                "$m = Get-CimInstance -Namespace root/WMI "
                "-ClassName WmiMonitorBrightnessMethods; "
                "$m | ForEach-Object { "
                "Invoke-CimMethod -InputObject $_ "
                "-MethodName WmiSetBrightness "
                f"-Arguments @{{Timeout=1; Brightness={target}}} "
                "}"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
                creationflags=creation_flags
            )
            with self._lock:
                self._current_brightness = target
        except Exception:
            pass

    def stop(self):
        self._running = False
        self._has_work.set()

def send_mouse_wheel(delta: int):
    try:
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(delta), 0)
    except Exception:
        pass

def send_key_down(vk_code: int):
    user32.keybd_event(vk_code, 0, 0, 0)


def send_key_up(vk_code: int):
    user32.keybd_event(vk_code, 0, 2, 0)


def send_key_tap(vk_code: int, delay_s: float = 0.015):
    send_key_down(vk_code)
    time.sleep(delay_s)
    send_key_up(vk_code)


def get_audio_endpoint():
    try:
        devices = AudioUtilities.GetSpeakers()
        if devices is None:
            return None
        if hasattr(devices, "EndpointVolume"):
            return devices.EndpointVolume
        if hasattr(devices, "Activate"):
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        pass
    return None


def get_finger_extensions(pts):
    if len(pts) < 21:
        return [False] * 5
    wrist = pts[0]
    p17 = pts[17]
    d_tip4 = hypot(pts[4][0] - p17[0], pts[4][1] - p17[1])
    d_ip3 = hypot(pts[3][0] - p17[0], pts[3][1] - p17[1])
    thumb_ext = d_tip4 > d_ip3 * 1.06

    tips = [8, 12, 16, 20]
    pips = [6, 10, 14, 18]
    ext = [thumb_ext]
    for t_i, p_i in zip(tips, pips):
        d_tip = hypot(pts[t_i][0] - wrist[0], pts[t_i][1] - wrist[1])
        d_pip = hypot(pts[p_i][0] - wrist[0], pts[p_i][1] - wrist[1])
        ext.append(d_tip > d_pip * 1.12)
    return ext


def extract_features_from_cvzone_landmarks(lm_list):
    pts = np.array([[pt[0], pt[1], pt[2] if len(pt) > 2 else 0.0] for pt in lm_list], dtype=np.float64)
    if len(pts) != 21:
        return None
    wrist = pts[0].copy()
    pts -= wrist
    mcp_mid = pts[9]
    scale = np.linalg.norm(mcp_mid)
    if scale < 1e-6:
        scale = 1.0
    pts /= scale

    features = []
    features.extend(pts.flatten())

    finger_joints = [
        [(0, 1, 2), (1, 2, 3), (2, 3, 4)],
        [(0, 5, 6), (5, 6, 7), (6, 7, 8)],
        [(0, 9, 10), (9, 10, 11), (10, 11, 12)],
        [(0, 13, 14), (13, 14, 15), (14, 15, 16)],
        [(0, 17, 18), (17, 18, 19), (18, 19, 20)],
    ]
    for finger in finger_joints:
        for (a, b, c) in finger:
            v1 = pts[a] - pts[b]
            v2 = pts[c] - pts[b]
            cos_angle = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-8)
            features.append(float(np.arccos(np.clip(cos_angle, -1.0, 1.0))))

    tips = [4, 8, 12, 16, 20]
    for i in range(len(tips)):
        for j in range(i + 1, len(tips)):
            features.append(float(np.linalg.norm(pts[tips[i]] - pts[tips[j]])))

    pip_indices = [3, 6, 10, 14, 18]
    tip_indices = [4, 8, 12, 16, 20]
    for pip_i, tip_i in zip(pip_indices, tip_indices):
        features.append(1.0 if np.linalg.norm(pts[tip_i]) > np.linalg.norm(pts[pip_i]) else 0.0)

    return np.array(features, dtype=np.float32)

def main():
    cap = None
    for cam_idx in [0, 1, 2]:
        temp_cap = cv2.VideoCapture(cam_idx, cv2.CAP_DSHOW)
        if temp_cap.isOpened():
            ret, test_frame = temp_cap.read()
            if ret:
                cap = temp_cap
                break
            temp_cap.release()
        else:
            temp_cap.release()

    if cap is None:
        cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    audio_endpoint = get_audio_endpoint()
    hand_detector = HandDetector(staticMode=False, maxHands=2, detectionCon=0.75, minTrackCon=0.6)
    model_path = os.path.join("models", "static_gesture_rf.joblib")
    ml_model = None
    label_encoder = None
    if os.path.exists(model_path):
        try:
            data = joblib.load(model_path)
            if isinstance(data, dict):
                ml_model = data.get("model")
                label_encoder = data.get("classes")
            else:
                ml_model = data
        except Exception:
            pass

    clutch_engaged = False
    clutch_consecutive_frames = 0
    last_clutch_toggle_time = 0.0
    clutch_latch = False

    mouse_mode = MouseMode(pointer_area=0.75)
    mouse_clutch = False
    ok_since = None
    ok_latched = False
    last_ok_toggle = 0.0

    thumbs_up_latched = False
    thumbs_down_latched = False

    fist_volume_active = False
    fist_anchor_y = None
    last_volume_step_time = 0.0
    last_fist_volume_time = 0.0

    alt_tab_active = False
    alt_tab_anchor_x = None
    alt_release_timer = 0.0
    ALT_RELEASE_COOLDOWN = 0.45
    last_tab_step_time = 0.0
    three_finger_hold_start = None
    two_finger_hold_start = None
    ALT_TAB_HOLD_REQUIRED = 0.35
    alt_tab_deflection_side = 0
    alt_tab_deflection_start = 0.0

    fist_win_active = False
    fist_win_anchor_y = None
    fist_win_last_step_y = None
    fist_win_last_step_time = 0.0
    WIN_SNAP_DEADZONE = 70
    WIN_SNAP_STEP_INTERVAL = 0.5

    two_finger_scroll_active = False
    two_finger_scroll_anchor_y = None
    last_scroll_step_time = 0.0

    brightness_ctrl = WindowsBrightnessController()
    brightness_scroll_active = False
    brightness_last_y = None
    brightness_subpixel_acc = 0.0
    brightness_stabilize_frames = 0

    pinch_currently_closed = False
    pinch_count = 0
    last_pinch_time = 0.0
    last_media_toggle_time = 0.0

    history_centers = deque(maxlen=16)
    history_times = deque(maxlen=16)
    last_swipe_time = 0.0
    last_swipe_direction = 0
    last_mute_unmute_time = 0.0

    last_two_hand_dist = None
    last_zoom_time = 0.0

    screenshot_hold_start = None
    screenshot_cooldown = 0.0
    screenshot_flash_frames = 0

    recent_action_text = "SYSTEM READY"
    recent_action_time = time.time()
    streak_fist=0
    while True:
        success, img = cap.read()
        if not success:
            time.sleep(0.01)
            success, img = cap.read()
            if not success:
                break

        img = cv2.flip(img, 1)
        now = time.time()
        hands, img = hand_detector.findHands(img, draw=True, flipType=False)

        ok_seen = False
        if hands:
            ok_lm = hands[0].get("lmList", [])
            if len(ok_lm) >= 21:
                ok_ext = get_finger_extensions(ok_lm)
                ok_scale = max(15.0, hypot(
                    ok_lm[5][0] - ok_lm[17][0], ok_lm[5][1] - ok_lm[17][1]))
                ok_gap = hypot(ok_lm[4][0] - ok_lm[8][0],
                                    ok_lm[4][1] - ok_lm[8][1])
                ok_seen = (ok_gap <= 0.34 * ok_scale
                           and ok_ext[2] and ok_ext[3] and ok_ext[4])
        if ok_seen:
            if ok_since is None:
                ok_since = now
            if (not ok_latched and now - ok_since >= 0.60
                    and now - last_ok_toggle >= 1.2):
                mouse_clutch = not mouse_clutch
                last_ok_toggle = now
                ok_latched = True
                mouse_mode.reset(now)
                if alt_tab_active:
                    send_key_up(VK_MENU)
                    alt_tab_active = False
                if fist_win_active:
                    send_key_up(0x5B)
                    fist_win_active = False
                clutch_consecutive_frames = 0
                clutch_latch = False
                pinch_count = 0
                pinch_currently_closed = False
                recent_action_text = "MOUSE MODE ON" if mouse_clutch else "MOUSE MODE OFF"
                recent_action_time = now
        else:
            ok_since = None
            ok_latched = False

        if mouse_clutch and not ok_seen:
            try:
                mouse_status = mouse_mode.process(hands[0] if hands else None, img.shape, now)
                if mouse_status not in ("POINTER", "MOUSE READY"):
                    recent_action_text = mouse_status
                    recent_action_time = now
            except Exception as exc:
                mouse_mode.reset(now)
                mouse_clutch = False
                recent_action_text = f"MOUSE ERROR: {type(exc).__name__}"
                recent_action_time = now

        current_gesture = "NEUTRAL"
        confidence = 0.0

        two_hand_screenshot = False
        if len(hands) >= 2 and not mouse_clutch and not ok_seen:
            lm1 = hands[0].get("lmList", [])
            lm2 = hands[1].get("lmList", [])
            if len(lm1) >= 21 and len(lm2) >= 21:
                e1 = get_finger_extensions(lm1)
                e2 = get_finger_extensions(lm2)

                if (e1[0] and e1[1] and not e1[3] and not e1[4]) and (e2[0] and e2[1] and not e2[3] and not e2[4]):
                    two_hand_screenshot = True

            if not two_hand_screenshot:
                h1 = hands[0].get("center", (0, 0))
                h2 = hands[1].get("center", (0, 0))
                dist = hypot(h1[0] - h2[0], h1[1] - h2[1])

                if last_two_hand_dist is not None and (now - last_zoom_time) > 0.18:
                    delta_dist = dist - last_two_hand_dist
                    if abs(delta_dist) > 18.0:
                        if delta_dist > 0:
                            pyautogui.hotkey("ctrl", "=")
                            recent_action_text = "ZOOM IN"
                        else:
                            pyautogui.hotkey("ctrl", "-")
                            recent_action_text = "ZOOM OUT"
                        recent_action_time = now
                        last_zoom_time = now
                last_two_hand_dist = dist
            else:
                last_two_hand_dist = None
        else:
            last_two_hand_dist = None

        if len(hands) > 0 and not mouse_clutch and not ok_seen:
            hand = hands[0]
            lm_list = hand.get("lmList", [])
            center = hand.get("center", (0, 0))

            history_centers.append(center)
            history_times.append(now)

            ext = get_finger_extensions(lm_list) if len(lm_list) >= 21 else [False] * 5
            if ml_model is not None and len(lm_list) == 21:
                feat = extract_features_from_cvzone_landmarks(lm_list)
                if feat is not None:
                    probs = ml_model.predict_proba(feat.reshape(1, -1))[0]
                    best_idx = int(np.argmax(probs))
                    best_conf = float(probs[best_idx])
                    if best_conf >= 0.65:
                        confidence = best_conf
                        if label_encoder is not None:
                            current_gesture = label_encoder[best_idx]
                        else:
                            current_gesture = str(best_idx)

            is_yo = (ext[1] and ext[4] and not ext[2] and not ext[3]) or (ext[0] and ext[4] and not ext[1] and not ext[2] and not ext[3])

            is_three = ext[1] and ext[2] and ext[3] and not ext[4]

            is_two = ext[1] and ext[2] and not ext[3] and not ext[4]

            is_ring_pinky = ext[3] and ext[4] and not ext[1] and not ext[2]


            d_8_12 = 100.0
            d_8_12_norm = 1.0
            if len(lm_list) >= 21:
                hand_scale = max(1.0, hypot(lm_list[0][0] - lm_list[9][0], lm_list[0][1] - lm_list[9][1]))
                d_8_12 = hypot(lm_list[8][0] - lm_list[12][0], lm_list[8][1] - lm_list[12][1])
                d_8_12_norm = d_8_12 / hand_scale

            is_two_close = is_two and (d_8_12_norm < 0.38 or d_8_12 < 48.0)

            is_fist = not any(ext) or (current_gesture == "FIST" and confidence >= 0.70)
            is_thumb_only = ext[0] and not ext[1] and not ext[2] and not ext[3] and not ext[4]
            is_thumb_up = (is_thumb_only and len(lm_list) >= 21 and (lm_list[4][1] < lm_list[2][1])) or (current_gesture == "THUMBS_UP" and confidence >= 0.75)
            is_thumb_down = (is_thumb_only and len(lm_list) >= 21 and (lm_list[4][1] > lm_list[2][1])) or (current_gesture == "THUMBS_DOWN" and confidence >= 0.75)

            if is_fist:
                last_fist_volume_time = now
                pinch_count = 0
                pinch_currently_closed = False


            is_pinch = False
            pinch_dist_norm = 1.0
            if len(lm_list) >= 21 and not is_fist and (now - last_fist_volume_time) > 0.40:
                hand_scale = max(1.0, hypot(lm_list[0][0] - lm_list[9][0], lm_list[0][1] - lm_list[9][1]))
                pinch_px = hypot(lm_list[4][0] - lm_list[8][0], lm_list[4][1] - lm_list[8][1])
                pinch_dist_norm = pinch_px / hand_scale


                d4_12_px = hypot(lm_list[4][0] - lm_list[12][0], lm_list[4][1] - lm_list[12][1])
                d4_12_norm = d4_12_px / hand_scale


                model_is_pinch = (current_gesture == "PINCH_INDEX_THUMB" and confidence >= 0.70)
                geom_is_pinch = (pinch_dist_norm < 0.28 or pinch_px < 35.0) and (d4_12_norm > 0.25 or d4_12_px > 24.0)

                if model_is_pinch or geom_is_pinch:
                    is_pinch = True
                    current_gesture = "PINCH_INDEX_THUMB"
                    confidence = max(confidence, 0.95)


            is_l_frame = ext[0] and ext[1] and not ext[2] and not ext[3] and not ext[4] and not is_pinch


            if is_yo:
                current_gesture = "YO_CLUTCH"
                confidence = max(confidence, 0.98)
            elif is_fist:
                current_gesture = "FIST"
                confidence = max(confidence, 0.95)
            elif is_three:
                current_gesture = "THREE_FINGERS"
                confidence = max(confidence, 0.96)
            elif is_two_close:
                current_gesture = "TWO_FINGERS_CLOSE"
                confidence = max(confidence, 0.96)
            elif is_two:
                current_gesture = "TWO_FINGERS"
                confidence = max(confidence, 0.90)
            elif is_l_frame:
                current_gesture = "CAMERA_FRAME_SCREENSHOT"
                confidence = max(confidence, 0.95)
            elif is_thumb_up:
                current_gesture = "THUMBS_UP"
                confidence = max(confidence, 0.95)
            elif is_thumb_down:
                current_gesture = "THUMBS_DOWN"
                confidence = max(confidence, 0.95)

            if is_yo:
                clutch_consecutive_frames += 1
                if clutch_consecutive_frames >= 15 and not clutch_latch and (now - last_clutch_toggle_time) > 1.2:
                    clutch_engaged = not clutch_engaged
                    last_clutch_toggle_time = now
                    clutch_latch = True
                    recent_action_text = f"CLUTCH -> {'ON (MEDIA MODE)' if clutch_engaged else 'OFF (WINDOW MODE)'} (Yo Sign 🤘)"
                    recent_action_time = now

                    if alt_tab_active:
                        send_key_up(VK_MENU)
                        alt_tab_active = False
            else:
                clutch_consecutive_frames = 0
                clutch_latch = False

            is_screenshot_active = two_hand_screenshot or is_l_frame
            if is_screenshot_active:
                if screenshot_hold_start is None:
                    screenshot_hold_start = now
                hold_shot = now - screenshot_hold_start

                if hold_shot >= 0.85 and (now - screenshot_cooldown) > 1.5:
                    os.makedirs("screenshots", exist_ok=True)
                    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                    shot_path = os.path.join("screenshots", f"screenshot_{ts_str}.png")
                    try:
                        shot_img = pyautogui.screenshot()
                        shot_img.save(shot_path)
                    except Exception:
                        pass
                    pyautogui.hotkey("win", "prtsc")
                    screenshot_cooldown = now
                    screenshot_hold_start = None
                    screenshot_flash_frames = 3
                    recent_action_text = f"SCREENSHOT SAVED ({ts_str})"
                    recent_action_time = now
                elif hold_shot > 0.15:
                    remaining_s = max(0.0, 0.85 - hold_shot)
                    recent_action_text = f"HOLD FOR SCREENSHOT ({remaining_s:.1f}s)"
                    recent_action_time = now
            else:
                screenshot_hold_start = None

            if not clutch_engaged:

                if is_fist and not alt_tab_active and len(lm_list) >= 21:
                    streak_fist+=1
                    if streak_fist>=20:
                        wrist_y = float(lm_list[0][1])
                        if not fist_win_active:

                            send_key_down(0x5B)
                            fist_win_active = True
                            fist_win_anchor_y = wrist_y
                            fist_win_last_step_y = wrist_y
                            fist_win_last_step_time = now
                            recent_action_text = "FIST WIN MODE (Move Up/Down to Snap)"
                            recent_action_time = now
                        else:
                            dy = wrist_y - fist_win_last_step_y
                            if abs(dy) >= WIN_SNAP_DEADZONE and (now - fist_win_last_step_time) >= WIN_SNAP_STEP_INTERVAL:
                                if dy > 0:
                                    send_key_tap(0x28)
                                    recent_action_text = "WIN + DOWN ⬇ (Snap/Minimize)"
                                else:
                                    send_key_tap(0x26)
                                    recent_action_text = "WIN + UP ⬆ (Maximize/Restore)"
                                recent_action_time = now
                                fist_win_last_step_y = wrist_y
                                fist_win_last_step_time = now
                else:
                    streak_fist=0
                    if fist_win_active:

                        send_key_up(0x5B)
                        fist_win_active = False
                        fist_win_anchor_y = None
                        fist_win_last_step_y = None
                        
                is_three_active = is_three or (current_gesture == "THREE_FINGERS" and confidence >= 0.70)

                hand_holding_alt = is_three_active or (alt_tab_active and not is_fist and not is_yo and not is_two_close and (ext[1] and ext[2]))

                if is_three_active:
                    if three_finger_hold_start is None:
                        three_finger_hold_start = now

                    hold_duration = now - three_finger_hold_start

                    if not alt_tab_active and hold_duration >= ALT_TAB_HOLD_REQUIRED:
                        send_key_down(VK_MENU)
                        time.sleep(0.02)
                        send_key_tap(VK_TAB)
                        alt_tab_active = True
                        palm_ref_x = lm_list[9][0] if len(lm_list) >= 21 else center[0]
                        alt_tab_anchor_x = palm_ref_x
                        alt_tab_deflection_side = 0
                        alt_tab_deflection_start = 0.0
                        last_tab_step_time = now
                        recent_action_text = "ALT-TAB SWITCHER ACTIVE (Three Fingers)"
                        recent_action_time = now
                else:
                    three_finger_hold_start = None
                    two_finger_hold_start = None

                if alt_tab_active and hand_holding_alt:
                    alt_release_timer = now

                    palm_x = lm_list[9][0] if len(lm_list) >= 21 else center[0]
                    if alt_tab_anchor_x is not None:
                        dx = palm_x - alt_tab_anchor_x
                        TAB_DEADZONE = 24.0

                        if dx >= TAB_DEADZONE:

                            if alt_tab_deflection_side != 1:

                                send_key_tap(VK_TAB)
                                last_tab_step_time = now
                                alt_tab_deflection_side = 1
                                alt_tab_deflection_start = now
                                recent_action_text = "ALT-TAB -> NEXT"
                                recent_action_time = now
                            else:

                                hold_time = now - alt_tab_deflection_start
                                if hold_time >= 0.22:
                                    step_interval = 0.10 if dx > 55.0 else 0.16
                                    if (now - last_tab_step_time) > step_interval:
                                        send_key_tap(VK_TAB)
                                        last_tab_step_time = now
                                        recent_action_text = "ALT-TAB -> NEXT (Fast)" if step_interval < 0.12 else "ALT-TAB -> NEXT"
                                        recent_action_time = now
                        elif dx <= -TAB_DEADZONE:

                            if alt_tab_deflection_side != -1:

                                send_key_down(VK_SHIFT)
                                time.sleep(0.008)
                                send_key_tap(VK_TAB)
                                time.sleep(0.008)
                                send_key_up(VK_SHIFT)
                                last_tab_step_time = now
                                alt_tab_deflection_side = -1
                                alt_tab_deflection_start = now
                                recent_action_text = "ALT-TAB -> PREV"
                                recent_action_time = now
                            else:

                                hold_time = now - alt_tab_deflection_start
                                if hold_time >= 0.22:
                                    step_interval = 0.10 if dx < -55.0 else 0.16
                                    if (now - last_tab_step_time) > step_interval:
                                        send_key_down(VK_SHIFT)
                                        time.sleep(0.008)
                                        send_key_tap(VK_TAB)
                                        time.sleep(0.008)
                                        send_key_up(VK_SHIFT)
                                        last_tab_step_time = now
                                        recent_action_text = "ALT-TAB -> PREV (Fast)" if step_interval < 0.12 else "ALT-TAB -> PREV"
                                        recent_action_time = now
                        else:

                            alt_tab_deflection_side = 0
                            alt_tab_deflection_start = 0.0
                            alt_tab_anchor_x = alt_tab_anchor_x * 0.85 + palm_x * 0.15


                if alt_tab_active and not hand_holding_alt:
                    if (now - alt_release_timer) > ALT_RELEASE_COOLDOWN:
                        send_key_up(VK_MENU)
                        alt_tab_active = False
                        alt_tab_anchor_x = None
                        alt_tab_deflection_side = 0
                        alt_tab_deflection_start = 0.0
                        recent_action_text = "WINDOW SWITCHED"
                        recent_action_time = now

                is_scroll_pose = is_two_close and not alt_tab_active
                if is_scroll_pose:
                    if not two_finger_scroll_active:
                        two_finger_scroll_active = True
                        two_finger_scroll_anchor_y = center[1]
                        last_scroll_step_time = now
                        recent_action_text = "TWO-FINGER SCROLL READY"
                        recent_action_time = now

                    if two_finger_scroll_anchor_y is not None and (now - last_scroll_step_time) > 0.050:
                        dy_scroll = two_finger_scroll_anchor_y - center[1]

                        if abs(dy_scroll) >= 18.0:
                            eff_dy = abs(dy_scroll) - 18.0
                            direction = 1 if dy_scroll > 0 else -1
                            scroll_speed = direction * int(35 + min(eff_dy * 2.0, 150))
                            send_mouse_wheel(scroll_speed)
                            recent_action_text = f"SCROLL {'UP' if dy_scroll > 0 else 'DOWN'} (Two Fingers)"
                            recent_action_time = now
                            last_scroll_step_time = now
                        else:
                            two_finger_scroll_anchor_y = two_finger_scroll_anchor_y * 0.85 + center[1] * 0.15
                else:
                    two_finger_scroll_active = False
                    two_finger_scroll_anchor_y = None

                is_bri_pose = is_ring_pinky and not alt_tab_active and not is_fist
                if is_bri_pose:
                    cur_y = float(lm_list[9][1]) if len(lm_list) >= 21 else float(center[1])

                    if not brightness_scroll_active:
                        brightness_scroll_active = True
                        brightness_last_y = cur_y
                        brightness_subpixel_acc = 0.0
                        brightness_stabilize_frames = 3
                        cur_bri = brightness_ctrl.get_brightness()
                        recent_action_text = f"BRIGHTNESS SCROLL ({cur_bri}%) — Move Hand Up/Down"
                        recent_action_time = now

                    if brightness_stabilize_frames > 0:
                        brightness_last_y = cur_y
                        brightness_stabilize_frames -= 1
                    else:
                        raw_dy = float(brightness_last_y - cur_y)
                        brightness_last_y = cur_y
                        if abs(raw_dy) < 1.8:
                            raw_dy = 0.0
                        speed_mult = 1.0 + min(abs(raw_dy) * 0.04, 1.8)
                        brightness_subpixel_acc += (raw_dy * 0.33) * speed_mult
                        if abs(brightness_subpixel_acc) >= 1.0:
                            step = int(brightness_subpixel_acc)
                            brightness_subpixel_acc -= step
                            new_bri = brightness_ctrl.adjust_brightness(step)
                            recent_action_text = f"BRIGHTNESS: {new_bri}% ({'⬆' if step > 0 else '⬇'})"
                            recent_action_time = now
                else:
                    brightness_scroll_active = False
                    brightness_last_y = None
                    brightness_subpixel_acc = 0.0
                    brightness_stabilize_frames = 0

            if clutch_engaged:

                two_finger_scroll_active = False
                two_finger_scroll_anchor_y = None
                brightness_scroll_active = False
                brightness_last_y = None
                brightness_subpixel_acc = 0.0
                brightness_stabilize_frames = 0
                if fist_win_active:
                    send_key_up(0x5B)
                    fist_win_active = False
                    fist_win_last_step_y = None
                if alt_tab_active:
                    send_key_up(VK_MENU)
                    alt_tab_active = False
                    alt_tab_anchor_x = None
                    alt_tab_deflection_side = 0
                    alt_tab_deflection_start = 0.0

                if is_fist:
                    pinch_count = 0
                    pinch_currently_closed = False
                    last_fist_volume_time = now

                    if not fist_volume_active:
                        fist_volume_active = True
                        fist_anchor_y = center[1]
                        last_volume_step_time = now

                    if fist_anchor_y is not None and (now - last_volume_step_time) > 0.04:
                        dy_vol = fist_anchor_y - center[1]
                        if abs(dy_vol) >= 12.0:
                            step = 0.03 if dy_vol > 0 else -0.03
                            if audio_endpoint is not None:
                                try:
                                    cur_vol = audio_endpoint.GetMasterVolumeLevelScalar()
                                    new_vol = max(0.0, min(1.0, cur_vol + step))
                                    audio_endpoint.SetMasterVolumeLevelScalar(new_vol, None)
                                except Exception:
                                    pass
                            else:
                                send_key_tap(VK_VOLUME_UP if dy_vol > 0 else VK_VOLUME_DOWN)

                            recent_action_text = f"VOLUME {'UP' if dy_vol > 0 else 'DOWN'}"
                            recent_action_time = now
                            fist_anchor_y = center[1]
                            last_volume_step_time = now
                else:
                    fist_volume_active = False
                    fist_anchor_y = None

                if is_thumb_up:
                    if not thumbs_up_latched:
                        if audio_endpoint is not None:
                            try:
                                audio_endpoint.SetMute(0, None)
                            except Exception:
                                pass
                        else:
                            send_key_tap(VK_VOLUME_MUTE)
                        recent_action_text = "AUDIO UNMUTED (Thumbs Up)"
                        recent_action_time = now
                        last_mute_unmute_time = now
                        thumbs_up_latched = True
                else:
                    thumbs_up_latched = False

                if is_thumb_down:
                    if not thumbs_down_latched:
                        if audio_endpoint is not None:
                            try:
                                audio_endpoint.SetMute(1, None)
                            except Exception:
                                pass
                        else:
                            send_key_tap(VK_VOLUME_MUTE)
                        recent_action_text = "AUDIO MUTED (Thumbs Down)"
                        recent_action_time = now
                        last_mute_unmute_time = now
                        thumbs_down_latched = True
                else:
                    thumbs_down_latched = False

                if is_pinch and not fist_volume_active and (now - last_fist_volume_time) > 0.40:
                    if not pinch_currently_closed:
                        pinch_currently_closed = True
                        if (now - last_pinch_time) < 0.65:
                            pinch_count += 1
                        else:
                            pinch_count = 1
                        last_pinch_time = now

                        if pinch_count == 1:
                            recent_action_text = "PINCH 1/2 (Tap again to Play/Pause)"
                            recent_action_time = now
                        elif pinch_count >= 2 and (now - last_media_toggle_time) > 0.60:
                            send_key_tap(VK_MEDIA_PLAY_PAUSE)
                            recent_action_text = "MEDIA PLAY / PAUSE TOGGLED"
                            recent_action_time = now
                            last_media_toggle_time = now
                            pinch_count = 0
                else:
                    pinch_currently_closed = False
                    if pinch_count == 1 and (now - last_pinch_time) > 0.65:
                        pinch_count = 0

                is_open_swipe_pose = (sum(ext[1:]) >= 3) and not is_fist and not is_pinch and not is_yo
                time_since_other_gestures = min(
                    now - last_fist_volume_time,
                    now - last_pinch_time,
                    now - last_clutch_toggle_time,
                    now - last_mute_unmute_time
                )

                if is_open_swipe_pose and not fist_volume_active and time_since_other_gestures > 0.45:
                    if len(history_centers) >= 4:
                        curr_x, curr_y = history_centers[-1]
                        curr_t = history_times[-1]

                        best_swipe_dx = 0.0
                        best_swipe_dy = 0.0
                        best_swipe_dt = 1.0

                        for k in range(len(history_centers) - 2, max(-1, len(history_centers) - 10), -1):
                            prev_x, prev_y = history_centers[k]
                            prev_t = history_times[k]
                            dt = curr_t - prev_t
                            if 0.06 <= dt <= 0.32:
                                dx = curr_x - prev_x
                                dy = curr_y - prev_y
                                if abs(dx) > abs(best_swipe_dx):
                                    best_swipe_dx = dx
                                    best_swipe_dy = dy
                                    best_swipe_dt = dt

                        if abs(best_swipe_dx) >= 78.0 and best_swipe_dt > 0.0:
                            velocity_x = abs(best_swipe_dx) / best_swipe_dt
                            is_horizontal = abs(best_swipe_dx) >= abs(best_swipe_dy) * 1.65

                            is_opposite_recoil = (
                                (best_swipe_dx > 0 and last_swipe_direction == -1) or
                                (best_swipe_dx < 0 and last_swipe_direction == 1)
                            )
                            required_cooldown = 1.15 if is_opposite_recoil else 0.85

                            if velocity_x >= 300.0 and is_horizontal and (now - last_swipe_time) > required_cooldown:
                                if best_swipe_dx < 0:
                                    send_key_tap(VK_MEDIA_NEXT_TRACK)
                                    recent_action_text = "NEXT TRACK ->> (Swipe Left)"
                                    last_swipe_direction = -1
                                else:
                                    send_key_tap(VK_MEDIA_PREV_TRACK)
                                    recent_action_text = "<<- PREVIOUS TRACK (Swipe Right)"
                                    last_swipe_direction = 1

                                recent_action_time = now
                                last_swipe_time = now
                                history_centers.clear()
                                history_times.clear()

        else:

            three_finger_hold_start = None
            two_finger_hold_start = None
            fist_volume_active = False
            two_finger_scroll_active = False
            two_finger_scroll_anchor_y = None
            brightness_scroll_active = False
            brightness_last_y = None
            brightness_subpixel_acc = 0.0
            brightness_stabilize_frames = 0
            screenshot_hold_start = None
            if fist_win_active:
                send_key_up(0x5B)
                fist_win_active = False
                fist_win_last_step_y = None


            if alt_tab_active:
                if (now - alt_release_timer) > ALT_RELEASE_COOLDOWN:
                    send_key_up(VK_MENU)
                    alt_tab_active = False
                    alt_tab_anchor_x = None
                    alt_tab_deflection_side = 0
                    alt_tab_deflection_start = 0.0

        h_frame, w_frame = img.shape[:2]

        cv2.rectangle(img, (0, 0), (w_frame, 85), (20, 20, 20), -1)

        clutch_color = (0, 255, 0) if clutch_engaged else (255, 200, 0)
        mode_text = ("MOUSE MODE: ON (OK TO EXIT)" if mouse_clutch else
                     "CLUTCH: ON (VOLUME / MEDIA MODE)" if clutch_engaged else
                     "CLUTCH: OFF (WINDOW / NAV MODE)")
        cv2.putText(img, mode_text, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.72, clutch_color, 2)

        cv2.putText(img, f"Gesture: {current_gesture} ({confidence:.2f})", (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (240, 240, 240), 2)

        vol_pct = 50
        is_muted = False
        if audio_endpoint is not None:
            try:
                vol_pct = int(audio_endpoint.GetMasterVolumeLevelScalar() * 100)
                is_muted = bool(audio_endpoint.GetMute())
            except Exception:
                pass

        bri_pct = brightness_ctrl.get_brightness()
        mute_str = "MUTED" if is_muted else "UNMUTED"
        mute_color = (0, 0, 255) if is_muted else (0, 255, 0)
        cv2.putText(img, f"Bri: {bri_pct}%", (w_frame - 420, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        cv2.putText(img, f"Vol: {vol_pct}% [{mute_str}]", (w_frame - 270, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, mute_color, 2)

        if brightness_scroll_active:
            cv2.putText(img, f"BRIGHTNESS: {bri_pct}% (Ring+Pinky — Move Up/Down)", (w_frame - 490, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 255), 2)
        elif fist_win_active:
            cv2.putText(img, "FIST WIN MODE — Move Up/Down to Snap", (w_frame - 430, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 0), 2)
        elif alt_tab_active:
            cv2.putText(img, "ALT-TAB ACTIVE (Move Hand L/R)", (w_frame - 360, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (0, 255, 255), 2)
        elif two_finger_scroll_active:
            cv2.putText(img, "TWO-FINGER SCROLL (Move Up/Down)", (w_frame - 390, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 255), 2)

        if (now - recent_action_time) < 1.5:
            cv2.rectangle(img, (0, h_frame - 40), (w_frame, h_frame), (40, 40, 40), -1)
            cv2.putText(img, f"ACTION: {recent_action_text}", (20, h_frame - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)

        if screenshot_flash_frames > 0:
            cv2.rectangle(img, (0, 0), (w_frame, h_frame), (255, 255, 255), -1)
            screenshot_flash_frames -= 1

        cv2.imshow("Webcam Gesture Controller", img)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    mouse_mode.reset(time.time())
    if alt_tab_active:
        send_key_up(VK_MENU)
    brightness_ctrl.stop()

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
