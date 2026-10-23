// RGB Anywhere Setup: a small installer window. The release build carries the app zip inside the exe (see
// installerundle.ps1); a plain build downloads the latest release zip instead. Either way it checks the zip's
// SHA-256 and hands it to one elevated step (Windows shows its permission prompt then), which unpacks it to
// %ProgramFiles%\RGB Anywhere and runs install.ps1. The code goes to Program Files because the lighting daemon runs it
// as admin: only admins may change it. The user's settings stay in %LOCALAPPDATA%\RGB Anywhere. No console window.
//
//   RGB-Anywhere-Setup.exe          normal use
//   RGB-Anywhere-Setup.exe /auto    start installing with the default choices, no clicking (for testing)
//   RGBANYWHERE_NO_INSTALL=1        stop after checking the zip (for testing; skips the permission prompt)
//
// Copyright (C) 2026 EQQX2
// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WINVER 0x0A00
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <commctrl.h>
#include <wininet.h>
#include <wincrypt.h>
#include <shlobj.h>
#include <shellapi.h>
#include <cstdio>
#include <cstring>
#include <algorithm>
#include <cwchar>
#include <string>
#include <vector>

#ifndef DM_GETDEFID
#define DM_GETDEFID (WM_USER + 0)
#endif

static const wchar_t *BASE = L"https://github.com/EQQX0/rgbanywhere/releases/latest/download/";
static const wchar_t *ZIPNAME = L"RGBAnywhere-win64.zip";

enum Ids { ID_INSTALL = 1, ID_CANCEL = 2, ID_LOG = 3, ID_CHK_DESKTOP = 10, ID_CHK_STARTUP, ID_CHK_LAUNCH };
enum Msgs { WM_PROGRESS = WM_APP + 1, WM_STATUS, WM_FINISHED, WM_MARQUEE };
enum Page { PAGE_OPTIONS, PAGE_WORKING, PAGE_DONE };

static HWND g_wnd, g_title, g_sub, g_pathLabel, g_path, g_chkDesktop, g_chkStartup, g_chkLaunch, g_note, g_status, g_bar,
    g_detail, g_msg, g_btnPrimary, g_btnCancel, g_btnLog;
static HFONT g_font, g_fontTitle, g_fontBold;
static HBRUSH g_white, g_band;
static int g_dpi = 96, g_page = PAGE_OPTIONS;
static volatile bool g_cancel = false, g_elevated = false;
static bool g_success = false, g_launch = true, g_auto = false;
static std::wstring g_dest, g_legacy, g_log;

static int S(int v) { return MulDiv(v, g_dpi, 96); }

static std::wstring env(const wchar_t *name)
{
    wchar_t buf[2048];
    DWORD n = GetEnvironmentVariableW(name, buf, 2048);
    return n && n < 2048 ? std::wstring(buf, n) : std::wstring();
}

// ---- worker: download, verify, unpack, install -------------------------------------------------------------------

static void post_status(const std::wstring &s) { PostMessageW(g_wnd, WM_STATUS, 0, (LPARAM) new std::wstring(s)); }
static void post_progress(int pct) { PostMessageW(g_wnd, WM_PROGRESS, pct, 0); }

static bool download(HINTERNET net, const std::wstring &url, std::vector<char> *mem, const std::wstring &file, int from, int to,
                     const wchar_t *label)
{
    HINTERNET h = InternetOpenUrlW(net, url.c_str(), NULL, 0, INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE | INTERNET_FLAG_SECURE, 0);
    if (!h) return false;
    DWORD status = 0, len = sizeof status;
    HttpQueryInfoW(h, HTTP_QUERY_STATUS_CODE | HTTP_QUERY_FLAG_NUMBER, &status, &len, NULL);
    if (status != 200) { InternetCloseHandle(h); return false; }
    DWORD total = 0;
    len = sizeof total;
    HttpQueryInfoW(h, HTTP_QUERY_CONTENT_LENGTH | HTTP_QUERY_FLAG_NUMBER, &total, &len, NULL);
    FILE *f = NULL;
    if (!file.empty() && !(f = _wfopen(file.c_str(), L"wb"))) { InternetCloseHandle(h); return false; }
    std::vector<char> buf(1 << 16);
    unsigned long long done = 0;
    int last = -1;
    bool ok = true;
    while (!g_cancel) {
        DWORD got = 0;
        if (!InternetReadFile(h, buf.data(), (DWORD)buf.size(), &got)) { ok = false; break; }
        if (!got) break;
        if (f) fwrite(buf.data(), 1, got, f);
        else mem->insert(mem->end(), buf.begin(), buf.begin() + got);
        done += got;
        if (total) {
            int pct = (int)(done * 100 / total);
            if (pct != last) {
                last = pct;
                post_progress(from + (to - from) * pct / 100);
                wchar_t t[128];
                swprintf(t, 128, L"%ls  %llu of %lu MB", label, done >> 20, total >> 20);
                post_status(t);
            }
        }
    }
    if (f) fclose(f);
    InternetCloseHandle(h);
    return ok && !g_cancel;
}

