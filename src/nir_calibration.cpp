/*
 * =============================================================================
 * VMC ECO-VENDO — AS7263 NIR Spectrometer Standalone Calibration & Test Tool
 * =============================================================================
 * Pure hardware test sketch for the SparkFun AS7263 6-Channel NIR Spectrometer.
 * 
 * Hardware Connection (I2C):
 *   AS7263 SDA  --> ESP32 GPIO 21
 *   AS7263 SCL  --> ESP32 GPIO 22
 *   AS7263 VCC  --> 3.3V (Do NOT connect to 5V!)
 *   AS7263 GND  --> GND
 * 
 * Serial Monitor Settings:
 *   Baud Rate: 115200
 *   Line Ending: Both NL & CR (or Newline)
 * =============================================================================
 */

#include <Arduino.h>
#include <Wire.h>
#include <AS726X.h>

#define I2C_SDA_PIN 21
#define I2C_SCL_PIN 22
#define DEFAULT_BAUD 115200

AS726X sensor;

// Configuration state
uint8_t currentGain = 3;       // 0=1x, 1=3.7x, 2=16x, 3=64x
uint8_t currentIntegration = 50; // 50 * 2.8ms = 140ms integration time
bool bulbEnabled = false;       // On-board illumination bulb
uint8_t bulbCurrent = 2;       // 0=12.5mA, 1=25mA, 2=50mA, 3=100mA
bool autoStream = true;        // Stream continuously every interval
unsigned long streamIntervalMs = 500;
unsigned long lastMeasureTime = 0;

// Baseline snapshot
bool hasBaseline = false;
int baseR = 0, baseS = 0, baseT = 0, baseU = 0, baseV = 0, baseW = 0;

// PET Reference thresholds (configurable during calibration)
int petWMin = 200;
int petWMax = 5000;

// Draw an ASCII bar representing relative channel intensity
void printBar(const char* label, int val, int maxVal, const char* wavelength, float calVal) {
    const int BAR_WIDTH = 25;
    int filled = 0;
    if (maxVal > 0) {
        filled = (val * BAR_WIDTH) / maxVal;
        if (filled > BAR_WIDTH) filled = BAR_WIDTH;
    }
    
    Serial.printf("  %s (%s): [%-5d | %7.2f uW] ", label, wavelength, val, calVal);
    for (int i = 0; i < BAR_WIDTH; ++i) {
        if (i < filled) Serial.print("=");
        else Serial.print(" ");
    }
    Serial.println();
}

void printHelp() {
    Serial.println();
    Serial.println("===============================================================");
    Serial.println("       AS7263 NIR SPECTROMETER BENCH COMMANDS (type & Enter)    ");
    Serial.println("===============================================================");
    Serial.println("  [b] Toggle on-board NIR illumination BULB (ON/OFF)");
    Serial.println("  [1] Set Gain: 1x    | [2] Set Gain: 3.7x");
    Serial.println("  [3] Set Gain: 16x   | [4] Set Gain: 64x (Default)");
    Serial.println("  [+] Increase Integration Time (+28 ms)");
    Serial.println("  [-] Decrease Integration Time (-28 ms)");
    Serial.println("  [s] Set current reading as BASELINE (Empty Air)");
    Serial.println("  [x] Clear baseline");
    Serial.println("  [m] Toggle Mode: Continuous Stream vs. Single-Shot");
    Serial.println("  [space / Enter] Trigger Single-Shot measurement");
    Serial.println("  [h] Show this help menu");
    Serial.println("===============================================================");
    Serial.println();
}

