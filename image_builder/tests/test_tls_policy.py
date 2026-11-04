import ssl
from pathlib import Path

import pytest

from image_builder.payload import tls_policy

ROOTS = Path(__file__).parent / "fixtures/tls-roots.pem"


def fixture_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(ROOTS))
    return context


def test_all_affected_roots_are_removed_and_unrelated_trust_is_retained():
    source = fixture_context()
    filtered = tls_policy.trusted_certificates(source)
    assert len(filtered) == 3
    assert "40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367" in {
        tls_policy.fingerprint(c) for c in filtered
    }


def test_tls_still_requires_valid_certificates_and_hostnames(monkeypatch):
    source = fixture_context()
    monkeypatch.setattr(ssl, "create_default_context", lambda: source)
    context = tls_policy.hardened_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version == source.minimum_version
    rejected = tls_policy.runtime_rejections()
    tls_policy.audit_certificates(
        context.get_ca_certs(binary_form=True), "filtered context", rejected
    )


def test_bundle_preserves_unaffected_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(ssl, "create_default_context", fixture_context)
    bundle = tmp_path / "certs.pem"
    tls_policy.write_bundle(bundle)
    certificates = tls_policy.pem_certificates(bundle.read_text())
    assert len(certificates) == 3
    tls_policy.audit_certificates(certificates, "bundle")


def test_audit_fails_on_an_affected_trust_store():
    with pytest.raises(RuntimeError, match="emSign ECC Root CA - G3"):
        tls_policy.audit_certificates(
            tls_policy.pem_certificates(ROOTS.read_text()), "old bundle"
        )


def test_empty_store_cannot_pass_an_audit_or_be_written(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="no trust anchors"):
        tls_policy.audit_certificates([], "empty")
    monkeypatch.setattr(tls_policy, "trusted_certificates", lambda: [])
    with pytest.raises(RuntimeError, match="empty trust bundle"):
        tls_policy.write_bundle(tmp_path / "empty.pem")


def test_python_bootstrap_keeps_verification_enabled(monkeypatch):
    commands = []
    monkeypatch.setattr(
        tls_policy.subprocess, "run", lambda args, **kwargs: commands.append(args)
    )
    tls_policy.bootstrap_python("/python")
    command = commands[0]
    assert "--cert" in command
    assert f"pip=={tls_policy.PIP_VERSION}" in command
    assert f"certifi=={tls_policy.CERTIFI_VERSION}" in command
    assert "--trusted-host" not in command
    assert "--use-deprecated=legacy-certs" in command
