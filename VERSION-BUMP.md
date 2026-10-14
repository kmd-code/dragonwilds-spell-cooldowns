# Spell cooldown version bump

A Dragonwilds client update replaces the packed spell data, so the previous mod no longer matches. The shared app only downloads `mod/` from this repo over HTTPS. It does not rebuild the pak. Rebuild here, commit `mod/`, and push `main`.

Do not copy the pak onto the dedicated server. Cooldowns are client-only, and the server `~mods` folder was removed on 2026-10-08.

## 1. Wait until the client update has finished

Steam app `1374490`. Manifest:

`C:\Program Files (x86)\Steam\steamapps\appmanifest_1374490.acf`

`StateFlags` must be `4`, and `buildid` must equal `TargetBuildID`. If `BytesStaged` is still behind `BytesToStage`, the install is mid-update and a rebuild will pack the wrong files.

Pak folder:

`C:\Program Files (x86)\Steam\steamapps\common\RSDragonwilds\RSDragonwilds\Content\Paks`

The 2026-10-14 bump used client build `25812671`. The previous published mod, `1af8f9bdabf4`, was built on 2026-10-08.

## 2. Rebuild from that install

`gh` is not on PATH. Use `C:\Users\duval\AppData\Local\GitHubCLI\bin\gh.exe` (account `kmd-code`).

`retoc` 0.1.5 is already at `%LOCALAPPDATA%\Temp\retoc-win\retoc.exe`. `app/update-cooldowns.py` looks there, then downloads a copy to `%LOCALAPPDATA%\DragonwildsSpellCooldowns\retoc.exe` if it is missing. Python 3.14 on this PC can run the script.

From this repo:

```powershell
python app/update-cooldowns.py --paks "C:\Program Files (x86)\Steam\steamapps\common\RSDragonwilds\RSDragonwilds\Content\Paks" --retoc "$env:LOCALAPPDATA\Temp\retoc-win\retoc.exe"
```

What that command does:

1. `retoc to-legacy --version UE5_6` extracts each spell by filter: `USD_Rocksplosion`, `USD_Splinter`, `USD_Humidify`, `USD_HomeTeleport`, `USD_Harvest`.
2. Replaces the single little-endian float in that spell's `.uexp`.
3. `retoc to-zen --version UE5_6` writes `zzz_SpellCooldowns_P.pak`, `.utoc`, and `.ucas`.
4. Compares each packed chunk with the game chunk and stops unless the only difference is that one float.
5. Copies the three files into the game's `Content\Paks\~mods`.
6. Copies them to `mod/` and writes `mod/manifest.json`. The version is the first 12 hex chars of the SHA-256 of the three file hashes concatenated in pak, utoc, ucas order.

Vanilla seconds that still matched build `25812671`, and the values written into the pack:

| Spell | Asset | Vanilla | Mod |
|---|---|---|---|
| Rocksplosion | `USD_Rocksplosion` | 60 | 5 |
| Splinter | `USD_Splinter` | 45 | 5 |
| Humidify | `USD_Humidify` | 45 | 1 |
| Home Teleport | `USD_HomeTeleport` | 900 | 1 |
| Uproot | `USD_Harvest` | 5 | 1 |

Those pairs live in `SPELLS` at the top of `app/update-cooldowns.py`. On this bump they did not need to change. The new pack is still different because the game's chunk bytes changed. The script prints `Wrote manifest` with the id to publish. For build `25812671` that id is `88aaafaf40ed`. The previous published id was `1af8f9bdabf4`.

If the script says a spell no longer has a single vanilla cooldown, that old number changed. Search the extracted `.uexp` for the nearby float, update the old value in `SPELLS`, and run the command again. Leave the target seconds alone unless the intended cooldown changed.

The shared exe does not need a rebuild when only the pak changes. `app/cooldown_util.py` compares the installed chunk to the current game chunk and accepts it when the only change is one float equal to the target seconds. Rebuild `CooldownMod Util.exe` only if that Python changes. The exe friends already have is `C:\Users\duval\OneDrive\Desktop\CooldownMod Util\CooldownMod Util.exe`.

## 3. Publish

This PC has `core.autocrlf=true`. `.gitattributes` marks `*.pak`, `*.utoc`, and `*.ucas` as binary so a checkout does not rewrite them. Without that, a fresh clone shows those files as modified before any rebuild, and hashing that tree publishes the wrong version. Commit only after the script prints `Wrote manifest`, and check `mod/manifest.json` matches that line.

Commit `mod/manifest.json` and the three `mod/zzz_SpellCooldowns_P.*` files, then push `main`. `gh` is not on PATH. The app reads:

`https://raw.githubusercontent.com/kmd-code/dragonwilds-spell-cooldowns/main/mod/manifest.json`

and the three files next to it. The next launch on a friend's PC downloads this set when the checksums differ.
