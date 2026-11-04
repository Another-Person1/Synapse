import { X509Certificate } from "node:crypto";
import { rootCertificates } from "node:tls";

const policy = await Bun.file(new URL("./payload/tls-policy.json", import.meta.url)).json();
if (Bun.version !== policy.bun_version) {
  throw new Error(`Expected audited Bun ${policy.bun_version}; found ${Bun.version}`);
}

function audit(certificates: readonly string[], label: string) {
  if (certificates.length === 0) throw new Error(`${label}: empty certificate store`);
  for (const pem of certificates) {
    const cert = new X509Certificate(pem);
    const fingerprint = cert.fingerprint256.replaceAll(":", "").toLowerCase();
    if (policy.distrusted_roots[fingerprint]) {
      throw new Error(`${label}: still trusts ${policy.distrusted_roots[fingerprint]}`);
    }
  }
  console.log(`${label}: ${certificates.length} certificate entries checked`);
}

audit(rootCertificates, `Bun ${Bun.version} embedded trust`);
if (process.env.SSL_CERT_FILE) {
  const bundle = await Bun.file(process.env.SSL_CERT_FILE).text();
  audit(bundle.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g) ?? [], "Build CA bundle");
}
