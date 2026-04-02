# XiaoZhi Board: Home Assistant Voice PE

## Flashen

### 1. XiaoZhi Firmware klonen
```bash
git clone https://github.com/78/xiaozhi-esp32.git
cd xiaozhi-esp32
```

### 2. Board-Definition kopieren
```bash
cp -r /opt/hydrahive/voice/board-voice-pe main/boards/ha-voice-pe
```

### 3. Board auswählen und kompilieren
```bash
# ESP-IDF Umgebung laden
. ~/esp/esp-idf/export.sh

# Board konfigurieren
idf.py set-target esp32s3
idf.py menuconfig
# → XiaoZhi Configuration → Board → ha-voice-pe

# Kompilieren
idf.py build
```

### 4. Flashen
```bash
# Voice PE per USB-C anschließen
idf.py -p /dev/ttyUSB0 flash monitor
```

## Hardware-Pinout

| Funktion | Pin | Beschreibung |
|----------|-----|-------------|
| Mic BCLK | GPIO13 | I2S Bit Clock (Input) |
| Mic LRCLK | GPIO14 | I2S Word Select (Input) |
| Mic DIN | GPIO15 | I2S Data In |
| Spk BCLK | GPIO8 | I2S Bit Clock (Output) |
| Spk LRCLK | GPIO7 | I2S Word Select (Output) |
| Spk DOUT | GPIO10 | I2S Data Out |
| DAC SDA | GPIO5 | I2C (AIC3204) |
| DAC SCL | GPIO6 | I2C (AIC3204) |
| LEDs | GPIO21 | 12x WS2812B |
| LED Power | GPIO45 | Enable |
| Amp Enable | GPIO47 | Speaker on/off |
| Center Btn | GPIO0 | Push-to-talk |
| Mute | GPIO3 | Mute switch |
| XMOS Reset | GPIO4 | Voice processor |
| Encoder A | GPIO16 | Lautstärke |
| Encoder B | GPIO18 | Lautstärke |
| Jack Detect | GPIO17 | 3.5mm Klinke |
