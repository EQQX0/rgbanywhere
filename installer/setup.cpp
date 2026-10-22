// RGB Anywhere Setup: downloads the latest release zip, checks its SHA-256, unpacks it to
// %LOCALAPPDATA%\RGB Anywhere and starts Install.cmd (which asks for admin once).
//
// Copyright (C) 2026 EQQX2
// SPDX-License-Identifier: GPL-3.0-or-later
#include <windows.h>
#include <wininet.h>
#include <wincrypt.h>
#include <shlobj.h>
#include <cstdio>
#include <string>
#include <vector>

static const char *BASE = "https://github.com/EQQX0/rgbanywhere/releases/latest/download/";
static const char *ZIP = "RGBAnywhere-win64.zip";

static void pause_and_exit(int code)
{
    printf("\nPress Enter to close.");
    fflush(stdout);
    getchar();
    exit(code);
}

static void fail(const std::string &msg)
{
    printf("\nError: %s\n", msg.c_str());
    pause_and_exit(1);
}

static std::string env(const char *name)
{
    char buf[MAX_PATH * 2];
    DWORD n = GetEnvironmentVariableA(name, buf, sizeof buf);
    return n && n < sizeof buf ? std::string(buf, n) : std::string();
}

// Download url into memory (small files) or to a file (large). Returns false on any error.
static bool download(HINTERNET net, const std::string &url, std::vector<char> *mem, const std::string &file, bool progress)
{
    HINTERNET h = InternetOpenUrlA(net, url.c_str(), NULL, 0,
                                   INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE | INTERNET_FLAG_SECURE, 0);
    if (!h) return false;
    DWORD status = 0, len = sizeof status;
    HttpQueryInfoA(h, HTTP_QUERY_STATUS_CODE | HTTP_QUERY_FLAG_NUMBER, &status, &len, NULL);
    if (status != 200) { InternetCloseHandle(h); return false; }
    DWORD total = 0;
    len = sizeof total;
    HttpQueryInfoA(h, HTTP_QUERY_CONTENT_LENGTH | HTTP_QUERY_FLAG_NUMBER, &total, &len, NULL);

    FILE *f = NULL;
    if (!file.empty()) {
        f = fopen(file.c_str(), "wb");
        if (!f) { InternetCloseHandle(h); return false; }
    }
    std::vector<char> buf(1 << 16);
    unsigned long long done = 0;
    int lastPct = -1;
    for (;;) {
        DWORD got = 0;
        if (!InternetReadFile(h, buf.data(), (DWORD)buf.size(), &got)) { if (f) fclose(f); InternetCloseHandle(h); return false; }
        if (!got) break;
        if (f) fwrite(buf.data(), 1, got, f);
        else mem->insert(mem->end(), buf.begin(), buf.begin() + got);
        done += got;
        if (progress && total) {
            int pct = (int)(done * 100 / total);
            if (pct != lastPct) { printf("\r  %d%%  (%llu of %lu MB)", pct, done >> 20, total >> 20); fflush(stdout); lastPct = pct; }
        }
    }
    if (f) fclose(f);
    InternetCloseHandle(h);
    if (progress) printf("\n");
    return true;
}

static std::string sha256_file(const std::string &path)
{
    FILE *f = fopen(path.c_str(), "rb");
    if (!f) return "";
    HCRYPTPROV prov = 0;
    HCRYPTHASH hash = 0;
    if (!CryptAcquireContextA(&prov, NULL, NULL, PROV_RSA_AES, CRYPT_VERIFYCONTEXT) ||
        !CryptCreateHash(prov, CALG_SHA_256, 0, 0, &hash)) { fclose(f); return ""; }
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

static DWORD run(const std::string &cmd, const std::string &dir)
{
    STARTUPINFOA si = {sizeof si};
    PROCESS_INFORMATION pi = {};
    std::string c = cmd;
    if (!CreateProcessA(NULL, &c[0], NULL, NULL, FALSE, 0, NULL, dir.empty() ? NULL : dir.c_str(), &si, &pi)) return (DWORD)-1;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return code;
}

int main()
{
    printf("RGB Anywhere Setup\n==================\n\n");
    std::string local = env("LOCALAPPDATA"), tmp = env("TEMP");
    if (local.empty() || tmp.empty()) fail("LOCALAPPDATA or TEMP is not set.");
    std::string dest = local + "\\RGB Anywhere";
    std::string zip = tmp + "\\" + ZIP;

    HINTERNET net = InternetOpenA("RGBAnywhereSetup/1.0", INTERNET_OPEN_TYPE_PRECONFIG, NULL, NULL, 0);
    if (!net) fail("Could not start the network stack.");

    printf("Downloading the checksum...\n");
    std::vector<char> sum;
    if (!download(net, std::string(BASE) + ZIP + ".sha256", &sum, "", false) || sum.size() < 64)
        fail("Could not download the checksum from GitHub. Check your internet connection.");
    std::string want(sum.begin(), sum.begin() + 64);

    printf("Downloading RGB Anywhere (about 75 MB)...\n");
    if (!download(net, std::string(BASE) + ZIP, NULL, zip, true))
        fail("Download failed. Check your internet connection and run this again.");
    InternetCloseHandle(net);

    printf("Checking the download...\n");
    for (auto &ch : want) ch = (char)tolower(ch);
    if (sha256_file(zip) != want) {
        DeleteFileA(zip.c_str());
        fail("The downloaded file does not match its checksum. Nothing was installed. Try again.");
    }

    printf("Unpacking to %s ...\n", dest.c_str());
    SHCreateDirectoryExA(NULL, dest.c_str(), NULL);
    char sys[MAX_PATH];
    GetSystemDirectoryA(sys, sizeof sys);
    DWORD rc = run(std::string("\"") + sys + "\\tar.exe\" -xf \"" + zip + "\" -C \"" + dest + "\" --strip-components=1", "");
    DeleteFileA(zip.c_str());
    if (rc != 0)
        fail("Could not unpack the files. If RGB Anywhere is already running, quit it from the tray icon and run this again.");

    if (!env("RGBANYWHERE_NO_INSTALL").empty()) {  // for testing the download and unpack only
        printf("\nUnpacked. Skipping the installer (RGBANYWHERE_NO_INSTALL is set).\n");
        return 0;
    }
    printf("\nStarting the installer. Accept the admin prompt when Windows asks.\n\n");
    rc = run("cmd /c Install.cmd", dest);
    if (rc != 0) printf("\nThe installer reported a problem (code %lu). Read the messages above.\n", rc);
    else printf("\nDone. Open \"RGB Anywhere\" from the Start menu.\n");
    pause_and_exit((int)rc);
    return 0;
}
