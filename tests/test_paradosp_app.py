import os
import stat

from paradox import paradosp_app


def test_prepare_arguments_creates_default_config(tmp_path):
    arguments = paradosp_app.prepare_arguments([], home=tmp_path)
    config_file = tmp_path / ".pai" / "paradosp.conf"

    assert arguments == ["--config", str(config_file)]
    assert config_file.read_text("utf-8") == paradosp_app.DEFAULT_CONFIG_TEXT
    if os.name != "nt":
        assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


def test_prepare_arguments_preserves_explicit_short_config(tmp_path):
    assert paradosp_app.prepare_arguments(
        ["-c", "/opt/paradosp/paradosp.conf"], home=tmp_path
    ) == ["-c", "/opt/paradosp/paradosp.conf"]


def test_prepare_arguments_preserves_explicit_long_config(tmp_path):
    assert paradosp_app.prepare_arguments(
        ["--config", "/opt/paradosp/paradosp.conf"], home=tmp_path
    ) == ["--config", "/opt/paradosp/paradosp.conf"]


def test_prepare_arguments_does_not_overwrite_existing_config(tmp_path):
    config_file = tmp_path / ".pai" / "paradosp.conf"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("CUSTOM = True\n", encoding="utf-8")

    paradosp_app.prepare_arguments([], home=tmp_path)

    assert config_file.read_text("utf-8") == "CUSTOM = True\n"
