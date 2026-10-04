#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Adafruit_PWMServoDriver.h>
#include <atomic>
#include <cstdarg>
#include <AS726X.h>
#include <HX711.h>
#include <esp_wifi.h>
#include <esp_bt.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include "machine_config.h"
#include "release_version.h"

// -----------------------------------------------------------------------------
// THREAD-SAFE SERIAL & DIAGNOSTICS LOGGING
// -----------------------------------------------------------------------------
SemaphoreHandle_t serialMutex = nullptr;

void emitSerialLine(const String& line) {
    if (serialMutex) xSemaphoreTake(serialMutex, portMAX_DELAY);
    Serial.println(line);
    if (serialMutex) xSemaphoreGive(serialMutex);
}

#if defined(PRODUCTION_RELEASE) || defined(NDEBUG)
    #define logDebug(tag, ...) ((void)0)
    #define logWarn(tag, ...)  ((void)0)
    #define logError(tag, ...) ((void)0)
#else
    void logMsg(const char* level, const char* tag, const char* format, va_list args) {
        char buffer[256];
        vsnprintf(buffer, sizeof(buffer), format, args);
        if (serialMutex) xSemaphoreTake(serialMutex, portMAX_DELAY);
        Serial.print("[");
        Serial.print(millis());
        Serial.print("] [");
        Serial.print(level);
        Serial.print("] [");
        Serial.print(tag);
        Serial.print("] ");
        Serial.println(buffer);
        if (serialMutex) xSemaphoreGive(serialMutex);
    }

    void logDebug(const char* tag, const char* format, ...) {
        va_list args;
        va_start(args, format);
        logMsg("DEBUG", tag, format, args);
        va_end(args);
    }

    void logWarn(const char* tag, const char* format, ...) {
        va_list args;
        va_start(args, format);
        logMsg("WARN", tag, format, args);
        va_end(args);
    }

    void logError(const char* tag, const char* format, ...) {
        va_list args;
        va_start(args, format);
        logMsg("ERROR", tag, format, args);
        va_end(args);
    }
#endif

// -----------------------------------------------------------------------------
// HARDWARE PIN DEFINITIONS
// -----------------------------------------------------------------------------
#define PIN_IR_TOP 32             // E18-D80NK Intake (Left Side Bus)
#define PIN_IR_BOTTOM 33          // E18-D80NK Drop (Left Side Bus)
#define PIN_PROX_METAL 25         // LJ12A3-4-Z/BX Metal (Left Side Bus)
#define PIN_HX711_DOUT 15         // HX711 Load Cell Data Out (Left Side Bus)
#define PIN_HX711_SCK 4           // HX711 Load Cell Serial Clock (Left Side Bus)
#define PIN_ULTRASONIC_TRIG 23    // HC-SR04 Trig (Right Side Top)
#define PIN_ULTRASONIC_ECHO 27    // HC-SR04 Echo (Left Side Bus)

#define PIN_FINISH_BTN 19         // Front Panel Button (Right Side, internal pull-up)
#define PIN_BUZZER 18             // Front Panel Buzzer (Right Side)
#define PIN_LED_GREEN 5           // Front Panel Green LED (Right Side)
#define PIN_LED_RED 17            // Front Panel Red LED (Right Side)

// PCA9685 I2C Servo Channels (2 Servos: Entrance Gate & Success Flap)
#define PCA9685_I2C_ADDR 0x40
#define PCA_CHANNEL_ENTRANCE 0
#define PCA_CHANNEL_SUCCESS 1

// PCA9685 50Hz PWM Calibration (4096 counts / 20ms period = 4.8828 us/count):
// 500us = 102 counts (0 deg), 1500us = 307 counts (90 deg true center), 2500us = 512 counts (180 deg)
#define SERVOMIN 102 // Calibrated 0 degrees baseline (500us pulse)
#define SERVOMAX 512 // Calibrated 180 degrees baseline (2500us pulse)

// -----------------------------------------------------------------------------
// PERIPHERALS & GLOBAL STATE
// -----------------------------------------------------------------------------
LiquidCrystal_I2C lcd(0x27, 20, 4);
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(PCA9685_I2C_ADDR);
bool pca9685Found = false;

AS726X spectrometer;
bool spectrometerFound = false;

HX711 scale;
bool hx711Found = false;
float lastMeasuredWeightG = 0.0f;

MachineConfig config;
MachineConfig desiredConfig; // UART task owns this; sensor task owns runtime config.
QueueHandle_t configQueue;
std::atomic<int> requestedGateTimeout{60};
std::atomic<bool> finishRequested{false};
Preferences preferences;




std::atomic<bool> topIrTriggered{false};
std::atomic<bool> bottomIrTriggered{false};

std::atomic<bool> isBinFull{false};
std::atomic<int> currentSessionBottles{0};
std::atomic<bool> entranceGateRequested{false};
std::atomic<bool> forceGateClose{false};

QueueHandle_t eventQueue;
SemaphoreHandle_t uiMutex;
TaskHandle_t sensorTaskHandle = NULL;
std::atomic<int> cachedBinDistanceCm{999};

enum EventMsg {
    MSG_BIN_FULL,
    MSG_BIN_OK,
    MSG_REJECT_TIN,
    MSG_REJECT_NON_PLASTIC,
    MSG_REJECT_NIR,
    MSG_VALIDATE_START,
    MSG_BOTTLE_SAVED,
    MSG_DROP_TIMEOUT,
    MSG_GATE_TIMEOUT,
    MSG_ITEM_CLEARED,
    MSG_RETRIEVAL_TIMEOUT
};

const char* getEventMsgName(EventMsg kind) {
    switch (kind) {
        case MSG_BIN_FULL: return "BIN_FULL";
        case MSG_BIN_OK: return "BIN_OK";
        case MSG_REJECT_TIN: return "REJECT_TIN";
        case MSG_REJECT_NON_PLASTIC: return "REJECT_NON_PLASTIC";
        case MSG_REJECT_NIR: return "REJECT_NIR";
        case MSG_VALIDATE_START: return "VALIDATE_START";
        case MSG_BOTTLE_SAVED: return "BOTTLE_SAVED";
        case MSG_DROP_TIMEOUT: return "DROP_TIMEOUT";
        case MSG_GATE_TIMEOUT: return "GATE_TIMEOUT";
        case MSG_ITEM_CLEARED: return "ITEM_CLEARED";
        case MSG_RETRIEVAL_TIMEOUT: return "RETRIEVAL_TIMEOUT";
        default: return "UNKNOWN";
    }
}

// -----------------------------------------------------------------------------
// CREDIT JOURNAL & ATOMIC STORAGE
// -----------------------------------------------------------------------------
// One durable receipt is allowed in flight. Never open the next intake until ACK.
// phase 1 = drop in progress/uncertain after reset, 2 = accepted, 3 = failed drop.
struct CreditJournal {
    uint32_t version = 2;
    uint64_t sequence = 0;
    uint32_t total = 0;
    uint8_t phase = 0;
    char session[37] = {};
};
CreditJournal creditJournal;
Preferences creditStore;
SemaphoreHandle_t creditMutex;
std::atomic<bool> creditStorageOk{false};
std::atomic<bool> depositCycleBusy{false};
struct QueuedEvent { EventMsg kind; char session[37]; };

bool saveCreditJournal() { // Caller holds creditMutex; NVS blob replacement is atomic.
    bool ok = creditStore.putBytes("receipt", &creditJournal, sizeof(creditJournal)) == sizeof(creditJournal);
    creditStorageOk = ok;
    if (!ok) {
        logError("JOURNAL", "Failed to persist credit journal to NVS!");
    }
    return ok;
}

String receiptId() {
    char value[96];
    if (creditJournal.session[0]) {
        snprintf(value, sizeof(value), "%012llx:%s:%llu", (unsigned long long)ESP.getEfuseMac(),
                 creditJournal.session, (unsigned long long)creditJournal.sequence);
    } else {
        snprintf(value, sizeof(value), "%012llx:%llu", (unsigned long long)ESP.getEfuseMac(),
                 (unsigned long long)creditJournal.sequence);
    }
    return String(value);
}

void scopedEvent(const char* event, const char* session) {
    JsonDocument doc;
    doc["event"] = event;
    doc["protocol"] = 2;
    doc["session_id"] = session;
    String output;
    serializeJson(doc, output);
    logDebug("TX-JSON", "Event '%s' for session '%s'", event, session);
    emitSerialLine(output);
}

// -----------------------------------------------------------------------------
// MULTI-SPECTRAL HARDENED NIR DISCRIMINATOR
// -----------------------------------------------------------------------------
struct NirEvaluation {
    bool is_pet;
    const char* reason_code;
    const char* reason_desc;
    float wv_ratio;
    float sr_ratio;
    float tw_ratio;
};

inline NirEvaluation evaluateNirSpectrum(int r, int s, int t, int u, int v, int w, float cal_w, int nir_min, int nir_max) {
    NirEvaluation res;
    res.wv_ratio = (v > 0) ? ((float)w / (float)v) : 0.0f;
    res.sr_ratio = (r > 0) ? ((float)s / (float)r) : 0.0f;
    res.tw_ratio = (w > 0) ? ((float)t / (float)w) : 0.0f;

    // 1. Absolute calibrated irradiance bounds [nir_min - nir_max]
    if (cal_w < (float)nir_min) {
        res.is_pet = false;
        res.reason_code = (cal_w < 22.0f) ? "colored_glass" : "nir_low_absorption";
        res.reason_desc = (cal_w < 22.0f) ? "Colored Glass Detected" : "Optical Signal Too Weak";
        return res;
    }
    if (cal_w > (float)nir_max) {
        res.is_pet = false;
        res.reason_code = "nir_high_scatter";
        res.reason_desc = "Intense Scattering (Paper/Cardboard)";
        return res;
    }

    // 2. Minimum channel noise floor
    if (r < 100 || s < 50 || v < 15 || w < 10) {
        res.is_pet = false;
        res.reason_code = "nir_signal_noise";
        res.reason_desc = "Optical Signal Below Noise Floor";
        return res;
    }

    // Note on Clear Glass: Both smooth clear glass and smooth clear PET exhibit ~4%
    // Fresnel reflection at 860nm. Clear glass passes NIR and is authoritatively
    // rejected by the physical HX711 scale (> 65g, typically 250g - 450g).
    res.is_pet = true;
    res.reason_code = "pet_confirmed";
    res.reason_desc = "PET Plastic Confirmed";
    return res;
}

void gateStateEvent(bool open) {
    char session[37];
    xSemaphoreTake(creditMutex, portMAX_DELAY);
    strlcpy(session, creditJournal.session, sizeof(session));
    xSemaphoreGive(creditMutex);
    logDebug("AIRLOCK", "Gate state changed -> %s (session: '%s')", open ? "GATE_OPEN" : "GATE_CLOSED", session);
    scopedEvent(open ? "GATE_OPEN" : "GATE_CLOSED", session);
}

