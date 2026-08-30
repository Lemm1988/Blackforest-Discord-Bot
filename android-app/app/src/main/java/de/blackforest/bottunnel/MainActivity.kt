package de.blackforest.bottunnel

import android.net.http.SslCertificate
import android.net.http.SslError
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.webkit.SslErrorHandler
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.io.ByteArrayInputStream
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

class MainActivity : AppCompatActivity() {

    companion object {
        private const val BACKEND_URL = "https://127.0.0.1:8500"
        private const val PREFS_NAME = "tunnel_prefs"
        private const val KEY_TICKET = "iroh_ticket"

        // SHA-256-Fingerabdruck von backend/ssl/server.crt auf dem Pi.
        // MUSS aktualisiert werden, falls das Zertifikat je neu erzeugt wird
        // (z.B. via: openssl x509 -in backend/ssl/server.crt -noout -fingerprint -sha256).
        private const val EXPECTED_FINGERPRINT_SHA256 =
            "6C:6F:DE:28:DF:07:7E:0B:E6:C1:3D:F4:F2:11:64:47:4D:6E:32:2A:6E:F6:D5:46:3A:72:73:79:FC:E1:AA:BD"

        // Verzoegerung, bis der Iroh-Tunnel (NAT-Hole-Punching oder Relay-Fallback)
        // typischerweise steht, bevor die WebView den ersten Ladeversuch macht.
        private const val TUNNEL_STARTUP_DELAY_MS = 2000L
    }

    private lateinit var prefs: android.content.SharedPreferences
    private lateinit var ticketLayout: LinearLayout
    private lateinit var loadingLayout: LinearLayout
    private lateinit var webView: WebView
    private lateinit var ticketInput: EditText

    private var tunnelRunningForThisSession = false
    private val mainHandler = Handler(Looper.getMainLooper())
    private var pendingLoad: Runnable? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val masterKey = MasterKey.Builder(this)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        prefs = EncryptedSharedPreferences.create(
            this,
            PREFS_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )

        ticketLayout = findViewById(R.id.ticketLayout)
        loadingLayout = findViewById(R.id.loadingLayout)
        webView = findViewById(R.id.webView)
        ticketInput = findViewById(R.id.ticketInput)

        findViewById<Button>(R.id.saveTicketButton).setOnClickListener {
            val ticket = ticketInput.text.toString().trim()
            if (ticket.isEmpty()) {
                Toast.makeText(this, R.string.ticket_empty_error, Toast.LENGTH_SHORT).show()
            } else {
                prefs.edit().putString(KEY_TICKET, ticket).apply()
                connectAndShowWebView()
            }
        }

        setupWebView()

        onBackPressedDispatcher.addCallback(this) {
            if (webView.visibility == View.VISIBLE && webView.canGoBack()) {
                webView.goBack()
            } else {
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        val savedTicket = prefs.getString(KEY_TICKET, null)
        if (savedTicket.isNullOrEmpty()) {
            showTicketEntry()
        } else {
            connectAndShowWebView()
        }
    }

    override fun onStop() {
        super.onStop()
        pendingLoad?.let { mainHandler.removeCallbacks(it) }
        pendingLoad = null
        if (tunnelRunningForThisSession) {
            TunnelManager.stop()
            tunnelRunningForThisSession = false
        }
    }

    private fun setupWebView() {
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                val actual = fingerprintOf(error)
                if (actual != null && actual.equals(EXPECTED_FINGERPRINT_SHA256, ignoreCase = true)) {
                    handler.proceed()
                } else {
                    handler.cancel()
                    Toast.makeText(
                        this@MainActivity, R.string.cert_mismatch_error, Toast.LENGTH_LONG
                    ).show()
                }
            }

            override fun onReceivedError(
                view: WebView, errorCode: Int, description: String?, failingUrl: String?
            ) {
                // Tunnel steht evtl. noch nicht ganz - kurz erneut versuchen statt Fehlerseite zu zeigen.
                view.postDelayed({ view.loadUrl(BACKEND_URL) }, 1500)
            }
        }
    }

    /**
     * Android liefert bei onReceivedSslError kein direktes Byte-Array des
     * Zertifikats - der Umweg ueber Bundle.saveState ist der uebliche,
     * dokumentierte Weg, um an die rohen DER-Bytes zu kommen.
     */
    private fun fingerprintOf(error: SslError): String? {
        return try {
            val bundle = SslCertificate.saveState(error.certificate)
            val bytes = bundle.getByteArray("x509-certificate") ?: return null
            val cert = CertificateFactory.getInstance("X.509")
                .generateCertificate(ByteArrayInputStream(bytes)) as X509Certificate
            val digest = MessageDigest.getInstance("SHA-256").digest(cert.encoded)
            digest.joinToString(":") { String.format("%02X", it) }
        } catch (e: Exception) {
            null
        }
    }

    private fun showTicketEntry() {
        ticketLayout.visibility = View.VISIBLE
        loadingLayout.visibility = View.GONE
        webView.visibility = View.GONE
    }

    private fun connectAndShowWebView() {
        val ticket = prefs.getString(KEY_TICKET, null)
        if (ticket.isNullOrEmpty()) {
            showTicketEntry()
            return
        }
        ticketLayout.visibility = View.GONE
        loadingLayout.visibility = View.VISIBLE
        webView.visibility = View.GONE

        if (!TunnelManager.start(this, ticket)) {
            Toast.makeText(this, R.string.tunnel_start_error, Toast.LENGTH_LONG).show()
            showTicketEntry()
            return
        }
        tunnelRunningForThisSession = true

        pendingLoad?.let { mainHandler.removeCallbacks(it) }
        val runnable = Runnable {
            loadingLayout.visibility = View.GONE
            webView.visibility = View.VISIBLE
            webView.loadUrl(BACKEND_URL)
        }
        pendingLoad = runnable
        mainHandler.postDelayed(runnable, TUNNEL_STARTUP_DELAY_MS)
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.main_menu, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == R.id.action_change_ticket) {
            if (tunnelRunningForThisSession) {
                TunnelManager.stop()
                tunnelRunningForThisSession = false
            }
            ticketInput.setText("")
            showTicketEntry()
            return true
        }
        return super.onOptionsItemSelected(item)
    }
}
