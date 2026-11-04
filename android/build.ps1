# Builds EqRGB.apk (no Gradle: aapt2 + javac + d8 + apksigner). Usage: powershell -File build.ps1 -Version 0.4.5
# The version should match the release tag the APK is attached to: the app updates itself by comparing the two.
param([Parameter(Mandatory)][string]$Version)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:JAVA_HOME) { $env:JAVA_HOME = (Get-ChildItem "$env:ProgramFiles\Eclipse Adoptium" -Directory -Filter "jdk-21*" | Select-Object -First 1).FullName }  # or set JAVA_HOME yourself
# Signing-key password: set RGB_KS_PASS, or the local default below is used for the key you generate here.
$v = [int[]]($Version -split '\.')
if ($v.Count -ne 3) { throw "-Version must look like 0.4.5" }
$versionCode = $v[0] * 10000 + $v[1] * 100 + $v[2]
$kspass = if ($env:RGB_KS_PASS) { $env:RGB_KS_PASS } else { "rgbcontrol" }
$jbin = "$env:JAVA_HOME\bin"
$bt = "$root\sdk\build-tools\34.0.0"
$jar = "$root\sdk\platforms\android-34\android.jar"
$b = "$root\build"
$ks = "$root\rgb-control.keystore"

# Android SDK pieces, fetched once into android\sdk (gitignored) with Google's command-line tools
if (-not ((Test-Path $bt) -and (Test-Path $jar))) {
    $cl = "$root\tools\cmdline-tools"
    if (-not (Test-Path "$cl\bin\sdkmanager.bat")) {
        New-Item -ItemType Directory -Force "$root\tools" | Out-Null
        curl.exe -L -s -o "$root\tools\cmdline.zip" "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
        Expand-Archive "$root\tools\cmdline.zip" -DestinationPath "$root\tools" -Force
        Remove-Item "$root\tools\cmdline.zip"
    }
    $sdkm = "$cl\bin\sdkmanager.bat"
    (1..12 | ForEach-Object { "y" }) | & $sdkm "--sdk_root=$root\sdk" --licenses | Out-Null
    & $sdkm "--sdk_root=$root\sdk" "build-tools;34.0.0" "platforms;android-34" | Out-Null
    if (-not ((Test-Path $bt) -and (Test-Path $jar))) { throw "Android SDK download failed" }
}

if (Test-Path $b) { Remove-Item $b -Recurse -Force }
New-Item -ItemType Directory -Force "$b\classes", "$b\dex" | Out-Null

# one-time signing key (kept so future builds can update an installed app)
if (-not (Test-Path $ks)) {
    & "$jbin\keytool.exe" -genkeypair -keystore $ks -storepass $kspass -keypass $kspass -alias rgb `
        -keyalg RSA -keysize 2048 -validity 36500 -dname "CN=EqRGB" | Out-Null
}

& "$bt\aapt2.exe" compile --dir "$root\app\res" -o "$b\res.zip"
if ($LASTEXITCODE) { throw "aapt2 compile failed" }
& "$bt\aapt2.exe" link -o "$b\unsigned.apk" -I $jar --manifest "$root\app\AndroidManifest.xml" `
    --min-sdk-version 24 --target-sdk-version 34 --version-code $versionCode --version-name $Version "$b\res.zip"
if ($LASTEXITCODE) { throw "aapt2 link failed" }

$src = (Get-ChildItem "$root\app\src" -Recurse -Filter *.java).FullName
& "$jbin\javac.exe" --release 8 -Xlint:-options -cp $jar -d "$b\classes" $src
if ($LASTEXITCODE) { throw "javac failed" }
& "$jbin\jar.exe" cf "$b\classes.jar" -C "$b\classes" .
# SDK build-tools 34.0.0 ships an old d8 that crashes on this code; use a newer one from maven.google.com
$r8 = "$root\tools\r8.jar"
if (-not (Test-Path $r8)) {
    New-Item -ItemType Directory -Force "$root\tools" | Out-Null
    curl.exe -L -s -o $r8 "https://dl.google.com/dl/android/maven2/com/android/tools/r8/8.5.35/r8-8.5.35.jar"
}
& "$jbin\java.exe" -cp $r8 com.android.tools.r8.D8 --lib $jar --min-api 24 --output "$b\dex" "$b\classes.jar"
if ($LASTEXITCODE) { throw "d8 failed" }

# add classes.dex to the APK without touching the other entries (resources.arsc must stay uncompressed)
python -I -c "import zipfile,sys; z=zipfile.ZipFile(sys.argv[1],'a'); z.write(sys.argv[2],'classes.dex'); z.close()" "$b\unsigned.apk" "$b\dex\classes.dex"
& "$bt\zipalign.exe" -p -f 4 "$b\unsigned.apk" "$b\aligned.apk"
if ($LASTEXITCODE) { throw "zipalign failed" }
$out = "$root\EqRGB.apk"
& "$bt\apksigner.bat" sign --ks $ks --ks-pass pass:$kspass --key-pass pass:$kspass --out $out "$b\aligned.apk"
if ($LASTEXITCODE) { throw "apksigner failed" }
& "$bt\apksigner.bat" verify --verbose $out | Select-Object -First 4
"built: $out ({0:N0} KB)" -f ((Get-Item $out).Length / 1KB)
