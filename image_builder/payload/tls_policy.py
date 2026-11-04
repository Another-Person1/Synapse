"""Apply official Mozilla/Temurin eMudhra distrust without disabling TLS."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

# Fingerprints cross-checked against official Certifi 2026.07.22 and
# 2025.01.31 bundles. Adoptium confirms these two compromised trust paths.
# Unrelated emSign roots remain subject to the platform/vendor trust policy.
POLICY = json.loads(Path(__file__).with_name("tls-policy.json").read_text())
DISTRUSTED_ROOTS = POLICY["distrusted_roots"]
PIP_VERSION = POLICY["pip_version"]
CERTIFI_VERSION = POLICY["certifi_version"]


def fingerprint(certificate):
    return hashlib.sha256(certificate).hexdigest()


def pem_certificates(text):
    return [
        ssl.PEM_cert_to_DER_cert(cert)
        for cert in re.findall(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.S
        )
    ]


def trusted_certificates(context=None):
    source = context if context is not None else ssl.create_default_context()
    return [
        cert
        for cert in source.get_ca_certs(binary_form=True)
        if fingerprint(cert) not in DISTRUSTED_ROOTS
    ]


def hardened_context():
    source = ssl.create_default_context()
    certificates = trusted_certificates(source)
    if not certificates:
        raise RuntimeError("No usable trust anchors remain")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.options = source.options
    context.minimum_version = source.minimum_version
    context.maximum_version = source.maximum_version
    context.verify_flags = source.verify_flags
    context.load_verify_locations(
        cadata="".join(ssl.DER_cert_to_PEM_cert(c) for c in certificates)
    )
    return context


def write_bundle(destination):
    certificates = trusted_certificates()
    if not certificates:
        raise RuntimeError("Refusing to write an empty trust bundle")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(ssl.DER_cert_to_PEM_cert(c) for c in certificates))


def runtime_rejections():
    """Recognize this managed runtime's explicit OpenSSL reject trust entry.

    X509_STORE enumeration includes negative trust entries too. Require an
    actual OpenSSL rejection, not just a matching subject or filename.
    """
    rejected = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
    if not rejected.is_file() or not shutil.which("openssl"):
        return set()
    exported = subprocess.run(
        ["openssl", "x509", "-in", str(rejected), "-outform", "PEM"],
        check=True,
        capture_output=True,
        text=True,
    )
    certificates = pem_certificates(exported.stdout)
    if len(certificates) != 1:
        return set()
    identifier = fingerprint(certificates[0])
    preloaded = {
        fingerprint(c)
        for c in ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).get_ca_certs(binary_form=True)
    }
    if identifier not in DISTRUSTED_ROOTS or identifier not in preloaded:
        return set()
    with tempfile.TemporaryDirectory(prefix="synapse-rejection-") as temporary:
        certificate = Path(temporary) / "root.pem"
        certificate.write_text(exported.stdout)
        result = subprocess.run(
            [
                "openssl",
                "verify",
                "-purpose",
                "sslserver",
                "-trusted",
                str(rejected),
                str(certificate),
            ],
            capture_output=True,
            text=True,
        )
    return (
        {identifier}
        if result.returncode and "error 28 at 0 depth" in result.stderr + result.stdout
        else set()
    )


def audit_certificates(certificates, label, explicitly_rejected=frozenset()):
    blocked = sorted(
        {
            DISTRUSTED_ROOTS[fingerprint(c)]
            for c in certificates
            if fingerprint(c) in DISTRUSTED_ROOTS
            and fingerprint(c) not in explicitly_rejected
        }
    )
    if blocked:
        raise RuntimeError(
            f"{label} still trusts distrusted roots: {', '.join(blocked)}"
        )
    if not certificates:
        raise RuntimeError(f"{label} has no trust anchors")
    return sum(fingerprint(c) not in explicitly_rejected for c in certificates)


def audit_python(system=False):
    import certifi
    from pip._vendor import certifi as pip_certifi

    context = (
        ssl.create_default_context(
            cafile="/etc/ssl/certs/ca-certificates.crt", capath="/etc/ssl/certs"
        )
        if system
        else ssl.create_default_context()
    )
    rejections = runtime_rejections()
    return {
        "openssl": ssl.OPENSSL_VERSION,
        "pip": importlib.metadata.version("pip"),
        "certifi": certifi.__version__,
        "system_roots": audit_certificates(
            context.get_ca_certs(binary_form=True),
            "Python default trust",
            rejections,
        ),
        "explicit_runtime_rejections": sorted(rejections),
        "certifi_roots": audit_certificates(
            pem_certificates(Path(certifi.where()).read_text()), "Certifi"
        ),
        "pip_roots": audit_certificates(
            pem_certificates(Path(pip_certifi.where()).read_text()),
            "pip vendored Certifi",
        ),
    }


def bootstrap_python(python):
    # Older pip combines its bundled roots with system roots. During bootstrap,
    # explicitly select its documented PEM-only path and the filtered bundle.
    # Certificate and hostname verification remain enabled.
    with tempfile.TemporaryDirectory(prefix="synapse-tls-") as temporary:
        bundle = Path(temporary) / "ca-bundle.pem"
        write_bundle(bundle)
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "--cert",
                str(bundle),
                "--use-deprecated=legacy-certs",
                "install",
                "--upgrade",
                f"pip=={PIP_VERSION}",
                f"certifi=={CERTIFI_VERSION}",
            ],
            check=True,
        )


def distrust_debian():
    config = Path("/etc/ca-certificates.conf")
    lines = config.read_text().splitlines()
    for index, line in enumerate(lines):
        name = line.lstrip("!")
        if not name or name.startswith("#"):
            continue
        cert = Path("/usr/share/ca-certificates") / name
        if cert.is_file() and any(
            fingerprint(c) in DISTRUSTED_ROOTS
            for c in pem_certificates(cert.read_text())
        ):
            lines[index] = "!" + name
    config.write_text("\n".join(lines) + "\n")
    # Local custom roots are retained unless their exact DER fingerprint is one
    # of the distrusted public roots. Quarantine those instead of deleting them.
    for cert in Path("/usr/local/share/ca-certificates").rglob("*.crt"):
        if any(
            fingerprint(c) in DISTRUSTED_ROOTS
            for c in pem_certificates(cert.read_text())
        ):
            target = Path("/var/lib/synapse-image/distrusted-ca") / cert.name
            target.parent.mkdir(parents=True, exist_ok=True)
            cert.rename(target)
    subprocess.run(["update-ca-certificates", "--fresh"], check=True)
    audit_certificates(
        ssl.create_default_context(
            cafile="/etc/ssl/certs/ca-certificates.crt"
        ).get_ca_certs(binary_form=True),
        "Image system trust",
        runtime_rejections(),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("bundle", "bootstrap", "audit", "debian", "java")
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--github-env", action="store_true")
    parser.add_argument(
        "--system",
        action="store_true",
        help="Audit the image's OS CA bundle explicitly",
    )
    args = parser.parse_args()
    if args.command == "bundle":
        if not args.output:
            parser.error("bundle requires --output")
        write_bundle(args.output)
        if args.github_env:
            empty_directory = args.output.parent / "synapse-empty-ca-directory"
            empty_directory.mkdir(exist_ok=True)
            with open(os.environ["GITHUB_ENV"], "a") as stream:
                for name in (
                    "SSL_CERT_FILE",
                    "REQUESTS_CA_BUNDLE",
                    "CURL_CA_BUNDLE",
                    "PIP_CERT",
                    "GIT_SSL_CAINFO",
                    "CARGO_HTTP_CAINFO",
                ):
                    stream.write(f"{name}={args.output.resolve().as_posix()}\n")
                for name in ("SSL_CERT_DIR", "GIT_SSL_CAPATH"):
                    stream.write(f"{name}={empty_directory.resolve().as_posix()}\n")
    elif args.command == "bootstrap":
        bootstrap_python(args.python)
    elif args.command == "audit":
        print(json.dumps(audit_python(args.system), indent=2))
    elif args.command == "debian":
        distrust_debian()
    else:
        result = subprocess.run(
            ["keytool", "-list", "-cacerts", "-storepass", "changeit", "-rfc"],
            check=True,
            capture_output=True,
            text=True,
        )
        print(
            "JDK trust anchors checked:",
            audit_certificates(pem_certificates(result.stdout), "JDK"),
        )


if __name__ == "__main__":
    main()