void postEvent(EventMsg kind) {
    QueuedEvent event = {};
    event.kind = kind;
    xSemaphoreTake(creditMutex, portMAX_DELAY);
    strlcpy(event.session, creditJournal.session, sizeof(event.session));
    xSemaphoreGive(creditMutex);
    logDebug("QUEUE", "Posting UI event: %s for session '%s'", getEventMsgName(kind), event.session);
    xQueueSend(eventQueue, &event, pdMS_TO_TICKS(100)); // Display queue cannot lose credit.
}

bool prepareDrop() {
    xSemaphoreTake(creditMutex, portMAX_DELAY);
    bool ok = creditStorageOk && creditJournal.phase == 0 && creditJournal.session[0];
    if (ok) {
        ++creditJournal.sequence;
        creditJournal.phase = 1;
        ok = saveCreditJournal();
        logDebug("JOURNAL", "prepareDrop: Sequence incremented to %llu, Phase=1 (in progress), Saved=%d",
                 (unsigned long long)creditJournal.sequence, ok);
    } else {
        logWarn("JOURNAL", "prepareDrop REJECTED: storageOk=%d, phase=%d, session='%s'",
                creditStorageOk.load(), creditJournal.phase, creditJournal.session);
    }
    xSemaphoreGive(creditMutex);
    return ok;
}

void completeDrop(bool accepted) {
    xSemaphoreTake(creditMutex, portMAX_DELAY);
    creditJournal.phase = accepted ? 2 : 3;
    if (accepted) ++creditJournal.total;
    bool saved = saveCreditJournal(); // On failure, keep gates closed; retry before transmitting.
    currentSessionBottles = creditJournal.total;
    logDebug("JOURNAL", "completeDrop: accepted=%d -> Phase=%d, Total Bottles=%u, Saved=%d, Receipt=%s",
             accepted, creditJournal.phase, creditJournal.total, saved, receiptId().c_str());
    xSemaphoreGive(creditMutex);
}

void replayCredit() {
    JsonDocument doc;
    xSemaphoreTake(creditMutex, portMAX_DELAY);
    if (!creditStorageOk || creditJournal.phase == 0 || (creditJournal.phase == 1 && depositCycleBusy)) {
        xSemaphoreGive(creditMutex);
        return;
    }
    doc["event"] = creditJournal.phase == 2 ? "CREDIT_ADD" : "DEPOSIT_RECOVERY";
    doc["event_id"] = receiptId();
    doc["session_id"] = creditJournal.session;
    doc["protocol"] = 2;
    doc["bottles"] = creditJournal.phase == 2 ? 1 : 0;
    doc["sessionTotal"] = creditJournal.total;
    doc["phase"] = creditJournal.phase;

    String id = receiptId();
    uint8_t ph = creditJournal.phase;
    uint32_t tot = creditJournal.total;
    char sid[37];
    strlcpy(sid, creditJournal.session, sizeof(sid));
    xSemaphoreGive(creditMutex);

    String output;
    serializeJson(doc, output);
    logDebug("TX-JSON", "Replaying credit receipt: %s (ID=%s, Phase=%d, Total=%u, Session='%s')",
             ph == 2 ? "CREDIT_ADD" : "DEPOSIT_RECOVERY", id.c_str(), ph, tot, sid);
    emitSerialLine(output);
}

// -----------------------------------------------------------------------------
// HARDWARE ACTUATORS & SENSORS
// -----------------------------------------------------------------------------
void IRAM_ATTR isrTopIr() { topIrTriggered = true; }
void IRAM_ATTR isrBottomIr() { bottomIrTriggered = true; }

void setServoAngle(uint8_t channel, int angle) {
    if (pca9685Found) {
        if (angle < 0) angle = 0;
        if (angle > 180) angle = 180;
        int pulse = map(angle, 0, 180, SERVOMIN, SERVOMAX);
        pwm.setPWM(channel, 0, pulse);
        logDebug("SERVO", "Channel %u set to %d deg (PWM: %d)", channel, angle, pulse);
    } else {
        logWarn("SERVO", "Cannot drive Channel %u: PCA9685 PWM driver not detected!", channel);
    }
}

void buzz(int durationMs, int pulses = 1) {
    logDebug("BUZZER", "Buzzing %d ms x %d pulse(s)", durationMs, pulses);
    for(int i = 0; i < pulses; i++) {
        digitalWrite(PIN_BUZZER, HIGH);
        delay(durationMs); // Use delay since it can be called from Config Mode too
        digitalWrite(PIN_BUZZER, LOW);
        if(pulses > 1) delay(80);
    }
}

int getBinDistanceCm() {
    digitalWrite(PIN_ULTRASONIC_TRIG, LOW);
    delayMicroseconds(2);
    digitalWrite(PIN_ULTRASONIC_TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_ULTRASONIC_TRIG, LOW);
    long duration = pulseIn(PIN_ULTRASONIC_ECHO, HIGH, 30000);
    if (duration == 0) return 999;
    return duration * 0.034 / 2;
}

// -----------------------------------------------------------------------------
// NVS PREFERENCES CONFIGURATION
// -----------------------------------------------------------------------------
void loadPreferences() {
    preferences.begin("ecovendo", false);
    config.bin_full_threshold_cm = preferences.getInt("bin_cm", 15);
    config.bin_sensor_orientation = preferences.getInt("bin_orient", 0);
    config.bin_empty_depth_cm = preferences.getInt("bin_empty", 60);
    config.bin_debounce_s = preferences.getInt("bin_deb", 3);
    config.pet_nir_w_min = preferences.getInt("nir_min", 30);
    config.pet_nir_w_max = preferences.getInt("nir_max", 220);
    config.entrance_gate_timeout = preferences.getInt("ent_tout", 60);
    config.settle_time_ms = preferences.getInt("stl_ms", 500);
    config.success_drop_tout_ms = preferences.getInt("suc_tout", 3000);
    config.retrieval_timeout_s = preferences.getInt("ret_tout", 45);
    config.ent_open_angle = preferences.getInt("ent_open", 90);
    config.ent_close_angle = preferences.getInt("ent_close", 0);
    config.suc_open_angle = preferences.getInt("suc_open", 90);
    config.suc_close_angle = preferences.getInt("suc_close", 0);
    config.require_nir_sensor = preferences.getInt("req_nir", 1);
    config.require_weight_sensor = preferences.getInt("req_wt", 0);
    config.require_bin_sensor = preferences.getInt("req_bin", 0);
    config.min_bottle_weight_g = preferences.getInt("min_wt", 10);
    config.max_bottle_weight_g = preferences.getInt("max_wt", 65);
    config.weight_cal_factor = preferences.getInt("wt_cal", 420);
    config.config_timestamp = preferences.getULong("cfg_ts", 0);

    logDebug("NVS", "Loaded Hardware Preferences:");
    logDebug("NVS", "  Bin: Thresh=%d cm, Orient=%s, EmptyDepth=%d cm, Debounce=%d s",
             config.bin_full_threshold_cm,
             config.bin_sensor_orientation == 1 ? "Horizontal" : "Overhead",
             config.bin_empty_depth_cm, config.bin_debounce_s);
    logDebug("NVS", "  PET NIR Range: [%d - %d]", config.pet_nir_w_min, config.pet_nir_w_max);
    logDebug("NVS", "  Entrance Gate Timeout: %d s", config.entrance_gate_timeout);
    logDebug("NVS", "  Timings: Settle=%d ms, SuccessTout=%d ms, RetrievalTimeout=%d s",
             config.settle_time_ms, config.success_drop_tout_ms, config.retrieval_timeout_s);
    logDebug("NVS", "  Servos: Ent=[%d/%d], Suc=[%d/%d]",
             config.ent_open_angle, config.ent_close_angle,
             config.suc_open_angle, config.suc_close_angle);
    logDebug("NVS", "  Intake Requirements: NIR=%d, Weight=%d, BinFull=%d (Range: [%d - %d] g, CalFactor: %d)",
             config.require_nir_sensor, config.require_weight_sensor, config.require_bin_sensor,
             config.min_bottle_weight_g, config.max_bottle_weight_g, config.weight_cal_factor);
    logDebug("NVS", "  Config Timestamp: %lu", config.config_timestamp);
}

void savePreferences() {
    preferences.begin("ecovendo", false);
    preferences.putInt("bin_cm", config.bin_full_threshold_cm);
    preferences.putInt("bin_orient", config.bin_sensor_orientation);
    preferences.putInt("bin_empty", config.bin_empty_depth_cm);
    preferences.putInt("bin_deb", config.bin_debounce_s);
    preferences.putInt("nir_min", config.pet_nir_w_min);
    preferences.putInt("nir_max", config.pet_nir_w_max);
    preferences.putInt("ent_tout", config.entrance_gate_timeout);
    preferences.putInt("stl_ms", config.settle_time_ms);
    preferences.putInt("suc_tout", config.success_drop_tout_ms);
    preferences.putInt("ret_tout", config.retrieval_timeout_s);
    preferences.putInt("ent_open", config.ent_open_angle);
    preferences.putInt("ent_close", config.ent_close_angle);
    preferences.putInt("suc_open", config.suc_open_angle);
    preferences.putInt("suc_close", config.suc_close_angle);
    preferences.putInt("req_nir", config.require_nir_sensor);
    preferences.putInt("req_wt", config.require_weight_sensor);
    preferences.putInt("req_bin", config.require_bin_sensor);
    preferences.putInt("min_wt", config.min_bottle_weight_g);
    preferences.putInt("max_wt", config.max_bottle_weight_g);
    preferences.putInt("wt_cal", config.weight_cal_factor);
    preferences.putULong("cfg_ts", config.config_timestamp);
    logDebug("NVS", "Persisted hardware parameters (ts=%lu) to Flash.", config.config_timestamp);
}

