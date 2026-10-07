"""Where integration credentials live: Windows Credential Manager (encrypted by Windows for this user account), never a
plain file, never the logs, never anything the model sees.

Only long-lived secrets are stored (a refresh token and what it was granted). Short-lived access tokens stay in memory.
Each credential is stored under "Jarvis/<provider>/<account>".

On other systems the `keyring` package is used if installed; otherwise nothing is persisted (connect again each run).
"""

import ctypes
import json
import logging
import os

log = logging.getLogger("room-agent")
PREFIX = "Jarvis/"
MAX_BLOB = 2560  # Windows' limit for one generic credential


class _Memory:
    def __init__(self):
        self.data = {}

    def write(self, target, secret):
        self.data[target] = secret

    def read(self, target):
        return self.data.get(target)

    def delete(self, target):
        self.data.pop(target, None)


class _WindowsCredentials:
    """CredWriteW / CredReadW / CredDeleteW, through ctypes (no extra package)."""

    def __init__(self):
        from ctypes import wintypes as wt

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR), ("Comment", wt.LPWSTR),
                        ("LastWritten", wt.FILETIME), ("CredentialBlobSize", wt.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)), ("Persist", wt.DWORD),
                        ("AttributeCount", wt.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                        ("UserName", wt.LPWSTR)]

        self.CREDENTIAL = CREDENTIAL
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.write_ = adv.CredWriteW
        self.write_.argtypes = [ctypes.POINTER(CREDENTIAL), wt.DWORD]
        self.read_ = adv.CredReadW
        self.read_.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        self.delete_ = adv.CredDeleteW
        self.delete_.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD]
        self.free = adv.CredFree

    def write(self, target, secret):
        blob = secret.encode("utf-8")
        if len(blob) > MAX_BLOB:
            raise ValueError("credential too large for Windows Credential Manager")
        c = self.CREDENTIAL()
        c.Type = 1  # CRED_TYPE_GENERIC
        c.TargetName = target
        c.UserName = "jarvis"
        c.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE (this user, this PC; not roamed)
        buf = ctypes.create_string_buffer(blob, len(blob))
        c.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        c.CredentialBlobSize = len(blob)
        if not self.write_(ctypes.byref(c), 0):
            raise OSError(ctypes.get_last_error(), "CredWrite failed")

    def read(self, target):
        p = ctypes.POINTER(self.CREDENTIAL)()
        if not self.read_(target, 1, 0, ctypes.byref(p)):
            return None
        try:
            return ctypes.string_at(p.contents.CredentialBlob, p.contents.CredentialBlobSize).decode("utf-8")
        finally:
            self.free(p)

    def delete(self, target):
        self.delete_(target, 1, 0)


class _Keyring:
    def __init__(self):
        import keyring

        self.k = keyring

    def write(self, target, secret):
        self.k.set_password("jarvis", target, secret)

    def read(self, target):
        return self.k.get_password("jarvis", target)

    def delete(self, target):
        try:
            self.k.delete_password("jarvis", target)
        except Exception:
            pass


def _backend():
    if os.getenv("JARVIS_VAULT") == "memory":
        return _Memory()
    if os.name == "nt":
        return _WindowsCredentials()
    try:
        return _Keyring()
    except ImportError:
        log.warning("no secure credential store on this system: integrations won't stay connected across restarts")
        return _Memory()


class Vault:
    def __init__(self, backend=None, prefix=PREFIX):
        self.backend = backend or _backend()
        self.prefix = prefix

    def _target(self, provider, account):
        return f"{self.prefix}{provider}/{account.lower()}"

    def put(self, provider, account, data: dict):
        self.backend.write(self._target(provider, account), json.dumps(data, separators=(",", ":")))

    def get(self, provider, account):
        raw = self.backend.read(self._target(provider, account))
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def delete(self, provider, account):
        self.backend.delete(self._target(provider, account))


_vault = None


def vault():
    global _vault
    if _vault is None:
        _vault = Vault()
    return _vault
