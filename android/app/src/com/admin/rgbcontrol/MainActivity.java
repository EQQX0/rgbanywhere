package com.admin.rgbcontrol;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * RGB Anywhere for Android: a thin native shell around the control page your PC serves on the home network.
 * Pairing comes from the PC's QR code; the PC is found again by itself if its address changes.
 */
public class MainActivity extends Activity {
    static final int PORT = 47822;
    static final int BG = 0xFF08080C;
    static final int SURFACE = 0xFF1D1D25;
    static final int ACCENT = 0xFFFF7A33;
    static final int TEXT = 0xFFECECF1;
    static final int MUTED = 0xFF9D9DAA;
    static final int REQ_FILE = 41;

    SharedPreferences prefs;
    FrameLayout root;
    WebView web;
    LinearLayout setupView, offlineView;
    EditText linkField;
    TextView setupNote, offlineTitle, offlineBody;
    Button retryBtn, findBtn, pairBtn;
    ValueCallback<Uri[]> fileCallback;
    volatile boolean discovering;
    boolean pageFailed;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("pair", MODE_PRIVATE);
        buildUi();
        if (!handlePairIntent(getIntent())) start();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handlePairIntent(intent);
    }

    // ------------------------------------------------------------------ pairing
    static String hostPort(Uri u) { return u.getHost() + ":" + (u.getPort() == -1 ? 80 : u.getPort()); }

    String host() { return prefs.getString("host", null); }
    String token() { return prefs.getString("token", null); }

    /** rgbcontrol://pair?host=H:P&t=T  or  http://H:P/?t=T */
    boolean pairFromUri(Uri u) {
        if (u == null) return false;
        String host = null, t = u.getQueryParameter("t");
        if ("rgbcontrol".equals(u.getScheme())) host = u.getQueryParameter("host");
        else if ("http".equals(u.getScheme()) && u.getHost() != null)
            host = u.getHost() + ":" + (u.getPort() == -1 ? 80 : u.getPort());
        if (host == null || t == null || t.isEmpty()) return false;
        prefs.edit().putString("host", host).putString("token", t).apply();
        return true;
    }

    boolean handlePairIntent(Intent i) {
        if (i != null && Intent.ACTION_VIEW.equals(i.getAction()) && pairFromUri(i.getData())) {
            i.setData(null);
            start();
            return true;
        }
        return false;
    }

    void start() {
        if (host() == null || token() == null) { showSetup(null); return; }
        pageFailed = false;
        // Cover the WebView while loading so its built-in error page / blank page never flashes.
        showOffline("Connecting…", "", false);
        // The first request carries the token; the PC answers with a cookie and redirects to "/".
        web.loadUrl("http://" + host() + "/?t=" + Uri.encode(token()));
    }

    // ------------------------------------------------------------------ UI
    int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    Button button(String text, boolean primary, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(15);
        b.setTextColor(primary ? 0xFF111114 : TEXT);
        GradientDrawable g = new GradientDrawable();
        g.setCornerRadius(dp(12));
        g.setColor(primary ? 0xFFECECF1 : SURFACE);
        if (!primary) g.setStroke(dp(1), 0x33FFFFFF);
        b.setBackground(g);
        b.setOnClickListener(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(50));
        lp.topMargin = dp(10);
        b.setLayoutParams(lp);
        return b;
    }

    TextView text(String s, int size, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(size);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setLineSpacing(0, 1.25f);
        return t;
    }

    LinearLayout panel() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        l.setBackgroundColor(BG);
        l.setPadding(dp(28), dp(28), dp(28), dp(28));
        l.setVisibility(View.GONE);
        l.setClickable(true);
        return l;
    }

    void buildUi() {
        root = new FrameLayout(this);
        root.setBackgroundColor(BG);

        web = new WebView(this);
        web.setBackgroundColor(BG);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUserAgentString(s.getUserAgentString() + " RGBControlApp/1");
        CookieManager.getInstance().setAcceptCookie(true);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if (hostPort(u).equals(host())) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                // Only errors for the address we're connecting to now count. A cancelled attempt at the old
                // address can report its error late, after we've already switched to the rediscovered one.
                if (r.isForMainFrame() && hostPort(r.getUrl()).equals(host())) {
                    pageFailed = true;
                    onPcUnreachable();  // the overlay hides WebView's own error page
                }
            }

            @Override
            public void onReceivedHttpError(WebView v, WebResourceRequest r, WebResourceResponse e) {
                if (r.isForMainFrame() && e.getStatusCode() == 401) {
                    pageFailed = true;
                    showSetup("This phone isn't signed in to your PC any more (the link was changed). Pair again.");
                }
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                if (!pageFailed && !"about:blank".equals(url)) offlineView.setVisibility(View.GONE);
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView w, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;
                Intent i = new Intent(Intent.ACTION_GET_CONTENT);
                i.addCategory(Intent.CATEGORY_OPENABLE);
                i.setType("*/*");
                i.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"image/*", "video/*"});
                try {
                    startActivityForResult(Intent.createChooser(i, "Choose an image or video"), REQ_FILE);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        // --- setup / pairing screen
        setupView = panel();
        setupView.addView(text("Connect to your PC", 24, TEXT, true));
        setupNote = text("", 15, MUTED, false);
        setupNote.setPadding(0, dp(12), 0, dp(4));
        setupView.addView(setupNote);
        linkField = new EditText(this);
        linkField.setHint("Paste the link from your PC");
        linkField.setHintTextColor(MUTED);
        linkField.setTextColor(TEXT);
        linkField.setSingleLine(true);
        linkField.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        GradientDrawable fg = new GradientDrawable();
        fg.setColor(0x66000000);
        fg.setStroke(dp(1), 0x33FFFFFF);
        fg.setCornerRadius(dp(12));
        linkField.setBackground(fg);
        linkField.setPadding(dp(14), 0, dp(14), 0);
        LinearLayout.LayoutParams flp = new LinearLayout.LayoutParams(-1, dp(50));
        flp.topMargin = dp(18);
        linkField.setLayoutParams(flp);
        setupView.addView(linkField);
        setupView.addView(button("Connect", true, v -> {
            if (pairFromUri(Uri.parse(linkField.getText().toString().trim()))) start();
            else setupNote.setText("That doesn't look like the link from your PC. It starts with http:// and has ?t= in it.");
        }));
        root.addView(setupView, new FrameLayout.LayoutParams(-1, -1));

        // --- can't reach the PC
        offlineView = panel();
        offlineTitle = text("", 22, TEXT, true);
        offlineBody = text("", 15, MUTED, false);
        offlineBody.setPadding(0, dp(10), 0, dp(10));
        offlineView.addView(offlineTitle);
        offlineView.addView(offlineBody);
        retryBtn = button("Try again", true, v -> start());
        findBtn = button("Find my PC", false, v -> discover());
        pairBtn = button("Pair again", false, v -> showSetup(null));
        offlineView.addView(retryBtn);
        offlineView.addView(findBtn);
        offlineView.addView(pairBtn);
        root.addView(offlineView, new FrameLayout.LayoutParams(-1, -1));

        setContentView(root);
    }

    void showSetup(String note) {
        offlineView.setVisibility(View.GONE);
        setupNote.setText(note != null ? note :
            "On your PC, open RGB Anywhere, click Phone, and scan the QR code with your phone's camera. "
            + "When asked how to open the link, choose RGB Anywhere.\n\nOr paste the link here:");
        setupView.setVisibility(View.VISIBLE);
    }

    void showOffline(String title, String body, boolean buttons) {
        setupView.setVisibility(View.GONE);
        offlineTitle.setText(title);
        offlineBody.setText(body);
        int vis = buttons ? View.VISIBLE : View.GONE;
        retryBtn.setVisibility(vis);
        findBtn.setVisibility(vis);
        pairBtn.setVisibility(vis);
        offlineView.setVisibility(View.VISIBLE);
    }

    // ------------------------------------------------------------------ finding the PC again
    void onPcUnreachable() {
        // Most likely the router gave the PC a new address (or the PC is asleep). Look for it once automatically.
        if (!discovering) discover();
    }

    void discover() {
        if (discovering) return;
        discovering = true;
        showOffline("Looking for your PC…", "Checking this Wi-Fi network.", false);
        new Thread(() -> {
            String found = scan();
            runOnUiThread(() -> {
                discovering = false;
                if (found != null && !found.equals(host())) {
                    prefs.edit().putString("host", found).apply();
                    start();
                } else if (found != null) {
                    showOffline("Found your PC, but it isn't answering", "Make sure RGB Anywhere is running on your PC.", true);
                } else {
                    showOffline("Can't reach your PC",
                        "Make sure your PC is on, RGB Anywhere is running with \"Allow phone access\" switched on, "
                        + "and this phone is on the same Wi-Fi.", true);
                }
            });
        }).start();
    }

    /** Returns "ip:port" of a PC running RGB Anywhere on this /24 network, or null. */
    String scan() {
        String prefix = localPrefix();
        if (prefix == null) return null;
        ExecutorService ex = Executors.newFixedThreadPool(48);
        AtomicReference<String> hit = new AtomicReference<>();
        for (int i = 1; i <= 254; i++) {
            final String ip = prefix + i;
            ex.submit(() -> { if (hit.get() == null && probe(ip)) hit.compareAndSet(null, ip + ":" + PORT); });
        }
        ex.shutdown();
        try { ex.awaitTermination(20, TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
        ex.shutdownNow();
        return hit.get();
    }

    boolean probe(String ip) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://" + ip + ":" + PORT + "/api/hello").openConnection();
            c.setConnectTimeout(450);
            c.setReadTimeout(700);
            if (c.getResponseCode() != 200) return false;
            InputStream in = c.getInputStream();
            byte[] buf = new byte[200];
            int n = in.read(buf);
            return n > 0 && new String(buf, 0, n).contains("rgb-control");
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** "192.168.1." for the Wi-Fi interface, or null if there's no private IPv4 address. */
    String localPrefix() {
        try {
            String best = null;
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (!(a instanceof Inet4Address) || !a.isSiteLocalAddress()) continue;
                    String ip = a.getHostAddress();
                    String p = ip.substring(0, ip.lastIndexOf('.') + 1);
                    if (ni.getName().startsWith("wlan")) return p;  // prefer Wi-Fi
                    if (best == null) best = p;
                }
            }
            return best;
        } catch (Exception e) {
            return null;
        }
    }

    // ------------------------------------------------------------------ system callbacks
    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == REQ_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(req, res, data);
    }

    @Override
    public void onBackPressed() {
        if (setupView.getVisibility() == View.VISIBLE && host() != null) { setupView.setVisibility(View.GONE); return; }
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        // coming back after the phone slept / changed Wi-Fi: reload if the page had failed
        if (pageFailed && host() != null && !discovering) start();
    }

    @Override
    protected void onPause() {
        web.onPause();
        super.onPause();
    }
}
