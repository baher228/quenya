#!/usr/bin/env python3

import base64
import binascii
import datetime as dt
import json
import os
import plistlib
import re
import secrets
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization


BASE_URL = "https://api.appstoreconnect.apple.com"
WWDR_URL = "https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer"
PROFILE_NAME = "Quenya App Store"
BUNDLE_ID_RESOURCE = "3XX3V34YQT"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
KEYCHAIN_NAME = "quenya-signing.keychain-db"
KEYCHAIN_PASSWORD_NAME = "keychain-password"


class SetupFailure(Exception):
    pass


class APIError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"App Store Connect returned HTTP {status}")


def required_environment(name):
    value = os.environ.get(name)
    if not value:
        raise SetupFailure(f"{name} is required")
    return value


def write_private_file(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
    path.chmod(0o600)


def run_command(arguments, *, redact=False, input_data=None):
    try:
        result = subprocess.run(
            arguments,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        raise SetupFailure(f"{Path(arguments[0]).name} could not be started") from None
    if result.returncode:
        if redact:
            detail = f"{Path(arguments[0]).name} failed (exit {result.returncode})"
        else:
            detail = result.stderr.decode("utf-8", "replace").strip()
            if not detail:
                detail = f"{Path(arguments[0]).name} failed (exit {result.returncode})"
        raise SetupFailure(detail)
    return result.stdout


def load_der_certificate(content):
    try:
        return x509.load_der_x509_certificate(base64.b64decode(content))
    except (binascii.Error, ValueError, TypeError):
        raise SetupFailure("App Store Connect returned an invalid certificate") from None


def certificate_expiry(attributes, certificate):
    value = attributes.get("expirationDate")
    if value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise SetupFailure("App Store Connect returned an invalid certificate expiry") from None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    return certificate.not_valid_after_utc


def public_key_bytes(key):
    public_key = key.public_key() if callable(getattr(key, "public_key", None)) else key
    return public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


class ASCClient:
    def __init__(self):
        self.key_id = required_environment("ASC_KEY_ID")
        self.issuer_id = required_environment("ASC_ISSUER_ID")
        key_path = Path(required_environment("ASC_KEY_PATH")).expanduser()
        try:
            self.private_key = key_path.read_bytes()
            serialization.load_pem_private_key(self.private_key, password=None)
        except (OSError, ValueError, TypeError):
            raise SetupFailure("ASC_KEY_PATH could not be loaded") from None

    def _token(self):
        now = int(time.time())
        return jwt.encode(
            {
                "iss": self.issuer_id,
                "iat": now,
                "exp": now + 15 * 60,
                "aud": "appstoreconnect-v1",
            },
            self.private_key,
            algorithm="ES256",
            headers={"kid": self.key_id, "typ": "JWT"},
        )

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            BASE_URL + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            raise APIError(error.code, raw.decode("utf-8", "replace")) from None
        except urllib.error.URLError:
            raise SetupFailure("App Store Connect request failed") from None
        if status >= 400:
            raise APIError(status, raw.decode("utf-8", "replace"))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise SetupFailure("App Store Connect returned invalid JSON") from None


def signing_directory():
    configured = os.environ.get("QUENYA_SIGNING_DIR", "~/ios-work/signing")
    directory = Path(configured).expanduser().resolve()
    if directory == REPOSITORY_ROOT or REPOSITORY_ROOT in directory.parents:
        raise SetupFailure("QUENYA_SIGNING_DIR must be outside the repository")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def private_key(directory):
    key_path = directory / "dist.key"
    if not key_path.exists():
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                result = subprocess.run(
                    ["openssl", "genrsa", "2048"],
                    stdout=output,
                    stderr=subprocess.PIPE,
                    check=False,
                )
        except OSError:
            key_path.unlink(missing_ok=True)
            raise SetupFailure("openssl could not generate the distribution key") from None
        if result.returncode:
            key_path.unlink(missing_ok=True)
            raise SetupFailure("openssl could not generate the distribution key")
    key_path.chmod(0o600)
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        raise SetupFailure("The distribution key could not be loaded") from None
    return key_path, key


def certificate_records(client, key_path, private):
    query = urllib.parse.urlencode(
        [("filter[certificateType]", "DISTRIBUTION"), ("limit", "200")]
    )
    response = client.request("GET", f"/v1/certificates?{query}")
    expected_public_key = public_key_bytes(private)
    now = dt.datetime.now(dt.timezone.utc)
    for record in response.get("data", []):
        attributes = record.get("attributes", {})
        content = attributes.get("certificateContent")
        if not content:
            continue
        certificate = load_der_certificate(content)
        expiry = certificate_expiry(attributes, certificate)
        if (
            expiry > now
            and public_key_bytes(certificate.public_key()) == expected_public_key
        ):
            return record, certificate, "reused"
    csr = run_command(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(key_path),
            "-subj",
            "/CN=Quenya Distribution/C=GB",
        ]
    ).decode("utf-8")
    response = client.request(
        "POST",
        "/v1/certificates",
        {
            "data": {
                "type": "certificates",
                "attributes": {
                    "certificateType": "DISTRIBUTION",
                    "csrContent": csr,
                },
            }
        },
    )
    record = response.get("data")
    if not record:
        raise SetupFailure("App Store Connect did not return a distribution certificate")
    attributes = record.get("attributes", {})
    certificate = load_der_certificate(attributes.get("certificateContent", ""))
    if public_key_bytes(certificate.public_key()) != expected_public_key:
        raise SetupFailure("The created certificate does not match the distribution key")
    return record, certificate, "created"


def profile_certificate_ids(profile):
    data = (
        profile.get("relationships", {})
        .get("certificates", {})
        .get("data", [])
    )
    return {item.get("id") for item in data if item.get("id")}


def profiles_for_certificate(client, certificate_id):
    query = urllib.parse.urlencode(
        [
            ("filter[name]", PROFILE_NAME),
            ("filter[profileType]", "IOS_APP_STORE"),
            ("include", "certificates"),
        ]
    )
    response = client.request("GET", f"/v1/profiles?{query}")
    profiles = response.get("data", [])
    reusable = next(
        (
            profile
            for profile in profiles
            if profile.get("attributes", {}).get("profileState") == "ACTIVE"
            and certificate_id in profile_certificate_ids(profile)
        ),
        None,
    )
    if reusable:
        return reusable, "reused"
    if profiles:
        client.request("DELETE", f"/v1/profiles/{profiles[0]['id']}")
    response = client.request(
        "POST",
        "/v1/profiles",
        {
            "data": {
                "type": "profiles",
                "attributes": {
                    "name": PROFILE_NAME,
                    "profileType": "IOS_APP_STORE",
                },
                "relationships": {
                    "bundleId": {
                        "data": {
                            "type": "bundleIds",
                            "id": BUNDLE_ID_RESOURCE,
                        }
                    },
                    "certificates": {
                        "data": [
                            {
                                "type": "certificates",
                                "id": certificate_id,
                            }
                        ]
                    },
                },
            }
        },
    )
    profile = response.get("data")
    if not profile:
        raise SetupFailure("App Store Connect did not return a provisioning profile")
    return profile, "created"


def decode_and_install_profile(profile, directory):
    attributes = profile.get("attributes", {})
    content = attributes.get("profileContent")
    if not content:
        raise SetupFailure("The provisioning profile response has no profileContent")
    profile_path = directory / "profile.mobileprovision"
    try:
        profile_bytes = base64.b64decode(content)
    except (binascii.Error, ValueError, TypeError):
        raise SetupFailure("The provisioning profile content is invalid") from None
    write_private_file(profile_path, profile_bytes)
    decoded = run_command(
        ["security", "cms", "-D", "-i", str(profile_path)]
    )
    try:
        profile_data = plistlib.loads(decoded)
        profile_uuid = str(uuid.UUID(profile_data["UUID"]))
    except (KeyError, ValueError, plistlib.InvalidFileException, TypeError):
        raise SetupFailure("The provisioning profile did not contain a valid UUID") from None
    name = profile_data.get("Name", attributes.get("name", PROFILE_NAME))
    profile_name = attributes.get("name", name)
    for path in (
        Path.home() / "Library/MobileDevice/Provisioning Profiles",
        Path.home() / "Library/Developer/Xcode/UserData/Provisioning Profiles",
    ):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private_file(path / f"{profile_uuid}.mobileprovision", profile_path.read_bytes())
    return profile_uuid, profile_name


def keychain_password(directory, keychain):
    password_path = directory / KEYCHAIN_PASSWORD_NAME
    if password_path.exists():
        password = password_path.read_text(encoding="utf-8")
        if not password:
            raise SetupFailure("The keychain password file is empty")
        password_path.chmod(0o600)
        return password
    if keychain.exists():
        raise SetupFailure("The signing keychain exists but its password file is missing")
    password = secrets.token_urlsafe(48)
    write_private_file(password_path, password.encode("utf-8"))
    return password


def fingerprint(certificate):
    return certificate.fingerprint(hashes.SHA1()).hex().upper()


def keychain_fingerprints(keychain):
    try:
        output = run_command(
            ["security", "find-certificate", "-a", "-Z", str(keychain)],
            redact=True,
        ).decode("utf-8", "replace")
    except SetupFailure:
        return set()
    return {
        re.sub(r"[^A-F0-9]", "", value.upper())
        for value in re.findall(r"SHA-1 hash:\s*([A-F0-9:]+)", output, re.IGNORECASE)
    }


def wwdr_certificate(directory):
    der_path = directory / "AppleWWDRCAG3.cer"
    pem_path = directory / "AppleWWDRCAG3.pem"
    if not der_path.exists():
        try:
            with urllib.request.urlopen(WWDR_URL, timeout=60) as response:
                content = response.read()
        except (urllib.error.URLError, TimeoutError):
            raise SetupFailure("The Apple WWDR G3 certificate could not be downloaded") from None
        write_private_file(der_path, content)
    content = der_path.read_bytes()
    try:
        certificate = x509.load_der_x509_certificate(content)
    except ValueError:
        try:
            certificate = x509.load_pem_x509_certificate(content)
        except ValueError:
            raise SetupFailure("The Apple WWDR G3 certificate is invalid") from None
    write_private_file(pem_path, certificate.public_bytes(serialization.Encoding.PEM))
    return der_path, pem_path, certificate


def import_identity(directory, key_path, certificate, keychain):
    identity_fingerprint = fingerprint(certificate)
    try:
        identities = run_command(
            ["security", "find-identity", "-v", "-p", "codesigning", str(keychain)],
            redact=True,
        ).decode("utf-8", "replace")
    except SetupFailure:
        identities = ""
    normalized_identities = re.sub(r"[^A-F0-9]", "", identities.upper())
    if identity_fingerprint not in normalized_identities:
        _, wwdr_pem, _ = wwdr_certificate(directory)
        cert_path = directory / "dist.crt.pem"
        write_private_file(
            cert_path,
            certificate.public_bytes(serialization.Encoding.PEM),
        )
        p12_path = directory / "distribution.p12"
        p12_password_path = directory / "distribution-p12-password"
        p12_password = secrets.token_urlsafe(48)
        write_private_file(p12_password_path, p12_password.encode("utf-8"))
        try:
            run_command(
                [
                    "openssl",
                    "pkcs12",
                    "-export",
                    "-legacy",
                    "-inkey",
                    str(key_path),
                    "-in",
                    str(cert_path),
                    "-certfile",
                    str(wwdr_pem),
                    "-out",
                    str(p12_path),
                    "-passout",
                    f"file:{p12_password_path}",
                ],
                redact=True,
            )
            p12_path.chmod(0o600)
            run_command(
                [
                    "security",
                    "import",
                    str(p12_path),
                    "-k",
                    str(keychain),
                    "-P",
                    p12_password,
                    "-T",
                    "/usr/bin/codesign",
                    "-T",
                    "/usr/bin/security",
                ],
                redact=True,
            )
        finally:
            p12_path.unlink(missing_ok=True)
            p12_password_path.unlink(missing_ok=True)
            cert_path.unlink(missing_ok=True)


def configure_keychain(directory, key_path, certificate):
    keychain = directory / KEYCHAIN_NAME
    password = keychain_password(directory, keychain)
    if not keychain.exists():
        run_command(
            ["security", "create-keychain", "-p", password, str(keychain)],
            redact=True,
        )
    run_command(
        ["security", "set-keychain-settings", "-lut", "21600", str(keychain)],
        redact=True,
    )
    run_command(
        ["security", "unlock-keychain", "-p", password, str(keychain)],
        redact=True,
    )
    existing = shlex.split(
        run_command(["security", "list-keychains", "-d", "user"]).decode(
            "utf-8", "replace"
        )
    )
    resolved_keychain = str(keychain.resolve())
    if resolved_keychain not in {
        str(Path(item).expanduser().resolve()) for item in existing
    }:
        run_command(
            ["security", "list-keychains", "-d", "user", "-s", *existing, resolved_keychain],
            redact=True,
        )
    der_path, _, wwdr = wwdr_certificate(directory)
    if fingerprint(wwdr) not in keychain_fingerprints(keychain):
        run_command(
            [
                "security",
                "import",
                str(der_path),
                "-k",
                str(keychain),
                "-T",
                "/usr/bin/codesign",
                "-T",
                "/usr/bin/security",
            ],
            redact=True,
        )
    import_identity(directory, key_path, certificate, keychain)
    run_command(
        [
            "security",
            "set-key-partition-list",
            "-S",
            "apple-tool:,apple:",
            "-k",
            password,
            str(keychain),
        ],
        redact=True,
    )
    return keychain.resolve()


def main():
    try:
        directory = signing_directory()
        key_path, key = private_key(directory)
        client = ASCClient()
        certificate_record, certificate, certificate_status = certificate_records(
            client, key_path, key
        )
        certificate_attributes = certificate_record.get("attributes", {})
        certificate_id = certificate_record["id"]
        expiry = certificate_expiry(certificate_attributes, certificate)
        serial = certificate_attributes.get("serialNumber") or format(
            certificate.serial_number, "X"
        )
        profile, profile_status = profiles_for_certificate(client, certificate_id)
        profile_uuid, profile_name = decode_and_install_profile(profile, directory)
        keychain = configure_keychain(directory, key_path, certificate)
        print(
            f"certificate id={certificate_id} serial={serial} "
            f"expiry={expiry.isoformat()} ({certificate_status})"
        )
        print(
            f"profile id={profile['id']} UUID={profile_uuid} "
            f"name={profile_name} ({profile_status})"
        )
        print(f"keychain path={keychain}")
        return 0
    except APIError as error:
        sys.stderr.write(f"App Store Connect HTTP {error.status}\n")
        sys.stderr.write(error.body)
        if error.body and not error.body.endswith("\n"):
            sys.stderr.write("\n")
        return 1
    except SetupFailure as error:
        sys.stderr.write(f"Signing setup failed: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