void setup() {
    Serial.begin(DEFAULT_BAUD);
    delay(1000);

    Serial.println();
    Serial.println("===============================================================");
    Serial.println("     VMC ECO-VENDO — AS7263 NIR SPECTROMETER CALIBRATION       ");
    Serial.println("===============================================================");
    Serial.printf("Starting I2C bus on SDA=GPIO %d, SCL=GPIO %d...\n", I2C_SDA_PIN, I2C_SCL_PIN);
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, 100000);

    Serial.println("Probing I2C bus for AS7263 at address 0x49...");
    Wire.beginTransmission(0x49);
    byte error = Wire.endTransmission();
    if (error != 0) {
        Serial.printf("[ERROR] I2C Device NOT found at address 0x49! Error code: %d\n", error);
        Serial.println("Please check your wiring:");
        Serial.println("  - AS7263 SDA -> ESP32 GPIO 21");
        Serial.println("  - AS7263 SCL -> ESP32 GPIO 22");
        Serial.println("  - AS7263 VIN -> 3.3V");
        Serial.println("  - AS7263 GND -> GND");
        Serial.println("Halted. Fix wiring and press ESP32 Reset button.");
        while (true) delay(1000);
    }
    Serial.println("[OK] I2C Device responded at address 0x49!");

    Serial.println("Initializing AS726X library (Gain=64x, Mode=One-Shot All Channels)...");
    if (!sensor.begin(Wire, currentGain, 3)) {
        Serial.println("[ERROR] AS726X initialization failed! Sensor did not acknowledge configuration.");
        Serial.println("Halted. Press ESP32 Reset button to retry.");
        while (true) delay(1000);
    }

    sensor.setIntegrationTime(currentIntegration);
    sensor.setGain(currentGain);
    sensor.disableBulb();
    sensor.disableIndicator();

    Serial.println("[SUCCESS] AS7263 NIR Spectrometer initialized and ready!");
    Serial.printf("Sensor Temperature: %d C (%0.1f F)\n", sensor.getTemperature(), sensor.getTemperatureF());
    Serial.printf("Initial Gain: 64x | Integration: %d (* 2.8ms = %d ms)\n",
                  currentIntegration, currentIntegration * 28 / 10);
    Serial.println("Illumination Bulb: OFF (type 'b' to toggle ON for reflective plastic testing)");

    printHelp();
}

void processMeasurement() {
    // If bulb is enabled, take measurement with bulb; otherwise passive
    if (bulbEnabled) {
        sensor.enableBulb();
        delay(50); // Settle illumination
    }

    sensor.takeMeasurements();

    if (bulbEnabled) {
        // Keep bulb on or off depending on user setting
    }

    // Read Raw Counts (16-bit integer ADC counts)
    int r = sensor.getR();
    int s = sensor.getS();
    int t = sensor.getT();
    int u = sensor.getU();
    int v = sensor.getV();
    int w = sensor.getW();

    // Read Calibrated Spectral Irradiance (uW / cm^2)
    float calR = sensor.getCalibratedR();
    float calS = sensor.getCalibratedS();
    float calT = sensor.getCalibratedT();
    float calU = sensor.getCalibratedU();
    float calV = sensor.getCalibratedV();
    float calW = sensor.getCalibratedW();

    int tempC = sensor.getTemperature();

    // Peak determination
    int maxVal = max(r, max(s, max(t, max(u, max(v, w)))));
    if (maxVal < 1) maxVal = 1;

    // Check PET Plastic target absorption criteria
    bool isPetTarget = (calW >= petWMin && calW <= petWMax);
    float wrRatio = (r > 0) ? ((float)w / (float)r) : 0.0f;

    Serial.println("--------------------------------------------------------------------------------");
    Serial.printf("TIME: %lu ms | Temp: %d C | Bulb: %s | Gain: %s | Integ: %d ms\n",
                  millis(), tempC, bulbEnabled ? "ON (50mA)" : "OFF",
                  currentGain == 3 ? "64x" : (currentGain == 2 ? "16x" : (currentGain == 1 ? "3.7x" : "1x")),
                  currentIntegration * 28 / 10);
    Serial.println("--------------------------------------------------------------------------------");

    // Spectral Bars (All 6 NIR channels)
    printBar("R", r, maxVal, "610 nm", calR);
    printBar("S", s, maxVal, "680 nm", calS);
    printBar("T", t, maxVal, "730 nm", calT);
    printBar("U", u, maxVal, "760 nm", calU);
    printBar("V", v, maxVal, "810 nm", calV);
    printBar("W", w, maxVal, "860 nm", calW);

    Serial.println("--------------------------------------------------------------------------------");
    Serial.printf("Summary: Raw[R=%d, S=%d, T=%d, U=%d, V=%d, W=%d] | Peak: %d\n", r, s, t, u, v, w, maxVal);
    Serial.printf("Calibrated (uW/cm2): [R=%.1f, S=%.1f, T=%.1f, U=%.1f, V=%.1f, W=%.1f]\n",
                  calR, calS, calT, calU, calV, calW);
    Serial.printf("W/R Ratio: %.2f | Calibrated W: %.2f uW/cm2\n", wrRatio, calW);

    if (hasBaseline) {
        int dR = r - baseR;
        int dW = w - baseW;
        Serial.printf("Delta from Baseline: dR=%+d, dS=%+d, dT=%+d, dU=%+d, dV=%+d, dW=%+d\n",
                      dR, s - baseS, t - baseT, u - baseU, v - baseV, dW);
    }

    // Material Classification Guidance
    if (maxVal < 30 && !bulbEnabled) {
        Serial.println(">>> CLASSIFICATION: [AMBIENT DARK / WEAK SIGNAL] -> Turn Bulb ON ('b') for reflection!");
    } else if (isPetTarget) {
        Serial.printf(">>> CLASSIFICATION: [PET PLASTIC MATCH!] (Calibrated W=%.2f in range [%d - %d])\n",
                      calW, petWMin, petWMax);
    } else if (calW < petWMin) {
        Serial.printf(">>> CLASSIFICATION: [BELOW PET MIN] (Calibrated W=%.2f < %d)\n", calW, petWMin);
    } else {
        Serial.printf(">>> CLASSIFICATION: [ABOVE PET MAX / OPAQUE] (Calibrated W=%.2f > %d)\n", calW, petWMax);
    }
    Serial.println();
}

