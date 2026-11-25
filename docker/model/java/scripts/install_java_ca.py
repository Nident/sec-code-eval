"""Import the configured Python CA bundle into Java's separate trust store."""
import hashlib
from pathlib import Path
import re
import ssl
import subprocess
import sys


def certificates(text):
    return re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', text, re.S)


def fingerprint(pem):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def main():
    bundle = Path(sys.argv[1] if len(sys.argv) > 1 else '/etc/ssl/certs/corporate-ca-bundle.pem')
    current = subprocess.run(['keytool', '-list', '-rfc', '-cacerts', '-storepass', 'changeit'],
                             check=True, capture_output=True, text=True)
    known = {fingerprint(pem) for pem in certificates(current.stdout)}
    imported = 0
    import tempfile
    for pem in certificates(bundle.read_text()):
        digest = fingerprint(pem)
        if digest in known:
            continue
        with tempfile.NamedTemporaryFile(mode='w', suffix='.crt') as f:
            f.write(pem)
            f.flush()
            subprocess.run(['keytool', '-importcert', '-noprompt', '-trustcacerts', '-cacerts',
                            '-storepass', 'changeit', '-alias', 'secbench-' + digest,
                            '-file', f.name], check=True, capture_output=True)
        known.add(digest)
        imported += 1
    print(f'Java trust store: imported {imported} additional CA certificates')


if __name__ == '__main__':
    main()
