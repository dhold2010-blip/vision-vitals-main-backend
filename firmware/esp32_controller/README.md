# ESP32 + ESP32-CAM reference client

This firmware keeps the Vision Vitals backend as the authority for accounts,
device authorization, image validation, storage, and AI analysis. The ESP32
controller is an input/control device only. It does not contain Gemini,
database, JWT-signing, or other backend server credentials.

## Boards and wiring

Use an ESP32 DevKit / ESP32-WROOM-32 as the Wi-Fi and control board, plus an
AI-Thinker-style ESP32-CAM with OV2640 as the JPEG camera. Join grounds between
the boards.

| Connection | ESP32 controller | ESP32-CAM |
|---|---:|---:|
| Camera UART TX → controller RX | GPIO 16 (UART2 RX) | GPIO 14 (UART1 TX) |
| Controller UART TX → camera RX | GPIO 17 (UART2 TX) | GPIO 13 (UART1 RX) |
| Ground | GND | GND |
| Capture button | GPIO 32 to GND | — |
| White illumination / LED ring control | GPIO 25 through a suitable transistor/MOSFET driver | — |
| Optional status LED | GPIO 2 through an appropriate resistor/driver | — |
| Optional VL53L0X SDA / SCL | GPIO 21 / GPIO 22 | — |

Do not power an LED ring directly from an ESP32 GPIO. Use a suitable driver and
power supply. Verify the specific camera-board pinout before wiring; GPIO 13/14
are used for the camera UART in this example, so the camera board's SD-card
interface is not available at the same time.

The camera board waits for a `C` byte, captures one JPEG, then returns the
binary frame protocol:

```text
4 bytes: ASCII "VVJ1"
4 bytes: unsigned JPEG length, big-endian
N bytes: JPEG payload
```

The example UART runs at 460800 baud and the controller rejects frames above
512 KiB. The controller bounds the frame length, generates a capture idempotency key,
and streams a multipart request to the backend. It also reports optional
VL53L0X readings as positioning metadata only.

## Build and provision

Use Arduino IDE / Arduino CLI with the ESP32 Arduino core. Install
**ArduinoJson** and **Adafruit VL53L0X** (and its Adafruit BusIO dependency).
Open each `.ino` in its own sketch directory; install the appropriate board
support for the AI-Thinker ESP32-CAM.

For the controller, copy `secrets.example.h` to the ignored local filename
`secrets.h`, then enter the Wi-Fi details, HTTPS API origin, registered
`device_id`, one-time device secret, and a trusted root CA certificate for the
backend host. Never commit `secrets.h`; never disable certificate validation.
The checked-in example certificate is a placeholder and will not permit a TLS
connection. Obtain the correct CA certificate chain for the deployed backend
through the deployment's normal trust-management process.

The local header is a prototype provisioning mechanism and embeds values in
the firmware image. For production, provision device credentials into protected
NVS and enable the ESP32 platform's flash encryption and secure boot; do not
rely on source-file exclusion alone to protect a flashed device.

Register the device with an authenticated user via
`POST /api/v1/devices/register`. The backend returns the device secret once.
Provision that device-specific secret securely to the controller (not the
camera board), then discard the registration response from temporary logs.
Revoke or rotate it if the controller is lost or transferred.

## Runtime sequence and API contract

The controller boots, joins Wi-Fi, authenticates with
`POST /api/v1/devices/authenticate`, and heartbeats every 20 seconds. It waits
for the physical button, reads the optional range sensor, enables illumination,
asks the camera board for a JPEG, then posts it to
`POST /api/v1/devices/{device_id}/capture` with:

- `X-Device-Session`
- a stable `Idempotency-Key` reused for bounded transport retries
- multipart `image` with MIME `image/jpeg`
- optional multipart `capture_metadata` JSON

Retry behavior is bounded to three retries (four total attempts) with
exponential backoff; permanent 4xx responses are not retried. A repeated idempotency key returns the original
capture and does not create a second analysis. The backend enforces the upload
limit, validates the MIME and decoded format, checks image quality, records
capture lifecycle transitions, and uses the same analysis pipeline as app
camera and upload sources.

If installed, the VL53L0X is reported to
`POST /api/v1/devices/{device_id}/sensor-readings` in millimetres. Its value is
only for positioning/framing guidance; it is not a body measurement or medical
reading. The firmware may run without the optional sensor.

## Development without boards

The Python `device.MockHardwareDevice` path and `AI_PROVIDER=mock` exercise the
real registration, authentication, heartbeat, JPEG capture, sensor, and
analysis endpoints without ESP32 hardware or Gemini credentials. The Arduino
sketches are reference firmware; physical pinout, sensor, camera-module, TLS
certificate, and memory behavior still need verification on the exact boards.