# Kalınlaştırma koşusunu Görev Zamanlayıcı'ya bağlar: Ollama sunucusu + çekici + hakem, oturum açılışında otomatik.
# Kullanım: .\verify\baslat-kalinlastir.ps1            (görevleri kurar ve hemen başlatır)
#           .\verify\baslat-kalinlastir.ps1 -Kaldir    (görevleri siler; DUR dosyasıyla nazik durdurma ayrıdır)
param([switch]$Kaldir)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo '.venv\Scripts\python.exe'
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
$kalin = Join-Path $PSScriptRoot 'kalinlastir.py'
$gorevler = @(
  @{ ad = 'Verify-Ollama';  exe = $ollama; arg = 'serve';                      gecikme = 'PT30S' },
  @{ ad = 'Verify-Cekici';  exe = $py;     arg = "`"$kalin`" cek";              gecikme = 'PT2M' },
  @{ ad = 'Verify-Hakem';   exe = $py;     arg = "`"$kalin`" yargila";          gecikme = 'PT3M' }
)
foreach ($g in $gorevler) { Unregister-ScheduledTask -TaskName $g.ad -Confirm:$false -ErrorAction SilentlyContinue }
if ($Kaldir) { "görevler kaldırıldı"; return }
Remove-Item (Join-Path $PSScriptRoot 'DUR') -ErrorAction SilentlyContinue
foreach ($g in $gorevler) {
  $action = New-ScheduledTaskAction -Execute $g.exe -Argument $g.arg -WorkingDirectory $repo
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $trigger.Delay = $g.gecikme
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 5)
  Register-ScheduledTask -TaskName $g.ad -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited | Out-Null
}
# sırayla başlat: önce Ollama, sonra işçiler
Start-ScheduledTask -TaskName 'Verify-Ollama'; Start-Sleep 8
Start-ScheduledTask -TaskName 'Verify-Cekici'; Start-Sleep 3
Start-ScheduledTask -TaskName 'Verify-Hakem'
Start-Sleep 5
Get-ScheduledTask -TaskName 'Verify-*' | Select-Object TaskName, State | Format-Table -AutoSize
"Durdurmak için: New-Item '$PSScriptRoot\DUR'  (işçiler partiyi bitirip çıkar; görevler oturum açılışında yeniden başlar — kalıcı durdurma: -Kaldir)"
