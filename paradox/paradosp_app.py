"""Single-file PEX entrypoint for running PAI on an Orange Pi."""

import os
from pathlib import Path
import platform
import sys
from typing import List, Optional


DEFAULT_CONFIG_TEXT = (
    'SAFELINK_CRAWL_URL = "https://safelinksystem.app/core/webhook/ultrasync/crawl"\n'
)


def prepare_arguments(arguments: List[str], home: Optional[Path] = None) -> List[str]:
    """Create a minimal writable config when no explicit config was supplied."""
    if "-c" in arguments or "--config" in arguments:
        return list(arguments)

    config_dir = (home or Path.home()) / ".pai"
    config_file = config_dir / "paradosp.conf"
    config_dir.mkdir(parents=True, exist_ok=True)
    if not config_file.exists():
        config_file.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
        os.chmod(config_file, 0o666)

    return [*arguments, "--config", str(config_file)]


def run_self_test() -> int:
    """Check that the PEX contains the runtime modules used on the Orange Pi."""
    import requests  # noqa: F401
    import serial  # noqa: F401
    import serial_asyncio  # noqa: F401

    from paradox.connections.serial.connection import SerialCommunication  # noqa: F401
    from paradox.hardware.spectra_magellan import Panel  # noqa: F401
    from paradox.interfaces.safelink import SafeLinkInterface  # noqa: F401
    from paradox.lib.encodings import register_encodings

    register_encodings()
    "Paradox SP4000".encode("paradox-en")
    print(
        "paradosp self-test OK: platform={} arch={} python={}".format(
            platform.system(), platform.machine(), platform.python_version()
        )
    )
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return run_self_test()

    sys.argv[1:] = prepare_arguments(sys.argv[1:])
    from paradox.console_scripts.pai_run import main as pai_main

    pai_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
