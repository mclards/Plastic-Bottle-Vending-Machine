// Compile with C++14: assertions execute at compile time without device hardware.
#include "../src/machine_config.h"
constexpr bool angle(int value) { MachineConfig c; c.ent_open_angle=value; return validMachineConfig(c); }
constexpr bool timeout(int value) { MachineConfig c; c.entrance_gate_timeout=value; return validMachineConfig(c); }
constexpr bool settle(int value) { MachineConfig c; c.settle_time_ms=value; return validMachineConfig(c); }
constexpr bool retrieval(int value) { MachineConfig c; c.retrieval_timeout_s=value; return validMachineConfig(c); }
constexpr bool nir(int low, int high) { MachineConfig c; c.pet_nir_w_min=low; c.pet_nir_w_max=high; return validMachineConfig(c); }
static_assert(angle(0) && angle(180) && !angle(-1) && !angle(181), "servo angle bounds");
static_assert(timeout(1) && timeout(600) && !timeout(0) && !timeout(601), "gate timeout bounds");
static_assert(settle(1) && settle(30000) && !settle(-1) && !settle(30001), "delay bounds");
static_assert(retrieval(5) && retrieval(300) && !retrieval(4) && !retrieval(301), "retrieval timeout bounds");
static_assert(nir(0,1) && !nir(1,1) && !nir(2,1) && !nir(0,65536), "NIR ordering and bounds");

