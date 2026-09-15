$base = 'C:\Users\__DIR__/Mark-LIII'
# Stop ALL old JARVIS + launcher processes
$p = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*main.py*' -or $_.CommandLine -like '*voice_launch.py*' }
foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force }
Start-Sleep -Seconds 2

# Start voice launcher (same command the startup registry uses)
Start-Process -FilePath (Join-Path $base '.venv\Scripts\pythonw.exe') `
    -ArgumentList ('"' + (Join-Path $base 'voice-launcher\voice_launch.py') + '"') `
    -WorkingDirectory (Join-Path $base 'voice-launcher') -WindowStyle Hidden
Start-Sleep -Seconds 3

# Start JARVIS (exactly one)
Start-Process -FilePath (Join-Path $base '.venv\Scripts\pythonw.exe') `
    -ArgumentList 'main.py' -WorkingDirectory $base -WindowStyle Hidden
Start-Sleep -Seconds 8

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*main.py*' -or $_.CommandLine -like '*voice_launch.py*' } |
    ForEach-Object {
        $c = $_.CommandLine
        if ($c.Length -gt 90) { $c = $c.Substring(0, 90) }
        Write-Output ("RUNNING pid=" + $_.ProcessId + " cmd=" + $c)
    }
