/**
 * Board-Definition: Home Assistant Voice Preview Edition (Voice PE)
 * Für XiaoZhi ESP32 Firmware — https://github.com/78/xiaozhi-esp32
 *
 * Basierend auf: https://github.com/esphome/home-assistant-voice-pe
 * ESP32-S3, 16MB Flash, PSRAM (Octal 80MHz)
 *
 * Kopiere diesen Ordner nach: xiaozhi-esp32/main/boards/ha-voice-pe/
 */

#ifndef _BOARD_HA_VOICE_PE_CONFIG_H_
#define _BOARD_HA_VOICE_PE_CONFIG_H_

/* ── Audio Input (Mikrofon, I2S) ─────────────────────────────────── */
#define AUDIO_INPUT_SAMPLE_RATE   16000
#define AUDIO_I2S_MIC_NUM         I2S_NUM_0
#define AUDIO_I2S_MIC_GPIO_BCLK   GPIO_NUM_13
#define AUDIO_I2S_MIC_GPIO_WS     GPIO_NUM_14   /* LRCLK */
#define AUDIO_I2S_MIC_GPIO_DIN    GPIO_NUM_15

/* ── Audio Output (Lautsprecher, I2S) ────────────────────────────── */
#define AUDIO_OUTPUT_SAMPLE_RATE  48000
#define AUDIO_I2S_SPK_NUM         I2S_NUM_1
#define AUDIO_I2S_SPK_GPIO_BCLK   GPIO_NUM_8
#define AUDIO_I2S_SPK_GPIO_WS     GPIO_NUM_7    /* LRCLK */
#define AUDIO_I2S_SPK_GPIO_DOUT   GPIO_NUM_10

/* ── Audio Codec DAC (AIC3204 via I2C) ───────────────────────────── */
#define AUDIO_CODEC_I2C_PORT      I2C_NUM_0
#define AUDIO_CODEC_I2C_SDA       GPIO_NUM_5
#define AUDIO_CODEC_I2C_SCL       GPIO_NUM_6
#define AUDIO_CODEC_I2C_FREQ      400000

/* ── Speaker Amplifier ───────────────────────────────────────────── */
#define AUDIO_AMP_ENABLE_GPIO     GPIO_NUM_47
#define AUDIO_AMP_ENABLE_LEVEL    1             /* HIGH = an */

/* ── LEDs (12x WS2812B) ─────────────────────────────────────────── */
#define LED_GPIO                  GPIO_NUM_21
#define LED_POWER_GPIO            GPIO_NUM_45   /* Power Enable für LEDs */
#define LED_COUNT                 12
#define LED_BRIGHTNESS            80            /* 0-255, Standard */

/* ── Buttons ─────────────────────────────────────────────────────── */
#define BUTTON_GPIO               GPIO_NUM_0    /* Center Button */
#define BUTTON_MUTE_GPIO          GPIO_NUM_3    /* Mute Switch */

/* ── Rotary Encoder (optional) ───────────────────────────────────── */
#define ENCODER_GPIO_A            GPIO_NUM_16
#define ENCODER_GPIO_B            GPIO_NUM_18

/* ── 3.5mm Klinke ───────────────────────────────────────────────── */
#define JACK_DETECT_GPIO          GPIO_NUM_17

/* ── XMOS Voice Processor ────────────────────────────────────────── */
#define XMOS_RESET_GPIO           GPIO_NUM_4
/* XMOS kommuniziert über I2C (gleicher Bus wie AIC3204) */

#endif /* _BOARD_HA_VOICE_PE_CONFIG_H_ */
