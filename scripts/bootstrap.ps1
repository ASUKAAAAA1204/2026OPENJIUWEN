param(
 [Parameter(Mandatory=$true)][string]$Python,
 [string]$Environment='.venv-m0',
 [ValidateSet('core','jiuwen','ledger')][string]$Profile='core',
 [string]$IndexUrl='https://pypi.org/simple'
)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$taskOldTemp=$env:TEMP
$taskOldTmp=$env:TMP
$taskTemp=Join-Path $taskRoot 'outputs/baseline/install-temp'
New-Item -ItemType Directory -Force $taskTemp | Out-Null
$env:TEMP=$taskTemp
$env:TMP=$taskTemp
Push-Location $taskRoot
try {
 if (!(Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Explicit Python executable does not exist' }
 $taskTarget=[IO.Path]::GetFullPath((Join-Path $taskRoot $Environment))
 if (!$taskTarget.StartsWith($taskRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Environment must stay inside workspace' }
 if (Test-Path -LiteralPath $taskTarget) { throw 'Target exists; use a fresh environment for reproducible bootstrap' }
 & $Python -m venv $taskTarget
 if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
 $taskInterpreter=Join-Path $taskTarget 'Scripts/python.exe'
 $taskPackages=@('-r','requirements/m0-core-py312.lock.txt')
 if ($Profile -eq 'jiuwen') { $taskPackages=@('-r','requirements/m0-jiuwen-py312.lock.txt') }
 if ($Profile -eq 'ledger') { $taskPackages=@('-r','requirements/m1-ledger-py312.lock.txt') }
 & $taskInterpreter -m pip --disable-pip-version-check --no-cache-dir install --index-url $IndexUrl --timeout 5 --retries 0 @taskPackages
 if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; environment is not accepted' }
 & $taskInterpreter -m pip --disable-pip-version-check install --no-build-isolation --no-deps -e .
 if ($LASTEXITCODE -ne 0) { throw 'Project installation failed' }
 & $taskInterpreter -m pip check
 if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed' }
 & $taskInterpreter scripts/baseline/check_imports.py
 if ($LASTEXITCODE -ne 0) { throw 'Import origins check failed' }
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 } finally { $env:TEMP=$taskOldTemp; $env:TMP=$taskOldTmp; Pop-Location }
