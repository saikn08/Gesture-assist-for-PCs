# Gesture Assist for PCs
CONTROL YOUR COMPUTER WITH YOUR HAND-- control media, volume, mouse, shift through different apps, easily scroll through data, and even more..

Gesture Assist for PCs is a Windows webcam controller that uses OpenCV and CVZone hand landmarks to turn hand poses and movements into mouse, window, display, and media actions. An OK-sign clutch enables mouse control; a separate Yo/shaka-style clutch switches the remaining controls between window/navigation and media/volume modes. The camera preview shows the current mode, recognized gesture, system levels, and recent action.

## Getting started

Install Python and a working webcam, then install the packages used by the script:

```powershell
python -m pip install opencv-python cvzone mediapipe pyautogui pycaw comtypes joblib numpy
python main.py
```

This program is Windows-specific: it uses Win32 key/mouse events, PyCAW audio, and Windows WMI/PowerShell for brightness. Press **q** in the camera window to quit. The script tries camera indices 0, 1, and 2 before falling back to camera 0. It mirrors the image and tracks up to two hands. An optional model at `models/static_gesture_rf.joblib` can supplement some gesture labels; the geometric controls also work without that file.

## The two clutches

| Pose | Effect |
| --- | --- |
| Touch thumb and index to make an **OK sign**, with middle, ring, and little fingers extended. Hold about **0.6 seconds**. | Toggle **mouse mode** on/off. Repeat the pose to exit. There is a 1.2-second toggle cooldown; release the pose before toggling again. Mouse mode has priority over the other controls. |
| Extend **index and little fingers** while middle and ring are curled (thumb may be in either position), **or** extend **thumb and little finger** while the other three are curled. Hold for **15 detected frames**. | Toggle the separate clutch between **window/navigation** (off) and **media/volume** (on). There is a 1.2-second cooldown; release the pose before toggling again. This mode remains selected when mouse mode is exited. |

The on-screen banner reports `MOUSE MODE`, `CLUTCH: OFF`, or `CLUTCH: ON`. Gestures below require their stated mode. Screenshot and two-hand zoom work in either non-mouse clutch state.

## Mouse mode — OK clutch on

| Gesture | Action |
| --- | --- |
| Point with **index finger only**. | Move pointer. The middle **75% of the camera frame** maps to the whole screen; coordinates beyond it are clamped to the screen edge so you can reach corners without leaving the camera view. |
| After pointing, briefly touch **thumb to index**. | Left click at the current pointer location. |
| After pointing, briefly touch **thumb to middle**. | Right click at the current pointer location. |
| Keep index extended, middle folded, and touch **thumb to ring** for about **0.13 seconds**. | Hold the left mouse button to select text/items or drag an object. Move the index finger while held; separate thumb and ring to drop/release. |

Click gestures use hand-size-relative distances, require a short index-only pointing pose first, and are rearmed after release. The code does **not** implement a dedicated double-click. Two raised fingers are intentionally inactive in mouse mode. If the tracked hand or index tip is lost for roughly 0.3 seconds, mouse state resets and an active drag is released. Toggling mouse mode also releases a drag. The program temporarily disables PyAutoGUI's corner fail-safe for its own mouse actions because this mode intentionally reaches screen corners.

## Window/navigation mode — mouse off, other clutch off

| Gesture | Action |
| --- | --- |
| Hold **index, middle, and ring up**, little finger down, for about **0.35 seconds**. Move hand right/left while holding. | Open Alt-Tab, then cycle forward/backward through windows. Release the pose; Alt is released after about 0.45 seconds to choose the window. |
| Hold **index and middle up and close together**; move hand up/down. | Vertical mouse-wheel scrolling. Up scrolls up; down scrolls down. An 18-pixel motion threshold filters small tremors. There is no horizontal or diagonal scrolling. |
| Hold **ring and little fingers up**, index and middle curled; move hand up/down. | Increase/decrease display brightness. The first three frames stabilize the motion. Thumb position is not part of this pose check. |
| Hold a **fist** for about **20 frames**, then move it up/down by at least **70 camera pixels**. | Send Win+Up / Win+Down to maximize/restore or snap down/minimize the active window, depending on Windows' current window state. Steps are rate-limited. |

## Media/volume mode — mouse off, other clutch on

| Gesture | Action |
| --- | --- |
| Make a **fist** and move it up/down. | Raise/lower system volume, in 3% scalar steps when PyCAW is available; media volume keys are the fallback. |
| Show **thumbs up** / **thumbs down**. | Unmute / mute system audio. Each pose is latched until released. |
| **Double pinch** thumb and index, with a release between pinches, within **0.65 seconds**. | Toggle the system media play/pause key. This is one toggle action, not separate play and pause commands. |
| With at least **three non-thumb fingers extended**, swipe hand **left** / **right**. | **Next** / **previous** track. The motion must be fast and predominantly horizontal; cooldowns help reject repeated or recoil swipes. |

## Gestures available outside mouse mode in either clutch state

| Gesture | Action |
| --- | --- |
| Hold one **L-shaped hand** (thumb and index extended, remaining fingers curled), or form an L-shaped viewfinder with **both hands**, for about **0.85 seconds**. | Save a screenshot in `screenshots/screenshot_YYYYMMDD_HHMMSS.png` and also send Win+Print Screen. The capture has a 1.5-second cooldown. The two-hand frame check does not require the middle finger to be curled. |
| Show **two hands** that are *not* making the screenshot frame; move their detected centers apart/together. | Send Ctrl+= / Ctrl+- for application zoom in/out. A center-distance change over 18 camera pixels is required; actions are at least 0.18 seconds apart. Zoom depends on whether the active application supports these shortcuts. |

## How the program works

- CVZone supplies 21 landmarks for each detected hand. Geometric tests estimate extended fingers, fingertip proximity, and motion; an optional 93-feature classifier can supplement certain labels. The code does not contain face or eye tracking.
- The Windows mouse mode uses PyAutoGUI for pointer movement, clicks, and button-down/button-up dragging. The navigation scroller uses a Win32 vertical wheel event. Other Windows keys are sent through Win32 virtual-key events or PyAutoGUI hotkeys.
- Brightness updates run on a background thread. The program tries direct WMI COM first and PowerShell CIM as a fallback, so support depends on the display and Windows drivers. The displayed brightness value is the controller's target; a failed hardware update may leave it different from actual brightness.
- Audio uses PyCAW's default speaker endpoint when available, with media-key fallbacks for some actions. If screenshot saving fails, the Win+Print Screen hotkey is still attempted. The preview overlays a short action message and a white flash after a screenshot trigger.

Gesture recognition depends on lighting, occlusion, camera framing, and whether the active application responds to the shortcut. This README describes the implemented behavior, not a guarantee that every pose or OS action will be detected on every frame.
