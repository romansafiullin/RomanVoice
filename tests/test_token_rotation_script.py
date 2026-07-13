from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_token_rotation_is_atomic_private_and_non_disclosing():
    script = (
        PROJECT_ROOT / "scripts" / "rotate-romanvoice-service-token.ps1"
    ).read_text(encoding="utf-8")

    assert "RandomNumberGenerator" in script
    assert "MoveFileEx" in script
    assert "$moveFileReplaceExisting" in script
    assert "$moveFileWriteThrough" in script
    assert "icacls.exe $Path /inheritance:r /grant:r" in script
    assert "S-1-5-18" in script
    assert "S-1-5-32-544" in script
    assert "Test-FixedTimeBytesEqual" in script
    assert "Get-TokenFingerprint" in script
    assert 'Write-Output "$token"' not in script
    assert "service token rotated. Fingerprint" in script
