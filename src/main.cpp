#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Adafruit_PWMServoDriver.h>
#include <atomic>
#include <cstdarg>
#include <AS726X.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include "index_html.h"
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

// -----------------------------------------------------------------------------
// HARDWARE PIN DEFINITIONS
// -----------------------------------------------------------------------------
#define PIN_IR_TOP 32             // E18-D80NK Intake (Left Side Bus)
#define PIN_IR_BOTTOM 33          // E18-D80NK Drop (Left Side Bus)
#define PIN_PROX_METAL 25         // LJ12A3-4-Z/BX Metal (Left Side Bus)
// GPIO 15 is free/spare (Capacitive sensor omitted)
#define PIN_ULTRASONIC_TRIG 23    // HC-SR04 Trig (Right Side Top)
#define PIN_ULTRASONIC_ECHO 27    // HC-SR04 Echo (Left Side Bus)

#define PIN_FINISH_BTN 19         // Front Panel Button (Right Side, internal pull-up)
#define PIN_BUZZER 18             // Front Panel Buzzer (Right Side)
#define PIN_LED_GREEN 5           // Front Panel Green LED (Right Side)
#define PIN_LED_RED 17            // Front Panel Red LED (Right Side)

// PCA9685 I2C Servo Channels
#define PCA9685_I2C_ADDR 0x40
#define PCA_CHANNEL_ENTRANCE 0
#define PCA_CHANNEL_SUCCESS 1
#define PCA_CHANNEL_REJECT 2

#define SERVOMIN 125 // Global baseline 0 degrees (~500us pulse)
#define SERVOMAX 575 // Global baseline 180 degrees (~2400us pulse)

// -----------------------------------------------------------------------------
// PERIPHERALS & GLOBAL STATE
// -----------------------------------------------------------------------------
LiquidCrystal_I2C lcd(0x27, 20, 4);
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(PCA9685_I2C_ADDR);
bool pca9685Found = false;

AS726X spectrometer;
bool spectrometerFound = false;

MachineConfig config;
MachineConfig desiredConfig; // UART task owns this; sensor task owns runtime config.
QueueHandle_t configQueue;
std::atomic<bool> configRestartRequested{false};
std::atomic<int> requestedGateTimeout{60};
std::atomic<bool> finishRequested{false};
Preferences preferences;

bool isConfigMode = false;
WebServer server(80);
DNSServer dnsServer;

unsigned long lastActivityTime = 0;

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
    MSG_GATE_TIMEOUT
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
    char value[64];
    snprintf(value, sizeof(value), "%012llx:%llu", (unsigned long long)ESP.getEfuseMac(),
             (unsigned long long)creditJournal.sequence);
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
    config.pet_nir_w_min = preferences.getInt("nir_min", 200);
    config.pet_nir_w_max = preferences.getInt("nir_max", 5000);
    config.entrance_gate_timeout = preferences.getInt("ent_tout", 60);
    config.settle_time_ms = preferences.getInt("stl_ms", 500);
    config.success_drop_tout_ms = preferences.getInt("suc_tout", 3000);
    config.reject_drop_time_ms = preferences.getInt("rej_time", 2000);
    config.ent_open_angle = preferences.getInt("ent_open", 90);
    config.ent_close_angle = preferences.getInt("ent_close", 0);
    config.suc_open_angle = preferences.getInt("suc_open", 90);
    config.suc_close_angle = preferences.getInt("suc_close", 0);
    config.rej_open_angle = preferences.getInt("rej_open", 90);
    config.rej_close_angle = preferences.getInt("rej_close", 0);
    config.require_nir_sensor = preferences.getInt("req_nir", 1);

    logDebug("NVS", "Loaded Hardware Preferences:");
    logDebug("NVS", "  Bin Full Threshold: %d cm", config.bin_full_threshold_cm);
    logDebug("NVS", "  PET NIR Range: [%d - %d]", config.pet_nir_w_min, config.pet_nir_w_max);
    logDebug("NVS", "  Entrance Gate Timeout: %d s", config.entrance_gate_timeout);
    logDebug("NVS", "  Timings: Settle=%d ms, SuccessTout=%d ms, RejectTime=%d ms",
             config.settle_time_ms, config.success_drop_tout_ms, config.reject_drop_time_ms);
    logDebug("NVS", "  Servos: Ent=[%d/%d], Suc=[%d/%d], Rej=[%d/%d]",
             config.ent_open_angle, config.ent_close_angle,
             config.suc_open_angle, config.suc_close_angle,
             config.rej_open_angle, config.rej_close_angle);
    logDebug("NVS", "  Require NIR Sensor: %d (1=Strict, 0=Bench-Test)", config.require_nir_sensor);
}

