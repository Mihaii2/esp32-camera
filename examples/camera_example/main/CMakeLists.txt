idf_component_register(SRCS "take_picture.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_http_server esp_wifi esp_netif nvs_flash esp_psram esp_timer)