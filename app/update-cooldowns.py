#!/usr/bin/env python3
"""Rebuild the Dragonwilds spell-cooldown mod from the installed game files.

The game stores each cooldown as one number inside that spell's data. A game
update replaces those files, so an older mod stops matching. This reads the
current pack, changes only those five numbers, and copies the new mod into
the game's ~mods folder.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RETOC_VERSION = "v0.1.5"
RETOC_BASE = f"https://github.com/trumank/retoc/releases/download/{RETOC_VERSION}"

# Vanilla seconds still in the October 8 2026 packs, then the value we want.
SPELLS = (
    (
        "Content/Gameplay/UtilityMagic/PerkSpells/Rocksplosion/USD_Rocksplosion.uexp",
        "USD_Rocksplosion",
        60.0,
        5.0,
        "Rocksplosion",
    ),
    (
        "Content/Gameplay/UtilityMagic/PerkSpells/Splinter/USD_Splinter.uexp",
        "USD_Splinter",
        45.0,
        5.0,
        "Splinter",
    ),
    (
        "Content/Gameplay/UtilityMagic/PerkSpells/Farming/Humidify/USD_Humidify.uexp",
        "USD_Humidify",
        45.0,
        1.0,
        "Humidify",
    ),
    (
        "Content/Gameplay/UtilityMagic/PerkSpells/HomeTeleport/USD_HomeTeleport.uexp",
        "USD_HomeTeleport",
        900.0,
        1.0,
        "Home Teleport",
    ),
    (
        "Content/Gameplay/UtilityMagic/PerkSpells/Farming/USD_Harvest.uexp",
        "USD_Harvest",
        5.0,
        1.0,
        "Uproot",
    ),
)

MOD_NAMES = (
    "zzz_SpellCooldowns_P.pak",
    "zzz_SpellCooldowns_P.utoc",
    "zzz_SpellCooldowns_P.ucas",
)


def log(message: str) -> None:
    if sys.stdout is not None:
        print(message, flush=True)


def hidden_kwargs() -> dict:
    if platform.system() == "Windows":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run(cmd: list[str]) -> None:
    log("+ " + " ".join(cmd))
    result = subprocess.run(cmd, check=False, capture_output=True, **hidden_kwargs())
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(detail or Path(cmd[0]).name + " failed.")


def float_count(data: bytes, value: float) -> int:
    return data.count(struct.pack("<f", value))


def cache_dir() -> Path:
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(root) / "DragonwildsSpellCooldowns"
    else:
        path = Path.home() / ".cache" / "dragonwilds-spell-cooldowns"
    path.mkdir(parents=True, exist_ok=True)
    return path


def retoc_name() -> str:
    return "retoc.exe" if platform.system() == "Windows" else "retoc"


def find_retoc(explicit: str | None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path(__file__).resolve().parent / retoc_name())
    candidates.append(cache_dir() / retoc_name())
    candidates.append(Path("/home/dragonwilds/modtools") / retoc_name())
    candidates.append(Path("/tmp/retoc_cli-x86_64-unknown-linux-gnu/retoc"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Temp" / "retoc-win" / "retoc.exe")
    for path in candidates:
        if path.is_file():
            return path
    return None


def download_retoc() -> Path:
    dest = cache_dir() / retoc_name()
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows" and machine in ("amd64", "x86_64"):
        url = f"{RETOC_BASE}/retoc_cli-x86_64-pc-windows-msvc.zip"
        kind = "zip"
    elif system == "Linux" and machine in ("x86_64", "amd64"):
        url = f"{RETOC_BASE}/retoc_cli-x86_64-unknown-linux-gnu.tar.xz"
        kind = "tar"
    else:
        raise SystemExit(f"No retoc build is set up for {system} {machine}.")

    log(f"Downloading retoc {RETOC_VERSION}")
    with urllib.request.urlopen(url, timeout=120) as response:
        blob = response.read()
    extracted: Path | None = None
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            name = next(n for n in archive.namelist() if n.endswith("retoc.exe"))
            extracted = dest
            extracted.write_bytes(archive.read(name))
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:xz") as archive:
            member = next(m for m in archive.getmembers() if m.name.endswith("/retoc") or m.name == "retoc")
            fileobj = archive.extractfile(member)
            if fileobj is None:
                raise SystemExit("retoc archive did not contain the program.")
            extracted = dest
            extracted.write_bytes(fileobj.read())
        dest.chmod(0o755)
    log(f"Saved {dest}")
    return dest


def ensure_retoc(explicit: str | None) -> Path:
    found = find_retoc(explicit)
    if found:
        return found
    return download_retoc()


PAK_MARKERS = ("RSDragonwilds-Windows.utoc", "RSDragonwilds-LinuxServer.utoc")
GAME_APP_ID = "1374490"


def _norm_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _vdf_path(value: str) -> Path:
    while "\\\\" in value:
        value = value.replace("\\\\", "\\")
    return Path(value)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data[:64]:
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _paths_from_library_vdf(text: str) -> list[Path]:
    paths = [_vdf_path(match.group(1)) for match in re.finditer(r'"path"\s+"([^"]+)"', text)]
    # Older Steam library files store extra libraries as "1" "D:\\SteamLibrary".
    paths.extend(
        _vdf_path(match.group(1))
        for match in re.finditer(r'"\d+"\s+"([A-Za-z]:[^"]*)"', text)
    )
    return paths


def steam_libraries() -> list[Path]:
    libraries: list[Path] = []
    if platform.system() != "Windows":
        return libraries
    import winreg

    steam: str | None = None
    for hive, subkey in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam"),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                for value_name in ("SteamPath", "InstallPath"):
                    try:
                        steam, _ = winreg.QueryValueEx(key, value_name)
                        break
                    except OSError:
                        continue
        except OSError:
            continue
        if steam:
            break
    if not steam:
        return libraries
    libraries.append(Path(steam))
    for relative in ("config/libraryfolders.vdf", "steamapps/libraryfolders.vdf"):
        vdf = Path(steam) / relative
        try:
            if vdf.is_file():
                libraries.extend(_paths_from_library_vdf(_read_text(vdf)))
        except OSError:
            continue
    return libraries


def uninstall_game_dirs() -> list[Path]:
    if platform.system() != "Windows":
        return []
    import winreg

    found: list[Path] = []
    for hive, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {GAME_APP_ID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {GAME_APP_ID}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {GAME_APP_ID}"),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if location:
            found.append(Path(str(location)))
    return found


def drive_library_roots() -> list[Path]:
    """Steam library folders sitting on another drive, without walking the whole disk."""
    if platform.system() != "Windows":
        return []
    import ctypes
    import string

    kernel = ctypes.windll.kernel32
    mask = kernel.GetLogicalDrives()
    roots: list[Path] = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << index):
            continue
        root = f"{letter}:\\"
        # Fixed and removable only. Network drives can stall for a long time.
        if kernel.GetDriveTypeW(root) not in (2, 3):
            continue
        base = Path(root)
        roots.extend(
            (
                base / "SteamLibrary",
                base / "Steam",
                base / "Program Files (x86)" / "Steam",
                base / "Program Files" / "Steam",
            )
        )
        try:
            children = [child for child in base.iterdir() if child.is_dir()]
        except OSError:
            continue
        for child in children:
            name = child.name.casefold()
            if "steam" in name:
                roots.append(child)
            if name not in {"games", "game", "gaming"} and "steam" not in name:
                continue
            try:
                nested = [path for path in child.iterdir() if path.is_dir()]
            except OSError:
                continue
            roots.extend(path for path in nested if "steam" in path.name.casefold())
    return roots


def _marker_dir(root: Path) -> Path | None:
    try:
        if not root.is_dir():
            return None
    except OSError:
        return None
    for relative in (Path(), Path("RSDragonwilds/Content/Paks"), Path("Content/Paks")):
        for marker in PAK_MARKERS:
            candidate = root / relative / marker
            try:
                if candidate.is_file():
                    return candidate.parent
            except OSError:
                continue
    return None


def _paks_under(library: Path) -> Path | None:
    found = _marker_dir(library)
    if found:
        return found
    steamapps = library if library.name.casefold() == "steamapps" else library / "steamapps"
    try:
        if not steamapps.is_dir():
            return None
    except OSError:
        return None
    games: list[Path] = []
    manifest = steamapps / f"appmanifest_{GAME_APP_ID}.acf"
    try:
        if manifest.is_file():
            match = re.search(r'"installdir"\s+"([^"]+)"', _read_text(manifest))
            if match:
                games.append(steamapps / "common" / match.group(1))
    except OSError:
        pass
    common = steamapps / "common"
    try:
        if common.is_dir():
            games.extend(path for path in common.iterdir() if path.is_dir())
    except OSError:
        pass
    seen: set[str] = set()
    for game in games:
        key = _norm_key(game)
        if key in seen:
            continue
        seen.add(key)
        found = _marker_dir(game)
        if found:
            return found
    return None


def find_paks() -> Path:
    seen: set[str] = set()
    for source in (steam_libraries, uninstall_game_dirs, drive_library_roots):
        for library in source():
            key = _norm_key(library)
            if key in seen:
                continue
            seen.add(key)
            try:
                found = _paks_under(library)
            except OSError:
                continue
            if found:
                return found
    raise SystemExit(
        "RuneScape Dragonwilds was not found in Steam. "
        "Pass --paks with the folder that contains the game .utoc file."
    )


def extract_spells(retoc: Path, paks: Path, legacy: Path) -> None:
    for relative, filt, _old, _new, label in SPELLS:
        before = set(legacy.rglob("*.uexp"))
        run(
            [
                str(retoc),
                "to-legacy",
                "--no-shaders",
                "--no-script-objects",
                "--filter",
                filt,
                "--version",
                "UE5_6",
                str(paks),
                str(legacy),
            ]
        )
        target = next((path for path in legacy.rglob(Path(relative).name) if path.as_posix().endswith(relative.replace("\\", "/"))), None)
        if target is None:
            added = [path for path in legacy.rglob("*.uexp") if path not in before]
            raise SystemExit(f"Could not extract {label}. New files: {added}")


def patch_spells(legacy: Path, edit: Path) -> None:
    for relative, _filt, old, new, label in SPELLS:
        matches = [
            path
            for path in legacy.rglob(Path(relative).name)
            if path.as_posix().endswith(relative.replace("\\", "/"))
        ]
        if len(matches) != 1:
            raise SystemExit(f"Expected one {relative}, found {len(matches)}")
        source = matches[0]
        data = source.read_bytes()
        count = float_count(data, old)
        if count != 1:
            raise SystemExit(
                f"{label} no longer has a single {old:g} second cooldown (found {count}). "
                "The game data changed, so this utility stopped instead of editing the wrong number."
            )
        patched = data.replace(struct.pack("<f", old), struct.pack("<f", new), 1)
        if float_count(patched, new) < 1 or float_count(patched, old) != 0:
            raise SystemExit(f"{label} cooldown was not replaced.")
        mount = source.relative_to(legacy)
        out_exp = edit / mount
        out_exp.parent.mkdir(parents=True, exist_ok=True)
        out_exp.write_bytes(patched)
        shutil.copy2(source.with_suffix(".uasset"), out_exp.with_suffix(".uasset"))
        log(f"{label}: {old:g}s -> {new:g}s")


def game_utoc(paks: Path) -> Path:
    for name in ("RSDragonwilds-Windows.utoc", "RSDragonwilds-LinuxServer.utoc"):
        path = paks / name
        if path.is_file():
            return path
    raise SystemExit(f"No game .utoc in {paks}")


def retoc_get(retoc: Path, utoc: Path, chunk: str, dest: Path) -> None:
    run([str(retoc), "get", str(utoc), chunk, str(dest)])


def verify_pack(retoc: Path, utoc: Path, paks: Path, work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    listing = subprocess.check_output([str(retoc), "list", "--path", str(utoc)], text=True, errors="replace", **hidden_kwargs())
    game = game_utoc(paks)
    for relative, _filt, old, new, label in SPELLS:
        asset_name = Path(relative).with_suffix(".uasset").name
        chunk = None
        for line in listing.splitlines():
            if "ExportBundleData" in line and asset_name in line:
                parts = line.split()
                chunk = parts[1]
                break
        if not chunk:
            raise SystemExit(f"Packed mod is missing {label}.")
        mod_bin = work / f"{asset_name}.mod"
        game_bin = work / f"{asset_name}.game"
        retoc_get(retoc, utoc, chunk, mod_bin)
        retoc_get(retoc, game, chunk, game_bin)
        mod = mod_bin.read_bytes()
        original = game_bin.read_bytes()
        if len(mod) != len(original):
            raise SystemExit(f"{label} packed size changed ({len(original)} -> {len(mod)}).")
        diffs = [i for i, (a, b) in enumerate(zip(original, mod)) if a != b]
        if not diffs or diffs[-1] - diffs[0] > 3:
            raise SystemExit(f"{label} changed bytes outside a single cooldown value: {diffs[:12]}")
        if float_count(mod, new) < 1 or float_count(mod, old) != 0:
            raise SystemExit(
                f"{label} packed wrong: {new:g}s x{float_count(mod, new)}, old {old:g}s x{float_count(mod, old)}"
            )
        log(f"{label}: packed file differs from the game by the cooldown only.")
    log("Packed cooldowns match the five requested times.")


def write_manifest(dest: Path) -> None:
    files = {
        name: hashlib.sha256((dest / name).read_bytes()).hexdigest()
        for name in MOD_NAMES
    }
    version = hashlib.sha256("".join(files[name] for name in MOD_NAMES).encode()).hexdigest()[:12]
    payload = {"version": version, "files": files}
    (dest / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log(f"Wrote manifest {version}")


def publish(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in MOD_NAMES:
        src = source / name
        if not src.is_file():
            raise SystemExit(f"Missing packed file {src}")
        shutil.copy2(src, dest / name)
        log(f"Copied {name} ({src.stat().st_size} bytes) -> {dest}")
    if dest.name != "~mods":
        write_manifest(dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild and install the spell-cooldown mod.")
    parser.add_argument("--paks", type=Path, help="Folder containing the game .utoc file. Steam is used when omitted.")
    parser.add_argument("--install", type=Path, help="~mods folder. Defaults to a ~mods folder next to the game pack.")
    parser.add_argument("--publish", type=Path, help="Also copy the packed files here. Defaults to this script's folder for the Windows client.")
    parser.add_argument("--no-publish", action="store_true", help="Do not refresh the loose copies next to this script.")
    parser.add_argument("--retoc", help="Path to retoc. Downloaded when missing.")
    args = parser.parse_args()

    retoc = ensure_retoc(args.retoc)
    paks = args.paks or find_paks()
    if not paks.is_dir():
        raise SystemExit(f"Pak folder does not exist: {paks}")
    windows = (paks / "RSDragonwilds-Windows.utoc").is_file()
    linux = (paks / "RSDragonwilds-LinuxServer.utoc").is_file()
    if not windows and not linux:
        raise SystemExit(f"No Dragonwilds pack in {paks}")
    install = args.install or (paks / "~mods")
    publish_to = args.publish
    if publish_to is None and windows and not args.no_publish:
        publish_to = Path(__file__).resolve().parent.parent / "mod"

    log(f"Game pack: {paks}")
    log(f"Install to: {install}")
    with tempfile.TemporaryDirectory(prefix="spell-cooldowns-") as temp_name:
        temp = Path(temp_name)
        legacy = temp / "legacy"
        edit = temp / "edit"
        packed = temp / "packed"
        check = temp / "check"
        legacy.mkdir()
        edit.mkdir()
        packed.mkdir()
        extract_spells(retoc, paks, legacy)
        patch_spells(legacy, edit)
        utoc = packed / "zzz_SpellCooldowns_P.utoc"
        run([str(retoc), "to-zen", "--version", "UE5_6", str(edit), str(utoc)])
        verify_pack(retoc, utoc, paks, check)
        publish(packed, install)
        if publish_to and publish_to.resolve() != install.resolve():
            publish(packed, publish_to)
    log("Spell cooldown mod updated.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        if sys.stderr is not None:
            print(exc, file=sys.stderr)
        raise SystemExit(1)
