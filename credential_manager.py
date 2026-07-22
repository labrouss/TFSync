#!/usr/bin/env python3
"""
credential_manager.py
======================
Windows Credential Manager integration for TFSync, via the `cmdkey.exe`
CLI (no new dependency). Used when a source or destination share needs
different credentials than the Windows account actually running the sync
(e.g. migrating from/to a server outside the current domain).

Why cmdkey rather than TFSync storing/handling credentials itself:
- robocopy and the Win32 ACL APIs TFSync uses have no way to accept a
  username/password directly - they authenticate to a UNC path with
  whatever Windows already has cached for that server name. cmdkey is
  the standard way to pre-seed that cache.
- Credentials added this way live in Windows' own encrypted Credential
  Manager store (DPAPI-protected, under the Windows account that ran
  cmdkey) - not in TFSync's database. TFSync never stores a password;
  only the *username* is kept (in tfsync_store, alongside the job) purely
  so the GUI can show what's configured and detect when it's missing.

IMPORTANT LIMITATION: credentials set this way are scoped to the Windows
account that ran cmdkey. For a job using the default "interactive" Run As
mode (task_scheduler.py), that's your own account - the same one Task
Scheduler will use later, so this just works. For a job using "whether
logged on or not" mode under a *different* account, that account needs
its own cmdkey entry (run under that identity) - TFSync running as you
cannot add credentials into another account's Credential Manager store
without already having a way to act as that account. The GUI surfaces
this clearly rather than silently doing the wrong thing.
"""

from __future__ import annotations

import os
import subprocess
from typing import List, Optional


class CredentialError(RuntimeError):
    pass


def _require_windows() -> None:
    if os.name != "nt":
        raise CredentialError("Credential management requires Windows (cmdkey.exe).")


def _redact(args: List[str]) -> List[str]:
    """Returns a copy of args with the value following /pass: masked, so a
    password never appears in an error message shown to the user."""
    redacted = []
    for arg in args:
        if arg.lower().startswith("/pass:"):
            redacted.append("/pass:********")
        else:
            redacted.append(arg)
    return redacted


def _run_cmdkey(args: List[str], allow_not_found: bool = False) -> subprocess.CompletedProcess:
    _require_windows()
    proc = subprocess.run(["cmdkey.exe", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        if allow_not_found and "not found" in stderr.lower():
            return proc
        # Defense in depth: scrub the raw password out of any echoed output too.
        for arg in args:
            if arg.lower().startswith("/pass:"):
                raw_password = arg[len("/pass:"):]
                if raw_password:
                    stderr = stderr.replace(raw_password, "********")
        raise CredentialError(f"cmdkey {' '.join(_redact(args))} failed:\n{stderr}")
    return proc


def add_credential(host: str, username: str, password: str) -> None:
    """
    Adds (or overwrites) a Windows Credential Manager entry for `host`,
    under the CURRENTLY RUNNING Windows account. `host` should be a plain
    server name/address (no leading \\\\ - strip that first if extracting
    it from a UNC path). The password is never stored by TFSync - only
    handed to cmdkey.exe for this one call and then discarded.
    """
    if not host:
        raise CredentialError("No server address to attach credentials to.")
    if not username:
        raise CredentialError("Username is required.")
    if not password:
        raise CredentialError("Password is required.")
    _run_cmdkey(["/add:" + host, "/user:" + username, "/pass:" + password])


def remove_credential(host: str) -> None:
    """Removes a Credential Manager entry for `host`, if one exists. Safe/idempotent."""
    if not host:
        return
    _run_cmdkey(["/delete:" + host], allow_not_found=True)


def host_from_path(path: str) -> Optional[str]:
    """Extracts a plain server name/address from a UNC path, suitable for
    use as a cmdkey target (no leading backslashes). Returns None if the
    path doesn't look like a UNC path."""
    import acl_compare_core as core
    host = core.parse_unc_host(path)
    return host.lstrip("\\") if host else None


if __name__ == "__main__":
    # Pure-logic self-test - safe on any platform (doesn't call cmdkey itself).
    print("host_from_path examples:")
    for p in [r"\\fs.example.com\share\folder", r"\\server\share", "C:\\local\\path"]:
        print(f"  {p!r} -> {host_from_path(p)!r}")

    print("\n_redact examples:")
    args = ["/add:fs.example.com", "/user:DOMAIN\\user", "/pass:SuperSecret123"]
    print(f"  {args} -> {_redact(args)}")
    assert "SuperSecret123" not in str(_redact(args))
    print("\nOK")
