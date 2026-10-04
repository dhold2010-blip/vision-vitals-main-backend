#include "esp_camera.h"

HardwareSerial CameraLink(1);
constexpr int kLinkRxPin = 13;
constexpr int kLinkTxPin = 14;
constexpr uint32_t kLinkBaud = 460800;

void setup() {
  Serial.begin(115200);
  CameraLink.begin(kLinkBaud, SERIAL_8N1, kLinkRxPin, kLinkTxPin);

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 5;
  config.pin_d1 = 18;
  config.pin_d2 = 19;
  config.pin_d3 = 21;
  config.pin_d4 = 36;
  config.pin_d5 = 39;
  config.pin_d6 = 34;
  config.pin_d7 = 35;
  config.pin_xclk = 0;
  config.pin_pclk = 22;
  config.pin_vsync = 25;
  config.pin_href = 23;
  config.pin_sccb_sda = 26;
  config.pin_sccb_scl = 27;
  config.pin_pwdn = 32;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;

  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("OV2640 camera initialization failed");
    while (true) delay(1000);
  }
}

void loop() {
  if (!CameraLink.available()) {
    delay(5);
    return;
  }
  if (CameraLink.read() != 'C') return;

  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame || frame->format != PIXFORMAT_JPEG || frame->len == 0) {
    if (frame) esp_camera_fb_return(frame);
    Serial.println("No JPEG frame available");
    return;
  }

  const uint32_t length = static_cast<uint32_t>(frame->len);
  const uint8_t header[8] = {
      'V', 'V', 'J', '1',
      static_cast<uint8_t>((length >> 24) & 0xff),
      static_cast<uint8_t>((length >> 16) & 0xff),
      static_cast<uint8_t>((length >> 8) & 0xff),
      static_cast<uint8_t>(length & 0xff),
  };
  CameraLink.write(header, sizeof(header));
  CameraLink.write(frame->buf, frame->len);
  CameraLink.flush();
  esp_camera_fb_return(frame);
}