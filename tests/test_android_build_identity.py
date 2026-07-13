from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANDROID_ROOT = PROJECT_ROOT / "clients" / "android-ime"


def _version_properties() -> dict[str, str]:
    values = {}
    for line in (ANDROID_ROOT / "version.properties").read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        values[key] = value
    return values


def test_android_builds_share_single_version_metadata_source():
    values = _version_properties()
    gradle = (ANDROID_ROOT / "app" / "build.gradle").read_text(encoding="utf-8")
    manual = (ANDROID_ROOT / "build-debug-apk.ps1").read_text(encoding="utf-8")

    assert int(values["versionCode"]) > 1
    assert values["versionName"] != "0.1.0"
    assert 'file("../version.properties")' in gradle
    assert "version.properties" in manual
    assert 'versionCode 1' not in gradle
    assert '"--version-code", "1"' not in manual
    assert 'versionNameSuffix "-debug"' in gradle
    assert '"--debug-mode"' in manual

    workflow = (PROJECT_ROOT / ".github" / "workflows" / "romanvoice-ci.yml").read_text(
        encoding="utf-8"
    )
    assert "clients\\android-ime\\version.properties" in workflow
    assert '"platforms;android-$($metadata.compileSdk)"' in workflow
    assert '"build-tools;$($metadata.buildToolsVersion)"' in workflow


def test_manifest_does_not_force_every_build_to_be_debuggable():
    manifest = (
        ANDROID_ROOT / "app" / "src" / "main" / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")

    assert "android:debuggable" not in manifest


def test_settings_displays_installed_version_identity():
    settings = (
        ANDROID_ROOT
        / "app"
        / "src"
        / "main"
        / "java"
        / "app"
        / "romanvoice"
        / "ime"
        / "SettingsActivity.java"
    ).read_text(encoding="utf-8")

    assert "getLongVersionCode()" in settings
    assert '"Installed build: "' in settings
