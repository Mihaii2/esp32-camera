#include "sdkconfig.h"
#include <esp_log.h>
#include <esp_system.h>
#include <nvs_flash.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"

#ifndef portTICK_RATE_MS
#define portTICK_RATE_MS portTICK_PERIOD_MS
#endif

#include "esp_camera.h"

#define BOARD_ESP32S3_GOOUUU 1
#include "camera_pinout.h"

static const char *TAG = "live_stream";

#if ESP_CAMERA_SUPPORTED
static camera_config_t camera_config = {
    .pin_pwdn = CAM_PIN_PWDN,
    .pin_reset = CAM_PIN_RESET,
    .pin_xclk = CAM_PIN_XCLK,
    .pin_sccb_sda = CAM_PIN_SIOD,
    .pin_sccb_scl = CAM_PIN_SIOC,

    .pin_d7 = CAM_PIN_D7,
    .pin_d6 = CAM_PIN_D6,
    .pin_d5 = CAM_PIN_D5,
    .pin_d4 = CAM_PIN_D4,
    .pin_d3 = CAM_PIN_D3,
    .pin_d2 = CAM_PIN_D2,
    .pin_d1 = CAM_PIN_D1,
    .pin_d0 = CAM_PIN_D0,
    .pin_vsync = CAM_PIN_VSYNC,
    .pin_href = CAM_PIN_HREF,
    .pin_pclk = CAM_PIN_PCLK,

    .xclk_freq_hz = 10000000,          // 10MHz prevents sensor timing jitter
    .ledc_timer = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,

    .pixel_format = PIXFORMAT_JPEG,
    .frame_size = FRAMESIZE_QVGA,       // 320x240
    .jpeg_quality = 14,                // Leaner JPEG for fast transmission
    .fb_count = 1,
    .fb_location = CAMERA_FB_IN_DRAM,
    .grab_mode = CAMERA_GRAB_LATEST,
};

static esp_err_t init_camera(void)
{
    esp_err_t err = esp_camera_init(&camera_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera Init Failed: 0x%x", err);
        return err;
    }
    return ESP_OK;
}
#endif

void app_main(void)
{
#if ESP_CAMERA_SUPPORTED
    esp_log_level_set("*", ESP_LOG_NONE);

    // Standard high-speed reliable baud: 921600
    uart_config_t uart_config = {
        .baud_rate = 921600,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE
    };
    uart_param_config(UART_NUM_0, &uart_config);
    uart_driver_install(UART_NUM_0, 4096, 0, 0, NULL, 0);

    if (ESP_OK != init_camera()) {
        return;
    }

    const uint8_t header[4] = {0xAA, 0xBB, 0xCC, 0xDD};

    while (1) {
        camera_fb_t *pic = esp_camera_fb_get();
        if (pic) {
            // Verify valid JPEG SOI marker (0xFF 0xD8) before sending
            if (pic->len > 4 && pic->buf[0] == 0xFF && pic->buf[1] == 0xD8) {
                uint32_t size = pic->len;
                uart_write_bytes(UART_NUM_0, (const char *)header, 4);
                uart_write_bytes(UART_NUM_0, (const char *)&size, 4);
                uart_write_bytes(UART_NUM_0, (const char *)pic->buf, pic->len);
            }
            esp_camera_fb_return(pic);
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
#endif
}