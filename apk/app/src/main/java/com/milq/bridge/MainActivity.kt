package com.milq.bridge

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.button.MaterialButton
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/* MilqBridge Android — Material UI: Status · Login · Models · Usage tabs. Talks to a bridge server (LAN). */

class Api(val ctx: Context) {
    private val prefs get() = ctx.getSharedPreferences("mb", 0)
    var base: String
        get() = (prefs.getString("base", "") ?: "").ifBlank { "http://127.0.0.1:11434" }
        set(v) { prefs.edit().putString("base", v.trimEnd('/')).apply() }
    var key: String
        get() = prefs.getString("key", "") ?: ""
        set(v) { prefs.edit().putString("key", v).apply() }

    fun get(path: String, auth: Boolean = true) = req("GET", path, null, auth)
    fun post(path: String, body: JSONObject? = null) = req("POST", path, (body ?: JSONObject()).toString(), true)
    fun chat(model: String, prompt: String): Pair<Int, String> {
        val b = JSONObject().put("model", model)
            .put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", prompt)))
        return req("POST", "/v1/chat/completions", b.toString(), true)
    }
    fun autoKey() { // fetch /local/key if reachable (same-LAN servers set BRIDGE_LAN=1)
        try { val (st, b) = get("/local/key", auth = false)
            if (st == 200) key = JSONObject(b).optString("key", key) } catch (_: Exception) {}
    }

    private fun req(method: String, path: String, body: String?, auth: Boolean): Pair<Int, String> {
        return try {
            val c = URL(base + path).openConnection() as HttpURLConnection
            c.requestMethod = method
            c.connectTimeout = 12000; c.readTimeout = 90000
            c.setRequestProperty("Content-Type", "application/json")
            if (auth && key.isNotEmpty()) c.setRequestProperty("Authorization", "Bearer $key")
            if (body != null) { c.doOutput = true; c.outputStream.use { it.write(body.toByteArray()) } }
            val st = c.responseCode
            val text = (if (st in 200..299) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
            st to text
        } catch (e: Exception) { 0 to (e.message ?: "network error") }
    }
}

fun dp(ctx: Context, v: Float) = (v * ctx.resources.displayMetrics.density).toInt()

class MainActivity : AppCompatActivity() {
    private lateinit var api: Api
    private val pool = Executors.newCachedThreadPool()
    private lateinit var pages: LinearLayout
    private lateinit var nav: BottomNavigationView

    // status page
    private lateinit var urlInput: EditText
    private lateinit var keyInput: EditText
    private lateinit var statusBox: LinearLayout
    private lateinit var agentTxt: TextView

    // login page
    private lateinit var loginState: TextView
    private lateinit var codeTv: TextView
    private lateinit var startBtn: MaterialButton
    private lateinit var openBtn: MaterialButton
    private var verifyUrl: String? = null
    private var polling = false

