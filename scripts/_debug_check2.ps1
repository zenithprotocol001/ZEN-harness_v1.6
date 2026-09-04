Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead("C:\Users\Rex\.config\opencode\harness_benchmark\relay\harness_benchmark-v1.5.1-20260909.zip")
$names = @($z.Entries.FullName)
$z.Dispose()
$names | ForEach-Object { $_.Substring(0, [Math]::Min(50, $_.Length)) } | Sort-Object | Get-Unique | Select-Object -First 20
"---"
$names | Where-Object { $_.StartsWith("harness_benchmark/") } | Measure-Object | Select-Object Count
"---"
$names | Select-Object -First 3
"---"
$names | Select-Object -Last 3
