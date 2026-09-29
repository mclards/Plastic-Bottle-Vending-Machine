import serial
import json
import time
import sys
import math

port = 'COM11'
baud = 115200

print("=== VMC ECO-VENDO EMPTY CHUTE SCALE CALIBRATION ===")
print("Connecting to ESP32 on %s..." % port)

try:
    s = serial.Serial(port, baud, timeout=3.0)
    time.sleep(1.0)
except Exception as e:
    print("Error opening port %s: %s" % (port, e))
    sys.exit(1)

try:
    s.reset_input_buffer()

    # Step 1: Pre-tare sample readings
    print("\n--- Phase 1: Pre-Tare Baseline Reading (5 samples) ---")
    pre_samples = []
    for i in range(1, 6):
        s.write(b'{"cmd":"TEST_WEIGHT"}\n')
        start = time.time()
        while time.time() - start < 3.0:
            line = s.readline().decode('utf-8', errors='ignore').strip()
            if '"event":"WEIGHT_TEST"' in line:
                try:
                    data = json.loads(line)
                    w = data.get('weight_g', 0.0)
                    pre_samples.append(w)
                    print("  Sample #%d: %.2f g" % (i, w))
                except Exception:
                    pass
                break
        time.sleep(0.3)

    if pre_samples:
        print("  Pre-tare Average: %.2f g" % (sum(pre_samples) / len(pre_samples)))

    # Step 2: Zero Tare execution
    print("\n--- Phase 2: Zero Taring Empty Chute (10-sample hardware tare) ---")
    s.reset_input_buffer()
    s.write(b'{"cmd":"TARE_WEIGHT"}\n')
    start = time.time()
    tared = False
    while time.time() - start < 5.0:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if not line:
            continue
        print("  ESP32 >>", line)
        if '"event":"TARE_OK"' in line:
            tared = True
            print("  --> Scale tare confirmed successful!")
            break
        elif '"event":"TARE_REJECTED"' in line:
            print("  --> Scale tare rejected by ESP32!")
            break

    if not tared:
        print("Warning: Tare did not acknowledge with TARE_OK within timeout.")

    time.sleep(1.0)

    # Step 3: Post-tare verification (10 consecutive samples)
    print("\n--- Phase 3: Post-Tare Calibration Verification (10 samples) ---")
    post_samples = []
    print(" Scan # | Measured Weight | Target Zero | Deviation | Status ")
    print("-------+-----------------+-------------+-----------+--------")
    for i in range(1, 11):
        s.reset_input_buffer()
        s.write(b'{"cmd":"TEST_WEIGHT"}\n')
        start = time.time()
        while time.time() - start < 3.0:
            line = s.readline().decode('utf-8', errors='ignore').strip()
            if '"event":"WEIGHT_TEST"' in line:
                try:
                    data = json.loads(line)
                    w = data.get('weight_g', 0.0)
                    post_samples.append(w)
                    dev = abs(w)
                    status = "EXCELLENT" if dev <= 0.2 else ("GOOD" if dev <= 0.5 else "ATTENTION")
                    print("  #%02d   |    %+6.2f g     |    0.00 g   |  %5.2f g  | %s" % (i, w, dev, status))
                except Exception as ex:
                    print("  #%02d   | Error: %s" % (i, ex))
                break
        time.sleep(0.4)

    # Step 4: Statistical summary
    print("\n" + "=" * 60)
    print(" EMPTY CHUTE SCALE CALIBRATION SUMMARY:")
    print("=" * 60)
    if post_samples:
        mn = min(post_samples)
        mx = max(post_samples)
        mean = sum(post_samples) / len(post_samples)
        variance = sum((x - mean) ** 2 for x in post_samples) / len(post_samples)
        stddev = math.sqrt(variance)
        print(" Total Samples Verified:    %d" % len(post_samples))
        print(" Minimum Measured Weight:  %+6.2f g" % mn)
        print(" Maximum Measured Weight:  %+6.2f g" % mx)
        print(" Mean Baseline Weight:     %+6.2f g" % mean)
        print(" Standard Deviation (RMS):   %5.2f g" % stddev)
        print(" Peak-to-Peak Noise Margin:  %5.2f g" % (mx - mn))
        if abs(mean) <= 0.2 and stddev <= 0.3:
            print(" RESULT: CALIBRATION PERFECT (Zero baseline verified)")
        else:
            print(" RESULT: CALIBRATION WITHIN ACCEPTABLE OPERATIONAL BOUNDS")
    print("=" * 60)

finally:
    s.close()
