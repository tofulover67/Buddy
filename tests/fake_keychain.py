"""An in-memory login Keychain for the tests. On a Mac, Buddy Network locks
its saved files with a key it keeps in the Keychain through
/usr/bin/security (e2e.py); every test gets this instead (tests/_paths.py
installs it), so the suite never adds to the real Keychain - and a Mac
without one (the release build's runner) is no different."""

import subprocess

from pages.buddy_network import e2e

NOT_FOUND, DUPLICATE = 44, 45


class FakeKeychain:
    def __init__(self):
        self.items: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        self.calls.append(args)
        command, flags = args[0], dict(zip(args[1::2], args[2::2]))
        where = (flags.get("-s"), flags.get("-a"))
        if command == "find-generic-password":
            if where not in self.items:
                return subprocess.CompletedProcess(args, NOT_FOUND, "", "not found")
            return subprocess.CompletedProcess(args, 0, self.items[where] + "\n", "")
        if command == "add-generic-password":
            if where in self.items:
                return subprocess.CompletedProcess(args, DUPLICATE, "", "already exists")
            self.items[where] = flags["-w"]
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected security command: {args}")

    def forget(self):
        """As if on another Mac user account: the key is gone."""
        self.items.clear()
        e2e._keychain_cache = None


KEYCHAIN = FakeKeychain()


def install():
    e2e._security = KEYCHAIN
    e2e._keychain_cache = None
