import os
import time
import logging

log = logging.getLogger(__name__)

# OPi One: Pin 7 -> PA6 -> Linux GPIO 6
# Used for the 12V Cabinet Marquee Strips (Red & Green) via 1-Channel Relay Module
# Default Low (0) = NC (Red / Not Ready). High (1) = NO (Green / Ready).
GPIO_PIN = 6

def _write_file(path, content):
    try:
        with open(path, 'w') as f:
            f.write(str(content))
    except Exception as e:
        # Silently fail if not on real hardware, lacks permissions, or simulator
        pass

def init_gpio():
    # Export the GPIO if not already exported
    if not os.path.exists('/sys/class/gpio/gpio{}/value'.format(GPIO_PIN)):
        _write_file('/sys/class/gpio/export', GPIO_PIN)
        time.sleep(0.1) # Wait for udev / sysfs to settle
    
    # Set as output
    _write_file('/sys/class/gpio/gpio{}/direction'.format(GPIO_PIN), 'out')
    
    # Initialize to OFF
    set_led_state(False)

def set_led_state(is_on):
    value = '1' if is_on else '0'
    _write_file('/sys/class/gpio/gpio{}/value'.format(GPIO_PIN), value)

def start_marquee():
    """Called when the system is online and ready"""
    try:
        init_gpio()
        set_led_state(True)
    except Exception as e:
        log.warning("Failed to start marquee: {}".format(e))
    
def stop_marquee():
    """Called on shutdown"""
    try:
        set_led_state(False)
    except Exception as e:
        log.warning("Failed to stop marquee: {}".format(e))

