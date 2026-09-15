Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    ForEach-Object {
        Write-Output ("pid=" + $_.ProcessId + " parent=" + $_.ParentProcessId +
            " created=" + $_.CreationDate.ToString("HH:mm:ss") + " cmd=" + $_.CommandLine)
    }
