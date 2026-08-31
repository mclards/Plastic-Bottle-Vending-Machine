#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_PWMServoDriver.h>
#include <atomic>
#include <AS726X.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include "index_html.h"

#define PIN_IR_TOP 18
#define PIN_IR_BOTTOM 19
#define PIN_PROX_METAL 23
#define PIN_PROX_CAPACITIVE 15
#define PIN_ULTRASONIC_TRIG 14
#define PIN_ULTRASONIC_ECHO 12
#define PIN_FINISH_BTN 34 // GPIO 34 for Finish Button / Config Trigger
#define PIN_BUZZER 33
#define PIN_LED_GREEN 25
#define PIN_LED_RED 26

// PCA9685 I2C Servo Channels
#define PCA9685_I2C_ADDR 0x40
#define PCA_CHANNEL_ENTRANCE 0
#define PCA_CHANNEL_SUCCESS 1
#define PCA_CHANNEL_REJECT 2

#define SERVOMIN 125 // Global baseline 0 degrees
#define SERVOMAX 575 // Global baseline 180 degrees

LiquidCrystal_I2C lcd(0x27, 20, 4);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(PCA9685_I2C_ADDR);
bool pca9685Found = false;

AS726X spectrometer;
bool spectrometerFound = false;

struct MachineConfig {
    int bin_full_threshold_cm = 15;
    int pet_nir_w_min = 200;
    int pet_nir_w_max = 5000;
    int entrance_gate_timeout = 30;
    
    // Hardware Timings
    int settle_time_ms = 500;
    int success_drop_tout_ms = 3000;
    int reject_drop_time_ms = 2000;

    // Independent Servo Angles for Fine-Tuning
    int ent_open_angle = 90;
    int ent_close_angle = 0;
    int suc_open_angle = 90;
    int suc_close_angle = 0;
    int rej_open_angle = 90;
    int rej_close_angle = 0;
};
MachineConfig config;
Preferences preferences;

bool isConfigMode = false;
WebServer server(80);
DNSServer dnsServer;

volatile bool topIrTriggered = false;
volatile bool bottomIrTriggered = false;

std::atomic<bool> isBinFull{false};
std::atomic<int> currentSessionBottles{0};
std::atomic<bool> entranceGateRequested{false};
std::atomic<bool> forceGateClose{false};

QueueHandle_t eventQueue;
SemaphoreHandle_t uiMutex;

enum EventMsg {
    MSG_BIN_FULL,
    MSG_BIN_OK,
    MSG_REJECT_TIN,
    MSG_REJECT_NON_PLASTIC,
    MSG_REJECT_NIR,
    MSG_VALIDATE_START,
    MSG_BOTTLE_SAVED,
    MSG_DROP_TIMEOUT
};

void IRAM_ATTR isrTopIr() { topIrTriggered = true; }
void IRAM_ATTR isrBottomIr() { bottomIrTriggered = true; }

void setServoAngle(uint8_t channel, int angle) {
    if (pca9685Found) {
        int pulse = map(angle, 0, 180, SERVOMIN, SERVOMAX);
        pwm.setPWM(channel, 0, pulse);
    }
}