void savePreferences() {
    preferences.putInt("bin_cm", config.bin_full_threshold_cm);
    preferences.putInt("nir_min", config.pet_nir_w_min);
    preferences.putInt("nir_max", config.pet_nir_w_max);
    preferences.putInt("ent_tout", config.entrance_gate_timeout);
    preferences.putInt("stl_ms", config.settle_time_ms);
    preferences.putInt("suc_tout", config.success_drop_tout_ms);
    preferences.putInt("rej_time", config.reject_drop_time_ms);
    preferences.putInt("ent_open", config.ent_open_angle);
    preferences.putInt("ent_close", config.ent_close_angle);
    preferences.putInt("suc_open", config.suc_open_angle);
    preferences.putInt("suc_close", config.suc_close_angle);
    preferences.putInt("rej_open", config.rej_open_angle);
    preferences.putInt("rej_close", config.rej_close_angle);
    preferences.putInt("req_nir", config.require_nir_sensor);
    logDebug("NVS", "Persisted hardware parameters to Flash.");
}

// -----------------------------------------------------------------------------
// WEB CONFIGURATION PORTAL HANDLERS
// -----------------------------------------------------------------------------
void handleRoot() {
    lastActivityTime = millis();
    logDebug("HTTP", "GET / served to %s", server.client().remoteIP().toString().c_str());
    String html = index_html;

    html.replace("%BIN_CM%", String(config.bin_full_threshold_cm));
    html.replace("%ENT_TOUT%", String(config.entrance_gate_timeout));
    html.replace("%STL_MS%", String(config.settle_time_ms));
    html.replace("%SUC_TOUT%", String(config.success_drop_tout_ms));
    html.replace("%REJ_TIME%", String(config.reject_drop_time_ms));
    html.replace("%NIR_MIN%", String(config.pet_nir_w_min));
    html.replace("%NIR_MAX%", String(config.pet_nir_w_max));
    html.replace("%ENT_OPEN%", String(config.ent_open_angle));
    html.replace("%ENT_CLOSE%", String(config.ent_close_angle));
    html.replace("%SUC_OPEN%", String(config.suc_open_angle));
    html.replace("%SUC_CLOSE%", String(config.suc_close_angle));
    html.replace("%REJ_OPEN%", String(config.rej_open_angle));
    html.replace("%REJ_CLOSE%", String(config.rej_close_angle));
    html.replace("%REQ_NIR%", String(config.require_nir_sensor));
    server.send(200, "text/html", html);
}

