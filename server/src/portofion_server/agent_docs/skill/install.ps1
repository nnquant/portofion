# Installs the portofion agent skill and saves the API key.
#
#   $env:PORTOFION_API_KEY='pfn_...'; irm {{BASE_URL}}/skill/install.ps1 | iex
#
# PORTOFION_SKILL_DIRS  ';'-separated skill folders (default: ~\.claude\skills, plus ~\.codex\skills if ~\.codex exists)
# PORTOFION_CONFIG      credentials file (default: ~\.portofion\config.json)
& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $base = '{{BASE_URL}}'
  $key = $env:PORTOFION_API_KEY

  $tmp = Join-Path ([IO.Path]::GetTempPath()) ('portofion-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tmp | Out-Null
  try {
    $zip = Join-Path $tmp 'portofion.zip'
    Invoke-WebRequest -UseBasicParsing -Uri "$base/skill/portofion.zip" -OutFile $zip

    if ($env:PORTOFION_SKILL_DIRS) {
      $dirs = @($env:PORTOFION_SKILL_DIRS -split ';' | Where-Object { $_ })
    } else {
      $dirs = @(Join-Path $HOME '.claude\skills')
      if (Test-Path (Join-Path $HOME '.codex')) { $dirs += (Join-Path $HOME '.codex\skills') }
    }
    foreach ($d in $dirs) {
      New-Item -ItemType Directory -Force -Path $d | Out-Null
      Expand-Archive -Path $zip -DestinationPath $d -Force
      Write-Output "skill installed: $(Join-Path $d 'portofion')"
    }

    if ($key) {
      $cfg = if ($env:PORTOFION_CONFIG) { $env:PORTOFION_CONFIG } else { Join-Path $HOME '.portofion\config.json' }
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $cfg) | Out-Null
      [IO.File]::WriteAllText($cfg, (@{ url = $base; api_key = $key } | ConvertTo-Json -Compress))
      Write-Output "credentials saved: $cfg"
      try {
        $who = Invoke-RestMethod -Uri "$base/api/v1/agent/whoami" -Headers @{ Authorization = "Bearer $key" }
        Write-Output "connected: workspace $($who.workspace), key $($who.key), scopes $($who.scopes -join ',')"
      } catch {
        Write-Output "warning: the key check failed: $($_.Exception.Message)"
      }
    }

    $py = $null
    foreach ($c in 'python', 'python3', 'py') {
      if (Get-Command $c -ErrorAction SilentlyContinue) {
        & $c -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
      }
    }
    if (-not $py) { Write-Output "note: the skill's script needs Python 3.8+, which was not found on PATH" }
  } finally {
    Remove-Item -Recurse -Force -Path $tmp -ErrorAction SilentlyContinue
  }
}
