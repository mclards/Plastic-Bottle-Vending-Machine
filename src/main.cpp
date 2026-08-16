#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ESP32Servo.h>
#include "HX711.h"
#include <atomic>
#include "AS726X.h"

#define PIN_IR_TOP 18
#define PIN_IR_BOTTOM 19
#define PIN_PROX_METAL 23
#define PIN_PROX_CAPACITIVE 15
#define PIN_HX711_DT 4
#define PIN_HX711_SCK 5
#define PIN_ULTRASONIC_TRIG 14
#define PIN_ULTRASONIC_ECHO 12
#define PIN_DOOR_SWITCH 27
#define PIN_SERVO_ENTRANCE 21
#define PIN_SERVO_GATE 13
#define PIN_SOLENOID_REJ 32
#define PIN_BUZZER 33
#define PIN_LED_GREEN 25
#define PIN_LED_RED 26

const float MIN_PET_WEIGHT = 10.0;
const float MAX_PET_WEIGHT = 65.0;
const int BIN_FULL_THRESHOLD_CM = 15;

LiquidCrystal_I2C lcd(0x27, 20, 4);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
Servo hatchServo;
Servo entranceServo;
HX711 scale;
AS726X spectrometer;
bool spectrometerFound = false;

// Calibration thresholds for AS7263
// W channel is 860nm. You will need to adjust this threshold.
const int PET_NIR_W_MIN = 200; 
const int PET_NIR_W_MAX = 5000;

volatile bool topIrTriggered = false;
volatile bool bottomIrTriggered = false;

std::atomic<bool> isBinFull{false};
std::atomic<int> currentSessionBottles{0};
std::atomic<bool> entranceGateRequested{false};
std::atomic<int> entranceGateTimeout{30};

QueueHandle_t eventQueue;
SemaphoreHandle_t uiMutex;

enum EventMsg {
    MSG_BIN_FULL,
    MSG_BIN_OK,
    MSG_REJECT_TIN,
    MSG_REJECT_WEIGHT,
    MSG_REJECT_NON_PLASTIC,
    MSG_REJECT_NIR,
    MSG_VALIDATE_START,
    MSG_BOTTLE_SAVED,
    MSG_DROP_TIMEOUT
};

void IRAM_ATTR isrTopIr() { topIrTriggered = true; }
void IRAM_ATTR isrBottomIr() { bottomIrTriggered = true; }

