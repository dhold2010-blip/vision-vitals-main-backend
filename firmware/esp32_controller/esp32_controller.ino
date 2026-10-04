#include <Adafruit_VL53L0X.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Wire.h>
#include <esp_heap_caps.h>
#include <time.h>
#include <cstring>
#include <cstdio>

#include "secrets.h"

namespace {
constexpr int kCameraRxPin = 16;
constexpr int kCameraTxPin = 17;
constexpr int kCaptureButtonPin = 32;
constexpr int kIlluminationPin = 25;
constexpr int kStatusLedPin = 2;
constexpr uint32_t kCameraBaud = 460800;
constexpr size_t kMaxJpegBytes = 512 * 1024;
constexpr uint32_t kHeartbeatMs = 20000;
constexpr uint8_t kMaxRetries = 3;

HardwareSerial CameraLink(2);
Adafruit_VL53L0X rangeSensor;
bool rangeSensorReady = false;
String deviceSession;
uint32_t lastHeartbeat = 0;
uint32_t nextWifiAttemptAt = 0;
uint32_t wifiRetryDelayMs = 1000;
uint32_t nextAuthAttemptAt = 0;
uint32_t authRetryDelayMs = 1000;
bool ntpConfigured = false;

struct CameraFrame {
  uint8_t *data = nullptr;
  size_t length = 0;

  void release() {
    if (data) {
      heap_caps_free(data);
      data = nullptr;
      length = 0;
    }
  }
};

String apiUrl(const String &path) {
  String base = VV_BACKEND_BASE_URL;
  while (base.endsWith("/")) base.remove(base.length() - 1);
  return base + path;
}

void setStatus(bool ready) { digitalWrite(kStatusLedPin, ready ? HIGH : LOW); }

void maintainWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiRetryDelayMs = 1000;
    if (!ntpConfigured) {
      configTime(0, 0, "pool.ntp.org", "time.google.com");
      ntpConfigured = true;
    }
    return;
  }
  if (static_cast<int32_t>(millis() - nextWifiAttemptAt) < 0) return;
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  WiFi.begin(VV_WIFI_SSID, VV_WIFI_PASSWORD);
  nextWifiAttemptAt = millis() + wifiRetryDelayMs;
  wifiRetryDelayMs = min(wifiRetryDelayMs * 2, 30000UL);
}

bool postJson(const String &path, const String &body, bool authenticated,
              String &responseBody, int &statusCode) {
  WiFiClientSecure tls;
  tls.setCACert(VV_BACKEND_ROOT_CA);
  HTTPClient http;
  if (!http.begin(tls, apiUrl(path))) return false;
  http.setTimeout(15000);
  http.addHeader("Content-Type", "application/json");
  if (authenticated) http.addHeader("X-Device-Session", deviceSession);
  statusCode = http.POST(body);
  responseBody = http.getString();
  http.end();
  return statusCode > 0;
}

bool authenticateDevice() {
  if (WiFi.status() != WL_CONNECTED) return false;
  StaticJsonDocument<256> request;
  request["device_id"] = VV_DEVICE_ID;
  request["device_secret"] = VV_DEVICE_SECRET;
  String body;
  serializeJson(request, body);

  String response;
  int status = 0;
  if (!postJson("/api/v1/devices/authenticate", body, false, response, status) ||
      status < 200 || status >= 300) {
    return false;
  }
  JsonDocument document;
  if (deserializeJson(document, response)) return false;
  const char *token = document["data"]["device_session_token"];
  if (!token || !*token) return false;
  deviceSession = token;
  authRetryDelayMs = 1000;
  nextAuthAttemptAt = 0;
  return true;
}

void maintainDeviceSession() {
  if (!deviceSession.isEmpty() ||
      static_cast<int32_t>(millis() - nextAuthAttemptAt) < 0) {
    return;
  }
  if (!authenticateDevice()) {
    nextAuthAttemptAt = millis() + authRetryDelayMs;
    authRetryDelayMs = min(authRetryDelayMs * 2, 30000UL);
  }
}

bool heartbeat() {
  StaticJsonDocument<160> request;
  request["firmware_version"] = "1.0.0";
  request["software_version"] = "esp32-controller-1.0.0";
  String body;
  serializeJson(request, body);

  String response;
  int status = 0;
  const String path = String("/api/v1/devices/") + VV_DEVICE_ID + "/heartbeat";
  return postJson(path, body, true, response, status) && status >= 200 && status < 300;
}

