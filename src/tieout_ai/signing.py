"""Signed confirmations: only a person holding the confirmer's key can confirm.

`tieout keys init` creates an Ed25519 keypair. The private key is encrypted with a
passphrase the person chooses and lives outside the workspace, in the user
profile (`~/.tieout/keys`, i.e. `%USERPROFILE%\\.tieout\\keys` on Windows, or
$TIEOUT_KEYS_DIR). The key records the name of its owner. It is deliberately not
under %APPDATA%: Windows redirects a packaged app's writes there to a private
copy, so the Claude desktop app's MCP server and a person's own terminal could
see two different keys.

`tieout confirm` asks for the passphrase and signs the assumption's ID, a hash of
its content, the confirmer's name and the time. `certify` verifies every
confirmation against the public key: one that is unsigned, signed by another
key, has a bad signature, or whose assumption changed after it was signed,
counts as unconfirmed. So writing `"confirmed": true` into the store by hand
achieves nothing.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tieout_ai.hashing import canonical_json, sha256_hex
from tieout_ai.models import Assumption

PRIVATE_FILE = "confirmer_ed25519.key"
PUBLIC_FILE = "confirmer_ed25519.pub"
OWNER_FILE = "confirmer.json"
MIN_PASSPHRASE = 8

# The parts of an assumption a confirmation vouches for. Editing any of them
# after confirming invalidates the signature.
_CONTENT_FIELDS = (
    "id",
    "description",
    "parameter",
    "value",
    "scope",
    "elevated",
    "elevated_reason",
    "proposed_by",
)


class SigningError(Exception):
    """No key, a wrong passphrase, or a key that cannot be created."""


def keys_dir() -> Path:
    override = os.environ.get("TIEOUT_KEYS_DIR")
    if override:
        return Path(override)
    migrate_legacy_keys()
    return Path.home() / ".tieout" / "keys"


def migrate_legacy_keys() -> Path | None:
    """Copy a key from ~/.recon/keys (the earlier name) to ~/.tieout/keys, once.

    Only for the default location, only when there is no key at the new one yet, and
    only a complete key whose copy has the same fingerprint. The original is left in
    place. Returns the new folder if a key was copied, otherwise None.
    """
    if os.environ.get("TIEOUT_KEYS_DIR"):
        return None
    old, new = Path.home() / ".recon" / "keys", Path.home() / ".tieout" / "keys"
    files = (PRIVATE_FILE, PUBLIC_FILE, OWNER_FILE)
    if (new / PRIVATE_FILE).exists() or not all((old / name).is_file() for name in files):
        return None
    new.mkdir(parents=True, exist_ok=True)
    _write_private(new / PRIVATE_FILE, (old / PRIVATE_FILE).read_bytes())
    for name in (PUBLIC_FILE, OWNER_FILE):
        (new / name).write_bytes((old / name).read_bytes())
    copied, original = load_trusted_key(new), load_trusted_key(old)
    if copied is None or original is None or copied.fingerprint != original.fingerprint:
        raise SigningError(f"Could not copy the confirmer key from {old} to {new}")
    return new


def fingerprint(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return "SHA256:" + sha256_hex(raw)[:32]


def content_hash(assumption: Assumption) -> str:
    data = assumption.model_dump(mode="json")
    return sha256_hex(canonical_json({k: data[k] for k in _CONTENT_FIELDS}))


def _payload(assumption_id: str, digest: str, confirmed_by: str, confirmed_at: str) -> bytes:
    return canonical_json(
        {
            "assumption_id": assumption_id,
            "content_hash": digest,
            "confirmed_by": confirmed_by,
            "confirmed_at": confirmed_at,
        }
    )


def _record_payload(kind: str, record: Any, signed_by: str, signed_at: str) -> bytes:
    return canonical_json(
        {"kind": kind, "record": record, "signed_by": signed_by, "signed_at": signed_at}
    )


@dataclass(frozen=True)
class TrustedKey:
    """The public half: what certify checks confirmations against."""

    public_key: Ed25519PublicKey
    owner: str

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.public_key)

    def problem(self, assumption: Assumption) -> str | None:
        """Why a confirmed assumption's confirmation cannot be trusted, or None if it can."""
        if not assumption.confirmed:
            return None
        if not assumption.signature or not assumption.confirmed_content_hash:
            return "the confirmation is not signed"
        if assumption.signing_key_fingerprint != self.fingerprint:
            return (
                f"signed with key {assumption.signing_key_fingerprint}, not this machine's "
                f"confirmer key {self.fingerprint}"
            )
        if content_hash(assumption) != assumption.confirmed_content_hash:
            return "the assumption was changed after it was confirmed"
        when = assumption.confirmed_at.isoformat() if assumption.confirmed_at else ""
        payload = _payload(
            assumption.id, assumption.confirmed_content_hash, assumption.confirmed_by or "", when
        )
        try:
            self.public_key.verify(base64.b64decode(assumption.signature), payload)
        except (InvalidSignature, ValueError):
            return "the signature does not match"
        return None

    def record_problem(
        self, kind: str, record: Any, signature: dict[str, Any] | None
    ) -> str | None:
        """Why a signed record (e.g. a connection) cannot be trusted, or None if it can."""
        if not signature:
            return "is not signed"
        if signature.get("fingerprint") != self.fingerprint:
            return (
                f"is signed with key {signature.get('fingerprint')}, not this machine's "
                f"confirmer key {self.fingerprint}"
            )
        payload = _record_payload(kind, record, signature.get("by", ""), signature.get("at", ""))
        try:
            self.public_key.verify(base64.b64decode(signature.get("value", "")), payload)
        except (InvalidSignature, ValueError):
            return "was changed after a person signed it"
        return None


