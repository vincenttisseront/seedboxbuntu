<#
.SYNOPSIS
    SeedBoxBuntu - construction de l'ISO sous Windows (Docker Desktop).

.DESCRIPTION
    Fonctionne depuis Windows 10 ou 11, sans WSL à installer soi-même, sans Git Bash.
    Lancez-le depuis le dossier des sources (celui qui contient build.sh, lib/ et rootfs/) :
    build.sh y est exécuté dans un conteneur Ubuntu 24.04 de Docker Desktop. L'image de P2P Aggregator est construite
    par ce même Docker Desktop, puis embarquée dans l'ISO.

    L'ISO est produite dans le sous-dossier « out » (modifiable avec -OutputDir). Le téléchargement d'Ubuntu et les fichiers de
    travail restent dans le volume Docker « seedboxbuntu-build » : un second lancement
    ne retélécharge pas Ubuntu. Supprimez-le avec : docker volume rm seedboxbuntu-build

    Les certificats racines de Windows sont transmis au conteneur : la construction
    fonctionne derrière un proxy d'entreprise qui inspecte le HTTPS.

    Prérequis : Docker Desktop démarré, en mode conteneurs Linux, avec environ 20 Go
    libres dans son disque virtuel, et une connexion Internet.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build.ps1
    Demande le jeton GitHub, télécharge Ubuntu Server 24.04 et construit l'ISO.

.EXAMPLE
    .\build.ps1 -IsoPath .\ubuntu-24.04.5-live-server-amd64.iso -Keyboard fr -Timezone Europe/Paris
#>
[CmdletBinding()]
param(
    # Jeton GitHub en lecture du dépôt privé p2p-aggregator (demandé s'il est absent)
    [string]$GithubToken = $env:GITHUB_TOKEN,
    # ISO Ubuntu Server déjà téléchargée ; sinon téléchargée automatiquement
    [string]$IsoPath = "",
    [string]$HostName = "seedbox",
    [string]$Keyboard = "fr",
    [string]$KeyboardVariant = "",
    [string]$Locale = "fr_FR.UTF-8",
    [string]$Timezone = "Europe/Paris",
    [int]$GrubTimeout = 20,
    [string]$P2pBranch = "main",
    # Clone local de p2p-aggregator à utiliser au lieu de GitHub (le jeton devient facultatif)
    [string]$P2pSource = "",
    # Certificat racine PEM d'un proxy d'entreprise, à installer aussi sur le serveur
    [string]$ExtraCaCerts = "",
    # Ne pas embarquer l'image P2P Aggregator : elle sera construite au premier démarrage
    [switch]$NoPrebuild,
    # Dossier de sortie de l'ISO (par défaut : sous-dossier « out » des sources)
    [string]$OutputDir = ""
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$utf8 = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = $utf8 } catch { }

function Write-Step([string]$text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Info([string]$text) { Write-Host "    $text" }
function Stop-Build([string]$text) { Write-Host "[ERREUR] $text" -ForegroundColor Red; exit 1 }

Write-Step "SeedBoxBuntu : construction de l'ISO sous Windows"

# --- 1. Docker Desktop
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Build "Docker Desktop est introuvable : installez-le (https://www.docker.com/products/docker-desktop/), démarrez-le, puis relancez."
}
$osType = (& docker info --format '{{.OSType}}' 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $osType) { Stop-Build "Docker est installé mais pas démarré : lancez Docker Desktop, attendez qu'il soit prêt, puis relancez." }
if ($osType.Trim() -ne 'linux') { Stop-Build "Docker Desktop est en mode « conteneurs Windows » : clic droit sur l'icône Docker > « Switch to Linux containers », puis relancez." }

