# enable-autostart.ps1 — control what starts with Windows.
#
#   Default:  wake-word listener only  ->  "Jarvis good morning" works after
#             every reboot, while the main JARVIS app stays manual.
#   -Full:    also auto-start the main app (HUD + voice session at boot).
#   -Off:     remove auto-start entirely.
#
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File enable-autostart.ps1

param(
    [switch]$Full,
    [switch]$Off
)

$pyw   = 'C:\Users\__DIR__/Mark-LIII\.venv\Scripts\pythonw.exe'
$base  = 'C:\Users\__DIR__/Mark-LIII'
$run   = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'

if ($Off) {
    Remove-ItemProperty -Path $run -Name 'JARVIS_VOICE_LAUNCHER' -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $run -Name 'JARVIS_AI'             -ErrorAction SilentlyContinue
    Write-Output 'Auto-start removed: JARVIS will not start with Windows.'
    exit
}

# Wake-word listener: tiny, offline, lets 'Jarvis good morning' boot the app.
New-ItemProperty -Path $run -Name 'JARVIS_VOICE_LAUNCHER' `
    -Value ('"{0}" "{1}"' -f $pyw, (Join-Path $base 'voice-launcher\voice_launch.py')) `
    -PropertyType String -Force | Out-Null
Write-Output 'Wake-word listener will start with Windows ("Jarvis good morning" ready).'

if ($Full) {
    New-ItemProperty -Path $run -Name 'JARVIS_AI' `
        -Value ('"{0}" "{1}"' -f $pyw, (Join-Path $base 'main.py')) `
        -PropertyType String -Force | Out-Null
    Write-Output 'Main app will ALSO start with Windows.'
} else {
    Remove-ItemProperty -Path $run -Name 'JARVIS_AI' -ErrorAction SilentlyContinue
    Write-Output 'Main app stays manual: launch it yourself, or say "Jarvis good morning".'
}
