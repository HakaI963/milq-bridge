package com.milq.bridge

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/* MilqBridge Android — loads the bridge's mobile UI (same SPA you see in a browser).
   First run: server address + API key; then the full dashboard (Home · Models · Free Models · Usage · Agents)
   runs natively here. Bridge normally runs in Termux on this phone: http://127.0.0.1:11434 */

class MainActivity : AppCompatActivity() {
    private val prefs by lazy { getSharedPreferences("mb", 0) }
    private var web: WebView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Color.parseColor("#020617")
        val base = prefs.getString("base", "") ?: ""
        val key = prefs.getString("key", "") ?: ""
        if (base.isEmpty() || key.isEmpty()) showSetup(base, key) else showApp(base, key)
    }

    private fun dark(o: Int): LinearLayout = LinearLayout(this).apply {
        orientation = o
        setBackgroundColor(Color.parseColor("#020617"))
        setPadding(dp(18f), dp(18f), dp(18f), dp(18f))
    }

    private fun dp(v: Float) = (v * resources.displayMetrics.density).toInt()

    private fun tv(s: String, size: Float, color: Int, bold: Boolean = false): TextView =
        TextView(this).apply { text = s; textSize = size; setTextColor(color); if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD) }

    private fun field(hint: String, value: String, uri: Boolean = false): EditText = EditText(this).apply {
        this.hint = hint; setText(value)
        setTextColor(Color.parseColor("#F8FAFC")); setHintTextColor(Color.parseColor("#94A3B8"))
        setBackgroundColor(Color.parseColor("#131A2E"))
        setPadding(dp(14f), dp(14f), dp(14f), dp(14f))
        if (uri) inputType = android.text.InputType.TYPE_TEXT_VARIATION_URI
    }

    private fun showSetup(base0: String, key0: String) {
        val col = dark(LinearLayout.VERTICAL)
        col.gravity = Gravity.CENTER_HORIZONTAL
        col.addView(tv("MilqBridge", 26f, Color.parseColor("#F8FAFC"), true))
        col.addView(tv("FreeMilk free models → OpenAI API on this phone.\n\n1) In Termux run:\n   git clone https://github.com/HakaI963/milq-bridge && cd milq-bridge\n   BRIDGE_LAN=1 python3 bridge/server.py --port 11434\n2) The key is printed + saved at ~/.milq-bridge/bridge_key\n3) Enter address + key below.", 13f, Color.parseColor("#94A3B8")))
        val url = field("server URL (http://127.0.0.1:11434)", if (base0.isEmpty()) "http://127.0.0.1:11434" else base0, uri = true)
        val ky = field("bridge API key", key0)
        val tryBtn = MaterialButton(this).apply { text = "Fetch my key from server"; isAllCaps = false }
        val go = MaterialButton(this).apply { text = "Start"; isAllCaps = false; setBackgroundColor(Color.parseColor("#22C55E")); setTextColor(Color.parseColor("#0F172A")) }
        tryBtn.setOnClickListener {
            Thread {
                try {
                    val u = url.text.toString().trimEnd('/') + "/local/key"
                    val c = URL(u).openConnection() as HttpURLConnection
                    c.connectTimeout = 8000; c.readTimeout = 8000
                    val j = JSONObject(c.inputStream.bufferedReader().readText())
                    runOnUiThread { ky.setText(j.optString("key")); toast("key fetched") }
                } catch (e: Exception) { runOnUiThread { toast("fetch failed: ${e.message}") } }
            }.start()
        }
        go.setOnClickListener {
            val b = url.text.toString().trim().trimEnd('/')
            val k = ky.text.toString().trim()
            if (b.isEmpty() || k.isEmpty()) { toast("fill both fields"); return@setOnClickListener }
            prefs.edit().putString("base", b).putString("key", k).apply()
            (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager).hideSoftInputFromWindow(go.windowToken, 0)
            showApp(b, k)
        }
        val spacing = { v: View -> LinearLayout.LayoutParams(-1, -2).also { it.topMargin = dp(14f) } }
        col.addView(url, spacing(url)); col.addView(ky, spacing(ky)); col.addView(tryBtn, spacing(tryBtn)); col.addView(go, spacing(go))
        setContentView(ScrollView(this).apply { addView(col) })
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun showApp(base: String, key: String) {
        val w = WebView(this).apply {
            setBackgroundColor(Color.parseColor("#020617"))
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, req: android.webkit.WebResourceRequest): Boolean {
                    val u = req.url.toString()
                    if (!u.startsWith(base)) { // external links open in browser
                        try { startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, Uri.parse(u))) } catch (_: Exception) {}
                        return true
                    }
                    return false
                }
                override fun onReceivedError(view: WebView, f: android.webkit.WebResourceRequest?, e: android.webkit.WebResourceError?) {
                    if (f?.isForMainFrame == true) runOnUiThread {
                        toast("can't reach $base — start the bridge in Termux")
                    }
                }
            }
        }
        web = w
        setContentView(w)
        w.loadUrl("$base/?key=${Uri.encode(key)}")
    }

    override fun onBackPressed() {
        val w = web
        if (w != null && w.canGoBack()) w.goBack() else super.onBackPressed()
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()
}
