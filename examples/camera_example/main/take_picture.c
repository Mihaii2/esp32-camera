#include "sdkconfig.h"

#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <sys/socket.h>
#include <netinet/tcp.h>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_err.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "nvs_flash.h"

#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_http_server.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"

#include "esp_camera.h"

#define BOARD_ESP32S3_GOOUUU 1
#include "camera_pinout.h"

/* ============================================================
 * WIFI CONFIGURATION (Redmi Note 13 Pro Hotspot)
 * ============================================================ */

#define WIFI_SSID      "My_Redmi"
#define WIFI_PASS      "formula1"

#define WIFI_CONNECTED_BIT BIT0

static const char *TAG = "wifi_camera_stream";
static EventGroupHandle_t s_wifi_event_group;

/* ============================================================
 * STREAM MJPEG CONSTANTS
 * ============================================================ */

#define PART_BOUNDARY "123456789000000000000987654321"

static const char *STREAM_CONTENT_TYPE =
    "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;

static const char *STREAM_BOUNDARY =
    "\r\n--" PART_BOUNDARY "\r\n";

static const char *STREAM_PART =
    "Content-Type: image/jpeg\r\n"
    "Content-Length: %u\r\n\r\n";

/* ============================================================
 * CAMERA CONFIGURATION (SVGA 800x600, 24MHz, Triple Buffer)
 * ============================================================ */

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

    .xclk_freq_hz = 24000000,

    .ledc_timer = LEDC_TIMER_0,
    .ledc_channel = LEDC_CHANNEL_0,

    .pixel_format = PIXFORMAT_JPEG,
    .frame_size = FRAMESIZE_SVGA,
    .jpeg_quality = 12,

    .fb_count = 3,                      // 3 buffers in Octal PSRAM prevents FB-OVF under network jitter
    .fb_location = CAMERA_FB_IN_PSRAM,
    .grab_mode = CAMERA_GRAB_LATEST,
};

static esp_err_t init_camera(void)
{
    ESP_LOGI(TAG, "Initializare camera hardware (SVGA 800x600)...");

    esp_err_t err = esp_camera_init(&camera_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera Init Failed: 0x%x", err);
        return err;
    }

    for (int i = 0; i < 4; i++) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (fb) {
            esp_camera_fb_return(fb);
        }
        vTaskDelay(pdMS_TO_TICKS(15));
    }

    ESP_LOGI(TAG, "Camera initializata cu succes.");
    return ESP_OK;
}

static void tune_sensor_quality(void)
{
    sensor_t *s = esp_camera_sensor_get();
    if (!s) return;

    s->set_vflip(s, 0);
    s->set_hmirror(s, 1);

    s->set_brightness(s, 1);
    s->set_contrast(s, 1);
    s->set_saturation(s, 1);
    s->set_sharpness(s, 2);

    s->set_whitebal(s, 1);
    s->set_awb_gain(s, 1);
    s->set_wb_mode(s, 0);

    s->set_exposure_ctrl(s, 1);
    s->set_aec2(s, 0);
    s->set_ae_level(s, 2);
    s->set_gain_ctrl(s, 1);
    s->set_gainceiling(s, (gainceiling_t)GAINCEILING_16X);

    s->set_raw_gma(s, 1);
    s->set_lenc(s, 1);
    s->set_bpc(s, 1);
    s->set_wpc(s, 1);

    ESP_LOGI(TAG, "Sensor tuning aplicat cu succes!");
}
#endif

/* ============================================================
 * LOW-LATENCY STREAM HANDLER WITH TELEMETRY
 * ============================================================ */