// SHA-256 of `len` bytes of a file from `from` (the whole file by default)
static std::string sha256_file(const std::wstring &path, unsigned long long from = 0, unsigned long long len = ~0ULL)
{
    FILE *f = _wfopen(path.c_str(), L"rb");
    if (!f) return "";
    if (_fseeki64(f, (long long)from, SEEK_SET) != 0) { fclose(f); return ""; }
    HCRYPTPROV prov = 0;
    HCRYPTHASH hash = 0;
    if (!CryptAcquireContextW(&prov, NULL, NULL, PROV_RSA_AES, CRYPT_VERIFYCONTEXT) || !CryptCreateHash(prov, CALG_SHA_256, 0, 0, &hash)) {
        fclose(f);
        return "";
    }
    std::vector<BYTE> buf(1 << 16);
    size_t n;
    while (len && (n = fread(buf.data(), 1, (size_t)std::min<unsigned long long>(buf.size(), len), f)) > 0) {
        CryptHashData(hash, buf.data(), (DWORD)n, 0);
        len -= n;
    }
    fclose(f);
    BYTE d[32];
    DWORD dl = sizeof d;
    CryptGetHashParam(hash, HP_HASHVAL, d, &dl, 0);
    CryptDestroyHash(hash);
    CryptReleaseContext(prov, 0);
    char hex[65];
    for (int i = 0; i < 32; i++) sprintf(hex + i * 2, "%02x", d[i]);
    return std::string(hex, 64);
}

// The zip can ride inside this exe: [stub exe][zip][64 hex chars of its SHA-256][zip size, 8 bytes LE]["RGBAPAY1"].
struct Payload { bool found; unsigned long long off, size, total; std::string sha; };

static Payload find_payload()
{
    Payload p = {false, 0, 0, 0, ""};
    wchar_t self[MAX_PATH];
    GetModuleFileNameW(NULL, self, MAX_PATH);
    FILE *f = _wfopen(self, L"rb");
    if (!f) return p;
    _fseeki64(f, 0, SEEK_END);
    long long total = _ftelli64(f);
    char t[80];
    if (total > 80 && _fseeki64(f, total - 80, SEEK_SET) == 0 && fread(t, 1, 80, f) == 80 && memcmp(t + 72, "RGBAPAY1", 8) == 0) {
        unsigned long long size = 0;
        for (int i = 7; i >= 0; i--) size = (size << 8) | (unsigned char)t[64 + i];
        if (size > 0 && size <= (unsigned long long)total - 80) {
            p.found = true;
            p.size = size;
            p.total = (unsigned long long)total;
            p.off = p.total - 80 - size;
            p.sha.assign(t, 64);
        }
    }
    fclose(f);
    return p;
}

// Copies bytes [from, from + len) of src into a new file dst, reporting progress between pct `lo` and `hi`.
static bool copy_range(const std::wstring &src, unsigned long long from, unsigned long long len, const std::wstring &dst, int lo, int hi,
                       const wchar_t *label)
{
    FILE *in = _wfopen(src.c_str(), L"rb");
    if (!in) return false;
    FILE *out = _wfopen(dst.c_str(), L"wb");
    if (!out) { fclose(in); return false; }
    bool ok = _fseeki64(in, (long long)from, SEEK_SET) == 0;
    std::vector<char> buf(1 << 20);
    unsigned long long done = 0;
    int last = -1;
    while (ok && done < len && !g_cancel) {
        size_t want = (size_t)std::min<unsigned long long>(buf.size(), len - done);
        size_t got = fread(buf.data(), 1, want, in);
        if (!got || fwrite(buf.data(), 1, got, out) != got) { ok = false; break; }
        done += got;
        int pct = (int)(done * 100 / len);
        if (hi > lo && pct != last) {
            last = pct;
            post_progress(lo + (hi - lo) * pct / 100);
            wchar_t t[128];
            swprintf(t, 128, L"%ls  %llu of %llu MB", label, done >> 20, len >> 20);
            post_status(t);
        }
    }
    fclose(in);
    ok = (fclose(out) == 0) && ok && done == len;
    return ok && !g_cancel;
}

