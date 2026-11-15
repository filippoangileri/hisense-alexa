# Crea build\lambda.zip pronto da caricare su AWS Lambda (runtime Python 3.12+).
# Uso:  powershell -ExecutionPolicy Bypass -File .\build.ps1
#       powershell -ExecutionPolicy Bypass -File .\build.ps1 -FunctionName hisense-alexa   (carica anche con AWS CLI)
param([string]$FunctionName = "", [string]$Region = "eu-west-1")

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $root "build"
New-Item -ItemType Directory -Force $out | Out-Null
$zip = Join-Path $out "lambda.zip"
if (Test-Path $zip) { Remove-Item $zip -Confirm:$false }
Compress-Archive -Path (Join-Path $root "lambda\*.py") -DestinationPath $zip
Write-Host "Creato $zip"

if ($FunctionName) {
    aws lambda update-function-code --function-name $FunctionName --zip-file "fileb://$zip" --region $Region
}