@dataclass(frozen=True)
class Signer:
    """The private half, unlocked with the passphrase for one confirmation."""

    private_key: Ed25519PrivateKey
    owner: str

    def __post_init__(self) -> None:
        if not self.owner.strip():
            raise SigningError("A confirmer key needs the name of the person it belongs to")

    @classmethod
    def generate(cls, owner: str) -> Signer:
        return cls(Ed25519PrivateKey.generate(), owner)

    @property
    def trusted(self) -> TrustedKey:
        return TrustedKey(self.private_key.public_key(), self.owner)

    def sign_record(self, kind: str, record: Any, at: datetime | None = None) -> dict[str, str]:
        """A signature over any JSON record, by this key's owner."""
        when = (at or datetime.now(UTC)).astimezone(UTC).isoformat()
        value = self.private_key.sign(_record_payload(kind, record, self.owner, when))
        return {
            "by": self.owner,
            "at": when,
            "fingerprint": self.trusted.fingerprint,
            "value": base64.b64encode(value).decode("ascii"),
        }

    def sign(self, assumption: Assumption, at: datetime | None = None) -> Assumption:
        """The assumption, confirmed and signed by this key's owner."""
        when = (at or datetime.now(UTC)).astimezone(UTC)
        confirmed = assumption.model_copy(
            update={"confirmed": True, "confirmed_by": self.owner, "confirmed_at": when}
        )
        digest = content_hash(confirmed)
        signature = self.private_key.sign(
            _payload(confirmed.id, digest, self.owner, when.isoformat())
        )
        return confirmed.model_copy(
            update={
                "confirmed_content_hash": digest,
                "signature": base64.b64encode(signature).decode("ascii"),
                "signing_key_fingerprint": self.trusted.fingerprint,
            }
        )


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def init_keys(owner: str, passphrase: str, directory: Path | None = None) -> TrustedKey:
    """Create the confirmer keypair. Refuses to overwrite an existing key."""
    directory = directory or keys_dir()
    if (directory / PRIVATE_FILE).exists():
        raise SigningError(f"A confirmer key already exists in {directory}")
    if len(passphrase) < MIN_PASSPHRASE:
        raise SigningError(f"The passphrase must be at least {MIN_PASSPHRASE} characters")
    signer = Signer.generate(owner.strip())
    directory.mkdir(parents=True, exist_ok=True)
    _write_private(
        directory / PRIVATE_FILE,
        signer.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
        ),
    )
    public = signer.private_key.public_key()
    (directory / PUBLIC_FILE).write_bytes(
        public.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    owner_record: dict[str, Any] = {
        "owner": signer.owner,
        "fingerprint": fingerprint(public),
        "created_at": datetime.now(UTC).isoformat(),
    }
    (directory / OWNER_FILE).write_text(json.dumps(owner_record, indent=2) + "\n", encoding="utf-8")
    return signer.trusted


def load_trusted_key(directory: Path | None = None) -> TrustedKey | None:
    directory = directory or keys_dir()
    public_path, owner_path = directory / PUBLIC_FILE, directory / OWNER_FILE
    if not public_path.is_file() or not owner_path.is_file():
        return None
    public = serialization.load_pem_public_key(public_path.read_bytes())
    if not isinstance(public, Ed25519PublicKey):
        return None
    owner = json.loads(owner_path.read_text(encoding="utf-8"))["owner"]
    return TrustedKey(public, owner)


def load_signer(passphrase: str, directory: Path | None = None) -> Signer:
    directory = directory or keys_dir()
    path = directory / PRIVATE_FILE
    trusted = load_trusted_key(directory)
    if not path.is_file() or trusted is None:
        raise SigningError(f"No confirmer key in {directory}; a person must run `tieout keys init`")
    try:
        private = serialization.load_pem_private_key(
            path.read_bytes(), password=passphrase.encode("utf-8")
        )
    except (ValueError, TypeError):
        raise SigningError("Wrong passphrase") from None
    if not isinstance(private, Ed25519PrivateKey):
        raise SigningError("The confirmer key is not an Ed25519 key")
    if fingerprint(private.public_key()) != trusted.fingerprint:
        raise SigningError("The private key does not match the public key next to it")
    return Signer(private, trusted.owner)
