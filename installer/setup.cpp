// RGB Anywhere Setup: a small installer window. It downloads the latest release zip, checks its SHA-256,
// unpacks it to %LOCALAPPDATA%\RGB Anywhere and runs install.ps1 elevated (Windows shows its permission
// prompt at that point). No console window.
//
//   RGB-Anywhere-Setup.exe          normal use
//   RGB-Anywhere-Setup.exe /auto    start installing with the default choices, no clicking (for testing)
//   RGBANYWHERE_NO_INSTALL=1        stop after unpacking (for testing; skips the permission prompt)
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
static std::wstring g_dest, g_log;

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

static std::string sha256_file(const std::wstring &path)
{
    FILE *f = _wfopen(path.c_str(), L"rb");
    if (!f) return "";
    HCRYPTPROV prov = 0;
    HCRYPTHASH hash = 0;
    if (!CryptAcquireContextW(&prov, NULL, NULL, PROV_RSA_AES, CRYPT_VERIFYCONTEXT) || !CryptCreateHash(prov, CALG_SHA_256, 0, 0, &hash)) {
        fclose(f);
        return "";
    }
    std::vector<BYTE> buf(1 << 16);
    size_t n;
    while ((n = fread(buf.data(), 1, buf.size(), f)) > 0) CryptHashData(hash, buf.data(), (DWORD)n, 0);
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

static DWORD run_hidden(const std::wstring &cmd, const std::wstring &dir)
{
    STARTUPINFOW si = {sizeof si};
    PROCESS_INFORMATION pi = {};
    std::wstring c = cmd;
    if (!CreateProcessW(NULL, &c[0], NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, dir.empty() ? NULL : dir.c_str(), &si, &pi)) return (DWORD)-1;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return code;
}

struct Options { bool desktop, startup; };

// An update runs this elevated instead of unpacking first: the lighting daemon (elevated), OpenRGB and the app keep
// files in the install folder open, and only an elevated process can end the daemon. It frees the folder, unpacks
// the zip and then runs install.ps1 as a first install does.
static const char UPDATE_PS1[] = R"PS(param([string]$Dir, [string]$Zip, [switch]$Desktop, [switch]$NoStartup)
$log = Join-Path $Dir 'install.log'
Stop-ScheduledTask -TaskName 'RGB Anywhere Lighting' -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ProcessId -ne $PID -and
        $_.ExecutablePath.StartsWith($Dir + '\', [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
& "$env:SystemRoot\System32\tar.exe" -xf $Zip -C $Dir --strip-components=1 2>&1 | Out-File $log -Encoding utf8
if ($LASTEXITCODE) { "Unpacking failed (tar exit code $LASTEXITCODE)." | Add-Content $log; exit 3 }
$a = @{ Quiet = $true }
if ($Desktop) { $a.Desktop = $true }
if ($NoStartup) { $a.NoStartup = $true }
& (Join-Path $Dir 'install.ps1') @a
exit $LASTEXITCODE
)PS";

static bool write_file(const std::wstring &path, const char *text)
{
    FILE *f = _wfopen(path.c_str(), L"wb");
    if (!f) return false;
    bool ok = fwrite(text, 1, strlen(text), f) == strlen(text);
    return fclose(f) == 0 && ok;
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
    if (g_cancel) { DeleteFileW(zip.c_str()); finish(false, L""); return 0; }

    post_status(L"Unpacking files...");
    post_progress(80);
    SHCreateDirectoryExW(NULL, g_dest.c_str(), NULL);
    wchar_t sys[MAX_PATH];
    GetSystemDirectoryW(sys, MAX_PATH);
    // An earlier install may be running from the folder; then the elevated step unpacks (see UPDATE_PS1)
    bool update = GetFileAttributesW((g_dest + L"\\rgbd.py").c_str()) != INVALID_FILE_ATTRIBUTES;
    std::wstring script = std::wstring(tmp) + L"rgbanywhere-update.ps1";
    if (update) {
        if (!write_file(script, UPDATE_PS1)) {
            DeleteFileW(zip.c_str());
            finish(false, L"Could not write the update script to the temp folder.");
            return 0;
        }
    } else {
        DWORD rc = run_hidden(std::wstring(L"\"") + sys + L"\\tar.exe\" -xf \"" + zip + L"\" -C \"" + g_dest + L"\" --strip-components=1", L"");
        DeleteFileW(zip.c_str());
        if (rc != 0) {
            finish(false, L"Could not unpack the files. Check that the disk is not full and run Setup again.");
            return 0;
        }
    }
    wchar_t self[MAX_PATH];
    GetModuleFileNameW(NULL, self, MAX_PATH);   // this exe becomes the uninstaller (Settings > Apps runs it with /uninstall)
    CopyFileW(self, (g_dest + L"\\Uninstall.exe").c_str(), FALSE);
    if (!env(L"RGBANYWHERE_NO_INSTALL").empty()) {
        if (update) { DeleteFileW(zip.c_str()); DeleteFileW(script.c_str()); }
        post_progress(100);
        finish(true, update ? L"An earlier install is there; updating it needs the install step, which was skipped." : L"Unpacked (install step skipped).");
        return 0;
    }

    post_status(update ? L"Updating. Windows will ask for permission." : L"Setting up. Windows will ask for permission.");
    post_progress(88);
    PostMessageW(g_wnd, WM_MARQUEE, 1, 0);
    g_elevated = true;
    std::wstring args = update
        ? L"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + L"\" -Dir \"" + g_dest + L"\" -Zip \"" + zip + L"\""
        : L"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + g_dest + L"\\install.ps1\" -Quiet";
    if (opt.desktop) args += L" -Desktop";
    if (!opt.startup) args += L" -NoStartup";
    std::wstring ps = std::wstring(sys) + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
    SHELLEXECUTEINFOW sei = {sizeof sei};
    sei.fMask = SEE_MASK_NOCLOSEPROCESS;
    sei.lpVerb = L"runas";
    sei.lpFile = ps.c_str();
    sei.lpParameters = args.c_str();
    sei.lpDirectory = g_dest.c_str();
    sei.nShow = SW_HIDE;
    if (!ShellExecuteExW(&sei)) {
        DWORD e = GetLastError();
        g_elevated = false;
        if (update) { DeleteFileW(zip.c_str()); DeleteFileW(script.c_str()); }
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
    if (update) { DeleteFileW(zip.c_str()); DeleteFileW(script.c_str()); }
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
        g_note = make(L"STATIC", L"Windows will ask for permission once. Setup needs it to add the lighting service and the PawnIO driver (for RAM lights). About 250 MB of disk space.", SS_LEFT, 28, 234, 424, 38, 0, g_font);
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
    if (MessageBoxW(NULL, L"Remove RGB Anywhere from this PC?\n\nYour settings in the install folder are deleted. The PawnIO driver stays installed.",
                    L"Uninstall RGB Anywhere", MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES) return 2;
    wchar_t tmp[MAX_PATH];
    GetTempPathW(MAX_PATH, tmp);
    std::wstring script = std::wstring(tmp) + L"rgbanywhere-uninstall.ps1";
    if (!CopyFileW((dir + L"\\uninstall.ps1").c_str(), script.c_str(), FALSE)) {
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
    DeleteFileW(script.c_str());
    MessageBoxW(NULL, code == 0 ? L"RGB Anywhere was removed." : L"Could not remove everything. Close RGB Anywhere and try again.",
                L"Uninstall RGB Anywhere", MB_OK | (code == 0 ? MB_ICONINFORMATION : MB_ICONERROR));
    return (int)code;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, LPWSTR cmd, int)
{
    if (cmd && wcsstr(cmd, L"/uninstall")) return uninstall();
    g_auto = cmd && wcsstr(cmd, L"/auto");
    wchar_t local[MAX_PATH];
    if (FAILED(SHGetFolderPathW(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, local))) return 1;
    g_dest = std::wstring(local) + L"\\RGB Anywhere";

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
