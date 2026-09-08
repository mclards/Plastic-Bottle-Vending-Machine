"""Generate the ESP32 release header from the repository VERSION file."""
from pathlib import Path
import re
Import("env")
version = (Path(env.subst("$PROJECT_DIR")) / "VERSION").read_text().strip()
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
    raise ValueError("VERSION must be major.minor.patch")
build_dir = Path(env.subst("$BUILD_DIR"))
build_dir.mkdir(parents=True, exist_ok=True)
(build_dir / "release_version.h").write_text('#pragma once\n#define ECOFI_VERSION "' + version + '"\n')
env.Append(CPPPATH=[str(build_dir)])