// SENSOR & MECHANICAL WORKFLOW TASK (CORE 0)
// -----------------------------------------------------------------------------
void sensorTaskCode(void* parameter) {
    (void)parameter;
    logDebug("SENSOR", "SensorTask running on Core %d", xPortGetCoreID());
    TickType_t lastUltrasonicCheck = xTaskGetTickCount();
    bool lastBinState = false;

    // Secure all gates at startup
    logDebug("SENSOR", "Securing entrance and success servos at startup closed angles...");
    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
    setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);

    while (true) {
        // Apply configuration only between complete mechanical cycles. Only this
        // task drives vending servos; the UART task never interrupts a drop.
        MachineConfig pending;
        if (xQueueReceive(configQueue, &pending, 0) == pdTRUE) {
            config = pending;
            savePreferences();
            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
            setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
            logDebug("SENSOR", "Applied pending config and saved to Flash (ts=%lu).", config.config_timestamp);
            char saveBuf[96];
            snprintf(saveBuf, sizeof(saveBuf), "{\"event\":\"CONFIG_SAVED\",\"cfg_ts\":%lu}", config.config_timestamp);
            emitSerialLine(saveBuf);
            if (!config.require_bin_sensor && isBinFull.load()) {
                isBinFull = false;
                lastBinState = false;
                postEvent(MSG_BIN_OK);
            }
        }


        // 1. Check Bin Status (isolated non-blocking slice every 1500 ms)
        static unsigned long binDetectionStartMs = 0;
        if (xTaskGetTickCount() - lastUltrasonicCheck >= pdMS_TO_TICKS(1500)) {
            lastUltrasonicCheck = xTaskGetTickCount();
            int distance = getBinDistanceCm();
            cachedBinDistanceCm.store(distance);

            bool inRange = (distance < config.bin_full_threshold_cm && distance > 0);
            bool currentlyFull = false;

            if (config.require_bin_sensor) {
                if (config.bin_sensor_orientation == 1) {
                    // Horizontal side-mounted tripwire: must persist continuously for bin_debounce_s seconds
                    if (inRange) {
                        if (binDetectionStartMs == 0) binDetectionStartMs = millis();
                        if (millis() - binDetectionStartMs >= (unsigned long)(config.bin_debounce_s * 1000)) {
                            currentlyFull = true;
                        }
                    } else {
                        binDetectionStartMs = 0;
                        currentlyFull = false;
                    }
                } else {
                    // Overhead downward depth gauge: direct threshold comparison
                    binDetectionStartMs = 0;
                    currentlyFull = inRange;
                }
            } else {
                binDetectionStartMs = 0;
                currentlyFull = false;
            }

            if (currentlyFull != lastBinState) {
                isBinFull = currentlyFull;
                lastBinState = currentlyFull;
                EventMsg msg = currentlyFull ? MSG_BIN_FULL : MSG_BIN_OK;
                logDebug("BIN", "Bin status changed -> %s (Orient: %s, Measured: %d cm, Thresh: %d cm, Req: %d)",
                         currentlyFull ? "FULL" : "OK",
                         config.bin_sensor_orientation == 1 ? "HORIZ" : "OVERHEAD",
                         distance, config.bin_full_threshold_cm, config.require_bin_sensor);
                postEvent(msg);
            }
        }

        // Periodic telemetry heartbeat to Linux host gateway (every 3 seconds)
        static TickType_t lastTelemetryHeartbeat = 0;
        if (xTaskGetTickCount() - lastTelemetryHeartbeat >= pdMS_TO_TICKS(3000)) {
            lastTelemetryHeartbeat = xTaskGetTickCount();
            char hbBuf[448];
            bool hwReady = pca9685Found && (!config.require_nir_sensor || spectrometerFound) && (!config.require_weight_sensor || hx711Found) && (!config.require_bin_sensor || !isBinFull.load());
            snprintf(hbBuf, sizeof(hbBuf),
                     "{\"event\":\"HEARTBEAT\",\"bin_distance_cm\":%d,\"is_bin_full\":%s,\"pca9685_ready\":%s,\"spectrometer_ready\":%s,\"hx711_ready\":%s,\"hardware_ready\":%s,\"require_nir\":%d,\"require_weight\":%d,\"require_bin\":%d,\"bin_orient\":%d,\"bin_empty\":%d,\"bin_deb\":%d,\"gate_open\":%s,\"ap_active\":false,\"ap_stations\":0,\"cfg_ts\":%lu,\"protocol\":2}",
                     cachedBinDistanceCm.load(),
                     isBinFull.load() ? "true" : "false",
                     pca9685Found ? "true" : "false",
                     spectrometerFound ? "true" : "false",
                     hx711Found ? "true" : "false",
                     hwReady ? "true" : "false",
                     config.require_nir_sensor,
                     config.require_weight_sensor,
                     config.require_bin_sensor,
                     config.bin_sensor_orientation,
                     config.bin_empty_depth_cm,
                     config.bin_debounce_s,
                     depositCycleBusy.load() ? "true" : "false",
                     config.config_timestamp);
            emitSerialLine(hbBuf);
        }

        if (config.require_bin_sensor && isBinFull) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        // 2. Await Entrance Request
        if (entranceGateRequested.exchange(false)) {
            xSemaphoreTake(creditMutex, portMAX_DELAY);
            bool sensorReady = !config.require_nir_sensor || spectrometerFound;
            bool permitted = creditStorageOk && (creditJournal.phase == 0 || creditJournal.phase == 2) &&
                creditJournal.session[0] && !finishRequested && pca9685Found && sensorReady;
            if (permitted) {
                if (creditJournal.phase == 2) {
                    creditJournal.phase = 0;
                    saveCreditJournal();
                }
                depositCycleBusy = true;
            }
            char curSession[37];
            strlcpy(curSession, creditJournal.session, sizeof(curSession));
            uint8_t curPhase = creditJournal.phase;
            xSemaphoreGive(creditMutex);

            logDebug("CYCLE", "Entrance Gate Request: Permitted=%d (storageOk=%d, phase=%d, session='%s', finish=%d, pca=%d, spec=%d, req_nir=%d)",
                     permitted, creditStorageOk.load(), curPhase, curSession, finishRequested.load(), pca9685Found, spectrometerFound, config.require_nir_sensor);

            if (!permitted) {
                logWarn("CYCLE", "Deposit cycle not permitted! Bypassing entrance request.");
                if (!pca9685Found) {
                    emitSerialLine("{\"event\":\"HARDWARE_ALERT\",\"reason\":\"actuators_offline\"}");
                } else if (config.require_nir_sensor && !spectrometerFound) {
                    emitSerialLine("{\"event\":\"HARDWARE_ALERT\",\"reason\":\"spectrometer_offline\"}");
                }
                vTaskDelay(pdMS_TO_TICKS(20));
                continue;
            }

            logDebug("CYCLE", "Starting deposit cycle for session '%s'...", curSession);
            setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle); // Ensure drop flap is locked closed before entrance opens
            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_open_angle); // Open entrance
            gateStateEvent(true);

            // Tare scale cleanly right after entrance servo actuation settles to eliminate prior noise / mechanical drift
            vTaskDelay(pdMS_TO_TICKS(150)); // Allow entrance servo transit vibration to settle
            if (hx711Found && scale.wait_ready_timeout(200)) {
                scale.tare(3); // Fast 3-sample zero calibration
                lastMeasuredWeightG = 0.0f;
                logDebug("SCALE", "Scale tared cleanly after entrance servo opened. Offset=%ld", scale.get_offset());
            } else {
                logDebug("SCALE", "Scale tare bypassed (hx711Found=%d)", hx711Found);
            }
            topIrTriggered = false; // Clear any latch from servo movement vibration
            
            unsigned long openTime = millis();
            bool dropped = false;
            bool wasForced = false;
            
            const uint32_t gateTimeoutMs = requestedGateTimeout.load() * 1000UL;
            logDebug("AIRLOCK", "Entrance gate OPEN (Ch 0 -> %d deg). Waiting up to %u ms for bottle insertion...",
                     config.ent_open_angle, gateTimeoutMs);

            // Phase 1: Wait for bottle to enter (break the Top IR beam)
            unsigned long lastWaitLog = millis();
            while (millis() - openTime < gateTimeoutMs) {
                if (forceGateClose) {
                    wasForced = true;
                    logDebug("AIRLOCK", "Force gate close detected at +%lu ms!", millis() - openTime);
                    break;
                }
                // Check if top IR is triggered (either interrupt flag or direct LOW read)
                if (topIrTriggered || digitalRead(PIN_IR_TOP) == LOW) {
                    dropped = true;
                    topIrTriggered = false;
                    logDebug("AIRLOCK", "Top IR beam broken at +%lu ms! Bottle insertion detected.", millis() - openTime);
                    break;
                }
                if (millis() - lastWaitLog >= 5000) {
                    lastWaitLog = millis();
                    int rawTopIr = digitalRead(PIN_IR_TOP);
                    logDebug("AIRLOCK", "Awaiting bottle insertion: Top IR (GPIO %d)=%s (Waiting for beam break / LOW). Remaining: %lu s",
                             PIN_IR_TOP, rawTopIr == LOW ? "LOW (TRIGGERED)" : "HIGH (CLEAR)",
                             (gateTimeoutMs - (millis() - openTime)) / 1000);
                }
                vTaskDelay(pdMS_TO_TICKS(20));
            }

            // Phase 2: If a bottle was detected, WAIT for the bottle and hand to FULLY PASS through the entrance!
            // Do NOT slam the gate shut immediately on the bottle body or user's fingers!
            if (dropped && !wasForced) {
                logDebug("AIRLOCK", "Bottle detected at gate. Waiting for bottle/hand to fully clear entrance doorway...");
                unsigned long passageStart = millis();
                const unsigned long PASSAGE_TIMEOUT_MS = 8000; // Up to 8 seconds for insertion
                const unsigned long CLEAR_STABLE_MS = 500;     // Beam must be clear continuously for 500ms
                unsigned long clearStartTime = 0;
                bool passageCompleted = false;

                while (millis() - passageStart < PASSAGE_TIMEOUT_MS) {
                    if (forceGateClose) {
                        wasForced = true;
                        logDebug("AIRLOCK", "Force gate close during passage wait.");
                        break;
                    }

                    int rawTopIr = digitalRead(PIN_IR_TOP);
                    if (rawTopIr == HIGH) {
                        // Doorway beam is unbroken
                        if (clearStartTime == 0) {
                            clearStartTime = millis();
                        } else if (millis() - clearStartTime >= CLEAR_STABLE_MS) {
                            passageCompleted = true;
                            logDebug("AIRLOCK", "Entrance doorway clear & stable for %lu ms. Bottle safely in cradle.", CLEAR_STABLE_MS);
                            break;
                        }
                    } else {
                        // Object or hand still passing through entrance doorway
                        clearStartTime = 0;
                    }
                    vTaskDelay(pdMS_TO_TICKS(30));
                }

                if (!passageCompleted && !wasForced) {
                    logWarn("AIRLOCK", "Passage wait reached timeout (%lu ms). Closing gate safely.", PASSAGE_TIMEOUT_MS);
                    buzz(150, 1); // Short audible warning before closing
                    vTaskDelay(pdMS_TO_TICKS(250));
                }
            }

            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle); // Close entrance safely!
            gateStateEvent(false);
            forceGateClose = false;
            logDebug("AIRLOCK", "Entrance gate CLOSED (Ch 0 -> %d deg). Dropped=%d, WasForced=%d",
                     config.ent_close_angle, dropped, wasForced);
            
            if (!dropped) {
                if (!wasForced) {
                    logDebug("AIRLOCK", "Intake timeout reached (%u ms). No bottle inserted.", gateTimeoutMs);
                    EventMsg timeoutMsg = MSG_GATE_TIMEOUT;
                    postEvent(timeoutMsg);
                }

                // Tare to 0 as well when inserting is done and entrance gate closed safely
                vTaskDelay(pdMS_TO_TICKS(150)); // Allow entrance servo physical travel to settle
                if (hx711Found && scale.wait_ready_timeout(200)) {
                    scale.tare(3);
                    lastMeasuredWeightG = 0.0f;
                    logDebug("SCALE", "Scale tared to 0 after inserting finished / gate closed. Offset=%ld", scale.get_offset());
                }

                depositCycleBusy = false;
                continue;
            }
            
            logDebug("AIRLOCK", "Bottle in airlock chamber. Settling for %d ms...", config.settle_time_ms);
            vTaskDelay(pdMS_TO_TICKS(config.settle_time_ms)); // Settle in airlock

            // 3. Multi-Sensor Material Classification
            logDebug("SENSOR", "--- Starting Multi-Sensor Material Classification ---");
            bool isValid = true;
            EventMsg rejectReason = MSG_REJECT_NON_PLASTIC;
            const char* rejectReasonCode = "invalid_material";
            const char* rejectReasonDesc = "Invalid Item";
            float lastNirAbsorption = 0.0f;

            int metalReading = digitalRead(PIN_PROX_METAL);
            logDebug("SENSOR", "Proximity: Metal(GPIO%d)=%s (raw=%d)",
                     PIN_PROX_METAL, metalReading == LOW ? "TRIGGERED (METAL)" : "CLEAR (NO METAL)", metalReading);

            if (metalReading == LOW) {
                isValid = false;
                rejectReason = MSG_REJECT_TIN;
                rejectReasonCode = "metal_detected";
                rejectReasonDesc = "Tin Can Detected";
                logWarn("DECISION", "REJECT: %s", rejectReasonDesc);
            } 
            // 3b. HX711 Mass & Weight Discrimination (Sampled Unconditionally)
            float weightG = 0.0f;
            if (hx711Found) {
                weightG = scale.get_units(5);
                lastMeasuredWeightG = weightG;
                logDebug("WEIGHT", "Measured Bottle Weight: %.1f g (Valid Range: [%d - %d g])",
                         weightG, config.min_bottle_weight_g, config.max_bottle_weight_g);

                if (isValid && config.require_weight_sensor) {
                    if (weightG > config.max_bottle_weight_g) {
                        isValid = false;
                        rejectReason = MSG_REJECT_NON_PLASTIC;
                        rejectReasonCode = "overweight";
                        rejectReasonDesc = "Overweight Object (Glass / Heavy Item)";
                        logWarn("DECISION", "REJECT WEIGHT: %s (Weight: %.1f g > %d g)",
                                rejectReasonDesc, weightG, config.max_bottle_weight_g);
                    } else if (config.min_bottle_weight_g > 0 && weightG < (float)config.min_bottle_weight_g) {
                        isValid = false;
                        rejectReason = MSG_REJECT_NON_PLASTIC;
                        rejectReasonCode = "underweight";
                        rejectReasonDesc = "Underweight Object";
                        logWarn("DECISION", "REJECT WEIGHT: %s (Weight: %.1f g < %d g)",
                                rejectReasonDesc, weightG, config.min_bottle_weight_g);
                    } else {
                        logDebug("WEIGHT", "Bottle weight within authentic bounds: %.1f g ([%d - %d g])",
                                 weightG, config.min_bottle_weight_g, config.max_bottle_weight_g);
                    }
                }
            } else if (isValid && config.require_weight_sensor) {
                isValid = false;
                rejectReason = MSG_REJECT_NON_PLASTIC;
                rejectReasonCode = "scale_offline";
                rejectReasonDesc = "Weight Sensor Offline";
                logWarn("DECISION", "REJECT: %s", rejectReasonDesc);
            }

            // 3c. AS7263 NIR Optical Spectroscopy (Evaluated on Valid-Weight Items)
            if (isValid) {
                if (spectrometerFound) {
                    logDebug("NIR", "Triggering AS7263 NIR spectrometer measurements with illumination bulb...");
                    spectrometer.enableBulb();
                    delay(40);
                    spectrometer.takeMeasurements();
                    spectrometer.disableBulb();
                    float nirAbsorption = spectrometer.getCalibratedW();
                    lastNirAbsorption = nirAbsorption;
                    int r = spectrometer.getR();
                    int s = spectrometer.getS();
                    int t = spectrometer.getT();
                    int u = spectrometer.getU();
                    int v = spectrometer.getV();
                    int w = spectrometer.getW();
                    int tempC = spectrometer.getTemperature();

                    logDebug("NIR", "Spectral Channels: R=%d, S=%d, T=%d, U=%d, V=%d, W=%d | Temp=%d C",
                             r, s, t, u, v, w, tempC);
                    logDebug("NIR", "Calibrated W Channel: %.2f (PET Range: [%d - %d])",
                             nirAbsorption, config.pet_nir_w_min, config.pet_nir_w_max);

                    NirEvaluation eval = evaluateNirSpectrum(r, s, t, u, v, w, nirAbsorption, config.pet_nir_w_min, config.pet_nir_w_max);
                    logDebug("NIR", "Ratios: W/V=%.2f, S/R=%.2f, T/W=%.2f | Cal-W: %.2f | Verdict: %s (%s)",
                             eval.wv_ratio, eval.sr_ratio, eval.tw_ratio, nirAbsorption,
                             eval.is_pet ? "ACCEPT" : "REJECT", eval.reason_desc);

                    if (config.require_nir_sensor) {
                        if (!eval.is_pet) {
                            isValid = false;
                            rejectReason = MSG_REJECT_NIR;
                            rejectReasonCode = eval.reason_code;
                            rejectReasonDesc = eval.reason_desc;
                            logWarn("DECISION", "REJECT NIR: %s (code: %s, W/V: %.2f, S/R: %.2f)",
                                    eval.reason_desc, eval.reason_code, eval.wv_ratio, eval.sr_ratio);
                        } else {
                            logDebug("NIR", "NIR Multi-Spectral Match: PET Plastic Confirmed!");
                        }
                    }
                } else if (config.require_nir_sensor) {
                    isValid = false;
                    rejectReason = MSG_REJECT_NIR;
                    rejectReasonCode = "spectrometer_offline";
                    rejectReasonDesc = "Spectrometer Offline";
                    logWarn("DECISION", "REJECT: %s", rejectReasonDesc);
                }
            }

            // 4. Actuation
            if (isValid) {
                logDebug("DECISION", ">>> BOTTLE ACCEPTED AS VALID PET PLASTIC <<<");
                EventMsg startMsg = MSG_VALIDATE_START;
                postEvent(startMsg);
                
                bottomIrTriggered = false;
                if (!prepareDrop()) {
                    logError("JOURNAL", "prepareDrop failed! Storage unavailable or invalid journal state.");
                    depositCycleBusy = false;
                    postEvent(MSG_DROP_TIMEOUT);
                    continue;
                }

                logDebug("ACTUATION", "Opening success flap (Ch 1 -> %d deg). Awaiting drop transit & clear (tout=%d ms)...",
                         config.suc_open_angle, config.success_drop_tout_ms);
                setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_open_angle);

                unsigned long gateOpenTime = millis();
                bool passedDrop = false;
                bool bottleSeenInChute = false;
                unsigned long clearHoldStart = 0;
                const unsigned long CLEAR_HOLD_MS = 400; // Hold flap open for a moment after sensor clears

                while (millis() - gateOpenTime < static_cast<uint32_t>(config.success_drop_tout_ms)) {
                    // Check if object is currently breaking bottom sensor beam (LOW = obstacle detected)
                    bool sensorActive = (digitalRead(PIN_IR_BOTTOM) == LOW) || bottomIrTriggered;

                    if (sensorActive) {
                        bottleSeenInChute = true;
                        bottomIrTriggered = false; // consume ISR latch
                        clearHoldStart = 0;        // reset clear timer while bottle is still passing through
                    } else if (bottleSeenInChute) {
                        // Bottle was detected and has now cleared the sensor (beam HIGH / unobstructed)
                        if (clearHoldStart == 0) {
                            clearHoldStart = millis();
                            logDebug("ACTUATION", "Drop sensor cleared at %lu ms. Holding flap open for %u ms to clear sweep...",
                                     millis() - gateOpenTime, CLEAR_HOLD_MS);
                        } else if (millis() - clearHoldStart >= CLEAR_HOLD_MS) {
                            passedDrop = true;
                            logDebug("ACTUATION", "Drop transit complete! Bottle cleared into storage bin in %lu ms.",
                                     millis() - gateOpenTime);
                            break;
                        }
                    }
                    vTaskDelay(pdMS_TO_TICKS(20));
                }

                // Drop flap closes ONLY after object is no longer detected + hold moment, or on watchdog timeout
                setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
                logDebug("ACTUATION", "Closed success flap (Ch 1 -> %d deg). Drop Passed=%d",
                         config.suc_close_angle, passedDrop);

                if (passedDrop) {
                    completeDrop(true);
                    EventMsg okMsg = MSG_BOTTLE_SAVED;
                    postEvent(okMsg);
                    logDebug("CYCLE", "Deposit cycle successfully completed. Session bottles: %d",
                             currentSessionBottles.load());

                    // Tare to 0 as well after bottle has dropped into storage bin and cradle is empty
                    vTaskDelay(pdMS_TO_TICKS(100));
                    if (hx711Found && scale.wait_ready_timeout(200)) {
                        scale.tare(3);
                        lastMeasuredWeightG = 0.0f;
                        logDebug("SCALE", "Scale tared to 0 after bottle drop into bin. Offset=%ld", scale.get_offset());
                    }
                } else {
                    logWarn("ACTUATION", "Drop TIMEOUT! Bottom IR was not cleared/triggered within %d ms. Chute jam possible!",
                            config.success_drop_tout_ms);
                    completeDrop(false);
                    EventMsg failMsg = MSG_DROP_TIMEOUT; // Blocked in chute
                    postEvent(failMsg);
                }
            } else {
                // Reject Sequence: Manual Chute Retrieval
                logWarn("DECISION", ">>> BOTTLE REJECTED: %s (%s) <<<", rejectReasonCode, rejectReasonDesc);
                
                char curSession[37] = {};
                xSemaphoreTake(creditMutex, portMAX_DELAY);
                strlcpy(curSession, creditJournal.session, sizeof(curSession));
                xSemaphoreGive(creditMutex);

                // 1. Alert indicators: Red LED ON, buzzer alert
                digitalWrite(PIN_LED_RED, HIGH);
                digitalWrite(PIN_LED_GREEN, LOW);
                buzz(600, 1);

                // 2. LCD update
                xSemaphoreTake(uiMutex, portMAX_DELAY);
                lcd.setCursor(0, 0); lcd.print("=== VMC ECO-VENDO ==");
                lcd.setCursor(0, 1); lcd.print("STATUS: REJECTED!   ");
                char lineBuf[21];
                snprintf(lineBuf, sizeof(lineBuf), "%-20s", rejectReasonDesc);
                lcd.setCursor(0, 2); lcd.print(lineBuf);
                lcd.setCursor(0, 3); lcd.print("Please Remove Item  ");
                xSemaphoreGive(uiMutex);

                // 3. Emit structured REJECTED event to host gateway
                JsonDocument rejDoc;
                rejDoc["event"] = "REJECTED";
                rejDoc["session_id"] = curSession;
                rejDoc["protocol"] = 2;
                rejDoc["reason"] = rejectReasonCode;
                rejDoc["desc"] = rejectReasonDesc;
                String rejOutput;
                serializeJson(rejDoc, rejOutput);
                emitSerialLine(rejOutput);

                // 4. Ensure entrance gate is OPEN so user can reach into cradle to retrieve item
                setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_open_angle);
                gateStateEvent(true);

                // 5. Await manual removal within retrieval_timeout_s
                const uint32_t retrievalTimeoutMs = static_cast<uint32_t>(config.retrieval_timeout_s) * 1000UL;
                unsigned long startWait = millis();

                // Chute with invalid object: mark current weight, spectrum, and metal state as reference
                float initialWeightG = 0.0f;
                if (hx711Found) {
                    initialWeightG = scale.is_ready() ? scale.get_units(3) : lastMeasuredWeightG;
                    if (lastMeasuredWeightG > initialWeightG) {
                        initialWeightG = lastMeasuredWeightG;
                    }
                }
                float initialNirW = lastNirAbsorption;
                int initialMetal = metalReading;

                logDebug("RETRIEVAL", "Baseline with invalid object: Wt=%.1f g, NIR Cal-W=%.2f, Metal=%d. Waiting up to %u ms...",
                         initialWeightG, initialNirW, initialMetal, retrievalTimeoutMs);

                bool passageDetected = false;
                unsigned long passageClearStart = 0;
                const unsigned long PASSAGE_HOLD_MS = 400; // Entrance beam must be clear for 400ms after passage
                bool itemCleared = false;
                unsigned long lastAnalysisTime = 0;

                while (millis() - startWait < retrievalTimeoutMs) {
                    if (forceGateClose) {
                        logDebug("RETRIEVAL", "Force gate close detected during retrieval wait.");
                        break;
                    }

                    int topIrVal = digitalRead(PIN_IR_TOP);
                    int metalVal = digitalRead(PIN_PROX_METAL);

                    // 1. Entrance PIR / IR detection of hand & object passage
                    if (topIrVal == LOW || topIrTriggered) {
                        passageDetected = true;
                        topIrTriggered = false;
                        passageClearStart = 0; // Hand/object currently passing through doorway
                    } else if (passageDetected) {
                        // Hand/object has cleared doorway; start debounce moment
                        if (passageClearStart == 0) {
                            passageClearStart = millis();
                        }
                    }

                    // 2. Once passage is confirmed after a moment, analyze current weight vs previous, and trigger NIR
                    bool passageMomentElapsed = passageDetected && (passageClearStart > 0) && (millis() - passageClearStart >= PASSAGE_HOLD_MS);

                    if (passageMomentElapsed && (millis() - lastAnalysisTime >= 250)) {
                        lastAnalysisTime = millis();

                        // Analyze current weight vs previous
                        float currWeight = 0.0f;
                        if (hx711Found && scale.wait_ready_timeout(100)) {
                            currWeight = scale.get_units(3);
                            lastMeasuredWeightG = currWeight;
                        }
                        float weightDiff = initialWeightG - currWeight; // prev - current

                        // Weight condition: close to zero, or negative, or significantly large positive difference (weight dropped)
                        bool weightIndicatesEmpty = (currWeight <= 5.0f) ||
                                                    (weightDiff >= 8.0f) ||
                                                    (initialWeightG > 10.0f && currWeight <= initialWeightG * 0.35f);

                        // Trigger NIR and compare previous vs current
                        float currNirW = 0.0f;
                        if (spectrometerFound) {
                            spectrometer.enableBulb();
                            delay(40); // 40ms incandescent bulb warmup
                            spectrometer.takeMeasurements();
                            spectrometer.disableBulb();
                            currNirW = spectrometer.getCalibratedW();
                        }

                        // Empty chute baseline is tightly bounded in [18.0 - 30.0] uW/cm^2
                        bool nirIndicatesEmpty = spectrometerFound ? (currNirW >= 18.0f && currNirW <= 30.0f) : true;
                        bool nirDiffIndicatesEmpty = spectrometerFound ? (fabs(initialNirW - currNirW) >= 10.0f && currNirW <= 32.0f) : false;

                        // Inductive metal sensor must also be clear
                        bool metalCleared = (metalVal == HIGH);

                        logDebug("RETRIEVAL", "Analysis: PrevWt=%.1f, CurrWt=%.1f (diff=%.1f, ok=%d) | PrevNIR=%.2f, CurrNIR=%.2f (empty=%d) | Metal=%s",
                                 initialWeightG, currWeight, weightDiff, weightIndicatesEmpty,
                                 initialNirW, currNirW, (nirIndicatesEmpty || nirDiffIndicatesEmpty),
                                 metalCleared ? "CLEAR" : "DETECTED");

                        // OR LOGIC as specified: weight indicates empty OR NIR confirms empty chute -> retrieval success!
                        if ((weightIndicatesEmpty || nirIndicatesEmpty || nirDiffIndicatesEmpty) && metalCleared) {
                            itemCleared = true;
                            logDebug("RETRIEVAL", ">>> RETRIEVAL CONFIRMED! Item cleared successfully (WeightEmpty=%d, NirEmpty=%d) <<<",
                                     weightIndicatesEmpty, (nirIndicatesEmpty || nirDiffIndicatesEmpty));
                            break;
                        }
                    }

                    vTaskDelay(pdMS_TO_TICKS(20));
                }

                digitalWrite(PIN_LED_RED, LOW);

                if (itemCleared && !forceGateClose) {
                    // Item retrieved successfully!
                    logDebug("RETRIEVAL", "Item retrieved by user. Emitting ITEM_CLEARED.");
                    buzz(100, 1); // Pleasant confirmation beep

                    xSemaphoreTake(uiMutex, portMAX_DELAY);
                    lcd.setCursor(0, 0); lcd.print("=== VMC ECO-VENDO ==");
                    lcd.setCursor(0, 1); lcd.print("STATUS: ITEM REMOVED");
                    lcd.setCursor(0, 2); lcd.print("Slot Cleared! Ready ");
                    lcd.setCursor(0, 3); lcd.print("Insert Valid Bottle ");
                    xSemaphoreGive(uiMutex);

                    JsonDocument clrDoc;
                    clrDoc["event"] = "ITEM_CLEARED";
                    clrDoc["session_id"] = curSession;
                    clrDoc["protocol"] = 2;
                    String clrOutput;
                    serializeJson(clrDoc, clrOutput);
                    emitSerialLine(clrOutput);

                    // Re-arm entrance gate for immediate next bottle insert
                    entranceGateRequested = true;
                } else {
                    // Retrieval Timeout or Force Gate Close
                    logWarn("RETRIEVAL", "Retrieval TIMEOUT (%u s elapsed) or forced. Securing entrance gate.", config.retrieval_timeout_s);
                    buzz(600, 1); // Error buzzer

                    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
                    gateStateEvent(false);

                    xSemaphoreTake(uiMutex, portMAX_DELAY);
                    lcd.setCursor(0, 0); lcd.print("=== VMC ECO-VENDO ==");
                    lcd.setCursor(0, 1); lcd.print("STATUS: TIMEOUT     ");
                    lcd.setCursor(0, 2); lcd.print("Item Not Retrieved  ");
                    lcd.setCursor(0, 3); lcd.print("Session on Hold     ");
                    xSemaphoreGive(uiMutex);

                    JsonDocument toutDoc;
                    toutDoc["event"] = "RETRIEVAL_TIMEOUT";
                    toutDoc["session_id"] = curSession;
                    toutDoc["protocol"] = 2;
                    String toutOutput;
                    serializeJson(toutDoc, toutOutput);
                    emitSerialLine(toutOutput);
                }
            }
            
            depositCycleBusy = false;
            logDebug("CYCLE", "Deposit cycle finished. Airlock resting for 800 ms...");
            vTaskDelay(pdMS_TO_TICKS(800));
        }
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));
    }
}