bool readCameraFrame(CameraFrame &frame) {
  CameraLink.write('C');
  CameraLink.flush();
  CameraLink.setTimeout(30000);

  uint8_t header[8];
  if (CameraLink.readBytes(header, sizeof(header)) != sizeof(header) ||
      memcmp(header, "VVJ1", 4) != 0) {
    return false;
  }
  const uint32_t length = (static_cast<uint32_t>(header[4]) << 24) |
                          (static_cast<uint32_t>(header[5]) << 16) |
                          (static_cast<uint32_t>(header[6]) << 8) |
                          static_cast<uint32_t>(header[7]);
  if (length == 0 || length > kMaxJpegBytes) return false;

  frame.data = static_cast<uint8_t *>(
      heap_caps_malloc(length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!frame.data) frame.data = static_cast<uint8_t *>(malloc(length));
  if (!frame.data) return false;
  frame.length = length;
  if (CameraLink.readBytes(frame.data, length) != length) {
    frame.release();
    return false;
  }
  return true;
}

String newIdempotencyKey() {
  char key[33];
  snprintf(key, sizeof(key), "%08lx%08lx%08lx%08lx",
           static_cast<unsigned long>(esp_random()),
           static_cast<unsigned long>(esp_random()),
           static_cast<unsigned long>(esp_random()),
           static_cast<unsigned long>(esp_random()));
  return String(key);
}

String captureMetadata(float distanceMm, bool distanceAvailable) {
  StaticJsonDocument<256> metadata;
  metadata["image_source"] = "HARDWARE_CAMERA";
  metadata["camera_type"] = "ESP32_CAM";
  metadata["firmware_version"] = "1.0.0";
  metadata["software_version"] = "esp32-controller-1.0.0";
  time_t now = time(nullptr);
  if (now > 1700000000) {
    struct tm utc;
    gmtime_r(&now, &utc);
    char timestamp[25];
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc);
    metadata["capture_timestamp"] = timestamp;
  }
  if (distanceAvailable) metadata["sensor_distance_mm"] = distanceMm;
  String encoded;
  serializeJson(metadata, encoded);
  return encoded;
}

class MultipartJpegStream : public Stream {
 public:
  MultipartJpegStream(const String &metadata, const uint8_t *jpeg, size_t jpegLength,
                      const String &boundary)
      : jpeg_(jpeg), jpegLength_(jpegLength) {
    prefix_ = "--" + boundary +
              "\r\nContent-Disposition: form-data; name=\"capture_metadata\"\r\n"
              "Content-Type: application/json\r\n\r\n" +
              metadata +
              "\r\n--" + boundary +
              "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\n"
              "Content-Type: image/jpeg\r\n\r\n";
    suffix_ = "\r\n--" + boundary + "--\r\n";
    totalLength_ = prefix_.length() + jpegLength_ + suffix_.length();
  }

  size_t length() const { return totalLength_; }
  int available() override {
    const size_t remaining = totalLength_ - position_;
    return remaining > 0x7fffffff ? 0x7fffffff : static_cast<int>(remaining);
  }
  int read() override {
    if (position_ >= totalLength_) return -1;
    const int value = byteAt(position_);
    ++position_;
    return value;
  }
  int peek() override {
    return position_ < totalLength_ ? byteAt(position_) : -1;
  }
  void flush() override {}
  size_t write(uint8_t) override { return 0; }

 private:
  int byteAt(size_t index) const {
    if (index < prefix_.length()) return static_cast<uint8_t>(prefix_[index]);
    index -= prefix_.length();
    if (index < jpegLength_) return jpeg_[index];
    index -= jpegLength_;
    return static_cast<uint8_t>(suffix_[index]);
  }

  String prefix_;
  String suffix_;
  const uint8_t *jpeg_;
  size_t jpegLength_;
  size_t totalLength_ = 0;
  size_t position_ = 0;
};

int uploadJpegOnce(const CameraFrame &frame, const String &key,
                   const String &metadata) {
  const String boundary = "VV-" + key;
  MultipartJpegStream body(metadata, frame.data, frame.length, boundary);
  WiFiClientSecure tls;
  tls.setCACert(VV_BACKEND_ROOT_CA);
  HTTPClient http;
  const String path = String("/api/v1/devices/") + VV_DEVICE_ID + "/capture";
  if (!http.begin(tls, apiUrl(path))) return -1;
  http.setTimeout(30000);
  http.addHeader("Content-Type", "multipart/form-data; boundary=" + boundary);
  http.addHeader("X-Device-Session", deviceSession);
  http.addHeader("Idempotency-Key", key);
  const int status = http.sendRequest("POST", &body, body.length());
  http.end();
  return status;
}