static esp_err_t stream_handler(httpd_req_t *req)
{
    camera_fb_t *fb = NULL;
    esp_err_t res = ESP_OK;
    char part_buf[128];

    int sockfd = httpd_req_to_sockfd(req);
    if (sockfd >= 0) {
        int nodelay = 1;
        setsockopt(sockfd, IPPROTO_TCP, TCP_NODELAY, (const void *)&nodelay, sizeof(nodelay));

        // 2000ms gives adequate headroom for radio airtime jitter without tearing the session down
        struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
        setsockopt(sockfd, SOL_SOCKET, SO_SNDTIMEO, (const char *)&tv, sizeof(tv));
    }

    res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
    if (res != ESP_OK) return res;

    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    httpd_resp_set_hdr(req, "Pragma", "no-cache");

    ESP_LOGI(TAG, "Client conectat la video stream.");

    int64_t t_prev_frame = esp_timer_get_time();
    int64_t t_stats_window = esp_timer_get_time();

    uint32_t win_frames = 0;
    uint32_t win_bytes = 0;
    uint32_t win_stutters = 0;
    int64_t win_max_gap_us = 0;
    int64_t win_total_gap_us = 0;
    int64_t win_total_cap_us = 0;
    int64_t win_total_send_us = 0;

    while (true) {
        int64_t t_cap_start = esp_timer_get_time();
        fb = esp_camera_fb_get();
        int64_t t_cap_end = esp_timer_get_time();

        if (!fb) {
            ESP_LOGE(TAG, "Camera capture failed");
            res = ESP_FAIL;
            break;
        }

        size_t hlen = snprintf(part_buf, sizeof(part_buf), STREAM_PART, (unsigned)fb->len);

        int64_t t_send_start = esp_timer_get_time();

        res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
        if (res == ESP_OK) {
            res = httpd_resp_send_chunk(req, part_buf, hlen);
        }
        if (res == ESP_OK) {
            res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
        }

        int64_t t_send_end = esp_timer_get_time();
        size_t frame_bytes = fb->len;

        esp_camera_fb_return(fb);
        fb = NULL;

        if (res != ESP_OK) {
            ESP_LOGW(TAG, "Socket write failed / client dropped (err=0x%x)", res);
            break;
        }

        int64_t t_now = esp_timer_get_time();
        int64_t gap_us = t_now - t_prev_frame;
        t_prev_frame = t_now;

        win_frames++;
        win_bytes += frame_bytes;
        win_total_gap_us += gap_us;
        win_total_cap_us += (t_cap_end - t_cap_start);
        win_total_send_us += (t_send_end - t_send_start);

        if (gap_us > win_max_gap_us) {
            win_max_gap_us = gap_us;
        }
        if (gap_us > 80000) {
            win_stutters++;
        }

        if (t_now - t_stats_window >= 5000000) {
            float elapsed_sec = (float)(t_now - t_stats_window) / 1000000.0f;
            float fps = (float)win_frames / elapsed_sec;
            float kbps = ((float)win_bytes * 8.0f / 1024.0f) / elapsed_sec;
            float avg_gap_ms = (float)(win_total_gap_us / win_frames) / 1000.0f;
            float max_gap_ms = (float)win_max_gap_us / 1000.0f;
            float avg_cap_ms = (float)(win_total_cap_us / win_frames) / 1000.0f;
            float avg_send_ms = (float)(win_total_send_us / win_frames) / 1000.0f;

            ESP_LOGI(TAG, "[5s Stats] FPS: %4.1f | AvgGap: %4.1fms | MaxFreeze: %4.0fms | Drops>80ms: %2lu | Cap: %3.1fms | Send: %3.1fms | %5.0f kbps",
                     fps, avg_gap_ms, max_gap_ms, (unsigned long)win_stutters, avg_cap_ms, avg_send_ms, kbps);

            t_stats_window = t_now;
            win_frames = 0;
            win_bytes = 0;
            win_stutters = 0;
            win_max_gap_us = 0;
            win_total_gap_us = 0;
            win_total_cap_us = 0;
            win_total_send_us = 0;
        }

        taskYIELD();
    }

    if (fb) {
        esp_camera_fb_return(fb);
    }

    // Drain and clear pending DMA frame buffers to ensure a clean state
    for (int i = 0; i < 3; i++) {
        camera_fb_t *drain_fb = esp_camera_fb_get();
        if (drain_fb) {
            esp_camera_fb_return(drain_fb);
        }
    }

    return res;
}

/* ============================================================
 * HTTP SERVER SETUP
 * ============================================================ */

static httpd_handle_t start_webserver(void)
{
    httpd_handle_t server = NULL;
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();

    config.server_port = 80;
    config.ctrl_port = 32768;
    config.stack_size = 12288;
    config.task_priority = 6;
    config.core_id = 1;
    config.max_open_sockets = 2;
    config.lru_purge_enable = true;
    config.send_wait_timeout = 3;       // 3 seconds server-side wait before forcing socket purge

    httpd_uri_t stream_uri = {
        .uri       = "/",
        .method    = HTTP_GET,
        .handler   = stream_handler,
        .user_ctx  = NULL
    };

    ESP_LOGI(TAG, "Starting HTTP server on port: '%d' on Core %d", config.server_port, config.core_id);
    if (httpd_start(&server, &config) == ESP_OK) {
        httpd_register_uri_handler(server, &stream_uri);
        return server;
    }

    ESP_LOGE(TAG, "Error starting server!");
    return NULL;
}

/* ============================================================
 * WIFI EVENT HANDLER & INIT (Hotspot STA Mode)
 * ============================================================ */

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                               int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED) {
        esp_wifi_set_ps(WIFI_PS_NONE);
        ESP_LOGI(TAG, "Wi-Fi associated: Disabled modem sleep (WIFI_PS_NONE)");
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        ESP_LOGI(TAG, "Disconnected from Wi-Fi, reconnecting...");
        vTaskDelay(pdMS_TO_TICKS(500));
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "==================================================");
        ESP_LOGI(TAG, "CONNECTED! Stream URL: http://" IPSTR "/", IP2STR(&event->ip_info.ip));
        ESP_LOGI(TAG, "==================================================");

        esp_wifi_set_ps(WIFI_PS_NONE);
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void wifi_init_sta(void)
{
    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, &instance_got_ip));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
            .pmf_cfg = {
                .capable = false,
                .required = false
            },
            .listen_interval = 0,
        },
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    // 76 (~19 dBm) keeps transmit power high while protecting 3.3V rail stability
    esp_wifi_set_max_tx_power(76);
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20));

    ESP_LOGI(TAG, "Connecting to hotspot '%s'...", WIFI_SSID);
}

/* ============================================================
 * MAIN ENTRY POINT
 * ============================================================ */

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "Total Free Internal DRAM: %d bytes", (int)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    ESP_LOGI(TAG, "Total Free PSRAM/SPIRAM:  %d bytes", (int)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

#if ESP_CAMERA_SUPPORTED
    if (ESP_OK != init_camera()) {
        return;
    }

    tune_sensor_quality();
#endif

    wifi_init_sta();
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdFALSE, portMAX_DELAY);

#if ESP_CAMERA_SUPPORTED
    start_webserver();
#endif
}