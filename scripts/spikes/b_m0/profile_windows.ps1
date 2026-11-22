# Re-run the offline SDK workers and sample each process's Windows working set.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$JiuwenPython,
    [Parameter(Mandatory)][string]$FrameworkPython,
    [string]$Output = "outputs/b-m0/profile"
)
$ErrorActionPreference = "Stop"
$worker = Join-Path $PSScriptRoot "worker.py"
$sharedPython = (Resolve-Path -LiteralPath $FrameworkPython).Path
$interpreters = [ordered]@{openjiuwen=(Resolve-Path -LiteralPath $JiuwenPython).Path; langgraph=$sharedPython; agents=$sharedPython}
$root = [IO.Directory]::CreateDirectory((Join-Path $Output ("profile-" + [Guid]::NewGuid().ToString("N")))).FullName
$rows = foreach ($name in $interpreters.Keys) {
    $directory = [IO.Directory]::CreateDirectory((Join-Path $root $name)).FullName
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $interpreters[$name]
    # The Windows venv executable can be a launcher. The running SDK worker identifies its own PID.
    $bootstrap = "import os,runpy,sys; from pathlib import Path; Path('worker-pid.txt').write_text(str(os.getpid())); sys.argv=sys.argv[1:]; sys.path.insert(0,str(Path(sys.argv[0]).parent)); runpy.run_path(sys.argv[0],run_name='__main__')"
    $start.Arguments = '-B -c "' + $bootstrap + '" "' + $worker + '" ' + $name + ' m0'
    $start.WorkingDirectory = $directory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.EnvironmentVariables.Clear()
    foreach ($varName in ('PATH','SYSTEMROOT','WINDIR','COMSPEC','SYSTEMDRIVE','PATHEXT','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMDATA','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE')) {
        $value = [Environment]::GetEnvironmentVariable($varName)
        if ($value) { $start.EnvironmentVariables[$varName] = $value }
    }
    $start.EnvironmentVariables['PYTHONUTF8'] = '1'
    $start.EnvironmentVariables['OPENAI_AGENTS_DISABLE_TRACING'] = '1'
    $start.EnvironmentVariables['OTEL_SDK_DISABLED'] = 'true'
    $start.EnvironmentVariables['LANGSMITH_TRACING'] = 'false'
    $start.EnvironmentVariables['HF_HUB_OFFLINE'] = '1'
    $proc = [Diagnostics.Process]::Start($start)
    $workerProcess = $null
    $pidFile = Join-Path $directory 'worker-pid.txt'
    $peak = 0L
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        $stdout = $proc.StandardOutput.ReadToEndAsync()
        $stderr = $proc.StandardError.ReadToEndAsync()
        while (-not $proc.WaitForExit(50)) {
            if (-not $workerProcess -and [IO.File]::Exists($pidFile)) {
                $workerId = [IO.File]::ReadAllText($pidFile).Trim()
                if ($workerId) { $workerProcess = [Diagnostics.Process]::GetProcessById([int]$workerId) }
            }
            if ($workerProcess -and -not $workerProcess.HasExited) {
                $workerProcess.Refresh()
                $peak = [Math]::Max($peak, $workerProcess.WorkingSet64)
            }
            if ($timer.Elapsed.TotalSeconds -gt 180) { $proc.Kill(); throw "$name timed out" }
        }
        $null = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        if ($proc.ExitCode -ne 0) { throw "$name failed with exit code $($proc.ExitCode)" }
    } finally {
        $timer.Stop()
        if ($workerProcess) { $workerProcess.Dispose() }
        $proc.Dispose()
    }
    $result = Get-Content -Raw -Encoding utf8 (Join-Path $directory "result.json") | ConvertFrom-Json
    if (-not $result.passed -or $result.trials.Count -ne 36 -or $peak -le 0) { throw "$name measurements incomplete" }
    $bytes = (Get-ChildItem -LiteralPath $directory -Filter 'S??-*' -Directory | Get-ChildItem -File -Recurse | Measure-Object Length -Sum).Sum
    [pscustomobject]@{framework=$name; trials=$result.trials.Count; worker_wall_ns=$result.wall_ns;
        worker_cpu_ns=$result.cpu_ns; sampled_peak_working_set_bytes=$peak; scenario_files_bytes=$bytes;
        including_startup_ms=$timer.ElapsedMilliseconds}
}
$report = Join-Path $root 'report.json'
[ordered]@{format='b-m0-windows-profile-v1'; completed_at=[DateTimeOffset]::UtcNow.ToString('o');
    git_head=(git -C $PSScriptRoot rev-parse HEAD); script_sha256=(Get-FileHash $PSCommandPath).Hash.ToLowerInvariant();
    sampling_interval_ms=50; online_model_calls=0; frameworks=@($rows)} | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $report
Write-Output $report