struct Options { bool desktop, startup; };

// The elevated step, for a first install and an update alike. Setup itself runs without admin and cannot write to
// Program Files, so it only prepares the zip and the uninstaller in %TEMP% and checks them. A normal process can still
// change files there, so this step copies both into the protected folder first and checks their SHA-256 again there.
// It is passed inline (-EncodedCommand), not as a file another process could edit, after a prelude that sets $Dir,
// $Zip, $ZipSha, $Stub, $StubSha, $Legacy, $Desktop and $NoStartup. It frees the folder (the lighting daemon, OpenRGB
// and the app keep files open, and an install from before 0.3 ran from $Legacy), unpacks, then runs install.ps1.
static const char INSTALL_PS1[] = R"PS($ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$log = Join-Path $Dir 'install.log'
try {
    Stop-ScheduledTask -TaskName 'RGB Anywhere Lighting' -ErrorAction SilentlyContinue
    foreach ($d in @($Dir, $Legacy)) {
        Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ProcessId -ne $PID -and
                $_.ExecutablePath.StartsWith($d + '\', [StringComparison]::OrdinalIgnoreCase) } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    Start-Sleep -Seconds 2
    $z = Join-Path $Dir 'setup-payload.zip'
    Copy-Item -LiteralPath $Zip -Destination $z -Force
    if ((Get-FileHash -LiteralPath $z -Algorithm SHA256).Hash -ne $ZipSha) { throw 'the app files changed after Setup checked them' }
    $u = Join-Path $Dir 'Uninstall.exe.new'
    Copy-Item -LiteralPath $Stub -Destination $u -Force
    if ((Get-FileHash -LiteralPath $u -Algorithm SHA256).Hash -ne $StubSha) { throw 'Uninstall.exe changed after Setup wrote it' }
    & "$env:SystemRoot\System32\tar.exe" -xf $z -C $Dir --strip-components=1 2>&1 | Out-File $log -Encoding utf8
    if ($LASTEXITCODE) { throw "unpacking failed (tar exit code $LASTEXITCODE)" }
    Move-Item -LiteralPath $u -Destination (Join-Path $Dir 'Uninstall.exe') -Force
    Remove-Item -LiteralPath $z -Force
} catch {
    "FAILED: $_" | Add-Content $log
    exit 3
}
$a = @{ Quiet = $true }
if ($Desktop) { $a.Desktop = $true }
if ($NoStartup) { $a.NoStartup = $true }
& (Join-Path $Dir 'install.ps1') @a
exit $LASTEXITCODE
)PS";

static std::wstring widen(const char *s) { return std::wstring(s, s + strlen(s)); }

// A PowerShell string literal: single quotes, with any single quote doubled
static std::wstring ps_quote(const std::wstring &s)
{
    std::wstring out = L"'";
    for (wchar_t c : s) { out += c; if (c == L'\'') out += L'\''; }
    return out + L"'";
}

// powershell -EncodedCommand takes the script as base64 of its UTF-16LE text
static std::wstring encode_command(const std::wstring &script)
{
    static const char *b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::vector<unsigned char> b;
    for (wchar_t c : script) { b.push_back((unsigned char)(c & 0xFF)); b.push_back((unsigned char)((c >> 8) & 0xFF)); }
    std::wstring out;
    for (size_t i = 0; i < b.size(); i += 3) {
        unsigned v = b[i] << 16 | (i + 1 < b.size() ? b[i + 1] << 8 : 0) | (i + 2 < b.size() ? b[i + 2] : 0);
        out += (wchar_t)b64[v >> 18 & 63];
        out += (wchar_t)b64[v >> 12 & 63];
        out += i + 1 < b.size() ? (wchar_t)b64[v >> 6 & 63] : L'=';
        out += i + 2 < b.size() ? (wchar_t)b64[v & 63] : L'=';
    }
    return out;
}

static DWORD WINAPI worker(LPVOID p)
{
    Options opt = *(Options *)p;
    delete (Options *)p;
    std::wstring err;
    auto finish = [&](bool ok, const std::wstring &msg) { PostMessageW(g_wnd, WM_FINISHED, ok, (LPARAM) new std::wstring(msg)); };

    wchar_t tmp[MAX_PATH];
    GetTempPathW(MAX_PATH, tmp);
    std::wstring zip = std::wstring(tmp) + ZIPNAME;
    std::string zip_sha;   // the trusted hash (in this exe, or published with the release), never one of the temp file

    Payload pay = find_payload();
    if (pay.found) {
        // The app zip is inside this exe: no network needed.
        wchar_t self[MAX_PATH];
        GetModuleFileNameW(NULL, self, MAX_PATH);
        post_status(L"Reading the installer...");
        if (!copy_range(self, pay.off, pay.size, zip, 1, 70, L"Preparing")) {
            DeleteFileW(zip.c_str());
            if (g_cancel) { finish(false, L""); return 0; }
            finish(false, L"Could not read the files inside Setup. Check that the disk is not full and run Setup again.");
            return 0;
        }
        post_status(L"Checking the files...");
        post_progress(75);
        zip_sha = pay.sha;
        if (sha256_file(zip) != zip_sha) {
            DeleteFileW(zip.c_str());
            finish(false, L"This Setup file is damaged, so nothing was installed. Download it again.");
            return 0;
        }
    } else {
        HINTERNET net = InternetOpenW(L"RGBAnywhereSetup/1.1", INTERNET_OPEN_TYPE_PRECONFIG, NULL, NULL, 0);
        if (!net) { finish(false, L"Could not start the network stack."); return 0; }

        post_status(L"Preparing the download...");
        std::vector<char> sum;
        if (!download(net, std::wstring(BASE) + ZIPNAME + L".sha256", &sum, L"", 0, 1, L"") || sum.size() < 64) {
            InternetCloseHandle(net);
            if (g_cancel) { finish(false, L""); return 0; }
            finish(false, L"Could not reach GitHub. Check your internet connection and try again.");
            return 0;
        }
        std::string want(sum.begin(), sum.begin() + 64);
        for (auto &ch : want) ch = (char)tolower(ch);
        zip_sha = want;

        if (!download(net, std::wstring(BASE) + ZIPNAME, NULL, zip, 1, 70, L"Downloading")) {
            InternetCloseHandle(net);
            DeleteFileW(zip.c_str());
            if (g_cancel) { finish(false, L""); return 0; }
            finish(false, L"The download failed. Check your internet connection and try again.");
            return 0;
        }
        InternetCloseHandle(net);

        post_status(L"Checking the download...");
        post_progress(75);
        if (sha256_file(zip) != want) {
            DeleteFileW(zip.c_str());
            finish(false, L"The downloaded file did not match its checksum, so nothing was installed. Try again in a few minutes.");
            return 0;
        }
    }
    if (g_cancel) { DeleteFileW(zip.c_str()); finish(false, L""); return 0; }

    // The uninstaller is this exe without the zip; the elevated step moves it into the install folder
    wchar_t self[MAX_PATH];
    GetModuleFileNameW(NULL, self, MAX_PATH);   // Settings > Apps runs it with /uninstall
    std::wstring stub = std::wstring(tmp) + L"RGBAnywhere-Uninstall.exe";
    bool stub_ok = pay.found ? copy_range(self, 0, pay.off, stub, 0, 0, L"") : CopyFileW(self, stub.c_str(), FALSE) != 0;
    // Both hashes come from sources a normal process can't change: this exe is locked while it runs. Hashing the temp
    // files here instead would let another process swap them first and have the elevated step accept the swap.
    std::string stub_sha = pay.found ? sha256_file(self, 0, pay.off) : sha256_file(self);
    auto cleanup = [&]() { DeleteFileW(zip.c_str()); DeleteFileW(stub.c_str()); };
    if (!stub_ok || zip_sha.size() != 64 || stub_sha.size() != 64) {
        cleanup();
        finish(false, L"Could not prepare the files in the temp folder. Check that the disk is not full and run Setup again.");
        return 0;
    }
    // An earlier install: in Program Files, or (before 0.3) in %LOCALAPPDATA%, which the elevated step moves from
    bool update = GetFileAttributesW((g_dest + L"\\rgbd.py").c_str()) != INVALID_FILE_ATTRIBUTES ||
                  GetFileAttributesW((g_legacy + L"\\rgbd.py").c_str()) != INVALID_FILE_ATTRIBUTES;
    if (!env(L"RGBANYWHERE_NO_INSTALL").empty()) {
        cleanup();
        post_progress(100);
        finish(true, L"The files are ready (install step skipped).");
        return 0;
    }

    post_status(update ? L"Updating. Windows will ask for permission." : L"Setting up. Windows will ask for permission.");
    post_progress(88);
    PostMessageW(g_wnd, WM_MARQUEE, 1, 0);
    g_elevated = true;
    std::wstring script = L"$Dir = " + ps_quote(g_dest) + L"; $Legacy = " + ps_quote(g_legacy) + L"; $Zip = " + ps_quote(zip) +
                          L"; $ZipSha = " + ps_quote(widen(zip_sha.c_str())) + L"; $Stub = " + ps_quote(stub) +
                          L"; $StubSha = " + ps_quote(widen(stub_sha.c_str())) + L"; $Desktop = $" +
                          (opt.desktop ? L"true" : L"false") + L"; $NoStartup = $" + (opt.startup ? L"false" : L"true") +
                          L"\n" + widen(INSTALL_PS1);
    std::wstring args = L"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -EncodedCommand " + encode_command(script);
    wchar_t sys[MAX_PATH];
    GetSystemDirectoryW(sys, MAX_PATH);
    std::wstring ps = std::wstring(sys) + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
    SHELLEXECUTEINFOW sei = {sizeof sei};
    sei.fMask = SEE_MASK_NOCLOSEPROCESS;
    sei.lpVerb = L"runas";
    sei.lpFile = ps.c_str();
    sei.lpParameters = args.c_str();
    sei.lpDirectory = sys;
    sei.nShow = SW_HIDE;
    if (!ShellExecuteExW(&sei)) {
        DWORD e = GetLastError();
        g_elevated = false;
        cleanup();
        finish(false, e == ERROR_CANCELLED
                          ? L"Permission was declined. RGB Anywhere needs it to run the lighting service. Run Setup again and choose Yes."
                          : L"Could not start the installer step.");
        return 0;
    }
    post_status(L"Setting up the lighting service and driver. This can take a minute...");
    WaitForSingleObject(sei.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(sei.hProcess, &code);
    CloseHandle(sei.hProcess);
    g_elevated = false;
    cleanup();
    if (code != 0) { finish(false, L"The setup step failed. The log shows what went wrong."); return 0; }
    post_progress(100);
    finish(true, L"");
    return 0;
}

// ---- window ---------------------------------------------------------------------------------------------------------

static HWND make(const wchar_t *cls, const wchar_t *text, DWORD style, int x, int y, int w, int h, int id, HFONT font)
{
    HWND c = CreateWindowExW(0, cls, text, WS_CHILD | style, S(x), S(y), S(w), S(h), g_wnd, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL);
    SendMessageW(c, WM_SETFONT, (WPARAM)font, TRUE);
    return c;
}

static void show(HWND c, bool on) { ShowWindow(c, on ? SW_SHOW : SW_HIDE); }

static void set_page(int page)
{
    g_page = page;
    bool o = page == PAGE_OPTIONS, w = page == PAGE_WORKING, d = page == PAGE_DONE;
    for (HWND c : {g_pathLabel, g_path, g_chkDesktop, g_chkStartup, g_chkLaunch, g_note}) show(c, o);
    for (HWND c : {g_status, g_bar, g_detail}) show(c, w);
    show(g_msg, d);
    show(g_btnLog, d && !g_success && !g_log.empty());
    SetWindowTextW(g_btnPrimary, o ? L"Install" : L"Finish");
    show(g_btnPrimary, o || d);
    SetWindowTextW(g_btnCancel, d ? L"Close" : L"Cancel");
    if (d) show(g_btnCancel, !g_success);   // on success only Finish is needed
    else show(g_btnCancel, true);
    if (o) SendMessageW(g_btnPrimary, BM_SETSTYLE, BS_DEFPUSHBUTTON, TRUE);
    if (d) SendMessageW(g_btnPrimary, BM_SETSTYLE, BS_DEFPUSHBUTTON, TRUE);
    SetFocus(o || d ? g_btnPrimary : g_btnCancel);
}

static void start_install()
{
    g_launch = SendMessageW(g_chkLaunch, BM_GETCHECK, 0, 0) == BST_CHECKED;
    Options *o = new Options{SendMessageW(g_chkDesktop, BM_GETCHECK, 0, 0) == BST_CHECKED,
                             SendMessageW(g_chkStartup, BM_GETCHECK, 0, 0) == BST_CHECKED};
    set_page(PAGE_WORKING);
    SendMessageW(g_bar, PBM_SETPOS, 0, 0);
    CloseHandle(CreateThread(NULL, 0, worker, o, 0, NULL));
}

static void finish_page(bool ok, const std::wstring &msg)
{
    g_success = ok;
    g_log = ok ? L"" : g_dest + L"\\install.log";
    if (!ok && GetFileAttributesW(g_log.c_str()) == INVALID_FILE_ATTRIBUTES) g_log.clear();
    SendMessageW(g_bar, PBM_SETMARQUEE, 0, 0);
    SetWindowLongPtrW(g_bar, GWL_STYLE, GetWindowLongPtrW(g_bar, GWL_STYLE) & ~PBS_MARQUEE);
    if (ok) {
        SetWindowTextW(g_title, L"RGB Anywhere is installed");
        std::wstring t = L"You can open it any time from the shortcut it created.";
        if (!msg.empty()) t = msg;
        SetWindowTextW(g_msg, t.c_str());
    } else if (g_cancel) {
        PostMessageW(g_wnd, WM_CLOSE, 0, 0);
        return;
    } else {
        SetWindowTextW(g_title, L"Setup did not finish");
        SetWindowTextW(g_msg, msg.c_str());
    }
    set_page(PAGE_DONE);
}

static void launch_app()
{
    std::wstring pyw = g_dest + L"\\python\\pythonw.exe";
    std::wstring arg = L"\"" + g_dest + L"\\rgb_app.pyw\"";
    ShellExecuteW(NULL, L"open", pyw.c_str(), arg.c_str(), g_dest.c_str(), SW_SHOWNORMAL);
}

static void primary()
{
    if (g_page == PAGE_OPTIONS) start_install();
    else if (g_page == PAGE_DONE) {
        if (g_success && g_launch && env(L"RGBANYWHERE_NO_INSTALL").empty()) launch_app();
        DestroyWindow(g_wnd);
    }
}

static void cancel()
{
    if (g_page == PAGE_WORKING) {
        if (g_elevated) return;           // the permission step cannot be interrupted
        g_cancel = true;
        EnableWindow(g_btnCancel, FALSE);
        SetWindowTextW(g_status, L"Cancelling...");
    } else DestroyWindow(g_wnd);
}

static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    switch (m) {
    case WM_CREATE: {
        g_wnd = h;
        g_dpi = GetDpiForWindow(h);
        NONCLIENTMETRICSW ncm = {sizeof ncm};
        SystemParametersInfoForDpi(SPI_GETNONCLIENTMETRICS, sizeof ncm, &ncm, 0, g_dpi);
        g_font = CreateFontIndirectW(&ncm.lfMessageFont);
        LOGFONTW lf = ncm.lfMessageFont;
        lf.lfHeight = -MulDiv(18, g_dpi, 72); lf.lfWeight = FW_SEMIBOLD;
        g_fontTitle = CreateFontIndirectW(&lf);
        lf = ncm.lfMessageFont; lf.lfWeight = FW_SEMIBOLD;
        g_fontBold = CreateFontIndirectW(&lf);
        g_white = CreateSolidBrush(RGB(255, 255, 255));
        g_band = CreateSolidBrush(RGB(243, 243, 243));

        g_title = make(L"STATIC", L"Install RGB Anywhere", SS_LEFT | WS_VISIBLE, 28, 22, 424, 32, 0, g_fontTitle);
        g_sub = make(L"STATIC", L"One app for RAM, fan, motherboard, keyboard and mouse lighting.", SS_LEFT | WS_VISIBLE, 28, 58, 424, 20, 0, g_font);
        g_pathLabel = make(L"STATIC", L"Install location", SS_LEFT, 28, 100, 424, 18, 0, g_fontBold);
        g_path = make(L"STATIC", g_dest.c_str(), SS_LEFT | SS_PATHELLIPSIS, 28, 120, 424, 20, 0, g_font);
        g_chkDesktop = make(L"BUTTON", L"Add a shortcut to the desktop", BS_AUTOCHECKBOX | WS_TABSTOP, 28, 150, 424, 24, ID_CHK_DESKTOP, g_font);
        g_chkStartup = make(L"BUTTON", L"Start RGB Anywhere when Windows starts", BS_AUTOCHECKBOX | WS_TABSTOP, 28, 176, 424, 24, ID_CHK_STARTUP, g_font);
        g_chkLaunch = make(L"BUTTON", L"Open RGB Anywhere when setup finishes", BS_AUTOCHECKBOX | WS_TABSTOP, 28, 202, 424, 24, ID_CHK_LAUNCH, g_font);
        g_note = make(L"STATIC", L"Windows will ask for permission once. Setup needs it to add the lighting service and the PawnIO driver (for RAM lights, downloaded from its author). About 250 MB of disk space.", SS_LEFT, 28, 234, 424, 38, 0, g_font);
        for (HWND c : {g_chkDesktop, g_chkStartup, g_chkLaunch}) SendMessageW(c, BM_SETCHECK, BST_CHECKED, 0);

        g_status = make(L"STATIC", L"", SS_LEFT, 28, 112, 424, 20, 0, g_font);
        g_bar = make(PROGRESS_CLASSW, L"", PBS_SMOOTH, 28, 142, 424, 16, 0, g_font);
        SendMessageW(g_bar, PBM_SETRANGE32, 0, 100);
        g_detail = make(L"STATIC", L"This takes a few minutes. You can leave this window open.", SS_LEFT, 28, 170, 424, 20, 0, g_font);
        g_msg = make(L"STATIC", L"", SS_LEFT, 28, 100, 424, 100, 0, g_font);

        g_btnLog = make(L"BUTTON", L"Open log", BS_PUSHBUTTON | WS_TABSTOP, 28, 292, 92, 30, ID_LOG, g_font);
        g_btnPrimary = make(L"BUTTON", L"Install", BS_DEFPUSHBUTTON | WS_TABSTOP, 252, 292, 100, 30, ID_INSTALL, g_font);
        g_btnCancel = make(L"BUTTON", L"Cancel", BS_PUSHBUTTON | WS_TABSTOP, 360, 292, 92, 30, ID_CANCEL, g_font);
        set_page(PAGE_OPTIONS);
        if (g_auto) PostMessageW(h, WM_COMMAND, ID_INSTALL, 0);
        return 0;
    }
    case WM_COMMAND:
        switch (LOWORD(w)) {
        case ID_INSTALL: primary(); break;
        case ID_CANCEL: cancel(); break;
        case ID_LOG: ShellExecuteW(NULL, L"open", L"notepad.exe", (L"\"" + g_log + L"\"").c_str(), NULL, SW_SHOWNORMAL); break;
        }
        return 0;
    case WM_PROGRESS: SendMessageW(g_bar, PBM_SETPOS, w, 0); return 0;
    case WM_MARQUEE: {
        LONG_PTR st = GetWindowLongPtrW(g_bar, GWL_STYLE);
        SetWindowLongPtrW(g_bar, GWL_STYLE, st | PBS_MARQUEE);
        SendMessageW(g_bar, PBM_SETMARQUEE, TRUE, 30);
        return 0;
    }
    case WM_STATUS: {
        std::wstring *s = (std::wstring *)l;
        if (!g_cancel) SetWindowTextW(g_status, s->c_str());
        delete s;
        return 0;
    }
    case WM_FINISHED: {
        std::wstring *s = (std::wstring *)l;
        finish_page(w != 0, *s);
        delete s;
        if (g_auto) PostMessageW(h, WM_COMMAND, g_success ? ID_INSTALL : ID_CANCEL, 0);
        return 0;
    }
    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLORBTN: {
        HDC dc = (HDC)w;
        SetBkColor(dc, RGB(255, 255, 255));
        if ((HWND)l == g_note || (HWND)l == g_sub || (HWND)l == g_detail) SetTextColor(dc, RGB(80, 80, 80));
        else SetTextColor(dc, RGB(20, 20, 20));
        return (LRESULT)g_white;
    }
    case WM_ERASEBKGND: {
        RECT r;
        GetClientRect(h, &r);
        FillRect((HDC)w, &r, g_white);
        RECT b = {0, S(278), r.right, r.bottom};
        FillRect((HDC)w, &b, g_band);
        RECT line = {0, S(278), r.right, S(278) + 1};
        HBRUSH ln = CreateSolidBrush(RGB(224, 224, 224));
        FillRect((HDC)w, &line, ln);
        DeleteObject(ln);
        return 1;
    }
    case WM_CLOSE:
        if (g_page == PAGE_WORKING) { cancel(); return 0; }
        DestroyWindow(h);
        return 0;
    case WM_DESTROY:
        PostQuitMessage(g_page == PAGE_DONE ? (g_success ? 0 : 1) : (g_cancel ? 2 : 0));
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

// Uninstall.exe /uninstall: ask once, then remove everything through uninstall.ps1 (elevated, hidden).
static int uninstall()
{
    wchar_t self[MAX_PATH];
    GetModuleFileNameW(NULL, self, MAX_PATH);
    std::wstring dir = self;
    dir.resize(dir.find_last_of(L'\\'));
    if (MessageBoxW(NULL, L"Remove RGB Anywhere from this PC?\n\nYour settings are deleted too. The PawnIO driver stays installed.",
                    L"Uninstall RGB Anywhere", MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES) return 2;
    // Run from the install folder: in Program Files only admins can change it (a copy in %TEMP% could be swapped).
    // PowerShell reads the whole script before it runs, and the script deletes the folder only after it exits.
    std::wstring script = dir + L"\\uninstall.ps1";
    if (GetFileAttributesW(script.c_str()) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"uninstall.ps1 is missing from the install folder.", L"Uninstall RGB Anywhere", MB_OK | MB_ICONERROR);
        return 1;
    }
    wchar_t sys[MAX_PATH];
    GetSystemDirectoryW(sys, MAX_PATH);
    std::wstring ps = std::wstring(sys) + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
    std::wstring args = L"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + L"\" -Dir \"" + dir + L"\"";
    SHELLEXECUTEINFOW sei = {sizeof sei};
    sei.fMask = SEE_MASK_NOCLOSEPROCESS;
    sei.lpVerb = L"runas";
    sei.lpFile = ps.c_str();
    sei.lpParameters = args.c_str();
    sei.nShow = SW_HIDE;
    if (!ShellExecuteExW(&sei)) return 2;   // permission declined
    WaitForSingleObject(sei.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(sei.hProcess, &code);
    CloseHandle(sei.hProcess);
    MessageBoxW(NULL, code == 0 ? L"RGB Anywhere was removed." : L"Could not remove everything. Close RGB Anywhere and try again.",
                L"Uninstall RGB Anywhere", MB_OK | (code == 0 ? MB_ICONINFORMATION : MB_ICONERROR));
    return (int)code;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, LPWSTR cmd, int)
{
    if (cmd && wcsstr(cmd, L"/uninstall")) return uninstall();
    g_auto = cmd && wcsstr(cmd, L"/auto");
    wchar_t local[MAX_PATH], pf[MAX_PATH];
    if (FAILED(SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, local))) return 1;
    if (FAILED(SHGetFolderPathW(NULL, CSIDL_PROGRAM_FILES, NULL, 0, pf))) return 1;
    g_dest = std::wstring(pf) + L"\\RGB Anywhere";       // the code: only admins can change it
    g_legacy = std::wstring(local) + L"\\RGB Anywhere";  // the user's settings; also where installs before 0.3 put the code

    INITCOMMONCONTROLSEX ic = {sizeof ic, ICC_PROGRESS_CLASS};
    InitCommonControlsEx(&ic);
    WNDCLASSW wc = {};
    wc.lpfnWndProc = proc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursorW(NULL, IDC_ARROW);
    wc.hIcon = LoadIconW(inst, MAKEINTRESOURCEW(1));
    wc.lpszClassName = L"RGBAnywhereSetup";
    RegisterClassW(&wc);

    UINT dpi = GetDpiForSystem();
    RECT r = {0, 0, MulDiv(480, dpi, 96), MulDiv(330, dpi, 96)};
    DWORD style = WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    AdjustWindowRectExForDpi(&r, style, FALSE, WS_EX_CONTROLPARENT, dpi);
    int w = r.right - r.left, h = r.bottom - r.top;
    HWND wnd = CreateWindowExW(WS_EX_CONTROLPARENT, wc.lpszClassName, L"RGB Anywhere Setup", style,
                               (GetSystemMetrics(SM_CXSCREEN) - w) / 2, (GetSystemMetrics(SM_CYSCREEN) - h) / 2, w, h, NULL, NULL, inst, NULL);
    ShowWindow(wnd, SW_SHOW);
    SetForegroundWindow(wnd);
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (IsDialogMessageW(wnd, &msg)) continue;
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return (int)msg.wParam;
}
