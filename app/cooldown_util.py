#!/usr/bin/env python3
"""CooldownMod Util: install, update, or remove the local cooldown mod."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
import platform
import re
import shutil
import string
import tarfile
import urllib.error
import urllib.request
import winreg
import zipfile
import importlib.util
import struct
import subprocess
import sys
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

MOD_NAMES = (
    "zzz_SpellCooldowns_P.pak",
    "zzz_SpellCooldowns_P.utoc",
    "zzz_SpellCooldowns_P.ucas",
)
GITHUB_BRANCH = "main"
# Loaded from disk by this app. Keep them imported so a packaged exe includes them.
_PACKAGED_MODULES = (re, string, winreg)

BG = "#1a140e"
PANEL = "#241910"
GOLD = "#e2c074"
TEXT = "#f4ead2"
MUTED = "#cbb992"
BUTTON = "#3a2c1c"
BUTTON_ACTIVE = "#5c4630"


def bundled() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if bundled():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def root_dir() -> Path:
    if bundled():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def asset_dir() -> Path:
    bundled_assets = app_dir() / "assets"
    if (bundled_assets / "icon.ico").is_file() or (bundled_assets / "banner.png").is_file():
        return bundled_assets
    return root_dir() / "assets"


def load_updater():
    path = app_dir() / "update-cooldowns.py"
    spec = importlib.util.spec_from_file_location("update_cooldowns", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_admin() -> None:
    if is_admin():
        return
    if bundled():
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            None,
            str(root_dir()),
            1,
        )
        raise SystemExit(0)
    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            executable = pythonw
    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        str(executable),
        f'"{Path(__file__).resolve()}"',
        str(app_dir()),
        1,
    )
    raise SystemExit(0)


def mod_folder(paks: Path) -> Path:
    return paks / "~mods"


def files_present(folder: Path) -> bool:
    return all((folder / name).is_file() for name in MOD_NAMES)


def any_files(folder: Path) -> bool:
    return any((folder / name).is_file() for name in MOD_NAMES)


def changed_float(game: bytes, mod: bytes, target: float) -> bool:
    if len(game) != len(mod):
        return False
    diffs = [index for index, (left, right) in enumerate(zip(game, mod)) if left != right]
    if not diffs or diffs[-1] - diffs[0] > 3:
        return False
    for start in range(max(0, diffs[-1] - 3), diffs[0] + 1):
        new_value = struct.unpack_from("<f", mod, start)[0]
        old_value = struct.unpack_from("<f", game, start)[0]
        if new_value != new_value or old_value != old_value:
            continue
        if abs(new_value - target) > 0.05:
            continue
        if not (0.5 <= old_value <= 7200):
            continue
        return True
    return False


def inspect_mod(updater, retoc: Path, paks: Path) -> str:
    installed = mod_folder(paks)
    if not any_files(installed):
        return "missing"
    if not files_present(installed):
        return "outdated"
    game_utoc = None
    for name in ("RSDragonwilds-Windows.utoc", "RSDragonwilds-LinuxServer.utoc"):
        candidate = paks / name
        if candidate.is_file():
            game_utoc = candidate
            break
    if game_utoc is None:
        return "outdated"
    mod_utoc = installed / "zzz_SpellCooldowns_P.utoc"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    listing = subprocess.check_output(
        [str(retoc), "list", "--path", str(mod_utoc)],
        text=True,
        errors="replace",
        **flags,
    )
    chunks: dict[str, str] = {}
    for line in listing.splitlines():
        if "ExportBundleData" not in line:
            continue
        parts = line.split()
        chunks[Path(parts[-1]).name] = parts[1]
    expected = {f"{item[1]}.uasset" for item in updater.SPELLS}
    if set(chunks) != expected:
        return "outdated"
    with tempfile.TemporaryDirectory(prefix="cooldown-check-") as temp_name:
        work = Path(temp_name)
        for _relative, filt, _old, target, _label in updater.SPELLS:
            asset = f"{filt}.uasset"
            chunk = chunks[asset]
            game_bin = work / f"{filt}.game"
            mod_bin = work / f"{filt}.mod"
            updater.run([str(retoc), "get", str(game_utoc), chunk, str(game_bin)])
            updater.run([str(retoc), "get", str(mod_utoc), chunk, str(mod_bin)])
            if not changed_float(game_bin.read_bytes(), mod_bin.read_bytes(), target):
                return "outdated"
    return "current"


class GitHubOffline(Exception):
    pass


def opt_out_path() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "DragonwildsCooldownMod" / "disabled"


def opted_out() -> bool:
    return opt_out_path().is_file()


def set_opt_out(disabled: bool) -> None:
    path = opt_out_path()
    if disabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("off\n", encoding="utf-8")
    elif path.is_file():
        path.unlink()


def github_repository() -> str:
    override = os.environ.get("COOLDOWN_GITHUB_REPO", "").strip()
    if override:
        return override
    for path in (app_dir() / "repository.txt", root_dir() / "repository.txt"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                return text.split()[0]
    return ""


def mod_base_url() -> str:
    override = os.environ.get("COOLDOWN_MOD_BASE", "").strip()
    if override:
        return override.rstrip("/")
    repository = github_repository()
    if "/" not in repository or repository.startswith("http"):
        raise RuntimeError("Set the public GitHub repo in app/repository.txt as owner/name.")
    return f"https://raw.githubusercontent.com/{repository}/{GITHUB_BRANCH}/mod"


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "dragonwilds-cooldown-mod"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub returned {exc.code} while downloading the cooldown mod.") from exc
    except urllib.error.URLError as exc:
        raise GitHubOffline(str(exc.reason)) from exc


def sync_mod(paks: Path, progress=None) -> tuple[str, str]:
    """Download the published pak files when their checksums differ. No git."""
    if progress:
        progress("Checking GitHub...")
    manifest = json.loads(fetch_bytes(f"{mod_base_url()}/manifest.json").decode("utf-8"))
    version = str(manifest["version"])
    files = manifest["files"]
    folder = mod_folder(paks)
    current = True
    for name in MOD_NAMES:
        digest = files[name]
        path = folder / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            current = False
            break
    if current:
        return "current", version
    if progress:
        progress("Downloading the latest cooldown mod...")
    payloads: dict[str, bytes] = {}
    for name in MOD_NAMES:
        data = fetch_bytes(f"{mod_base_url()}/{name}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != files[name]:
            raise RuntimeError(f"{name} from GitHub did not match its published checksum.")
        payloads[name] = data
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in payloads.items():
            target = folder / name
            temporary = folder / f".{name}.download"
            temporary.write_bytes(data)
            temporary.replace(target)
    except PermissionError as exc:
        raise RuntimeError("Close RuneScape Dragonwilds, then open this again.") from exc
    return "updated", version


def check_state(progress=None) -> tuple[str, str, str]:
    def say(text: str) -> None:
        if progress:
            progress(text)

    say("Checking the installed game...")
    updater = load_updater()
    try:
        paks = updater.find_paks()
    except SystemExit:
        return "nogame", "", ""
    if opted_out():
        return "disabled", str(paks), ""
    try:
        repo, version = sync_mod(paks, progress)
    except GitHubOffline:
        repo, version = "offline", ""
    say("Checking the mod against this version of Dragonwilds...")
    folder = mod_folder(paks)
    if not any_files(folder):
        if repo == "offline":
            return "offline-missing", str(paks), ""
        return "missing", str(paks), version
    try:
        retoc = updater.ensure_retoc(None)
        game = inspect_mod(updater, retoc, paks)
    except Exception:
        if repo == "offline":
            return "offline-installed", str(paks), ""
        raise
    return f"{repo}-{game}", str(paks), version


def remove_installed(paks: Path) -> None:
    folder = mod_folder(paks)
    for name in MOD_NAMES:
        path = folder / name
        if path.is_file():
            path.unlink()


def rebuild(paks: Path) -> None:
    updater = load_updater()
    install = mod_folder(paks)
    publish = root_dir() / "mod"
    publish.mkdir(parents=True, exist_ok=True)
    previous = sys.argv
    try:
        sys.argv = [
            str(app_dir() / "update-cooldowns.py"),
            "--paks",
            str(paks),
            "--install",
            str(install),
            "--publish",
            str(publish),
        ]
        result = updater.main()
        if result not in (0, None):
            raise RuntimeError("The cooldown mod could not be built.")
    finally:
        sys.argv = previous


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CooldownMod Util")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.paks: Path | None = None
        self.busy = False
        self.banner_image = None
        self.icon_image = None
        assets = asset_dir()
        icon = assets / "icon.ico"
        if icon.is_file():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass
        png = assets / "icon.png"
        if png.is_file():
            self.icon_image = tk.PhotoImage(file=str(png))
            self.iconphoto(True, self.icon_image)
        self._build(assets / "banner.png")
        self.events = queue.Queue()
        self.after(100, self._poll)
        self.after(150, self.refresh)

    def _build(self, banner_path: Path) -> None:
        if banner_path.is_file():
            self.banner_image = tk.PhotoImage(file=str(banner_path))
            width = self.banner_image.width()
            height = self.banner_image.height()
            canvas = tk.Canvas(self, width=width, height=height, bg=BG, highlightthickness=0)
            canvas.pack()
            canvas.create_image(0, 0, anchor="nw", image=self.banner_image)
            canvas.create_text(
                width // 2,
                height // 2 - 8,
                text="CooldownMod Util",
                fill=GOLD,
                font=("Georgia", 26, "bold"),
            )
            canvas.create_text(
                width // 2,
                height // 2 + 28,
                text="Dragonwilds spell cooldowns",
                fill=MUTED,
                font=("Georgia", 12),
            )
        else:
            tk.Label(self, text="CooldownMod Util", bg=BG, fg=GOLD, font=("Georgia", 26, "bold")).pack(pady=24)

        body = tk.Frame(self, bg=BG, padx=28, pady=18)
        body.pack(fill="x")
        self.status = tk.Label(
            body,
            text="Checking the installed game...",
            bg=BG,
            fg=TEXT,
            wraplength=660,
            justify="center",
            font=("Georgia", 13),
        )
        self.status.pack(fill="x")
        self.buttons = tk.Frame(self, bg=BG, pady=8)
        self.buttons.pack(pady=(0, 18))

    def button(self, text: str, command) -> None:
        border = tk.Frame(self.buttons, bg=GOLD, padx=1, pady=1)
        border.pack(side="left", padx=8)
        tk.Button(
            border,
            text=text,
            command=command,
            bg=BUTTON,
            fg=TEXT,
            activebackground=BUTTON_ACTIVE,
            activeforeground="#fff8e8",
            relief="flat",
            bd=0,
            padx=18,
            pady=8,
            font=("Georgia", 12),
            cursor="hand2",
        ).pack()

    def clear_buttons(self) -> None:
        for child in self.buttons.winfo_children():
            child.destroy()

    def _finish_and_refresh(self) -> None:
        self.busy = False
        self.refresh()

    def refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.configure(text="Checking the installed game...")
        self.clear_buttons()
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "error":
                    self._show_error(event[1])
                elif kind == "state":
                    self._show_state(event[1], event[2], event[3])
                elif kind == "progress":
                    self.status.configure(text=event[1])
                elif kind == "refresh":
                    self._finish_and_refresh()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._poll)

    def _refresh_worker(self) -> None:
        try:
            state, paks, version = check_state(lambda text: self.events.put(("progress", text)))
        except Exception as exc:
            self.events.put(("error", exc))
            return
        self.events.put(("state", state, paks, version))

    def _show_error(self, exc: Exception) -> None:
        self.busy = False
        self.status.configure(text="The cooldown mod could not be checked.")
        self.clear_buttons()
        self.button("Close", self.destroy)
        messagebox.showerror("CooldownMod Util", str(exc))

    def _show_state(self, state: str, paks: str, version: str) -> None:
        self.busy = False
        self.paks = Path(paks) if paks else None
        self.clear_buttons()
        tag = f" ({version})" if version else ""
        messages = {
            "nogame": "RuneScape Dragonwilds was not found in Steam.",
            "disabled": "The cooldown mod is turned off.",
            "missing": "The cooldown mod from GitHub could not be installed.",
            "offline-missing": "GitHub could not be reached, and no cooldown mod is installed.",
            "current-current": f"This cooldown mod matches the GitHub version{tag} and this version of Dragonwilds.",
            "updated-current": f"Installed the latest cooldown mod from GitHub{tag}. It matches this version of Dragonwilds.",
            "current-outdated": f"This mod matches the GitHub version{tag}. It does not match this version of Dragonwilds.",
            "updated-outdated": f"Installed the latest cooldown mod from GitHub{tag}. It does not match this version of Dragonwilds.",
            "offline-current": "GitHub could not be reached. The installed mod matches this version of Dragonwilds.",
            "offline-outdated": "GitHub could not be reached. The installed mod does not match this version of Dragonwilds.",
            "offline-installed": "GitHub could not be reached. The installed cooldown mod was left in place.",
        }
        self.status.configure(text=messages.get(state, "The cooldown mod could not be checked."))
        if state == "disabled":
            self.button("Install", lambda: self._run("install"))
        elif state.endswith("-current") or state.endswith("-outdated") or state == "offline-installed":
            self.button("Uninstall", lambda: self._run("uninstall"))
        self.button("Close", self.destroy)

    def _run(self, action: str) -> None:
        if self.busy or self.paks is None:
            return
        self.busy = True
        self.clear_buttons()
        if action == "uninstall":
            self.status.configure(text="Removing the cooldown mod...")
        else:
            self.status.configure(text="Installing the cooldown mod...")
        threading.Thread(target=self._run_worker, args=(action,), daemon=True).start()

    def _run_worker(self, action: str) -> None:
        try:
            if action == "uninstall":
                remove_installed(self.paks)
                set_opt_out(True)
            else:
                set_opt_out(False)
        except (Exception, SystemExit) as exc:
            self.events.put(("error", exc if isinstance(exc, Exception) else RuntimeError(str(exc))))
            return
        self.events.put(("refresh",))


def _smoke() -> None:
    app = App()
    app.withdraw()

    def labels(widget):
        found = []
        for child in widget.winfo_children():
            if child.winfo_class() == "Button":
                found.append(child.cget("text"))
            found.extend(labels(child))
        return found

    def finish():
        if app.busy or app.status.cget("text").startswith("Checking") or app.status.cget("text").startswith("Downloading"):
            app.after(200, finish)
            return
        print("STATUS", app.status.cget("text"))
        print("BUTTONS", " ".join(labels(app)))
        app.destroy()

    app.after(300, finish)
    app.mainloop()


if __name__ == "__main__":
    if "--check" in sys.argv:
        state, _paks, version = check_state()
        print(state, version)
    elif "--smoke" in sys.argv:
        _smoke()
    else:
        ensure_admin()
        App().mainloop()