    // models / usage
    private lateinit var modelsList: LinearLayout
    private lateinit var usageList: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        api = Api(this)
        window.statusBarColor = Color.parseColor("#020617")

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setBackgroundColor(Color.parseColor("#020617")) }
        pages = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; layoutParams = LinearLayout.LayoutParams(-1, 0, 1f) }
        pages.addView(statusPage(), LinearLayout.LayoutParams(-1, -1))
        pages.addView(loginPage(), LinearLayout.LayoutParams(-1, -1))
        pages.addView(modelsPage(), LinearLayout.LayoutParams(-1, -1))
        pages.addView(usagePage(), LinearLayout.LayoutParams(-1, -1))
        nav = BottomNavigationView(this).apply {
            menu.add(0, 0, 0, "Status")
            menu.add(0, 1, 1, "Login")
            menu.add(0, 2, 2, "Models")
            menu.add(0, 3, 3, "Usage")
            setBackgroundColor(Color.parseColor("#0E1223"))
            setOnItemSelectedListener { item ->
                for (i in 0 until pages.childCount) pages.getChildAt(i).visibility = if (item.itemId == i) View.VISIBLE else View.GONE
                when (item.itemId) { 0 -> refreshStatus(); 2 -> refreshModels(); 3 -> refreshUsage() }
                true
            }
        }
        root.addView(pages)
        root.addView(nav, LinearLayout.LayoutParams(-1, -2))
        setContentView(root)
        refreshStatus()
    }

    // ---------- builders ----------
    private fun card(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(this@MainActivity, 16f), dp(this@MainActivity, 14f), dp(this@MainActivity, 16f), dp(this@MainActivity, 14f))
        background = GradientDrawable().apply {
            setColor(Color.parseColor("#0E1223")); cornerRadius = dp(this@MainActivity, 14f).toFloat()
            setStroke(dp(this@MainActivity, 1), Color.parseColor("#334155"))
        }
    }

    private fun tv(s: String, size: Float, color: Int = Color.parseColor("#F8FAFC"), bold: Boolean = false): TextView =
        TextView(this).apply { text = s; textSize = size; setTextColor(color); if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD) }

    private fun mbtn(label: String, filled: Boolean = true): MaterialButton = MaterialButton(this).apply {
        text = label; isAllCaps = false
        if (filled) { setBackgroundColor(Color.parseColor("#22C55E")); setTextColor(Color.parseColor("#0F172A")) }
        else { setStroke(dp(this@MainActivity, 1), Color.parseColor("#334155")) }
    }

    private fun padCol(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(this@MainActivity, 16f), dp(this@MainActivity, 18f), dp(this@MainActivity, 16f), dp(this@MainActivity, 84f))
    }

    private fun et(hint: String, cur: String = "", uri: Boolean = false): EditText = EditText(this).apply {
        this.hint = hint; setText(cur)
        setTextColor(Color.parseColor("#F8FAFC")); setHintTextColor(Color.parseColor("#94A3B8"))
        if (uri) inputType = InputType.TYPE_TEXT_VARIATION_URI
    }

    // ---------- STATUS ----------
    private fun statusPage(): ScrollView {
        val sc = ScrollView(this)
        val col = padCol()
        col.addView(tv("MilqBridge", 26f, bold = true))
        col.addView(tv("FreeMilk models → OpenAI API for any agent", 13f, Color.parseColor("#94A3B8")))

        val c = card().apply { layoutParams = lp(14f) }
        c.addView(tv("Bridge server", 15f, bold = true))
        urlInput = et("http://192.168.x.x:11434", api.base, uri = true)
        keyInput = et("bridge API key", api.key)
        val save = mbtn("Save & connect").apply { setOnClickListener {
            api.base = urlInput.text.toString().ifBlank { api.base }
            api.key = keyInput.text.toString().trim()
            pool.execute { api.autoKey() }
            refreshStatus()
            updateAgent()
        } }
        c.addView(urlInput); c.addView(keyInput); c.addView(save)
        statusBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        c.addView(statusBox)
        col.addView(c)

        val c2 = card().apply { layoutParams = lp(14f) }
        c2.addView(tv("Use in any coding agent", 15f, bold = true))
        agentTxt = tv("", 13f, Color.parseColor("#38BDF8"))
        updateAgent()
        c2.addView(agentTxt)
        c2.addView(mbtn("Copy config").apply { setOnClickListener {
            copy("export OPENAI_BASE_URL=${api.base}/v1\nexport OPENAI_API_KEY=${api.key}")
        } })
        col.addView(c2)
        sc.addView(col)
        return sc
    }
    private fun lp(top: Float): LinearLayout.LayoutParams = LinearLayout.LayoutParams(-1, -2).also { it.topMargin = dp(this, top) }

    private fun updateAgent() {
        if (!::agentTxt.isInitialized) return
        agentTxt.text = "OPENAI_BASE_URL=${api.base}/v1\nOPENAI_API_KEY=${api.key.ifBlank { "<save key first>" }}"
    }

    private fun refreshStatus() {
        pool.execute {
            val (stH, h) = api.get("/health", auth = false)
            val (stS, s) = api.get("/status")
            val hj = try { JSONObject(h) } catch (e: Exception) { JSONObject() }
            val sj = if (stS == 200) try { JSONObject(s) } catch (e: Exception) { JSONObject() } else JSONObject()
            runOnUiThread {
                statusBox.removeAllViews()
                if (stH != 200) { statusBox.addView(tv("● server unreachable — check URL / BRIDGE_LAN=1", 13f, Color.parseColor("#EF4444"))); return@runOnUiThread }
                statusBox.addView(tv("● online · ${hj.optInt("models")} models", 13f, Color.parseColor("#22C55E")))
                val login = sj.optJSONObject("login")
                val signed = login?.optBoolean("signed_in") == true
                statusBox.addView(tv("ChatGPT: " + if (signed) "connected (${login.optString("plan")})" else "not connected", 13f,
                    if (signed) Color.parseColor("#22C55E") else Color.parseColor("#F59E0B")))
                sj.optJSONObject("usage_summary")?.let { u ->
                    statusBox.addView(tv("requests ${u.optInt("requests")} · errors ${u.optInt("errors")} · tokens ${u.optInt("in_tokens") + u.optInt("out_tokens")}", 13f, Color.parseColor("#94A3B8")))
                }
            }
        }
    }

    // ---------- LOGIN ----------
    private fun loginPage(): ScrollView {
        val sc = ScrollView(this)
        val col = padCol()
        val c = card()
        c.addView(tv("ChatGPT / Codex device login", 16f, bold = true))
        c.addView(tv("Same flow the FreeMilk app uses (usercode + PKCE + polling, verified live). Enter the one-time code on the approval page with your ChatGPT account.", 13f, Color.parseColor("#94A3B8")))
        codeTv = tv("", 30f, Color.parseColor("#22C55E"), bold = true).apply { gravity = Gravity.CENTER; letterSpacing = 2f }
        loginState = tv("idle", 13f, Color.parseColor("#94A3B8"))
        startBtn = mbtn("Start device login").apply { setOnClickListener { startLogin() } }
        openBtn = mbtn("Open approval page", filled = false).apply {
            visibility = View.GONE
            setOnClickListener { verifyUrl?.let { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(it))) } ?: toast("start login first") }
        }
        c.addView(codeTv); c.addView(loginState); c.addView(startBtn); c.addView(openBtn)
        col.addView(c)
        sc.addView(col)
        return sc
    }

    private fun startLogin() {
        startBtn.isEnabled = false
        loginState.text = "requesting one-time code…"
        codeTv.text = ""
        pool.execute {
            val (st, b) = api.post("/login/codex/start")
            val j = try { JSONObject(b) } catch (e: Exception) { JSONObject() }
            runOnUiThread {
                startBtn.isEnabled = true
                if (st == 200 && j.optString("stage") == "pending") {
                    codeTv.text = j.optString("user_code")
                    verifyUrl = j.optString("verification_url")
                    loginState.text = "waiting for approval…"
                    openBtn.visibility = View.VISIBLE
                    beginPoll()
                } else loginState.text = "failed: ${j.optString("error", b.take(140))}"
            }
        }
    }

    private fun beginPoll() {
        if (polling) return
        polling = true
        val r = object : Runnable {
            override fun run() {
                if (!polling) return
                pool.execute {
                    val (st, b) = api.get("/login/codex/status", auth = false)
                    val j = try { JSONObject(b) } catch (e: Exception) { JSONObject() }
                    runOnUiThread {
                        if (!polling) return@runOnUiThread
                        when {
                            j.optBoolean("signed_in") || j.optString("stage") == "ready" -> {
                                polling = false
                                codeTv.text = "✓"
                                loginState.text = "connected · ${j.optString("plan")}"
                                openBtn.visibility = View.GONE
                                toast("ChatGPT connected")
                            }
                            j.optString("stage") in listOf("error", "expired") -> {
                                polling = false
                                loginState.text = "failed: ${j.optString("error")}"
                            }
                            else -> loginState.text = "waiting… (${j.optInt("polls")} polls)"
                        }
                    }
                }
                pages.postDelayed(this, 2500)
            }
        }
        pages.postDelayed(r, 2000)
    }

    // ---------- MODELS ----------
    private fun modelsPage(): ScrollView {
        val sc = ScrollView(this)
        val col = padCol()
        col.addView(tv("Models", 22f, bold = true))
        col.addView(tv("free = daily sips · plan = your ChatGPT · premium = ad-unlock or payment", 12f, Color.parseColor("#94A3B8")))
        modelsList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(modelsList)
        sc.addView(col)
        return sc
    }

    private fun refreshModels() {
        pool.execute {
            val (st, b) = api.get("/v1/models")
            val arr = if (st == 200) try { JSONObject(b).getJSONArray("data") } catch (e: Exception) { JSONArray() } else null
            val codes = ArrayList<Pair<Int, JSONObject>>()
            if (arr != null) for (i in 0 until arr.length()) codes.add(st to arr.getJSONObject(i))
            runOnUiThread {
                modelsList.removeAllViews()
                if (arr == null) { modelsList.addView(tv("could not load (set key & server in Status tab)", 13f, Color.parseColor("#F59E0B"))); return@runOnUiThread }
                for ((_, m) in codes) {
                    val milq = m.optJSONObject("milq") ?: JSONObject()
                    val tier = milq.optString("tier")
                    val color = when (tier) { "free" -> "#22C55E"; "plan" -> "#38BDF8"; else -> "#F59E0B" }
                    val c = card().apply { layoutParams = lp(10f) }
                    c.addView(tv(milq.optString("display", m.optString("id")), 15f, bold = true))
                    c.addView(tv(m.optString("id"), 11f, Color.parseColor("#94A3B8")))
                    c.addView(tv("$tier · ${milq.optString("api_format")} · ${(milq.optInt("context_window") / 1000)}k ctx", 12f, Color.parseColor(color)))
                    val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
                    if (tier == "premium") row.addView(mbtn("watch ad → unlock 1h", filled = false).apply {
                        layoutParams = LinearLayout.LayoutParams(-2, -2).also { it.marginEnd = dp(this@MainActivity, 8f) }
                        setOnClickListener {
                            pool.execute {
                                val (_, rb) = api.post("/ads/unlock", JSONObject().put("model", m.optString("id")))
                                runOnUiThread { toast(if (try { JSONObject(rb).optBoolean("ok") } catch (e: Exception) { false }) "unlocked for 1h" else "denied") }
                            }
                        }
                    })
                    row.addView(mbtn("test").apply {
                        setOnClickListener {
                            pool.execute {
                                val (cs, cb) = api.chat(m.optString("id"), "Reply with exactly: OK")
                                runOnUiThread { toast("HTTP $cs · ${cb.replace("\n", " ").take(90)}") }
                            }
                        }
                    })
                    c.addView(row)
                    modelsList.addView(c)
                }
            }
        }
    }

    // ---------- USAGE ----------
    private fun usagePage(): ScrollView {
        val sc = ScrollView(this)
        val col = padCol()
        col.addView(tv("Usage & request status", 22f, bold = true))
        usageList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        col.addView(usageList)
        sc.addView(col)
        return sc
    }

    private fun refreshUsage() {
        pool.execute {
            val (st, b) = api.get("/usage")
            runOnUiThread {
                usageList.removeAllViews()
                if (st != 200) { usageList.addView(tv("unauthorized / unreachable", 13f, Color.parseColor("#F59E0B"))); return@runOnUiThread }
                val u = try { JSONObject(b).getJSONObject("local") } catch (e: Exception) { JSONObject() }
                val sum = card().apply { layoutParams = lp(0f) }
                sum.addView(tv("${u.optInt("requests")} requests · ${u.optInt("errors")} errors", 15f, bold = true))
                sum.addView(tv("in ${u.optInt("in_tokens")} · out ${u.optInt("out_tokens")} tokens", 13f, Color.parseColor("#94A3B8")))
                val bym = u.optJSONObject("by_model")
                if (bym != null) {
                    val sb = StringBuilder()
                    bym.keys().forEach { k -> sb.append("$k ×${bym.getInt(k)}   ") }
                    sum.addView(tv(sb.toString().trim(), 12f, Color.parseColor("#38BDF8")))
                }
                usageList.addView(sum)
                val log = u.optJSONArray("log") ?: JSONArray()
                for (i in 0 until minOf(log.length(), 30)) {
                    val r = log.getJSONObject(i)
                    val c = card().apply { layoutParams = lp(8f) }
                    val ok = r.optInt("status") < 400
                    c.addView(tv("${r.optString("model")} · HTTP ${r.optInt("status")} · ${r.optInt("in")}/${r.optInt("out")} tok · ${r.optInt("ms")}ms", 12f,
                        Color.parseColor(if (ok) "#22C55E" else "#EF4444")))
                    if (r.optString("err").isNotEmpty()) c.addView(tv(r.optString("err"), 11f, Color.parseColor("#94A3B8")))
                    usageList.addView(c)
                }
            }
        }
    }

    // ---------- helpers ----------
    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()
    private fun copy(s: String) {
        val cm = getSystemService(CLIPBOARD_SERVICE) as android.content.ClipboardManager
        cm.setPrimaryClip(android.content.ClipData.newPlainText("milq", s)); toast("copied")
    }
    override fun onPause() { super.onPause(); polling = false }
}
