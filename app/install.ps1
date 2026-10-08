param(
    [string]$Source = "",
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

function Show-Notice {
    param([string]$Text, [string]$Title, [string]$Icon)
    Write-Output $Text
    if ($env:SPELL_COOLDOWNS_QUIET) { return }
    [System.Windows.Forms.MessageBox]::Show($Text, $Title, 'OK', $Icon) | Out-Null
}

$names = @(
    'zzz_SpellCooldowns_P.pak',
    'zzz_SpellCooldowns_P.utoc',
    'zzz_SpellCooldowns_P.ucas'
)

function Find-PaksInTree {
    param([string]$Root)
    if (-not $Root -or -not (Test-Path -LiteralPath $Root)) { return $null }
    $direct = @(
        (Join-Path $Root 'RSDragonwilds\Content\Paks\RSDragonwilds-Windows.utoc'),
        (Join-Path $Root 'Content\Paks\RSDragonwilds-Windows.utoc')
    )
    foreach ($marker in $direct) {
        if (Test-Path -LiteralPath $marker) { return (Split-Path -Parent $marker) }
    }
    $found = Get-ChildItem -LiteralPath $Root -Filter 'RSDragonwilds-Windows.utoc' -Recurse -Depth 5 -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { return $found.DirectoryName }
    return $null
}

function Convert-VdfPath {
    param([string]$Value)
    for ($i = 0; $i -lt 4 -and $Value.Contains('\\'); $i++) {
        $Value = $Value.Replace('\\', '\')
    }
    return $Value
}

function Get-SteamLibraries {
    $libraries = New-Object System.Collections.Generic.List[string]
    $steam = $null
    foreach ($key in @(
        'HKCU:\Software\Valve\Steam',
        'HKLM:\Software\WOW6432Node\Valve\Steam',
        'HKLM:\Software\Valve\Steam'
    )) {
        if (-not (Test-Path $key)) { continue }
        $props = Get-ItemProperty $key
        if ($props.SteamPath) { $steam = $props.SteamPath }
        elseif ($props.InstallPath) { $steam = $props.InstallPath }
        if ($steam) { break }
    }
    if (-not $steam) { return $libraries }

    $libraries.Add($steam)
    foreach ($relative in @(
        'config\libraryfolders.vdf',
        'steamapps\libraryfolders.vdf'
    )) {
        $vdf = Join-Path $steam $relative
        if (-not (Test-Path -LiteralPath $vdf)) { continue }
        $text = Get-Content -LiteralPath $vdf -Raw
        foreach ($match in [regex]::Matches($text, '"path"\s+"([^"]+)"')) {
            $libraries.Add((Convert-VdfPath $match.Groups[1].Value))
        }
        foreach ($match in [regex]::Matches($text, '"\d+"\s+"([A-Za-z]:[^"]*)"')) {
            $libraries.Add((Convert-VdfPath $match.Groups[1].Value))
        }
    }
    return $libraries
}

function Get-DriveSteamLibraries {
    $libraries = New-Object System.Collections.Generic.List[string]
    $drives = [System.IO.DriveInfo]::GetDrives() | Where-Object {
        $_.IsReady -and ($_.DriveType -eq 'Fixed' -or $_.DriveType -eq 'Removable')
    }
    foreach ($drive in $drives) {
        $root = $drive.RootDirectory.FullName
        foreach ($relative in @(
            'SteamLibrary',
            'Steam',
            'Program Files (x86)\Steam',
            'Program Files\Steam'
        )) {
            $libraries.Add((Join-Path $root $relative))
        }
        $children = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue)
        foreach ($child in $children) {
            $name = $child.Name.ToLowerInvariant()
            if ($name.Contains('steam')) { $libraries.Add($child.FullName) }
            if ($name -notin @('games', 'game', 'gaming') -and -not $name.Contains('steam')) { continue }
            foreach ($nested in @(Get-ChildItem -LiteralPath $child.FullName -Directory -ErrorAction SilentlyContinue)) {
                if ($nested.Name.ToLowerInvariant().Contains('steam')) {
                    $libraries.Add($nested.FullName)
                }
            }
        }
    }
    return $libraries
}

function Search-LibraryList {
    param($Libraries, $Seen)
    foreach ($library in @($Libraries)) {
        if (-not $library -or $Seen.ContainsKey($library)) { continue }
        $Seen[$library] = $true
        $steamapps = Join-Path $library 'steamapps'
        $manifest = Join-Path $steamapps 'appmanifest_1374490.acf'
        if (Test-Path -LiteralPath $manifest) {
            $text = Get-Content -LiteralPath $manifest -Raw
            $installDir = [regex]::Match($text, '"installdir"\s+"([^"]+)"').Groups[1].Value
            if ($installDir) {
                $paks = Find-PaksInTree (Join-Path $steamapps "common\$installDir")
                if ($paks) { return $paks }
            }
        }

        $common = Join-Path $steamapps 'common'
        if (-not (Test-Path -LiteralPath $common)) { continue }
        foreach ($game in (Get-ChildItem -LiteralPath $common -Directory -ErrorAction SilentlyContinue)) {
            foreach ($relative in @(
                'RSDragonwilds\Content\Paks\RSDragonwilds-Windows.utoc',
                'Content\Paks\RSDragonwilds-Windows.utoc'
            )) {
                $marker = Join-Path $game.FullName $relative
                if (Test-Path -LiteralPath $marker) {
                    return (Split-Path -Parent $marker)
                }
            }
        }
    }
    return $null
}

function Find-GamePaks {
    foreach ($key in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 1374490',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 1374490',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 1374490'
    )) {
        if (-not (Test-Path $key)) { continue }
        $location = (Get-ItemProperty $key).InstallLocation
        $paks = Find-PaksInTree $location
        if ($paks) { return $paks }
    }

    $seen = @{}
    $paks = Search-LibraryList (Get-SteamLibraries) $seen
    if ($paks) { return $paks }
    return Search-LibraryList (Get-DriveSteamLibraries) $seen
}

