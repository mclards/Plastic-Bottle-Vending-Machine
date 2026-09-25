"""Run full unit test suite directly on live Orange Pi."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.opi_access import connect, execute

def main():
    print("Connecting to live OPi to execute test suite...")
    client = connect()
    try:
        cmd = "cd /opt/ecofi && python3 -B -m unittest discover -s . -p 'test_*.py'"
        print("Executing: {}".format(cmd))
        code, out, err = execute(client, cmd, timeout=180)
        print("Exit code:", code)
        print("STDOUT:\n", out)
        print("STDERR:\n", err)
    finally:
        client.close()

if __name__ == '__main__':
    main()
