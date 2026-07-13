from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_firewall_remediation_is_scoped_backed_up_and_reversible():
    script = (
        PROJECT_ROOT / "scripts" / "configure-romanvoice-firewall.ps1"
    ).read_text(encoding="utf-8")

    assert "[ValidateSet('Audit', 'Apply')]" in script
    assert "advfirewall export" in script
    assert "-LocalPort 8799" in script
    assert "-RemoteAddress '100.64.0.0/10'" in script
    assert "-InterfaceAlias $TailscaleInterfaceAlias" in script
    assert "-Profile Private" in script
    assert "Get-BroadPythonPublicAllowRules" in script
    assert "function Disable-PersistentFirewallRules" in script
    assert "-PolicyStore PersistentStore" in script
    assert "Disable-PersistentFirewallRules -Rules $broadRules" in script
    assert "Disable-PersistentFirewallRules -Rules $genericRules" in script
    assert "advfirewall import" in script
    assert "Remove-NetFirewallRule" in script
    assert "Python314" not in script