# --- 2. Dossier des sources
if ($PSScriptRoot) { $srcDir = $PSScriptRoot } else { $srcDir = (Get-Location).Path }
foreach ($required in 'build.sh', 'lib', 'rootfs', 'stack', 'templates') {
    if (-not (Test-Path -LiteralPath (Join-Path $srcDir $required))) { Stop-Build "dossier des sources incomplet : '$required' est introuvable à côté de build.ps1" }
}
# Fins de ligne : bash refuse un script en CRLF (git avec autocrlf, éditeur Windows)
foreach ($file in (@(Get-Item -LiteralPath (Join-Path $srcDir 'build.sh')) + @(Get-ChildItem -LiteralPath (Join-Path $srcDir 'lib') -Filter '*.sh'))) {
    if ([System.IO.File]::ReadAllBytes($file.FullName) -contains 13) { Stop-Build "$($file.Name) a des fins de ligne Windows (CRLF) : récupérez les sources sans conversion (git config core.autocrlf false) ou convertissez en LF." }
}
if ($OutputDir) { $outDir = $OutputDir } else { $outDir = Join-Path $srcDir 'out' }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$outDir = (Resolve-Path $outDir).Path
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("seedboxbuntu-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {
    # --- 3. Certificats racines de Windows (proxy d'entreprise qui inspecte le HTTPS)
    $pem = New-Object System.Text.StringBuilder
    $count = 0
    foreach ($store in 'Cert:\LocalMachine\Root', 'Cert:\CurrentUser\Root') {
        if (-not (Test-Path $store)) { continue }
        foreach ($cert in Get-ChildItem $store) {
            [void]$pem.Append("-----BEGIN CERTIFICATE-----`n")
            [void]$pem.Append([Convert]::ToBase64String($cert.RawData, 'InsertLineBreaks').Replace("`r", ""))
            [void]$pem.Append("`n-----END CERTIFICATE-----`n")
            $count++
        }
    }
    $caDir = Join-Path $tmp 'host-ca'
    New-Item -ItemType Directory -Force -Path $caDir | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $caDir 'windows.pem'), $pem.ToString(), $utf8)
    Write-Info "certificats racines de Windows transmis au conteneur : $count"

    # --- 4. Paramètres
    if ($P2pSource -and -not (Test-Path -LiteralPath (Join-Path $P2pSource '.git'))) { Stop-Build "pas un clone git : $P2pSource" }
    if (-not $GithubToken -and -not $P2pSource) {
        $secure = Read-Host "Jeton GitHub en lecture du dépôt p2p-aggregator" -AsSecureString
        $GithubToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    }
    if (-not $GithubToken -and -not $P2pSource) { Stop-Build "jeton GitHub requis pour télécharger le dépôt privé p2p-aggregator" }

    $mounts = @('-v', "${outDir}:/out", '-v', "${tmp}:/launcher:ro", '-v', "${srcDir}:/src:ro", '-v', 'seedboxbuntu-build:/build',
                '-v', '/var/run/docker.sock:/var/run/docker.sock')
    $scriptArgs = @()
    if ($IsoPath) {
        if (-not (Test-Path -LiteralPath $IsoPath -PathType Leaf)) { Stop-Build "ISO introuvable : $IsoPath" }
        $iso = (Resolve-Path -LiteralPath $IsoPath).Path
        $mounts += @('-v', ((Split-Path $iso -Parent) + ':/iso:ro'))
        $scriptArgs += "/iso/" + (Split-Path $iso -Leaf)
    }
    if ($P2pSource) { $mounts += @('-v', ((Resolve-Path -LiteralPath $P2pSource).Path + ':/p2p-src:ro')) }
    if ($ExtraCaCerts) {
        if (-not (Test-Path -LiteralPath $ExtraCaCerts -PathType Leaf)) { Stop-Build "certificat introuvable : $ExtraCaCerts" }
        Copy-Item -LiteralPath $ExtraCaCerts (Join-Path $tmp 'extra-ca.pem')
    }

    # Variables transmises par fichier : le jeton n'apparaît pas sur la ligne de commande
    $envFile = Join-Path $tmp 'build.env'
    $vars = [ordered]@{
        GITHUB_TOKEN = $GithubToken; HOST_NAME = $HostName; KEYBOARD = $Keyboard; KEYBOARD_VARIANT = $KeyboardVariant
        LOCALE = $Locale; TIMEZONE = $Timezone; GRUB_TIMEOUT = "$GrubTimeout"; P2P_BRANCH = $P2pBranch
        P2P_PREBUILD = $(if ($NoPrebuild) { '0' } else { '1' }); WORKDIR = '/build'
    }
    if ($P2pSource) { $vars['P2P_SOURCE'] = '/p2p-src' }
    if ($ExtraCaCerts) { $vars['EXTRA_CA_CERTS'] = '/launcher/extra-ca.pem' }
    foreach ($name in 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY') {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $vars[$name] = $value }
    }
    foreach ($entry in $vars.GetEnumerator()) {
        if ("$($entry.Value)" -match "[`r`n]") { Stop-Build "valeur invalide pour $($entry.Key)" }
    }
    $lines = $vars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
    [System.IO.File]::WriteAllText($envFile, (($lines -join "`n") + "`n"), $utf8)

    # --- 5. Construction dans un conteneur Ubuntu 24.04
    Write-Step "construction dans un conteneur Linux Ubuntu 24.04 (20 à 40 minutes)"
    Write-Info "ISO produite dans : $outDir"
    $inner = @'
set -e
echo "    installation des outils dans le conteneur..."
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  xorriso curl ca-certificates python3 zstd git openssl docker.io >/dev/null \
  || { echo "[ERREUR] installation des outils impossible dans le conteneur" >&2; exit 1; }
if grep -q "BEGIN CERTIFICATE" /launcher/host-ca/windows.pem 2>/dev/null; then
  awk '/BEGIN CERTIFICATE/ {n++; f=sprintf("/usr/local/share/ca-certificates/poste-%04d.crt", n)} f {print > f} /END CERTIFICATE/ {close(f); f=""}' /launcher/host-ca/windows.pem
  update-ca-certificates >/dev/null 2>&1 || echo "[!] update-ca-certificates a échoué" >&2
fi
# Dossiers montés depuis Windows : propriétaire différent de root pour git
git config --global --add safe.directory '*'
cd /out
exec bash /src/build.sh "$@"
'@
    $tty = @()
    if (-not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) { $tty = @('-it') }
    # Script écrit dans un fichier : PowerShell 5.1 transmet mal les guillemets aux commandes externes
    [System.IO.File]::WriteAllText((Join-Path $tmp 'inner.sh'), $inner.Replace("`r", ""), $utf8)
    & docker run --rm @tty @mounts --env-file $envFile -e SB_WINDOWS=1 ubuntu:24.04 bash /launcher/inner.sh @scriptArgs
    $code = $LASTEXITCODE
    if ($code -ne 0) { Stop-Build "la construction a échoué (code $code) : voir les messages ci-dessus" }
    Write-Step "terminé : l'ISO est dans $outDir"
    Write-Info "Premier démarrage du serveur : l'assistant de la console demande le réseau, le nom et le mot de passe."
    Write-Info "Clé USB : Rufus en mode « DD » ou balenaEtcher. Machine virtuelle : Hyper-V (génération 2, démarrage sécurisé désactivé) ou VirtualBox."
}
finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