void buzz(int durationMs, int pulses = 1) {
    for(int i = 0; i < pulses; i++) {
        digitalWrite(PIN_BUZZER, HIGH);
        vTaskDelay(pdMS_TO_TICKS(durationMs));
        digitalWrite(PIN_BUZZER, LOW);
        if(pulses > 1) vTaskDelay(pdMS_TO_TICKS(80));
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

void sensorTaskCode(void* parameter) {
    TickType_t lastUltrasonicCheck = xTaskGetTickCount();
    bool lastBinState = false;

    while (true) {
        // Check bin distance every 1 second
        if (xTaskGetTickCount() - lastUltrasonicCheck >= pdMS_TO_TICKS(1000)) {
            int distance = getBinDistanceCm();
            bool currentlyFull = (distance < BIN_FULL_THRESHOLD_CM && distance > 0);
            
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

        if (entranceGateRequested) {
            entranceGateRequested = false;
            entranceServo.write(90); // Open entrance
            topIrTriggered = false;
            
            unsigned long openTime = millis();
            bool dropped = false;
            
            while (millis() - openTime < (entranceGateTimeout * 1000UL)) {
                if (topIrTriggered) {
                    dropped = true;
                    break;
                }
                vTaskDelay(pdMS_TO_TICKS(20));
            }
            
            entranceServo.write(0); // Secure the entrance instantly
            
            if (!dropped) {
                EventMsg failMsg = MSG_DROP_TIMEOUT;
                xQueueSend(eventQueue, &failMsg, portMAX_DELAY);
                continue;
            }
            
            vTaskDelay(pdMS_TO_TICKS(500)); // Wait for bottle to settle on scale

            if (scale.is_ready()) {
                float weight = scale.get_units(3);
                if (weight >= MIN_PET_WEIGHT) {
                    
                    if (digitalRead(PIN_PROX_METAL) == LOW) {
                        EventMsg msg = MSG_REJECT_TIN;
                        xQueueSend(eventQueue, &msg, portMAX_DELAY);
                        scale.tare();
                        continue;
                    }

                    if (weight > MAX_PET_WEIGHT) {
                        EventMsg msg = MSG_REJECT_WEIGHT;
                        xQueueSend(eventQueue, &msg, portMAX_DELAY);
                        scale.tare();
                        continue;
                    }

                    if (digitalRead(PIN_PROX_CAPACITIVE) == HIGH) {
                        EventMsg msg = MSG_REJECT_NON_PLASTIC;
                        xQueueSend(eventQueue, &msg, portMAX_DELAY);
                        scale.tare();
                        continue;
                    }

                    if (spectrometerFound) {
                        spectrometer.takeMeasurements();
                        int nirAbsorption = spectrometer.getCalibratedW();
                        if (nirAbsorption < PET_NIR_W_MIN || nirAbsorption > PET_NIR_W_MAX) {
                            EventMsg msg = MSG_REJECT_NIR;
                            xQueueSend(eventQueue, &msg, portMAX_DELAY);
                            scale.tare();
                            continue;
                        }
                    }

                    // Valid drop detected
                    EventMsg startMsg = MSG_VALIDATE_START;
                    xQueueSend(eventQueue, &startMsg, portMAX_DELAY);
                    
                    bottomIrTriggered = false;
                    hatchServo.write(90);

                    unsigned long gateOpenTime = millis();
                    bool passedDrop = false;

                    while (millis() - gateOpenTime < 3000) {
                        if (bottomIrTriggered) { // top was already triggered at entrance
                            passedDrop = true;
                            break;
                        }
                        vTaskDelay(pdMS_TO_TICKS(20));
                    }

                    hatchServo.write(0);

                    if (passedDrop) {
                        currentSessionBottles++;
                        EventMsg okMsg = MSG_BOTTLE_SAVED;
                        xQueueSend(eventQueue, &okMsg, portMAX_DELAY);
                    } else {
                        // Bottle got stuck inside machine
                        EventMsg failMsg = MSG_REJECT_WEIGHT; // generic fail
                        xQueueSend(eventQueue, &failMsg, portMAX_DELAY);
                    }
                    
                    vTaskDelay(pdMS_TO_TICKS(1500));
                    scale.tare();
                } else {
                    // Weight too small (e.g. leaf or wrapper fell in)
                    EventMsg msg = MSG_REJECT_WEIGHT;
                    xQueueSend(eventQueue, &msg, portMAX_DELAY);
                    scale.tare();
                }
            }
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
                case MSG_REJECT_WEIGHT:
                case MSG_REJECT_NON_PLASTIC:
                case MSG_REJECT_NIR:
                    digitalWrite(PIN_LED_RED, HIGH);
                    digitalWrite(PIN_LED_GREEN, LOW);
                    lcd.setCursor(0, 1); lcd.print("STATUS: REJECTED!   ");
                    lcd.setCursor(0, 2); 
                    if (msg == MSG_REJECT_TIN) lcd.print("Tin/Can Detected    ");
                    else if (msg == MSG_REJECT_WEIGHT) lcd.print("Liquid / Overweight ");
                    else if (msg == MSG_REJECT_NIR) lcd.print("Invalid Material NIR");
                    else lcd.print("No Plastic Detected ");
                    Serial.println("{\"event\":\"REJECTED\"}");
                    buzz(600, 1);
                    digitalWrite(PIN_SOLENOID_REJ, HIGH);
                    vTaskDelay(pdMS_TO_TICKS(500));
                    digitalWrite(PIN_SOLENOID_REJ, LOW);
                    vTaskDelay(pdMS_TO_TICKS(2000));
                    digitalWrite(PIN_LED_RED, LOW);
                    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
                    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
                    break;
                
                case MSG_VALIDATE_START:
                    digitalWrite(PIN_LED_GREEN, HIGH);
                    lcd.setCursor(0, 1); lcd.print("STATUS: VERIFIED OK ");
                    lcd.setCursor(0, 2); lcd.print("Please drop bottle  ");
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
                    lcd.setCursor(0, 1); lcd.print("STATUS: REJECTED!   ");
                    lcd.setCursor(0, 2); lcd.print("Drop Optical Timeout");
                    Serial.println("{\"event\":\"REJECTED\"}");
                    buzz(600, 1);
                    digitalWrite(PIN_SOLENOID_REJ, HIGH);
                    vTaskDelay(pdMS_TO_TICKS(500));
                    digitalWrite(PIN_SOLENOID_REJ, LOW);
                    vTaskDelay(pdMS_TO_TICKS(2000));
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
    pinMode(PIN_DOOR_SWITCH, INPUT_PULLUP);
    pinMode(PIN_ULTRASONIC_TRIG, OUTPUT);
    pinMode(PIN_ULTRASONIC_ECHO, INPUT);
    pinMode(PIN_SOLENOID_REJ, OUTPUT);
    pinMode(PIN_BUZZER, OUTPUT);
    pinMode(PIN_LED_GREEN, OUTPUT);
    pinMode(PIN_LED_RED, OUTPUT);

    attachInterrupt(digitalPinToInterrupt(PIN_IR_TOP), isrTopIr, FALLING);
    attachInterrupt(digitalPinToInterrupt(PIN_IR_BOTTOM), isrBottomIr, FALLING);

    hatchServo.attach(PIN_SERVO_GATE);
    entranceServo.attach(PIN_SERVO_ENTRANCE);
    entranceServo.write(0);
    hatchServo.write(0);

    scale.begin(PIN_HX711_DT, PIN_HX711_SCK);
    scale.set_scale(420.0);
    scale.tare();

    lcd.init();
    lcd.backlight();
    oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
    oled.clearDisplay();
    oled.setTextSize(1);
    oled.setTextColor(WHITE);
    oled.setCursor(0, 10);
    oled.println("Eco-Fi Vendo Ready");
    oled.display();

    if (spectrometer.begin() == false) {
        Serial.println("AS7263 Sensor does not appear to be connected.");
        spectrometerFound = false;
    } else {
        spectrometerFound = true;
    }

    lcd.setCursor(0, 0); lcd.print("=== ECO-FI VENDO ===");
    lcd.setCursor(0, 1); lcd.print("Ready for Deposit   ");
    lcd.setCursor(0, 2); lcd.print("Rate: 1 Bottle = 15m");
    lcd.setCursor(0, 3); lcd.print("Session Bottles: 0  ");

    eventQueue = xQueueCreate(10, sizeof(EventMsg));
    uiMutex = xSemaphoreCreateMutex();

    xTaskCreatePinnedToCore(
        sensorTaskCode,
        "SensorTask",
        4096,
        NULL,
        1,
        NULL,
        0 // Core 0
    );

    xTaskCreatePinnedToCore(
        commTaskCode,
        "CommTask",
        4096,
        NULL,
        1,
        NULL,
        1 // Core 1
    );
}

void loop() {
    if (Serial.available()) {
        String msg = Serial.readStringUntil('\n');
        if (msg.indexOf("\"OPEN_GATE\"") >= 0) {
            int timeout = 30;
            int idx = msg.indexOf("\"timeout\":");
            if (idx >= 0) {
                timeout = msg.substring(idx + 10).toInt();
                if (timeout <= 0) timeout = 30;
            }
            entranceGateTimeout = timeout;
            entranceGateRequested = true;
        }
    }
    vTaskDelay(pdMS_TO_TICKS(100));
}
