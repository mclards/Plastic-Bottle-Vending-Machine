#pragma once

struct MachineConfig {
    int bin_full_threshold_cm = 15;
    int pet_nir_w_min = 30;
    int pet_nir_w_max = 220;
    int entrance_gate_timeout = 60;
    
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

    // Sensor Verification Requirements
    int require_nir_sensor = 1;      // 1 = Strict NIR polymer check required, 0 = Bench-test mode (servos only)
    int require_weight_sensor = 0;   // 1 = Strict HX711 weight check required, 0 = Bypassed (sensor optional)
    int min_bottle_weight_g = 10;    // Minimum weight for empty plastic bottle (grams)
    int max_bottle_weight_g = 65;    // Maximum weight for empty plastic bottle (grams; rejects glass >250g)
    int weight_cal_factor = 420;     // HX711 pulses-per-gram calibration factor
};

constexpr bool validMachineConfig(const MachineConfig& c) {
    return c.bin_full_threshold_cm >= 1 && c.bin_full_threshold_cm <= 400 &&
        c.pet_nir_w_min >= 0 && c.pet_nir_w_max > c.pet_nir_w_min && c.pet_nir_w_max <= 65535 &&
        c.entrance_gate_timeout >= 1 && c.entrance_gate_timeout <= 600 &&
        c.settle_time_ms >= 1 && c.settle_time_ms <= 30000 &&
        c.success_drop_tout_ms >= 1 && c.success_drop_tout_ms <= 30000 &&
        c.reject_drop_time_ms >= 1 && c.reject_drop_time_ms <= 30000 &&
        c.ent_open_angle >= 0 && c.ent_open_angle <= 180 && c.ent_close_angle >= 0 && c.ent_close_angle <= 180 &&
        c.suc_open_angle >= 0 && c.suc_open_angle <= 180 && c.suc_close_angle >= 0 && c.suc_close_angle <= 180 &&
        c.rej_open_angle >= 0 && c.rej_open_angle <= 180 && c.rej_close_angle >= 0 && c.rej_close_angle <= 180 &&
        c.require_nir_sensor >= 0 && c.require_nir_sensor <= 1 &&
        c.require_weight_sensor >= 0 && c.require_weight_sensor <= 1 &&
        c.min_bottle_weight_g >= 1 && c.max_bottle_weight_g > c.min_bottle_weight_g && c.max_bottle_weight_g <= 5000 &&
        c.weight_cal_factor >= 1 && c.weight_cal_factor <= 50000;
}

static_assert(validMachineConfig(MachineConfig{}), "Default hardware settings must be valid");
