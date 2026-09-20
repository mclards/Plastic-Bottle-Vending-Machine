/*
 * =============================================================================
 * VMC ECO-VENDO — AS7263 NIR Spectrometer True Calibration & Material Profiler
 * =============================================================================
 * Multi-material spectrometer calibration bench for SparkFun AS7263 6-Channel NIR.
 * Distinguishes Clear PET, Colored PET, Clear Glass, Colored Glass, Labels,
 * Metal Cans, Paper/Cardboard, and Human Hand.
 * 
 * Hardware Connection (I2C):
 *   AS7263 SDA  --> ESP32 GPIO 21
 *   AS7263 SCL  --> ESP32 GPIO 22
 *   AS7263 VCC  --> 3.3V (Do NOT connect to 5V!)
 *   AS7263 GND  --> GND
 * 
 * Serial Monitor Settings:
 *   Baud Rate: 115200
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
uint8_t currentGain = 3;               // 0=1x, 1=3.7x, 2=16x, 3=64x (default 64x)
uint8_t currentIntegration = 50;         // 50 * 2.8ms = 140ms
bool bulbEnabled = false;               // Illumination bulb
uint8_t bulbCurrent = 2;               // 0=12.5mA, 1=25mA, 2=50mA, 3=100mA
bool autoStream = true;                // Stream continuously
unsigned long streamIntervalMs = 1500; // 1.5 seconds between scans
unsigned long lastMeasureTime = 0;
unsigned long scanCount = 0;

// Material Profile Data Structure
struct MaterialSample {
    bool captured;
    const char* label;
    int r, s, t, u, v, w;
    long rawSum;
    float calR, calW;
    float wrRatio;
    float trRatio;
    float wvRatio;
    int tempC;
};

#define NUM_SLOTS 9

// 9 Comprehensive Material Slots for Reverse Vending Calibration
MaterialSample samples[NUM_SLOTS] = {
    {false, "1. Empty Chamber (Air)",  0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0},
    {false, "2. Clear PET Body",       0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0},
    {false, "3. Colored PET (Green)",  0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0},
    {false, "4. Clear Glass Bottle",   0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0},
    {false, "5. Colored Glass (Beer)", 0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0},
    {false, "6. Bottle Label (OPP)",   0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0},
    {false, "7. Metal Can (Aluminum)", 0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0},
    {false, "8. Cardboard / Paper",    0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0},
    {false, "9. Human Hand / Skin",    0, 0, 0, 0, 0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0}
};

void printTableHeader() {
    Serial.println();
    Serial.println("  Scan | R(610) | S(680) | T(730) | U(760) | V(810) | W(860) | Raw Sum | Cal-W(uW) |  W/R  |  T/R  |  W/V  | Bulb");
    Serial.println("-------+--------+--------+--------+--------+--------+--------+---------+-----------+-------+-------+-------+------");
}

void printHelp() {
    Serial.println();
    Serial.println("=========================================================================================");
    Serial.println("         AS7263 NIR SPECTROMETER TRUE MATERIAL CALIBRATION BENCH (v2.4)                  ");
    Serial.println("=========================================================================================");
    Serial.println("  CONTROL COMMANDS:");
    Serial.println("    [p] Pause / Resume live streaming");
    Serial.println("    [b] Toggle on-board illumination BULB (ON/OFF) [Essential for bottles]");
    Serial.println("    [g] Cycle Gain (1x -> 3.7x -> 16x -> 64x)");
    Serial.println("    [+] Slower scroll (+500 ms) | [-] Faster scroll (-500 ms)");
    Serial.println("    [Space] / [m] Trigger single-shot scan");
    Serial.println("    [x] Clear all captured calibration samples");
    Serial.println();
    Serial.println("  MATERIAL CALIBRATION CAPTURE KEYS (Hold material in front of sensor, type number):");
    Serial.println("    [1] Capture EMPTY CHAMBER / AIR baseline");
    Serial.println("    [2] Capture CLEAR PET PLASTIC BOTTLE BODY (transparent plastic)");
    Serial.println("    [3] Capture COLORED PET BOTTLE (green Sprite / Mountain Dew, amber C2)");
    Serial.println("    [4] Capture CLEAR GLASS BOTTLE (soda / beverage glass)");
    Serial.println("    [5] Capture COLORED GLASS BOTTLE (green wine, brown beer glass)");
    Serial.println("    [6] Capture BOTTLE LABEL (cellophane wrap / printed plastic / paper label)");
    Serial.println("    [7] Capture METAL CAN (aluminum soda can / tin)");
    Serial.println("    [8] Capture CARDBOARD / PAPER CUP / NAPKIN");
    Serial.println("    [9] Capture HUMAN HAND / FINGER / NON-OBJECT");
    Serial.println();
    Serial.println("  ANALYSIS:");
    Serial.println("    [t] Print FULL SPECTRAL COMPARISON MATRIX & DISCRIMINATION ANALYSIS");
    Serial.println("    [h] or [?] Show this command list");
    Serial.println("=========================================================================================");
    Serial.println();
    printTableHeader();
}

void captureSample(int slotIdx) {
    if (slotIdx < 0 || slotIdx >= NUM_SLOTS) return;

    if (bulbEnabled) {
        sensor.enableBulb();
        delay(40);
    }
    sensor.takeMeasurements();

    samples[slotIdx].captured = true;
    samples[slotIdx].r = sensor.getR();
    samples[slotIdx].s = sensor.getS();
    samples[slotIdx].t = sensor.getT();
    samples[slotIdx].u = sensor.getU();
    samples[slotIdx].v = sensor.getV();
    samples[slotIdx].w = sensor.getW();
    samples[slotIdx].rawSum = (long)samples[slotIdx].r + samples[slotIdx].s + samples[slotIdx].t +
                              samples[slotIdx].u + samples[slotIdx].v + samples[slotIdx].w;
    samples[slotIdx].calR = sensor.getCalibratedR();
    samples[slotIdx].calW = sensor.getCalibratedW();
    samples[slotIdx].tempC = sensor.getTemperature();

    samples[slotIdx].wrRatio = (samples[slotIdx].r > 0) 
        ? ((float)samples[slotIdx].w / (float)samples[slotIdx].r) : 0.0f;
    samples[slotIdx].trRatio = (samples[slotIdx].r > 0) 
        ? ((float)samples[slotIdx].t / (float)samples[slotIdx].r) : 0.0f;
    samples[slotIdx].wvRatio = (samples[slotIdx].v > 0) 
        ? ((float)samples[slotIdx].w / (float)samples[slotIdx].v) : 0.0f;

    Serial.println();
    Serial.println("-----------------------------------------------------------------------------------------");
    Serial.printf("[CAPTURED] --> %s\n", samples[slotIdx].label);
    Serial.printf("  Raw Counts   : R(610)=%d, S(680)=%d, T(730)=%d, U(760)=%d, V(810)=%d, W(860)=%d | Sum=%ld\n",
                  samples[slotIdx].r, samples[slotIdx].s, samples[slotIdx].t,
                  samples[slotIdx].u, samples[slotIdx].v, samples[slotIdx].w, samples[slotIdx].rawSum);
    Serial.printf("  Calibrated   : Cal-R = %.2f uW/cm2 | Cal-W = %.2f uW/cm2\n", 
                  samples[slotIdx].calR, samples[slotIdx].calW);
    Serial.printf("  Ratios       : W/R = %.2f | T/R = %.2f | W/V = %.2f\n",
                  samples[slotIdx].wrRatio, samples[slotIdx].trRatio, samples[slotIdx].wvRatio);
    Serial.println("  (Type 't' anytime to view the complete side-by-side comparison matrix)");
    Serial.println("-----------------------------------------------------------------------------------------");
    if (autoStream) printTableHeader();
}

void printComparisonTable() {
    Serial.println();
    Serial.println("=============================================================================================================================");
    Serial.println("                                         AS7263 MULTI-MATERIAL SPECTRAL COMPARISON MATRIX                                    ");
    Serial.println("=============================================================================================================================");
    Serial.println(" Slot | Material Name          | R(610) | S(680) | T(730) | U(760) | V(810) | W(860) | Sum Raw | Cal-W(uW) |  W/R  |  T/R  |  W/V ");
    Serial.println("------+------------------------+--------+--------+--------+--------+--------+--------+---------+-----------+-------+-------+------");

    for (int i = 0; i < NUM_SLOTS; ++i) {
        if (samples[i].captured) {
            Serial.printf("  [%d] | %-22s | %6d | %6d | %6d | %6d | %6d | %6d | %7ld | %9.1f | %5.2f | %5.2f | %5.2f\n",
                          i + 1, samples[i].label,
                          samples[i].r, samples[i].s, samples[i].t,
                          samples[i].u, samples[i].v, samples[i].w,
                          samples[i].rawSum, samples[i].calW,
                          samples[i].wrRatio, samples[i].trRatio, samples[i].wvRatio);
        } else {
            Serial.printf("  [%d] | %-22s |  --- NOT YET CAPTURED (Press [%d] to record sample) ---\n",
                          i + 1, samples[i].label, i + 1);
        }
    }
    Serial.println("=============================================================================================================================");

    // In-depth physical analysis of captured samples
    Serial.println("\n[DISCRIMINATION ANALYSIS & THESIS OBSERVATIONS]");
    
    // 1. Clear PET vs Clear Glass (The Classic RVM Dilemma)
    if (samples[1].captured && samples[3].captured) {
        Serial.println("  ---------------------------------------------------------------------------------------");
        Serial.println("  [COMPARE] Clear PET Plastic vs Clear Glass Bottle:");
        Serial.printf("    * Cal-W (860nm)  : PET = %.1f uW/cm2  | Glass = %.1f uW/cm2 (Delta: %+.1f uW)\n",
                      samples[1].calW, samples[3].calW, samples[1].calW - samples[3].calW);
        Serial.printf("    * T(730) Far-Red : PET = %d counts       | Glass = %d counts (Delta: %+d)\n",
                      samples[1].t, samples[3].t, samples[1].t - samples[3].t);
        Serial.printf("    * Total Raw Sum  : PET = %ld           | Glass = %ld\n",
                      samples[1].rawSum, samples[3].rawSum);
        Serial.printf("    * T/R Ratio      : PET = %.2f            | Glass = %.2f\n",
                      samples[1].trRatio, samples[3].trRatio);
        Serial.println("  ---------------------------------------------------------------------------------------");
    }

    // 2. Clear PET vs Bottle Label
    if (samples[1].captured && samples[5].captured) {
        float labelGain = (samples[1].calW > 0) ? (samples[5].calW / samples[1].calW) : 0.0f;
        Serial.printf("  * Clear PET vs Label: Diffuse label scattering is %.1fx brighter than transparent PET wall.\n", labelGain);
    }

    // 3. Clear PET vs Empty Air
    if (samples[0].captured && samples[1].captured) {
        Serial.printf("  * Clear PET vs Empty Air: Cal-W delta is %+.1f uW/cm2 | T(730) delta is %+d counts.\n",
                      samples[1].calW - samples[0].calW, samples[1].t - samples[0].t);
    }

    // 4. Metal Can vs Plastics
    if (samples[6].captured) {
        Serial.printf("  * Metal Can: Total Raw Optical Sum = %ld | Cal-W = %.1f uW/cm2\n",
                      samples[6].rawSum, samples[6].calW);
    }

    Serial.println("=============================================================================================================================\n");
    if (autoStream) printTableHeader();
}

void processMeasurement() {
    scanCount++;

    if (bulbEnabled) {
        sensor.enableBulb();
        delay(40);
    }

    sensor.takeMeasurements();

    int r = sensor.getR();
    int s = sensor.getS();
    int t = sensor.getT();
    int u = sensor.getU();
    int v = sensor.getV();
    int w = sensor.getW();
    long rawSum = (long)r + s + t + u + v + w;

    float calW = sensor.getCalibratedW();
    float wrRatio = (r > 0) ? ((float)w / (float)r) : 0.0f;
    float trRatio = (r > 0) ? ((float)t / (float)r) : 0.0f;
    float wvRatio = (v > 0) ? ((float)w / (float)v) : 0.0f;

    if (scanCount % 20 == 1 && scanCount > 1) {
        printTableHeader();
    }

    Serial.printf(" #%03lu | %6d | %6d | %6d | %6d | %6d | %6d | %7ld | %9.1f | %5.2f | %5.2f | %5.2f |  %s\n",
                  scanCount % 1000, r, s, t, u, v, w, rawSum, calW, wrRatio, trRatio, wvRatio,
                  bulbEnabled ? "ON " : "OFF");
}

void handleSerialCommand(char cmd) {
    switch (cmd) {
        case 'p':
        case 'P':
            autoStream = !autoStream;
            if (autoStream) {
                Serial.println("\n[RESUMED] Live streaming active.");
                printTableHeader();
            } else {
                Serial.println("\n[PAUSED] Press 'p' to resume, or [Space] to step 1 scan.");
            }
            break;

        case 'b':
        case 'B':
            bulbEnabled = !bulbEnabled;
            if (bulbEnabled) {
                sensor.setBulbCurrent(bulbCurrent);
                sensor.enableBulb();
                Serial.println("\n[BULB] Illumination Bulb ENABLED (50 mA). Reflective spectroscopy active.");
            } else {
                sensor.disableBulb();
                Serial.println("\n[BULB] Illumination Bulb DISABLED. Passive ambient active.");
            }
            printTableHeader();
            break;

        case '1': captureSample(0); break;
        case '2': captureSample(1); break;
        case '3': captureSample(2); break;
        case '4': captureSample(3); break;
        case '5': captureSample(4); break;
        case '6': captureSample(5); break;
        case '7': captureSample(6); break;
        case '8': captureSample(7); break;
        case '9': captureSample(8); break;

        case 't':
        case 'T':
            printComparisonTable();
            break;

        case 'x':
        case 'X':
            for (int i = 0; i < NUM_SLOTS; ++i) {
                samples[i].captured = false;
            }
            Serial.println("\n[RESET] All captured material samples cleared.");
            printTableHeader();
            break;

        case 'g':
        case 'G': {
            currentGain = (currentGain + 1) % 4;
            sensor.setGain(currentGain);
            const char* gStr[] = {"1x", "3.7x", "16x", "64x"};
            Serial.printf("\n[GAIN] Switched to %s.\n", gStr[currentGain]);
            printTableHeader();
            break;
        }

        case '+':
            streamIntervalMs += 500;
            if (streamIntervalMs > 10000) streamIntervalMs = 10000;
            Serial.printf("\n[INTERVAL] Slower: %lu ms per scan.\n", streamIntervalMs);
            break;

        case '-':
            if (streamIntervalMs > 500) streamIntervalMs -= 500;
            Serial.printf("\n[INTERVAL] Faster: %lu ms per scan.\n", streamIntervalMs);
            break;

        case ' ':
        case 'm':
        case 'M':
            processMeasurement();
            break;

        case '\n':
        case '\r':
            // Ignore carriage return and newline from serial terminals
            break;

        case 'h':
        case 'H':
        case '?':
            printHelp();
            break;

        default:
            break;
    }
}

void setup() {
    Serial.begin(DEFAULT_BAUD);
    delay(1000);

    Serial.println();
    Serial.println("=========================================================================================");
    Serial.println("     VMC ECO-VENDO — AS7263 MULTI-MATERIAL SPECTROMETER BENCH (v2.4)                    ");
    Serial.println("=========================================================================================");
    Serial.printf("Starting I2C bus on SDA=GPIO %d, SCL=GPIO %d...\n", I2C_SDA_PIN, I2C_SCL_PIN);
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, 100000);

    Wire.beginTransmission(0x49);
    byte error = Wire.endTransmission();
    if (error != 0) {
        Serial.printf("[ERROR] AS7263 NOT found at address 0x49 (Error: %d)!\n", error);
        Serial.println("Please verify wiring: SDA->21, SCL->22, VIN->3.3V, GND->GND");
        while (true) delay(1000);
    }
    Serial.println("[OK] AS7263 responded at address 0x49!");

    if (!sensor.begin(Wire, currentGain, 3)) {
        Serial.println("[ERROR] AS726X begin failed!");
        while (true) delay(1000);
    }

    sensor.setIntegrationTime(currentIntegration);
    sensor.setGain(currentGain);
    sensor.disableBulb();
    sensor.disableIndicator();

    Serial.printf("[READY] Gain: 64x | Integration: 140 ms | Scan Interval: %lu ms\n", streamIntervalMs);
    Serial.println("Bulb: OFF (Press 'b' to toggle ON for reflective plastic & glass testing)");
    printHelp();
}

void loop() {
    while (Serial.available() > 0) {
        char c = Serial.read();
        handleSerialCommand(c);
    }

    if (autoStream && (millis() - lastMeasureTime >= streamIntervalMs)) {
        lastMeasureTime = millis();
        processMeasurement();
    }

    delay(10);
}