void handleSave() {
    lastActivityTime = millis();
    logDebug("HTTP", "POST /save received from %s", server.client().remoteIP().toString().c_str());
    const char* fields[] = {"bin_cm", "ent_tout", "stl_ms", "suc_tout", "rej_time", "nir_min", "nir_max",
        "ent_open", "ent_close", "suc_open", "suc_close", "rej_open", "rej_close", "req_nir"};
    for (const char* field : fields) {
        if (!server.hasArg(field)) continue;
        String value = server.arg(field);
        bool numeric = value.length() > 0 && value.length() <= 5;
        for (size_t i = 0; i < value.length(); ++i) numeric = numeric && value[i] >= '0' && value[i] <= '9';
        if (!numeric) {
            logWarn("HTTP", "Invalid field format for '%s': '%s'", field, value.c_str());
            server.send(400, "text/plain", "Invalid hardware settings");
            return;
        }
    }
    MachineConfig previous = config;
    if (server.hasArg("bin_cm")) config.bin_full_threshold_cm = server.arg("bin_cm").toInt();
    if (server.hasArg("ent_tout")) config.entrance_gate_timeout = server.arg("ent_tout").toInt();
    if (server.hasArg("stl_ms")) config.settle_time_ms = server.arg("stl_ms").toInt();
    if (server.hasArg("suc_tout")) config.success_drop_tout_ms = server.arg("suc_tout").toInt();
    if (server.hasArg("rej_time")) config.reject_drop_time_ms = server.arg("rej_time").toInt();
    if (server.hasArg("nir_min")) config.pet_nir_w_min = server.arg("nir_min").toInt();
    if (server.hasArg("nir_max")) config.pet_nir_w_max = server.arg("nir_max").toInt();
    if (server.hasArg("ent_open")) config.ent_open_angle = server.arg("ent_open").toInt();
    if (server.hasArg("ent_close")) config.ent_close_angle = server.arg("ent_close").toInt();
    if (server.hasArg("suc_open")) config.suc_open_angle = server.arg("suc_open").toInt();
    if (server.hasArg("suc_close")) config.suc_close_angle = server.arg("suc_close").toInt();
    if (server.hasArg("rej_open")) config.rej_open_angle = server.arg("rej_open").toInt();
    if (server.hasArg("rej_close")) config.rej_close_angle = server.arg("rej_close").toInt();
    if (server.hasArg("req_nir")) config.require_nir_sensor = server.arg("req_nir").toInt();

    if (!validMachineConfig(config)) {
        logWarn("HTTP", "Machine configuration validation failed. Reverting.");
        config = previous;
        server.send(400, "text/plain", "Invalid hardware settings");
        return;
    }
    savePreferences();
    logDebug("HTTP", "Configuration saved. Snapping servos to closed positions to verify tuning.");
    
    // Snap servos to new values immediately to visually test tuning
    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
    setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
    setServoAngle(PCA_CHANNEL_REJECT, config.rej_close_angle);
    
    String html = "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'><title>Configuration Saved</title>";
    html += "<style>:root{--bg:#f8fafc;--card:#ffffff;--text:#0f172a;--muted:#64748b;--border:#e2e8f0;--btn:#0f172a;--btn-txt:#ffffff}@media(prefers-color-scheme:dark){:root{--bg:#090d16;--card:#111827;--text:#f9fafb;--muted:#9ca3af;--border:#1f2937;--btn:#2563eb;--btn-txt:#ffffff}}";
    html += "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:16px;}";
    html += ".card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:28px 24px;max-width:380px;width:100%;text-align:center;box-sizing:border-box;}";
    html += "h2{font-size:18px;margin:0 0 8px;font-weight:600;}p{color:var(--muted);font-size:13px;margin:0 0 20px;line-height:1.5;}";
    html += "a{display:inline-block;text-decoration:none;background:var(--btn);color:var(--btn-txt);padding:10px 20px;border-radius:6px;font-size:14px;font-weight:600;}</style></head>";
    html += "<body><div class='card'><h2>Configuration Saved</h2><p>Parameters saved to flash storage. Servos snapped to closed positions.</p><a href='/'>Back to Configuration</a></div></body></html>";
    server.send(200, "text/html", html);
}

void handleStatus() {
    lastActivityTime = millis();
    JsonDocument doc;
    doc["success"] = true;
    
    xSemaphoreTake(creditMutex, portMAX_DELAY);
    doc["session"] = creditJournal.session[0] ? creditJournal.session : "None (Idle)";
    doc["phase"] = creditJournal.phase;
    xSemaphoreGive(creditMutex);

    doc["session_bottles"] = currentSessionBottles.load();
    doc["is_bin_full"] = isBinFull.load();
    doc["pca9685"] = pca9685Found;
    doc["spectrometer"] = spectrometerFound;
    doc["require_nir_sensor"] = config.require_nir_sensor;
    doc["hardware_ready"] = pca9685Found && (!config.require_nir_sensor || spectrometerFound);
    doc["version"] = ECOVENDO_VERSION;

    String out;
    serializeJson(doc, out);
    server.send(200, "application/json", out);
}

void handleReboot() {
    logDebug("HTTP", "GET /reboot received. Rebooting ESP32 into normal vending mode...");
    server.send(200, "text/plain", "Rebooting...");
    vTaskDelay(pdMS_TO_TICKS(500));
    ESP.restart();
}