bool uploadJpeg(const CameraFrame &frame, const String &metadata) {
  const String key = newIdempotencyKey();
  for (uint8_t attempt = 0; attempt <= kMaxRetries; ++attempt) {
    const int status = uploadJpegOnce(frame, key, metadata);
    if (status >= 200 && status < 300) return true;
    if (status == 401 && authenticateDevice()) {
      // Keep the same key: an earlier attempt may have reached the backend.
      const int retriedStatus = uploadJpegOnce(frame, key, metadata);
      if (retriedStatus >= 200 && retriedStatus < 300) {
        return true;
      }
    }
    const bool transient = status < 0 || status == 408 || status == 425 ||
                           status == 429 || status >= 500;
    if (!transient || attempt == kMaxRetries) return false;
    delay(500UL << attempt);
  }
  return false;
}

bool readDistance(float &distanceMm) {
  if (!rangeSensorReady) return false;
  VL53L0X_RangingMeasurementData_t measurement;
  rangeSensor.rangingTest(&measurement, false);
  if (measurement.RangeStatus == 4 || measurement.RangeMilliMeter == 0 ||
      measurement.RangeMilliMeter > 2000) {
    return false;
  }
  distanceMm = static_cast<float>(measurement.RangeMilliMeter);
  return true;
}

void submitSensorReading(float distanceMm) {
  StaticJsonDocument<192> request;
  request["sensor_type"] = "distance";
  request["value"] = distanceMm;
  request["unit"] = "mm";
  request["quality"] = "VALID";
  char timestamp[25];
  time_t now = time(nullptr);
  struct tm utc;
  gmtime_r(&now, &utc);
  strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%SZ", &utc);
  request["timestamp"] = timestamp;
  String body;
  serializeJson(request, body);
  String response;
  int status = 0;
  const String path = String("/api/v1/devices/") + VV_DEVICE_ID + "/sensor-readings";
  postJson(path, body, true, response, status);
}

void captureOnce() {
  setStatus(false);
  float distanceMm = 0;
  const bool distanceAvailable = readDistance(distanceMm);
  digitalWrite(kIlluminationPin, HIGH);
  delay(150);

  CameraFrame frame;
  const bool captured = readCameraFrame(frame);
  digitalWrite(kIlluminationPin, LOW);
  if (!captured) {
    Serial.println("Camera frame unavailable");
    setStatus(true);
    return;
  }

  if (distanceAvailable) submitSensorReading(distanceMm);
  const bool uploaded = uploadJpeg(
      frame, captureMetadata(distanceMm, distanceAvailable));
  frame.release();
  Serial.println(uploaded ? "Capture accepted" : "Capture failed; check API status");
  setStatus(true);
}
}  // namespace

void setup() {
  Serial.begin(115200);
  pinMode(kCaptureButtonPin, INPUT_PULLUP);
  pinMode(kIlluminationPin, OUTPUT);
  pinMode(kStatusLedPin, OUTPUT);
  digitalWrite(kIlluminationPin, LOW);
  setStatus(false);

  CameraLink.begin(kCameraBaud, SERIAL_8N1, kCameraRxPin, kCameraTxPin);
  Wire.begin(21, 22);
  rangeSensorReady = rangeSensor.begin();
  maintainWifi();
}

void loop() {
  maintainWifi();
  maintainDeviceSession();
  setStatus(WiFi.status() == WL_CONNECTED && !deviceSession.isEmpty());

  if (!deviceSession.isEmpty() && millis() - lastHeartbeat >= kHeartbeatMs) {
    if (!heartbeat()) deviceSession = "";
    lastHeartbeat = millis();
  }

  static bool wasPressed = false;
  const bool pressed = digitalRead(kCaptureButtonPin) == LOW;
  if (pressed && !wasPressed) {
    delay(35);
    if (digitalRead(kCaptureButtonPin) == LOW && WiFi.status() == WL_CONNECTED &&
        !deviceSession.isEmpty()) {
      captureOnce();
    }
  }
  wasPressed = pressed;
  delay(20);
}