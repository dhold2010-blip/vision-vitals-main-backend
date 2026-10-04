#pragma once

// Copy to secrets.h (ignored by Git) and replace every placeholder locally.
// Do not flash production credentials from this example.
#define VV_WIFI_SSID "replace-with-wifi-name"
#define VV_WIFI_PASSWORD "replace-with-wifi-password"
#define VV_BACKEND_BASE_URL "https://api.example.com"
#define VV_DEVICE_ID "replace-with-registered-device-id"
#define VV_DEVICE_SECRET "replace-with-one-time-device-secret"

static const char VV_BACKEND_ROOT_CA[] PROGMEM = R"PEM(
-----BEGIN CERTIFICATE-----
Replace with the trusted root CA PEM for the deployed backend.
-----END CERTIFICATE-----
)PEM";