void startWebServerRoutes() {
    server.on("/", handleRoot);
    server.on("/save", handleSave);
    server.on("/status", handleStatus);
    server.on("/reboot", handleReboot);
    server.on("/generate_204", handleRoot); // Captive Portal Android
    server.on("/hotspot-detect.html", handleRoot); // Captive Portal iOS
    server.onNotFound(handleRoot);
    server.begin();
    logDebug("CONFIG-AP", "Web server routes registered on port 80.");
}

void configPortalTaskCode(void* parameter) {
    (void)parameter;
    logDebug("CONFIG-AP", "Config portal daemon active on Core %d", xPortGetCoreID());
    lastActivityTime = millis();
    unsigned long lastStatusLog = 0;

    while (true) {
        int stations = WiFi.softAPgetStationNum();
        if (stations > 0) {
            lastActivityTime = millis();
        }
        
        if (millis() - lastStatusLog >= 15000) {
            lastStatusLog = millis();
            unsigned long idleTime = millis() - lastActivityTime;
            logDebug("CONFIG-AP", "Connected clients: %d | Idle elapsed: %lu / 300000 ms",
                     stations, idleTime);
        }

        if (millis() - lastActivityTime > 300000) {
            logWarn("CONFIG-AP", "Config Portal Inactivity Timeout reached (300s). Rebooting to normal mode...");
            emitSerialLine("Config Portal Inactivity Timeout. Rebooting...");
            lcd.clear();
            lcd.setCursor(0, 0); lcd.print("CONFIG TIMEOUT");
            lcd.setCursor(0, 1); lcd.print("Rebooting...");
            vTaskDelay(pdMS_TO_TICKS(1500));
            ESP.restart();
        }

        dnsServer.processNextRequest();
        server.handleClient();
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

// -----------------------------------------------------------------------------
// SENSOR & MECHANICAL WORKFLOW TASK (CORE 0)
// -----------------------------------------------------------------------------
void sensorTaskCode(void* parameter) {
    (void)parameter;
    logDebug("SENSOR", "SensorTask running on Core %d", xPortGetCoreID());
    TickType_t lastUltrasonicCheck = xTaskGetTickCount();
    bool lastBinState = false;

    // Secure all gates at startup
    logDebug("SENSOR", "Securing all servos at startup closed angles...");
    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
    setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
    setServoAngle(PCA_CHANNEL_REJECT, config.rej_close_angle);

    while (true) {
        // Apply configuration only between complete mechanical cycles. Only this
        // task drives vending servos; the UART task never interrupts a drop.
        MachineConfig pending;
        if (xQueueReceive(configQueue, &pending, 0) == pdTRUE) {
            config = pending;
            savePreferences();
            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
            setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
            setServoAngle(PCA_CHANNEL_REJECT, config.rej_close_angle);
            logDebug("SENSOR", "Applied pending config and saved to Flash.");
            emitSerialLine("{\"event\":\"CONFIG_SAVED\"}");
        }
        if (configRestartRequested) {
            logDebug("SENSOR", "Config restart requested. Closing entrance gate and restarting...");
            entranceGateRequested = false;
            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
            preferences.putBool("force_cfg", true);
            ESP.restart();
        }

        // 1. Check Bin Status (isolated non-blocking slice every 1500 ms)
        if (xTaskGetTickCount() - lastUltrasonicCheck >= pdMS_TO_TICKS(1500)) {
            lastUltrasonicCheck = xTaskGetTickCount();
            int distance = getBinDistanceCm();
            cachedBinDistanceCm.store(distance);
            bool currentlyFull = (distance < config.bin_full_threshold_cm && distance > 0);
            
            if (currentlyFull != lastBinState) {
                isBinFull = currentlyFull;
                lastBinState = currentlyFull;
                EventMsg msg = currentlyFull ? MSG_BIN_FULL : MSG_BIN_OK;
                logDebug("BIN", "Bin status changed -> %s (Measured: %d cm, Threshold: %d cm)",
                         currentlyFull ? "FULL" : "OK", distance, config.bin_full_threshold_cm);
                postEvent(msg);
            }
        }

        // Periodic telemetry heartbeat to Linux host gateway (every 3 seconds)
        static TickType_t lastTelemetryHeartbeat = 0;
        if (xTaskGetTickCount() - lastTelemetryHeartbeat >= pdMS_TO_TICKS(3000)) {
            lastTelemetryHeartbeat = xTaskGetTickCount();
            char hbBuf[256];
            bool hwReady = pca9685Found && (!config.require_nir_sensor || spectrometerFound) && !isBinFull.load();
            snprintf(hbBuf, sizeof(hbBuf),
                     "{\"event\":\"HEARTBEAT\",\"bin_distance_cm\":%d,\"is_bin_full\":%s,\"pca9685_ready\":%s,\"spectrometer_ready\":%s,\"hardware_ready\":%s,\"require_nir\":%d,\"gate_open\":%s,\"protocol\":2}",
                     cachedBinDistanceCm.load(),
                     isBinFull.load() ? "true" : "false",
                     pca9685Found ? "true" : "false",
                     spectrometerFound ? "true" : "false",
                     hwReady ? "true" : "false",
                     config.require_nir_sensor,
                     depositCycleBusy.load() ? "true" : "false");
            emitSerialLine(hbBuf);
        }

        if (isBinFull) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        // 2. Await Entrance Request
        if (entranceGateRequested.exchange(false)) {
            xSemaphoreTake(creditMutex, portMAX_DELAY);
            bool sensorReady = !config.require_nir_sensor || spectrometerFound;
            bool permitted = creditStorageOk && creditJournal.phase == 0 && creditJournal.session[0] &&
                !finishRequested && !configRestartRequested && pca9685Found && sensorReady;
            if (permitted) depositCycleBusy = true;
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
            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_open_angle); // Open entrance
            gateStateEvent(true);
            topIrTriggered = false;
            
            unsigned long openTime = millis();
            bool dropped = false;
            bool wasForced = false;
            
            const uint32_t gateTimeoutMs = requestedGateTimeout.load() * 1000UL;
            logDebug("AIRLOCK", "Entrance gate OPEN (Ch 0 -> %d deg). Waiting up to %u ms for bottle insertion...",
                     config.ent_open_angle, gateTimeoutMs);

            while (millis() - openTime < gateTimeoutMs) {
                if (topIrTriggered || forceGateClose) {
                    if (topIrTriggered) {
                        dropped = true;
                        logDebug("AIRLOCK", "Top IR triggered at +%lu ms!", millis() - openTime);
                    }
                    if (forceGateClose) {
                        wasForced = true;
                        logDebug("AIRLOCK", "Force gate close detected at +%lu ms!", millis() - openTime);
                    }
                    break;
                }
                vTaskDelay(pdMS_TO_TICKS(20));
            }
            
            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle); // Close entrance
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
                depositCycleBusy = false;
                continue;
            }
            
            logDebug("AIRLOCK", "Bottle in airlock chamber. Settling for %d ms...", config.settle_time_ms);
            vTaskDelay(pdMS_TO_TICKS(config.settle_time_ms)); // Settle in airlock

            // 3. Multi-Sensor Material Classification
            logDebug("SENSOR", "--- Starting Multi-Sensor Material Classification ---");
            bool isValid = true;
            EventMsg rejectReason = MSG_REJECT_NON_PLASTIC;
            const char* rejectReasonDesc = "None";

            int metalReading = digitalRead(PIN_PROX_METAL);
            logDebug("SENSOR", "Proximity: Metal(GPIO%d)=%s (raw=%d)",
                     PIN_PROX_METAL, metalReading == LOW ? "TRIGGERED (METAL)" : "CLEAR (NO METAL)", metalReading);

            if (metalReading == LOW) {
                isValid = false;
                rejectReason = MSG_REJECT_TIN;
                rejectReasonDesc = "Metal/Tin Can Detected";
                logWarn("DECISION", "REJECT: %s", rejectReasonDesc);
            } 
            else if (!spectrometerFound) {
                isValid = false;
                rejectReason = MSG_REJECT_NIR;
                rejectReasonDesc = "Spectrometer Offline";
                logWarn("DECISION", "REJECT: %s", rejectReasonDesc);
            }
            else {
                logDebug("NIR", "Triggering AS7263 NIR spectrometer measurements...");
                spectrometer.takeMeasurements();
                float nirAbsorption = spectrometer.getCalibratedW();
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

                if (nirAbsorption < config.pet_nir_w_min || nirAbsorption > config.pet_nir_w_max) {
                    isValid = false;
                    rejectReason = MSG_REJECT_NIR;
                    rejectReasonDesc = "NIR Spectrum Out of Range for PET Plastic";
                    logWarn("DECISION", "REJECT: %s (Val: %.2f)", rejectReasonDesc, nirAbsorption);
                } else {
                    logDebug("NIR", "NIR Spectrum MATCHES PET Plastic!");
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

                logDebug("ACTUATION", "Opening success flap (Ch 1 -> %d deg). Waiting for bottom IR (tout=%d ms)...",
                         config.suc_open_angle, config.success_drop_tout_ms);
                setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_open_angle);

                unsigned long gateOpenTime = millis();
                bool passedDrop = false;

                while (millis() - gateOpenTime < static_cast<uint32_t>(config.success_drop_tout_ms)) {
                    if (bottomIrTriggered) {
                        passedDrop = true;
                        logDebug("ACTUATION", "Bottom IR triggered in %lu ms! Bottle confirmed in storage bin.",
                                 millis() - gateOpenTime);
                        break;
                    }
                    vTaskDelay(pdMS_TO_TICKS(20));
                }

                setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
                logDebug("ACTUATION", "Closed success flap (Ch 1 -> %d deg). Drop Passed=%d",
                         config.suc_close_angle, passedDrop);

                if (passedDrop) {
                    completeDrop(true);
                    EventMsg okMsg = MSG_BOTTLE_SAVED;
                    postEvent(okMsg);
                    logDebug("CYCLE", "Deposit cycle successfully completed. Session bottles: %d",
                             currentSessionBottles.load());
                } else {
                    logWarn("ACTUATION", "Drop TIMEOUT! Bottom IR was not triggered within %d ms. Chute jam possible!",
                            config.success_drop_tout_ms);
                    completeDrop(false);
                    EventMsg failMsg = MSG_DROP_TIMEOUT; // Blocked in chute
                    postEvent(failMsg);
                }
            } else {
                // Reject Sequence
                logWarn("DECISION", ">>> BOTTLE REJECTED: %s <<<", rejectReasonDesc);
                EventMsg rejMsg = rejectReason;
                postEvent(rejMsg);
                
                logDebug("ACTUATION", "Opening reject flap (Ch 2 -> %d deg) for %d ms...",
                         config.rej_open_angle, config.reject_drop_time_ms);
                setServoAngle(PCA_CHANNEL_REJECT, config.rej_open_angle);
                vTaskDelay(pdMS_TO_TICKS(config.reject_drop_time_ms)); // Give time for gravity rejection
                setServoAngle(PCA_CHANNEL_REJECT, config.rej_close_angle);
                logDebug("ACTUATION", "Closed reject flap (Ch 2 -> %d deg).", config.rej_close_angle);
            }
            
            depositCycleBusy = false;
            logDebug("CYCLE", "Deposit cycle finished. Airlock resting for 1500 ms...");
            vTaskDelay(pdMS_TO_TICKS(1500));
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
        logDebug("I2C", "AS7263 NIR Spectrometer detected OK! Sensor Temp: %d C", spectrometer.getTemperature());
    }

    char bootBuf[256];
    snprintf(bootBuf, sizeof(bootBuf),
             "{\"event\":\"BOOT\",\"protocol\":2,\"firmware_version\":\"%s\",\"pca9685_ready\":%s,\"spectrometer_ready\":%s}",
             ECOVENDO_VERSION,
             pca9685Found ? "true" : "false",
             spectrometerFound ? "true" : "false");
    emitSerialLine(bootBuf);

    loadPreferences();
    if (!validMachineConfig(config)) {
        logWarn("NVS", "Loaded configuration invalid; reverting to defaults.");
        config = MachineConfig{};
    }
    desiredConfig = config;
    requestedGateTimeout = config.entrance_gate_timeout;

    logDebug("SERVO", "Aligning all servos to initial closed angles...");
    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
    setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
    setServoAngle(PCA_CHANNEL_REJECT, config.rej_close_angle);

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

    bool forceConfig = preferences.getBool("force_cfg", false);
    if (forceConfig) {
        preferences.putBool("force_cfg", false);
        logDebug("BOOT", "Force config flag was set in flash. Entering Config Mode.");
    }
    bool btnDown = (digitalRead(PIN_FINISH_BTN) == LOW);
    logDebug("BOOT", "Finish Button (GPIO 34) on boot: %s", btnDown ? "LOW (PRESSED)" : "HIGH (RELEASED)");

    // Check for Config Mode Trigger
    if (forceConfig || btnDown) {
        logDebug("BOOT", ">>> ENTERING CONFIG PORTAL AP MODE <<<");
        isConfigMode = true;
        lcd.setCursor(0, 0); lcd.print("** VMC ECO-VENDO ** ");
        lcd.setCursor(0, 1); lcd.print("SSID: ESP32 Vendo   ");
        lcd.setCursor(0, 2); lcd.print("IP: 192.168.4.1     ");
        lcd.setCursor(0, 3); lcd.print("Port: 80 - Active   ");

        WiFi.mode(WIFI_AP);
        WiFi.softAP("ESP32 Vendo", "admin1234");
        dnsServer.start(53, "*", WiFi.softAPIP());
        logDebug("CONFIG-AP", "SoftAP 'ESP32 Vendo' started at %s", WiFi.softAPIP().toString().c_str());

        startWebServerRoutes();

        xTaskCreatePinnedToCore(configPortalTaskCode, "ConfigTask", 4096, NULL, 1, NULL, 0);
        return; // Halt further setup for vending
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
    if (isConfigMode) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        return;
    }

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
            logDebug("CMD", "OPEN_GATE received. session='%s' (same=%d), timeout=%d, phase=%d, busy=%d",
                     sid, same, timeout, creditJournal.phase, depositCycleBusy.load());
            if (command["protocol"] == 2 && strlen(sid) > 0 && strlen(sid) <= 36 &&
                creditStorageOk && creditJournal.phase == 0 && (!depositCycleBusy || same) &&
                !finishRequested && !configRestartRequested && timeout >= 1 && timeout <= 600) {
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
            if (strcmp(sid, creditJournal.session) == 0) {
                entranceGateRequested = false;
                forceGateClose = true;
                logDebug("CMD", "Forced gate close flag set.");
            }
            xSemaphoreGive(creditMutex);
        } else if (strcmp(cmd, "TRIGGER_CONFIG") == 0) {
            logDebug("CMD", "TRIGGER_CONFIG received! Scheduling config restart...");
            configRestartRequested = true;
            entranceGateRequested = false;
            forceGateClose = true;
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
            if (!command["reject_drop_time_ms"].isNull()) {
                if (!command["reject_drop_time_ms"].is<int>()) fieldsValid = false;
                else next.reject_drop_time_ms = command["reject_drop_time_ms"];
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
            if (!command["rej_open_angle"].isNull()) {
                if (!command["rej_open_angle"].is<int>()) fieldsValid = false;
                else next.rej_open_angle = command["rej_open_angle"];
            }
            if (!command["rej_close_angle"].isNull()) {
                if (!command["rej_close_angle"].is<int>()) fieldsValid = false;
                else next.rej_close_angle = command["rej_close_angle"];
            }
            if (!command["require_nir_sensor"].isNull()) {
                if (!command["require_nir_sensor"].is<int>()) fieldsValid = false;
                else next.require_nir_sensor = command["require_nir_sensor"];
            }
            if (fieldsValid && validMachineConfig(next)) {
                desiredConfig = next;
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
            if (pca9685Found && channel >= 0 && channel <= 2 && angle >= 0 && angle <= 180 && !depositCycleBusy) {
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
        } else if (strcmp(cmd, "PING") == 0) {
            char pongBuf[256];
            bool hwReady = pca9685Found && (!desiredConfig.require_nir_sensor || spectrometerFound);
            snprintf(pongBuf, sizeof(pongBuf),
                     "{\"event\":\"PONG\",\"firmware_version\":\"%s\",\"protocol\":2,\"pca9685_ready\":%s,\"spectrometer_ready\":%s,\"hardware_ready\":%s,\"require_nir\":%d}",
                     ECOVENDO_VERSION,
                     pca9685Found ? "true" : "false",
                     spectrometerFound ? "true" : "false",
                     hwReady ? "true" : "false",
                     desiredConfig.require_nir_sensor);
            emitSerialLine(pongBuf);
            logDebug("CMD", "Responded to PING with PONG.");
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
