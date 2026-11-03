package com.admin.rgbcontrol;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;

/**
 * Updates: is a newer release out on GitHub, and fetching its APK. Mirrors the PC app's updater.py: the APK is only
 * offered when the release publishes a SHA-256 for it, and the download is checked against that before installing.
 * Android always asks the user to confirm the install itself.
 */
final class Updater {
    static final String API = "https://api.github.com/repos/EQQX0/rgbanywhere/releases/latest";
    static final String ASSET = "RGB-Anywhere.apk";
    static final String ACTION_RESULT = "com.admin.rgbcontrol.INSTALL_RESULT";
    // MainActivity is exported, so any app can send ACTION_RESULT. Only the result of our own session carries this secret.
    static final String EXTRA_NONCE = "nonce";
    private static final String PREFS = "updater";

    static boolean isOurResult(Context ctx, Intent i) {
        String expected = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(EXTRA_NONCE, null);
        return expected != null && expected.equals(i.getStringExtra(EXTRA_NONCE));
    }

    static final class Release {
        String version, url, sha256;
        long size;
    }

    interface Progress { void percent(int p); }

    private Updater() { }

    /** "v0.10.1" -> {0, 10, 1}, so versions compare as numbers, not text. */
    static int[] key(String v) {
        String[] parts = (v == null ? "" : v).replaceAll("[^0-9.]", " ").trim().split("[ .]+");
        int[] k = new int[parts.length];
        for (int i = 0; i < parts.length; i++) {
            try { k[i] = Integer.parseInt(parts[i]); } catch (NumberFormatException e) { k[i] = 0; }
        }
        return k;
    }

    static boolean newer(String a, String b) {
        int[] x = key(a), y = key(b);
        for (int i = 0; i < Math.max(x.length, y.length); i++) {
            int p = i < x.length ? x[i] : 0, q = i < y.length ? y[i] : 0;
            if (p != q) return p > q;
        }
        return false;
    }

    /** The newest release that carries the APK with a published checksum, or null when there's nothing to offer. */
    static Release latest() throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(API).openConnection();
        try {
            c.setConnectTimeout(8000);
            c.setReadTimeout(15000);
            c.setRequestProperty("Accept", "application/vnd.github+json");
            c.setRequestProperty("User-Agent", "RGBAnywhere");
            if (c.getResponseCode() != 200) return null;
            JSONObject rel = new JSONObject(readAll(c.getInputStream()));
            JSONArray assets = rel.optJSONArray("assets");
            if (assets == null) return null;
            for (int i = 0; i < assets.length(); i++) {
                JSONObject a = assets.getJSONObject(i);
                if (!ASSET.equals(a.optString("name"))) continue;
                String digest = a.optString("digest", "");
                String url = a.optString("browser_download_url", "");
                if (url.isEmpty() || !digest.startsWith("sha256:")) return null;
                Release r = new Release();
                r.version = rel.getString("tag_name").replaceFirst("^v", "");
                r.url = url;
                r.sha256 = digest.substring(7).toLowerCase();
                r.size = a.optLong("size", 0);
                return r;
            }
            return null;
        } finally {
            c.disconnect();
        }
    }

    private static String readAll(InputStream in) throws Exception {
        try (InputStream s = in) {
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = s.read(buf)) > 0) out.write(buf, 0, n);
            return out.toString("UTF-8");
        }
    }

    /** Fetch the APK into the app's cache and check it against the published SHA-256. */
    static File download(Context ctx, Release rel, Progress progress) throws Exception {
        File f = new File(ctx.getCacheDir(), ASSET);
        f.delete();
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        HttpURLConnection c = (HttpURLConnection) new URL(rel.url).openConnection();  // GitHub redirects to its CDN
        c.setConnectTimeout(10000);
        c.setReadTimeout(30000);
        c.setRequestProperty("User-Agent", "RGBAnywhere");
        try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(f)) {
            long total = c.getContentLength() > 0 ? c.getContentLength() : rel.size, done = 0;
            int shown = -1, n;
            byte[] buf = new byte[64 * 1024];
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                md.update(buf, 0, n);
                done += n;
                int pct = total > 0 ? (int) Math.min(99, done * 100 / total) : 0;
                if (progress != null && pct != shown) { shown = pct; progress.percent(pct); }
            }
        } finally {
            c.disconnect();
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : md.digest()) hex.append(String.format("%02x", b));
        if (!hex.toString().equals(rel.sha256)) {
            f.delete();
            throw new SecurityException("The download did not match its published checksum.");
        }
        return f;
    }

    /** Hand the verified APK to the system installer; the result comes back to MainActivity.onNewIntent. */
    static void install(Context ctx, File apk) throws Exception {
        PackageInstaller pi = ctx.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams p = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        int id = pi.createSession(p);
        try (PackageInstaller.Session s = pi.openSession(id)) {
            try (InputStream in = new FileInputStream(apk); OutputStream out = s.openWrite("update.apk", 0, apk.length())) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                s.fsync(out);
            }
            byte[] raw = new byte[16];
            new java.security.SecureRandom().nextBytes(raw);
            StringBuilder nonce = new StringBuilder();
            for (byte b : raw) nonce.append(String.format("%02x", b));
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(EXTRA_NONCE, nonce.toString()).apply();
            Intent i = new Intent(ctx, MainActivity.class).setAction(ACTION_RESULT).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
                    .putExtra(EXTRA_NONCE, nonce.toString());
            int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
            s.commit(PendingIntent.getActivity(ctx, id, i, flags).getIntentSender());
        }
    }
}