// -----------------------------------------------------------------------------
// USER INTERFACE & DISPLAY FEEDBACK TASK (CORE 1)
// -----------------------------------------------------------------------------
void commTaskCode(void* parameter) {
    (void)parameter;
    logDebug("COMM", "CommTask started on Core %d", xPortGetCoreID());
    QueuedEvent queued;

    while (true) {
        if (xQueueReceive(eventQueue, &queued, pdMS_TO_TICKS(50)) == pdTRUE) {
            xSemaphoreTake(uiMutex, portMAX_DELAY);
            EventMsg msg = queued.kind;
            logDebug("COMM", "Handling UI event: %s (Session: '%s')", getEventMsgName(msg), queued.session);
            switch(msg) {
                case MSG_BIN_FULL:
                    lcd.setCursor(0, 1); lcd.print("STATUS: STORAGE FULL");
                    lcd.setCursor(0, 2); lcd.print("Empty Bin Required  ");
                    digitalWrite(PIN_LED_RED, HIGH);
                    logDebug("COMM", "UI -> Storage Full alert. Red LED ON.");
                    emitSerialLine("{\"event\":\"BIN_FULL\"}");
                    break;
                
                case MSG_BIN_OK:
                    emitSerialLine("{\"event\":\"BIN_OK\"}");
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    digitalWrite(PIN_LED_RED, LOW);
                    logDebug("COMM", "UI -> Bin OK. Red LED OFF.");
                    break;

                case MSG_REJECT_TIN:
                case MSG_REJECT_NON_PLASTIC:
                case MSG_REJECT_NIR:
                    digitalWrite(PIN_LED_RED, HIGH);
                    digitalWrite(PIN_LED_GREEN, LOW);
                    lcd.setCursor(0, 1); lcd.print("STATUS: REJECTED!   ");
                    lcd.setCursor(0, 2); 
                    if (msg == MSG_REJECT_TIN) lcd.print("Tin/Can Detected    ");
                    else if (msg == MSG_REJECT_NIR) lcd.print("Invalid Material NIR");
                    else lcd.print("No Plastic Detected ");
                    scopedEvent("REJECTED", queued.session);
                    logDebug("COMM", "UI -> Rejection displayed: %s. Buzzing...", getEventMsgName(msg));
                    buzz(600, 1);
                    digitalWrite(PIN_LED_RED, LOW);
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    break;
                
                case MSG_VALIDATE_START:
                    digitalWrite(PIN_LED_GREEN, HIGH);
                    lcd.setCursor(0, 1); lcd.print("STATUS: VERIFIED OK ");
                    lcd.setCursor(0, 2); lcd.print("Dropping to bin...  ");
                    logDebug("COMM", "UI -> Verified OK. Green LED ON.");
                    break;

                case MSG_BOTTLE_SAVED:
                    logDebug("COMM", "UI -> Bottle saved! Emitting 2x chimes. Session Total: %d",
                             currentSessionBottles.load());
                    buzz(120, 2);
                    // Receipt emission/retry is independent of the display queue.
                    lcd.setCursor(0, 1); lcd.print("STATUS: BOTTLE SAVED");
                    lcd.setCursor(0, 3); lcd.print("Session Bottles: ");
                    lcd.print(currentSessionBottles.load());
                    lcd.print("  ");
                    vTaskDelay(pdMS_TO_TICKS(1200));
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    digitalWrite(PIN_LED_GREEN, LOW);
                    break;
                
                case MSG_DROP_TIMEOUT:
                    digitalWrite(PIN_LED_RED, HIGH);
                    digitalWrite(PIN_LED_GREEN, LOW);
                    lcd.setCursor(0, 1); lcd.print("STATUS: ERROR       ");
                    lcd.setCursor(0, 2); lcd.print("Drop / Sensor Error ");
                    scopedEvent("REJECTED", queued.session);
                    logWarn("COMM", "UI -> Chute drop error / timeout displayed.");
                    buzz(600, 1);
                    digitalWrite(PIN_LED_RED, LOW);
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    break;

                case MSG_GATE_TIMEOUT:
                    digitalWrite(PIN_LED_GREEN, LOW);
                    digitalWrite(PIN_LED_RED, LOW);
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    logDebug("COMM", "UI -> Gate intake timeout displayed.");
                    scopedEvent("TIMEOUT", queued.session);
                    break;
            }
            xSemaphoreGive(uiMutex);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

// -----------------------------------------------------------------------------
// SETUP (SYSTEM INITIALIZATION)
// -----------------------------------------------------------------------------
void setup() {
    Serial.begin(115200);
    serialMutex = xSemaphoreCreateMutex();

    // Decommission radio hardware completely to maximize 2-core real-time execution,
    // eliminate interrupt jitter, reduce power, and prevent analog sensor EMI
    esp_bt_controller_disable();
    esp_wifi_stop();
    esp_wifi_deinit();

    logDebug("BOOT", "==================================================");
    logDebug("BOOT", "       VMC ECO-VENDO Reverse Vending Machine ESP32       ");
    logDebug("BOOT", "       Firmware Version: %s", ECOVENDO_VERSION);
    logDebug("BOOT", "==================================================");
    logDebug("BOOT", "ESP32 Chip Model: %s, Rev %d, Cores: %d, CPU Freq: %d MHz",
             ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(), ESP.getCpuFreqMHz());
    logDebug("BOOT", "Flash Size: %u KB, Free Heap: %u bytes",
             ESP.getFlashChipSize() / 1024, ESP.getFreeHeap());
    logDebug("BOOT", "eFuse MAC Address: %012llX", (unsigned long long)ESP.getEfuseMac());

    logDebug("GPIO", "Configuring sensor and indicator GPIO pins...");
    pinMode(PIN_IR_TOP, INPUT_PULLUP);
    pinMode(PIN_IR_BOTTOM, INPUT_PULLUP);
    pinMode(PIN_PROX_METAL, INPUT_PULLUP);
    // GPIO 15 unused (capacitive sensor omitted)
    pinMode(PIN_FINISH_BTN, INPUT_PULLUP); // GPIO19: internal pull-up, no external resistor needed
    pinMode(PIN_ULTRASONIC_TRIG, OUTPUT);
    pinMode(PIN_ULTRASONIC_ECHO, INPUT);
    pinMode(PIN_BUZZER, OUTPUT);
    pinMode(PIN_LED_GREEN, OUTPUT);
    pinMode(PIN_LED_RED, OUTPUT);

    logDebug("GPIO", "Attaching interrupts: Top IR (GPIO %d), Bottom IR (GPIO %d)", PIN_IR_TOP, PIN_IR_BOTTOM);
    attachInterrupt(digitalPinToInterrupt(PIN_IR_TOP), isrTopIr, FALLING);
    attachInterrupt(digitalPinToInterrupt(PIN_IR_BOTTOM), isrBottomIr, FALLING);

    logDebug("I2C", "Starting I2C bus on SDA=GPIO 21, SCL=GPIO 22...");
    Wire.begin(21, 22);

    logDebug("I2C", "Probing PCA9685 PWM Servo Driver at 0x%02X...", PCA9685_I2C_ADDR);
    pca9685Found = pwm.begin();
    if (pca9685Found) {
        pwm.setPWMFreq(50);
        logDebug("I2C", "PCA9685 PWM Driver initialized OK (Frequency: 50 Hz)");
    } else {
        logError("I2C", "PCA9685 PWM Driver NOT FOUND at 0x%02X! Check I2C bus wiring.", PCA9685_I2C_ADDR);
    }

    logDebug("I2C", "Initializing LCD (0x27)...");
    lcd.init();
    lcd.backlight();

    logDebug("I2C", "Probing AS7263 NIR Spectrometer...");
    if (spectrometer.begin() == false) {
        logError("I2C", "AS7263 NIR Spectrometer missing or failed to initialize!");
        spectrometerFound = false;
    } else {
        spectrometerFound = true;
        spectrometer.setBulbCurrent(2);       // 50 mA current drive for optimal reflection
        spectrometer.setGain(3);              // 64x gain
        spectrometer.setIntegrationTime(50);   // 50 * 2.8ms = 140ms integration
        spectrometer.disableBulb();           // Keep bulb OFF while idle to prevent thermal drift
        spectrometer.disableIndicator();      // Keep red indicator LED OFF
        logDebug("I2C", "AS7263 NIR Spectrometer detected OK (Gain 64x, 140ms, 50mA)! Sensor Temp: %d C", spectrometer.getTemperature());
    }

    loadPreferences();
    if (!validMachineConfig(config)) {
        logWarn("NVS", "Loaded configuration invalid; reverting to defaults.");
        config = MachineConfig{};
    }
    desiredConfig = config;
    requestedGateTimeout = config.entrance_gate_timeout;

    logDebug("SCALE", "Probing HX711 Load Cell on DOUT=GPIO %d, SCK=GPIO %d...", PIN_HX711_DOUT, PIN_HX711_SCK);
    scale.begin(PIN_HX711_DOUT, PIN_HX711_SCK);
    if (scale.wait_ready_timeout(200)) {
        hx711Found = true;
        scale.set_scale(config.weight_cal_factor > 0 ? (float)config.weight_cal_factor : 420.0f);
        scale.tare();
        logDebug("SCALE", "HX711 Load Cell detected and tared successfully!");
    } else {
        hx711Found = false;
        logDebug("SCALE", "HX711 Load Cell not detected (running in NIR-only mode).");
    }

    char bootBuf[256];
    snprintf(bootBuf, sizeof(bootBuf),
             "{\"event\":\"BOOT\",\"protocol\":2,\"firmware_version\":\"%s\",\"pca9685_ready\":%s,\"spectrometer_ready\":%s,\"hx711_ready\":%s,\"cfg_ts\":%lu}",
             ECOVENDO_VERSION,
             pca9685Found ? "true" : "false",
             spectrometerFound ? "true" : "false",
             hx711Found ? "true" : "false",
             config.config_timestamp);
    emitSerialLine(bootBuf);

    logDebug("SERVO", "Aligning entrance and success servos to initial closed angles...");
    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
    setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);

    logDebug("NVS", "Initializing credit journal storage...");
    creditMutex = xSemaphoreCreateMutex();
    if (creditMutex && creditStore.begin("ecovendo-cred", false)) {
        size_t length = creditStore.getBytesLength("receipt");
        logDebug("NVS", "Credit store blob length: %u bytes (expected: %u)", length, sizeof(creditJournal));
        if (length == 0) {
            creditStorageOk = saveCreditJournal();
            logDebug("NVS", "Initialized new credit journal. Storage OK=%d", creditStorageOk.load());
        } else if (length == sizeof(creditJournal)) {
            creditStore.getBytes("receipt", &creditJournal, sizeof(creditJournal));
            creditStorageOk = creditJournal.version == 2 && creditJournal.phase <= 3 && creditJournal.session[36] == 0;
            logDebug("NVS", "Loaded existing journal: ver=%u, seq=%llu, total=%u, phase=%d, session='%s', valid=%d",
                     creditJournal.version, (unsigned long long)creditJournal.sequence,
                     creditJournal.total, creditJournal.phase, creditJournal.session, creditStorageOk.load());
        }
        currentSessionBottles = creditJournal.total;
    }
    if (!creditMutex || !creditStorageOk) {
        logError("NVS", "CRITICAL: Credit storage unavailable or corrupted! Halting to preserve data.");
        emitSerialLine("{\"event\":\"STORAGE_ERROR\"}");
        // Never replace an unreadable journal with an empty one.
        while (true) delay(1000);
    }



    // Normal Vending Setup
    logDebug("BOOT", ">>> STARTING NORMAL VENDING MODE <<<");
    lcd.setCursor(0, 0); lcd.print("=== VMC ECO-VENDO ==");
    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
    lcd.setCursor(0, 3); lcd.print("Session Bottles: 0  ");

    eventQueue = xQueueCreate(10, sizeof(QueuedEvent));
    configQueue = xQueueCreate(1, sizeof(MachineConfig));
    uiMutex = xSemaphoreCreateMutex();

    BaseType_t commCreated = xTaskCreatePinnedToCore(commTaskCode, "CommTask", 6144, NULL, 1, NULL, 1);
    BaseType_t sensorCreated = xTaskCreatePinnedToCore(sensorTaskCode, "SensorTask", 6144, NULL, 2, &sensorTaskHandle, 0);

    logDebug("BOOT", "CommTask (Core 1): %s", commCreated == pdPASS ? "OK" : "FAILED");
    logDebug("BOOT", "SensorTask (Core 0): %s", sensorCreated == pdPASS ? "OK" : "FAILED");

    if (!eventQueue || !configQueue || !uiMutex || commCreated != pdPASS || sensorCreated != pdPASS) {
        logError("BOOT", "CRITICAL: Failed to create FreeRTOS queues or worker tasks!");
        emitSerialLine("{\"event\":\"STARTUP_ERROR\"}");
        while (true) delay(1000);
    }

    logDebug("BOOT", "VMC ECO-VENDO ESP32 initialization COMPLETE. Ready for sessions.");
}

// -----------------------------------------------------------------------------
// MAIN LOOP: HOST UART PROTOCOL & FINISH BUTTON (CORE 1)
// -----------------------------------------------------------------------------
void loop() {

    static char serialLine[1024];
    static size_t serialLength = 0;
    static bool serialOverflow = false;
    bool messageReady = false;
    for (int budget = 0; budget < 256 && Serial.available(); ++budget) {
        char ch = Serial.read();
        if (ch == '\n') {
            if (!serialOverflow) {
                serialLine[serialLength] = 0;
                messageReady = true;
            } else {
                logWarn("UART", "Serial RX buffer overflow (>1024 bytes) discarded.");
            }
            serialLength = 0;
            serialOverflow = false;
            break;
        }
        if (ch != '\r' && !serialOverflow) {
            if (serialLength < sizeof(serialLine) - 1) serialLine[serialLength++] = ch;
            else serialOverflow = true;
        }
    }
    if (messageReady) {
        String msg(serialLine);
        logDebug("UART-RX", "Frame: %s", msg.c_str());
        JsonDocument command;
        DeserializationError err = deserializeJson(command, msg);
        if (err) {
            logWarn("UART", "JSON deserialize error: %s (Raw: '%s')", err.c_str(), msg.c_str());
            command.clear();
        }
        const char* cmd = command["cmd"] | "";
        const char* sid = command["session_id"] | "";
        if (strcmp(cmd, "OPEN_GATE") == 0) {
            int timeout = command["timeout"] | desiredConfig.entrance_gate_timeout;
            xSemaphoreTake(creditMutex, portMAX_DELAY);
            bool same = strcmp(sid, creditJournal.session) == 0;
            bool phaseOk = (creditJournal.phase == 0) || (same && creditJournal.phase == 2);
            logDebug("CMD", "OPEN_GATE received. session='%s' (same=%d, phaseOk=%d), timeout=%d, phase=%d, busy=%d",
                     sid, same, phaseOk, timeout, creditJournal.phase, depositCycleBusy.load());
            if (command["protocol"] == 2 && strlen(sid) > 0 && strlen(sid) <= 36 &&
                creditStorageOk && phaseOk && (!depositCycleBusy || same) &&
                !finishRequested && timeout >= 1 && timeout <= 600) {
                if (creditJournal.phase == 2) {
                    creditJournal.phase = 0;
                    saveCreditJournal();
                }
                if (!same) {
                    logDebug("CMD", "New session '%s' replacing previous '%s'. Resetting session bottle count.",
                             sid, creditJournal.session);
                    strlcpy(creditJournal.session, sid, sizeof(creditJournal.session));
                    creditJournal.total = 0;
                    currentSessionBottles = 0;
                    saveCreditJournal();
                }
                if (creditStorageOk) {
                    if (!depositCycleBusy) forceGateClose = false;
                    requestedGateTimeout = timeout;
                    entranceGateRequested = true;
                    if (sensorTaskHandle) xTaskNotifyGive(sensorTaskHandle);
                    logDebug("CMD", "Entrance gate request queued successfully (timeout=%d s).", timeout);
                }
            } else {
                logWarn("CMD", "OPEN_GATE rejected! Check session='%s', protocol=%d, phase=%d, storageOk=%d, busy=%d",
                        sid, (int)command["protocol"], creditJournal.phase, creditStorageOk.load(), depositCycleBusy.load());
            }
            xSemaphoreGive(creditMutex);
        } else if (strcmp(cmd, "CREDIT_ACK") == 0) {
            xSemaphoreTake(creditMutex, portMAX_DELAY);
            const char* id = command["event_id"] | "";
            String curId = receiptId();
            logDebug("CMD", "CREDIT_ACK received for event_id='%s', session='%s' (Current: ID='%s', Phase=%d)",
                     id, sid, curId.c_str(), creditJournal.phase);
            if (command["protocol"] == 2 && creditJournal.phase != 0 &&
                strcmp(sid, creditJournal.session) == 0 && curId == id &&
                !(creditJournal.phase == 1 && depositCycleBusy)) {
                uint8_t prevPhase = creditJournal.phase;
                creditJournal.phase = 0;
                if (!saveCreditJournal()) {
                    creditJournal.phase = prevPhase;
                    logError("CMD", "Failed to save credit journal after ACK!");
                } else {
                    logDebug("CMD", "Credit receipt acknowledged! Phase cleared (was %d -> 0). Ready for next drop.", prevPhase);
                }
            } else {
                logWarn("CMD", "CREDIT_ACK ignored/mismatch (CurID='%s' vs ReqID='%s')", curId.c_str(), id);
            }
            xSemaphoreGive(creditMutex);
        } else if (strcmp(cmd, "CLOSE_GATE") == 0) {
            xSemaphoreTake(creditMutex, portMAX_DELAY);
            logDebug("CMD", "CLOSE_GATE received for session='%s'", sid);
            if (sid[0] == '\0' || strcmp(sid, creditJournal.session) == 0) {
                entranceGateRequested = false;
                forceGateClose = true;
                logDebug("CMD", "Forced gate close flag set.");
            }
            if (pca9685Found && !depositCycleBusy) {
                setServoAngle(PCA_CHANNEL_ENTRANCE, desiredConfig.ent_close_angle);
                setServoAngle(PCA_CHANNEL_SUCCESS, desiredConfig.suc_close_angle);
                gateStateEvent(false);
            }
            xSemaphoreGive(creditMutex);
        } else if (strcmp(cmd, "SET_AP") == 0 || strcmp(cmd, "TRIGGER_CONFIG") == 0) {
            logDebug("CMD", "%s received: AP portal is permanently decommissioned for dual-core performance.", cmd);
            emitSerialLine("{\"event\":\"AP_STATUS\",\"active\":false,\"stations\":0}");
        } else if (strcmp(cmd, "FINISH_ACK") == 0) {
            xSemaphoreTake(creditMutex, portMAX_DELAY);
            logDebug("CMD", "FINISH_ACK received for session='%s'", sid);
            if (command["protocol"] == 2 && strcmp(sid, creditJournal.session) == 0) {
                finishRequested = false;
                forceGateClose = false;
                logDebug("CMD", "Finish request acknowledged by host.");
            }
            xSemaphoreGive(creditMutex);
        } else if (strcmp(cmd, "SET_CONFIG") == 0) {
            MachineConfig next = desiredConfig;
            bool fieldsValid = true;
            if (!command["bin_full_threshold_cm"].isNull()) {
                if (!command["bin_full_threshold_cm"].is<int>()) fieldsValid = false;
                else next.bin_full_threshold_cm = command["bin_full_threshold_cm"];
            }
            if (!command["pet_nir_w_min"].isNull()) {
                if (!command["pet_nir_w_min"].is<int>()) fieldsValid = false;
                else next.pet_nir_w_min = command["pet_nir_w_min"];
            }
            if (!command["pet_nir_w_max"].isNull()) {
                if (!command["pet_nir_w_max"].is<int>()) fieldsValid = false;
                else next.pet_nir_w_max = command["pet_nir_w_max"];
            }
            if (!command["entrance_gate_timeout"].isNull()) {
                if (!command["entrance_gate_timeout"].is<int>()) fieldsValid = false;
                else next.entrance_gate_timeout = command["entrance_gate_timeout"];
            }
            if (!command["settle_time_ms"].isNull()) {
                if (!command["settle_time_ms"].is<int>()) fieldsValid = false;
                else next.settle_time_ms = command["settle_time_ms"];
            }
            if (!command["success_drop_tout_ms"].isNull()) {
                if (!command["success_drop_tout_ms"].is<int>()) fieldsValid = false;
                else next.success_drop_tout_ms = command["success_drop_tout_ms"];
            }
            if (!command["retrieval_timeout_s"].isNull()) {
                if (!command["retrieval_timeout_s"].is<int>()) fieldsValid = false;
                else next.retrieval_timeout_s = command["retrieval_timeout_s"];
            }
            if (!command["ent_open_angle"].isNull()) {
                if (!command["ent_open_angle"].is<int>()) fieldsValid = false;
                else next.ent_open_angle = command["ent_open_angle"];
            }
            if (!command["ent_close_angle"].isNull()) {
                if (!command["ent_close_angle"].is<int>()) fieldsValid = false;
                else next.ent_close_angle = command["ent_close_angle"];
            }
            if (!command["suc_open_angle"].isNull()) {
                if (!command["suc_open_angle"].is<int>()) fieldsValid = false;
                else next.suc_open_angle = command["suc_open_angle"];
            }
            if (!command["suc_close_angle"].isNull()) {
                if (!command["suc_close_angle"].is<int>()) fieldsValid = false;
                else next.suc_close_angle = command["suc_close_angle"];
            }
            if (!command["require_nir_sensor"].isNull()) {
                if (!command["require_nir_sensor"].is<int>()) fieldsValid = false;
                else next.require_nir_sensor = command["require_nir_sensor"];
            }
            if (!command["require_weight_sensor"].isNull()) {
                if (!command["require_weight_sensor"].is<int>()) fieldsValid = false;
                else next.require_weight_sensor = command["require_weight_sensor"];
            }
            if (!command["min_bottle_weight_g"].isNull()) {
                if (!command["min_bottle_weight_g"].is<int>()) fieldsValid = false;
                else next.min_bottle_weight_g = command["min_bottle_weight_g"];
            }
            if (!command["max_bottle_weight_g"].isNull()) {
                if (!command["max_bottle_weight_g"].is<int>()) fieldsValid = false;
                else next.max_bottle_weight_g = command["max_bottle_weight_g"];
            }
            if (!command["require_bin_sensor"].isNull()) {
                if (!command["require_bin_sensor"].is<int>()) fieldsValid = false;
                else next.require_bin_sensor = command["require_bin_sensor"];
            }
            if (!command["bin_sensor_orientation"].isNull()) {
                if (!command["bin_sensor_orientation"].is<int>()) fieldsValid = false;
                else next.bin_sensor_orientation = command["bin_sensor_orientation"];
            }
            if (!command["bin_empty_depth_cm"].isNull()) {
                if (!command["bin_empty_depth_cm"].is<int>()) fieldsValid = false;
                else next.bin_empty_depth_cm = command["bin_empty_depth_cm"];
            }
            if (!command["bin_debounce_s"].isNull()) {
                if (!command["bin_debounce_s"].is<int>()) fieldsValid = false;
                else next.bin_debounce_s = command["bin_debounce_s"];
            }
            if (!command["weight_cal_factor"].isNull()) {
                if (!command["weight_cal_factor"].is<int>()) fieldsValid = false;
                else next.weight_cal_factor = command["weight_cal_factor"];
            }
            if (!command["timestamp"].isNull()) {
                if (command["timestamp"].is<unsigned long>()) {
                    next.config_timestamp = command["timestamp"].as<unsigned long>();
                } else if (command["timestamp"].is<long long>()) {
                    long long t = command["timestamp"].as<long long>();
                    if (t > 0) next.config_timestamp = (unsigned long)t;
                } else if (command["timestamp"].is<int>()) {
                    long t = command["timestamp"].as<long>();
                    if (t > 0) next.config_timestamp = (unsigned long)t;
                }
            }
            if (fieldsValid && validMachineConfig(next)) {
                desiredConfig = next;
                if (hx711Found && next.weight_cal_factor > 0) {
                    scale.set_scale((float)next.weight_cal_factor);
                }
                xQueueOverwrite(configQueue, &next);
                logDebug("CMD", "SET_CONFIG accepted and queued to SensorTask.");
            } else {
                logWarn("CMD", "SET_CONFIG rejected: invalid fields or configuration out of bounds.");
                emitSerialLine("{\"event\":\"CONFIG_INVALID\"}");
            }
        } else if (strcmp(cmd, "TEST_SERVO") == 0) {
            int channel = command["channel"] | -1;
            int angle = command["angle"] | -1;
            int holdMs = command["hold_ms"] | 1500;
            if (pca9685Found && channel >= 0 && channel <= 1 && angle >= 0 && angle <= 180 && !depositCycleBusy) {
                setServoAngle(channel, angle);
                logDebug("CMD", "TEST_SERVO: channel %d moved to %d deg (hold %d ms)", channel, angle, holdMs);
                char resBuf[128];
                snprintf(resBuf, sizeof(resBuf),
                         "{\"event\":\"SERVO_TEST_OK\",\"channel\":%d,\"angle\":%d}", channel, angle);
                emitSerialLine(resBuf);
            } else {
                logWarn("CMD", "TEST_SERVO rejected: pcaReady=%d, channel=%d, angle=%d, busy=%d",
                        pca9685Found, channel, angle, depositCycleBusy.load());
                emitSerialLine("{\"event\":\"SERVO_TEST_REJECTED\"}");
            }
        } else if (strcmp(cmd, "TEST_NIR") == 0) {
            bool autoClose = command["auto_close"] | false;
            bool openGate = command["open_gate"] | true;
            if (!spectrometerFound) {
                logDebug("NIR", "Spectrometer was offline; attempting dynamic I2C re-probe...");
                if (spectrometer.begin()) {
                    spectrometerFound = true;
                    spectrometer.setBulbCurrent(2);       // 50 mA current drive
                    spectrometer.setGain(3);              // 64x gain
                    spectrometer.setIntegrationTime(50);   // 140ms integration
                    spectrometer.disableBulb();
                    spectrometer.disableIndicator();
                    logDebug("NIR", "AS7263 NIR Spectrometer dynamically detected and initialized!");
                }
            }
            if (spectrometerFound && !depositCycleBusy) {
                logDebug("NIR", "--- On-Demand AS7263 NIR Spectrometer Scan (openGate=%d, autoClose=%d) ---", openGate, autoClose);

                // 1. Trigger the gate entrance servo to OPEN if requested
                if (openGate && pca9685Found) {
                    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_open_angle);
                    gateStateEvent(true);
                    delay(500); // Allow servo to travel to open position
                }

                spectrometer.enableBulb();
                delay(40); // 40ms warmup for incandescent emission stability
                spectrometer.takeMeasurements();
                spectrometer.disableBulb(); // Shut off immediately to prevent sensor thermal drift
                float nirAbsorption = spectrometer.getCalibratedW();
                int r = spectrometer.getR();
                int s = spectrometer.getS();
                int t = spectrometer.getT();
                int u = spectrometer.getU();
                int v = spectrometer.getV();
                int w = spectrometer.getW();
                int tempC = spectrometer.getTemperature();
                NirEvaluation eval = evaluateNirSpectrum(r, s, t, u, v, w, nirAbsorption, config.pet_nir_w_min, config.pet_nir_w_max);

                // 2. Only close gate if explicitly requested. In Teach mode, gate stays open until operator clicks OK!
                if (autoClose && pca9685Found) {
                    delay(250);
                    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
                    gateStateEvent(false);
                }

                logDebug("NIR", "Spectral Channels: R(610nm)=%d, S(680nm)=%d, T(730nm)=%d, U(760nm)=%d, V(810nm)=%d, W(860nm)=%d | Sensor Temp=%d C",
                         r, s, t, u, v, w, tempC);
                logDebug("NIR", "Ratios: W/V=%.2f, S/R=%.2f, T/W=%.2f | Cal-W: %.2f | Verdict: %s (%s)",
                         eval.wv_ratio, eval.sr_ratio, eval.tw_ratio, nirAbsorption,
                         eval.is_pet ? "ACCEPT" : "REJECT", eval.reason_desc);

                char nirBuf[448];
                snprintf(nirBuf, sizeof(nirBuf),
                         "{\"event\":\"NIR_TEST\",\"success\":true,\"r\":%d,\"s\":%d,\"t\":%d,\"u\":%d,\"v\":%d,\"w\":%d,\"calibrated_w\":%.2f,\"temp_c\":%d,\"pet_min\":%d,\"pet_max\":%d,\"wv_ratio\":%.2f,\"sr_ratio\":%.2f,\"tw_ratio\":%.2f,\"is_pet\":%s,\"reason\":\"%s\"}",
                         r, s, t, u, v, w, nirAbsorption, tempC, config.pet_nir_w_min, config.pet_nir_w_max,
                         eval.wv_ratio, eval.sr_ratio, eval.tw_ratio, eval.is_pet ? "true" : "false", eval.reason_code);
                emitSerialLine(nirBuf);
            } else {
                logWarn("CMD", "TEST_NIR rejected: spectrometerFound=%d, busy=%d", spectrometerFound, depositCycleBusy.load());
                char nirBuf[128];
                snprintf(nirBuf, sizeof(nirBuf),
                         "{\"event\":\"NIR_TEST\",\"success\":false,\"error\":\"%s\"}",
                         !spectrometerFound ? "spectrometer_offline" : "machine_busy");
                emitSerialLine(nirBuf);
            }
        } else if (strcmp(cmd, "TEST_WEIGHT") == 0) {
            if (hx711Found && !depositCycleBusy) {
                float weightG = scale.get_units(2);
                lastMeasuredWeightG = weightG;
                logDebug("SCALE", "--- Live HX711 Load Cell Scan: %.1f g ---", weightG);
                char wtBuf[192];
                snprintf(wtBuf, sizeof(wtBuf),
                         "{\"event\":\"WEIGHT_TEST\",\"success\":true,\"weight_g\":%.1f,\"min_g\":%d,\"max_g\":%d,\"cal_factor\":%d}",
                         weightG, config.min_bottle_weight_g, config.max_bottle_weight_g, config.weight_cal_factor);
                emitSerialLine(wtBuf);
            } else {
                logWarn("CMD", "TEST_WEIGHT rejected: hx711Found=%d, busy=%d", hx711Found, depositCycleBusy.load());
                char wtBuf[128];
                snprintf(wtBuf, sizeof(wtBuf),
                         "{\"event\":\"WEIGHT_TEST\",\"success\":false,\"error\":\"%s\"}",
                         !hx711Found ? "hx711_offline" : "machine_busy");
                emitSerialLine(wtBuf);
            }
        } else if (strcmp(cmd, "TARE_WEIGHT") == 0) {
            if (hx711Found && !depositCycleBusy) {
                scale.tare();
                logDebug("SCALE", "--- HX711 Scale Tared (Zeroed) ---");
                emitSerialLine("{\"event\":\"TARE_OK\",\"success\":true}");
            } else {
                emitSerialLine("{\"event\":\"TARE_REJECTED\",\"success\":false}");
            }
        } else if (strcmp(cmd, "PING") == 0) {
            char pongBuf[448];
            bool hwReady = pca9685Found && (!desiredConfig.require_nir_sensor || spectrometerFound) && (!desiredConfig.require_weight_sensor || hx711Found) && (!desiredConfig.require_bin_sensor || !isBinFull.load());
            snprintf(pongBuf, sizeof(pongBuf),
                     "{\"event\":\"PONG\",\"firmware_version\":\"%s\",\"protocol\":2,\"pca9685_ready\":%s,\"spectrometer_ready\":%s,\"hx711_ready\":%s,\"hardware_ready\":%s,\"require_nir\":%d,\"require_weight\":%d,\"require_bin\":%d,\"bin_orient\":%d,\"bin_empty\":%d,\"bin_deb\":%d,\"ap_active\":false,\"ap_stations\":0,\"cfg_ts\":%lu}",
                     ECOVENDO_VERSION,
                     pca9685Found ? "true" : "false",
                     spectrometerFound ? "true" : "false",
                     hx711Found ? "true" : "false",
                     hwReady ? "true" : "false",
                     desiredConfig.require_nir_sensor,
                     desiredConfig.require_weight_sensor,
                     desiredConfig.require_bin_sensor,
                     desiredConfig.bin_sensor_orientation,
                     desiredConfig.bin_empty_depth_cm,
                     desiredConfig.bin_debounce_s,
                     desiredConfig.config_timestamp);
            emitSerialLine(pongBuf);
            logDebug("CMD", "Responded to PING with PONG (cfg_ts=%lu).", desiredConfig.config_timestamp);
        } else if (strcmp(cmd, "REBOOT") == 0) {
            logDebug("CMD", "REBOOT command received. Restarting ESP32 in 100ms...");
            emitSerialLine("{\"event\":\"REBOOTING\",\"success\":true}");
            Serial.flush();
            delay(100);
            ESP.restart();
        } else if (strlen(cmd) > 0) {
            logWarn("CMD", "Unknown command received: '%s'", cmd);
        }
    }

    // Debounced physical finish. Complete/ACK the pending receipt before FINISH.
    static bool buttonWasDown = false;
    static uint32_t buttonChanged = 0;
    static bool buttonHandled = false;
    bool down = digitalRead(PIN_FINISH_BTN) == LOW;
    if (down != buttonWasDown) {
        buttonWasDown = down;
        buttonChanged = millis();
        buttonHandled = false;
    }
    if (down && !buttonHandled && millis() - buttonChanged >= 50) {
        buttonHandled = true;
        finishRequested = true;
        entranceGateRequested = false;
        forceGateClose = true;
        logDebug("BTN", "Finish button press detected (held >= 50ms). Session finish requested.");
    }

    static uint32_t lastReceiptSend = 0;
    if (millis() - lastReceiptSend >= 1000) {
        lastReceiptSend = millis();
        xSemaphoreTake(creditMutex, portMAX_DELAY);
        if (!creditStorageOk) saveCreditJournal();
        xSemaphoreGive(creditMutex);
        replayCredit();
        xSemaphoreTake(creditMutex, portMAX_DELAY);
        if (finishRequested && !depositCycleBusy && creditStorageOk && creditJournal.phase == 0) {
            if (creditJournal.session[0]) {
                logDebug("JOURNAL", "Transmitting FINISH event for session '%s'", creditJournal.session);
                scopedEvent("FINISH", creditJournal.session);
            } else {
                finishRequested = false;
            }
        }
        xSemaphoreGive(creditMutex);
    }
    vTaskDelay(pdMS_TO_TICKS(100));
}