void handleSerialCommand(char cmd) {
    switch (cmd) {
        case 'b':
        case 'B':
            bulbEnabled = !bulbEnabled;
            if (bulbEnabled) {
                sensor.setBulbCurrent(bulbCurrent);
                sensor.enableBulb();
                Serial.println("[BULB] Illumination Bulb ENABLED (50 mA). Reflective testing active.");
            } else {
                sensor.disableBulb();
                Serial.println("[BULB] Illumination Bulb DISABLED. Passive testing active.");
            }
            break;

        case '1':
            currentGain = 0;
            sensor.setGain(currentGain);
            Serial.println("[GAIN] Gain set to 1x");
            break;

        case '2':
            currentGain = 1;
            sensor.setGain(currentGain);
            Serial.println("[GAIN] Gain set to 3.7x");
            break;

        case '3':
            currentGain = 2;
            sensor.setGain(currentGain);
            Serial.println("[GAIN] Gain set to 16x");
            break;

        case '4':
            currentGain = 3;
            sensor.setGain(currentGain);
            Serial.println("[GAIN] Gain set to 64x (Highest Sensitivity)");
            break;

        case '+':
            if (currentIntegration <= 245) currentIntegration += 10;
            sensor.setIntegrationTime(currentIntegration);
            Serial.printf("[INTEGRATION] Increased to %d (* 2.8ms = %d ms)\n",
                          currentIntegration, currentIntegration * 28 / 10);
            break;

        case '-':
            if (currentIntegration >= 15) currentIntegration -= 10;
            sensor.setIntegrationTime(currentIntegration);
            Serial.printf("[INTEGRATION] Decreased to %d (* 2.8ms = %d ms)\n",
                          currentIntegration, currentIntegration * 28 / 10);
            break;

        case 's':
        case 'S':
            sensor.takeMeasurements();
            baseR = sensor.getR();
            baseS = sensor.getS();
            baseT = sensor.getT();
            baseU = sensor.getU();
            baseV = sensor.getV();
            baseW = sensor.getW();
            hasBaseline = true;
            Serial.printf("[BASELINE] Recorded Baseline: R=%d, S=%d, T=%d, U=%d, V=%d, W=%d\n",
                          baseR, baseS, baseT, baseU, baseV, baseW);
            break;

        case 'x':
        case 'X':
            hasBaseline = false;
            Serial.println("[BASELINE] Baseline cleared.");
            break;

        case 'm':
        case 'M':
            autoStream = !autoStream;
            Serial.printf("[MODE] Auto-stream mode: %s\n", autoStream ? "ON (Every 500 ms)" : "OFF (Single-Shot: press Space)");
            break;

        case ' ':
        case '\n':
        case '\r':
            processMeasurement();
            break;

        case 'h':
        case 'H':
            printHelp();
            break;

        default:
            break;
    }
}

void loop() {
    // Process user input from Serial Monitor
    while (Serial.available() > 0) {
        char c = Serial.read();
        handleSerialCommand(c);
    }

    // Auto-stream measurement
    if (autoStream && (millis() - lastMeasureTime >= streamIntervalMs)) {
        lastMeasureTime = millis();
        processMeasurement();
    }

    delay(10);
}