if ($MyInvocation.InvocationName -ne '.') {
try {
    if (-not $Remove) {
        if (-not $Source) {
            $Source = Split-Path -Parent $PSCommandPath
        }
        $Source = $Source.Trim().Trim('"').TrimEnd('\')
    }

    $paks = Find-GamePaks
    if (-not $paks) {
        Show-Notice "RuneScape Dragonwilds was not found in Steam.`r`n`r`nInstall the game from Steam, then double-click this again." 'Spell cooldowns' 'Warning'
        exit 1
    }

    $mods = Join-Path $paks '~mods'
    if ($Remove) {
        $removed = $false
        foreach ($name in $names) {
            $target = Join-Path $mods $name
            if (Test-Path -LiteralPath $target) {
                Remove-Item -LiteralPath $target -Force
                $removed = $true
            }
        }
        if (-not $removed) {
            Show-Notice "The shorter cooldowns were not installed on this computer." 'Spell cooldowns' 'Information'
            exit 0
        }
        Show-Notice "The normal cooldowns are back.`r`n`r`nClose RuneScape Dragonwilds if it is open, then start it from Steam." 'Spell cooldowns' 'Information'
        exit 0
    }

    foreach ($name in $names) {
        $file = Join-Path $Source $name
        if (-not (Test-Path -LiteralPath $file)) {
            Show-Notice "Unzip the whole folder first, then double-click `"Double-click to install`".`r`n`r`nDo not run it from inside the zip window." 'Spell cooldowns' 'Warning'
            exit 1
        }
    }

    New-Item -ItemType Directory -Force -Path $mods | Out-Null
    foreach ($name in $names) {
        Copy-Item -LiteralPath (Join-Path $Source $name) -Destination (Join-Path $mods $name) -Force
    }

    Show-Notice @"
Shorter cooldowns are installed.

Close RuneScape Dragonwilds if it is open, then start it from Steam.

Rocksplosion and Splinter: 5 seconds
Humidify, Home Teleport, and Uproot: 1 second

Files were copied to:
$mods
"@ 'Spell cooldowns' 'Information'
    exit 0
}
catch {
    $detail = $_.Exception.Message
    if ($detail -match 'being used by another process') {
        $detail = "Close RuneScape Dragonwilds, then double-click this again."
    }
    if ($Remove) {
        $lead = "The normal cooldowns could not be restored."
    } else {
        $lead = "The shorter cooldowns could not be installed."
    }
    Show-Notice "$lead`r`n`r`n$detail" 'Spell cooldowns' 'Error'
    exit 1
}
}
