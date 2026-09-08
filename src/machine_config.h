#pragma once

struct MachineConfig {
    int bin_full_threshold_cm = 15;
    int pet_nir_w_min = 200;
    int pet_nir_w_max = 5000;
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
        c.rej_open_angle >= 0 && c.rej_open_angle <= 180 && c.rej_close_angle >= 0 && c.rej_close_angle <= 180;
}

static_assert(validMachineConfig(MachineConfig{}), "Default hardware settings must be valid");