void buzz(int durationMs, int pulses = 1) {
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

void loadPreferences() {
    preferences.begin("ecofi", false);
    config.bin_full_threshold_cm = preferences.getInt("bin_cm", 15);
    config.pet_nir_w_min = preferences.getInt("nir_min", 200);
    config.pet_nir_w_max = preferences.getInt("nir_max", 5000);
    config.entrance_gate_timeout = preferences.getInt("ent_tout", 30);
    config.settle_time_ms = preferences.getInt("stl_ms", 500);
    config.success_drop_tout_ms = preferences.getInt("suc_tout", 3000);
    config.reject_drop_time_ms = preferences.getInt("rej_time", 2000);
    config.ent_open_angle = preferences.getInt("ent_open", 90);
    config.ent_close_angle = preferences.getInt("ent_close", 0);
    config.suc_open_angle = preferences.getInt("suc_open", 90);
    config.suc_close_angle = preferences.getInt("suc_close", 0);
    config.rej_open_angle = preferences.getInt("rej_open", 90);
    config.rej_close_angle = preferences.getInt("rej_close", 0);
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
}

void handleRoot() {
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
    server.send(200, "text/html", html);
}

void handleSave() {
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

    savePreferences();
    
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

void configPortalTaskCode(void* parameter) {
    unsigned long lastActivityTime = millis();
    while (true) {
        if (WiFi.softAPgetStationNum() > 0) {
            lastActivityTime = millis();
        }
        
        if (millis() - lastActivityTime > 60000) {
            Serial.println("Config Portal Inactivity Timeout. Rebooting...");
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

void sensorTaskCode(void* parameter) {
    TickType_t lastUltrasonicCheck = xTaskGetTickCount();
    bool lastBinState = false;

    // Secure all gates at startup
    setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
    setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
    setServoAngle(PCA_CHANNEL_REJECT, config.rej_close_angle);

    while (true) {
        // 1. Check Bin Status
        if (xTaskGetTickCount() - lastUltrasonicCheck >= pdMS_TO_TICKS(1000)) {
            int distance = getBinDistanceCm();
            bool currentlyFull = (distance < config.bin_full_threshold_cm && distance > 0);
            
            if (currentlyFull != lastBinState) {
                isBinFull = currentlyFull;
                lastBinState = currentlyFull;
                EventMsg msg = currentlyFull ? MSG_BIN_FULL : MSG_BIN_OK;
                xQueueSend(eventQueue, &msg, 0);
            }
            lastUltrasonicCheck = xTaskGetTickCount();
        }

        if (isBinFull) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        // 2. Await Entrance Request
        if (entranceGateRequested) {
            entranceGateRequested = false;
            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_open_angle); // Open entrance
            topIrTriggered = false;
            
            unsigned long openTime = millis();
            bool dropped = false;
            
            while (millis() - openTime < (config.entrance_gate_timeout * 1000UL)) {
                if (topIrTriggered || forceGateClose) {
                    if (topIrTriggered) dropped = true;
                    break;
                }
                vTaskDelay(pdMS_TO_TICKS(20));
            }
            
            setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle); // Close entrance
            forceGateClose = false;
            
            if (!dropped) {
                EventMsg failMsg = MSG_DROP_TIMEOUT;
                xQueueSend(eventQueue, &failMsg, portMAX_DELAY);
                continue;
            }
            
            vTaskDelay(pdMS_TO_TICKS(config.settle_time_ms)); // Settle in airlock

            // 3. Validation
            bool isValid = true;
            EventMsg rejectReason = MSG_REJECT_NON_PLASTIC;

            if (digitalRead(PIN_PROX_METAL) == LOW) {
                isValid = false;
                rejectReason = MSG_REJECT_TIN;
            } 
            else if (digitalRead(PIN_PROX_CAPACITIVE) == HIGH) {
                isValid = false;
                rejectReason = MSG_REJECT_NON_PLASTIC;
            }
            else if (spectrometerFound) {
                spectrometer.takeMeasurements();
                int nirAbsorption = spectrometer.getCalibratedW();
                if (nirAbsorption < config.pet_nir_w_min || nirAbsorption > config.pet_nir_w_max) {
                    isValid = false;
                    rejectReason = MSG_REJECT_NIR;
                }
            }

            // 4. Actuation
            if (isValid) {
                EventMsg startMsg = MSG_VALIDATE_START;
                xQueueSend(eventQueue, &startMsg, portMAX_DELAY);
                
                bottomIrTriggered = false;
                setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_open_angle);

                unsigned long gateOpenTime = millis();
                bool passedDrop = false;

                while (millis() - gateOpenTime < config.success_drop_tout_ms) {
                    if (bottomIrTriggered) {
                        passedDrop = true;
                        break;
                    }
                    vTaskDelay(pdMS_TO_TICKS(20));
                }

                setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);

                if (passedDrop) {
                    currentSessionBottles++;
                    EventMsg okMsg = MSG_BOTTLE_SAVED;
                    xQueueSend(eventQueue, &okMsg, portMAX_DELAY);
                } else {
                    EventMsg failMsg = MSG_DROP_TIMEOUT; // Blocked in chute
                    xQueueSend(eventQueue, &failMsg, portMAX_DELAY);
                }
            } else {
                // Reject Sequence
                EventMsg rejMsg = rejectReason;
                xQueueSend(eventQueue, &rejMsg, portMAX_DELAY);
                
                setServoAngle(PCA_CHANNEL_REJECT, config.rej_open_angle);
                vTaskDelay(pdMS_TO_TICKS(config.reject_drop_time_ms)); // Give time for gravity rejection
                setServoAngle(PCA_CHANNEL_REJECT, config.rej_close_angle);
            }
            
            vTaskDelay(pdMS_TO_TICKS(1500));
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

void commTaskCode(void* parameter) {
    EventMsg msg;

    while (true) {
        if (xQueueReceive(eventQueue, &msg, pdMS_TO_TICKS(50)) == pdTRUE) {
            xSemaphoreTake(uiMutex, portMAX_DELAY);
            switch(msg) {
                case MSG_BIN_FULL:
                    lcd.setCursor(0, 1); lcd.print("STATUS: STORAGE FULL");
                    lcd.setCursor(0, 2); lcd.print("Empty Bin Required  ");
                    digitalWrite(PIN_LED_RED, HIGH);
                    Serial.println("{\"event\":\"BIN_FULL\"}");
                    break;
                
                case MSG_BIN_OK:
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    digitalWrite(PIN_LED_RED, LOW);
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
                    Serial.println("{\"event\":\"REJECTED\"}");
                    buzz(600, 1);
                    digitalWrite(PIN_LED_RED, LOW);
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    break;
                
                case MSG_VALIDATE_START:
                    digitalWrite(PIN_LED_GREEN, HIGH);
                    lcd.setCursor(0, 1); lcd.print("STATUS: VERIFIED OK ");
                    lcd.setCursor(0, 2); lcd.print("Dropping to bin...  ");
                    break;

                case MSG_BOTTLE_SAVED:
                    buzz(120, 2);
                    Serial.print("{\"event\":\"CREDIT_ADD\",\"bottles\":1,\"sessionTotal\":");
                    Serial.print(currentSessionBottles);
                    Serial.println("}");
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
                    Serial.println("{\"event\":\"REJECTED\"}");
                    buzz(600, 1);
                    digitalWrite(PIN_LED_RED, LOW);
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    break;
            }
            xSemaphoreGive(uiMutex);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_IR_TOP, INPUT_PULLUP);
    pinMode(PIN_IR_BOTTOM, INPUT_PULLUP);
    pinMode(PIN_PROX_METAL, INPUT_PULLUP);
    pinMode(PIN_PROX_CAPACITIVE, INPUT_PULLUP);
    pinMode(PIN_FINISH_BTN, INPUT_PULLUP);
    pinMode(PIN_ULTRASONIC_TRIG, OUTPUT);
    pinMode(PIN_ULTRASONIC_ECHO, INPUT);
    pinMode(PIN_BUZZER, OUTPUT);
    pinMode(PIN_LED_GREEN, OUTPUT);
    pinMode(PIN_LED_RED, OUTPUT);

    attachInterrupt(digitalPinToInterrupt(PIN_IR_TOP), isrTopIr, FALLING);
    attachInterrupt(digitalPinToInterrupt(PIN_IR_BOTTOM), isrBottomIr, FALLING);

    Wire.begin(21, 22);
    pwm.begin();
    pwm.setPWMFreq(50);
    pca9685Found = true;

    lcd.init();
    lcd.backlight();
    oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
    oled.clearDisplay();
    oled.setTextSize(1);
    oled.setTextColor(WHITE);
    oled.setCursor(0, 10);
    oled.println("Booting ECO-Fi...");
    oled.display();

    if (spectrometer.begin() == false) {
        Serial.println("AS7263 Sensor missing!");
        spectrometerFound = false;
    } else {
        spectrometerFound = true;
    }

    loadPreferences();

    bool forceConfig = preferences.getBool("force_cfg", false);
    if (forceConfig) {
        preferences.putBool("force_cfg", false);
    }

    // Check for Config Mode Trigger
    if (forceConfig || digitalRead(PIN_FINISH_BTN) == LOW) {
        isConfigMode = true;
        lcd.setCursor(0, 0); lcd.print("=== ECO-Fi CONFIG ==");
        lcd.setCursor(0, 1); lcd.print("WIFI: ECO-Fi-Config ");
        lcd.setCursor(0, 2); lcd.print("IP: 192.168.4.1     ");
        
        oled.clearDisplay();
        oled.setCursor(0, 0);
        oled.println("CONFIG MODE");
        oled.println("Connect to WiFi:");
        oled.println("ECO-Fi-Config");
        oled.display();

        WiFi.mode(WIFI_AP);
        WiFi.softAP("ECO-Fi-Hardware-Config", "admin1234");
        dnsServer.start(53, "*", WiFi.softAPIP());

        server.on("/", handleRoot);
        server.on("/save", handleSave);
        server.on("/generate_204", handleRoot); // Captive Portal Android
        server.on("/hotspot-detect.html", handleRoot); // Captive Portal iOS
        server.onNotFound(handleRoot);
        server.begin();

        xTaskCreatePinnedToCore(configPortalTaskCode, "ConfigTask", 4096, NULL, 1, NULL, 0);
        return; // Halt further setup for vending
    }

    // Normal Vending Setup
    lcd.setCursor(0, 0); lcd.print("=== ECO-Fi VENDO ===");
    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
    lcd.setCursor(0, 3); lcd.print("Session Bottles: 0  ");

    eventQueue = xQueueCreate(10, sizeof(EventMsg));
    uiMutex = xSemaphoreCreateMutex();

    xTaskCreatePinnedToCore(sensorTaskCode, "SensorTask", 4096, NULL, 1, NULL, 0);
    xTaskCreatePinnedToCore(commTaskCode, "CommTask", 4096, NULL, 1, NULL, 1);
}

void loop() {
    if (isConfigMode) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        return;
    }

    if (Serial.available()) {
        String msg = Serial.readStringUntil('\n');
        if (msg.indexOf("\"OPEN_GATE\"") >= 0) {
            entranceGateRequested = true;
        } else if (msg.indexOf("\"CLOSE_GATE\"") >= 0) {
            forceGateClose = true;
        } else if (msg.indexOf("\"TRIGGER_CONFIG\"") >= 0) {
            preferences.putBool("force_cfg", true);
            ESP.restart();
        } else if (msg.indexOf("\"SET_CONFIG\"") >= 0) {
            JsonDocument doc;
            DeserializationError error = deserializeJson(doc, msg);
            if (!error) {
                if (!doc["bin_full_threshold_cm"].isNull()) config.bin_full_threshold_cm = doc["bin_full_threshold_cm"];
                if (!doc["pet_nir_w_min"].isNull()) config.pet_nir_w_min = doc["pet_nir_w_min"];
                if (!doc["pet_nir_w_max"].isNull()) config.pet_nir_w_max = doc["pet_nir_w_max"];
                if (!doc["entrance_gate_timeout"].isNull()) config.entrance_gate_timeout = doc["entrance_gate_timeout"];
                if (!doc["settle_time_ms"].isNull()) config.settle_time_ms = doc["settle_time_ms"];
                if (!doc["success_drop_tout_ms"].isNull()) config.success_drop_tout_ms = doc["success_drop_tout_ms"];
                if (!doc["reject_drop_time_ms"].isNull()) config.reject_drop_time_ms = doc["reject_drop_time_ms"];
                if (!doc["ent_open_angle"].isNull()) config.ent_open_angle = doc["ent_open_angle"];
                if (!doc["ent_close_angle"].isNull()) config.ent_close_angle = doc["ent_close_angle"];
                if (!doc["suc_open_angle"].isNull()) config.suc_open_angle = doc["suc_open_angle"];
                if (!doc["suc_close_angle"].isNull()) config.suc_close_angle = doc["suc_close_angle"];
                if (!doc["rej_open_angle"].isNull()) config.rej_open_angle = doc["rej_open_angle"];
                if (!doc["rej_close_angle"].isNull()) config.rej_close_angle = doc["rej_close_angle"];
                
                savePreferences();
                
                // Snap servos to their new close positions immediately
                setServoAngle(PCA_CHANNEL_ENTRANCE, config.ent_close_angle);
                setServoAngle(PCA_CHANNEL_SUCCESS, config.suc_close_angle);
                setServoAngle(PCA_CHANNEL_REJECT, config.rej_close_angle);
                
                Serial.println("{\"event\":\"CONFIG_SAVED\"}");
            }
        }
    }
    vTaskDelay(pdMS_TO_TICKS(100));
}
