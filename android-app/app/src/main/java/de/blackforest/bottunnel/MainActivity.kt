package de.blackforest.bottunnel

import android.net.http.SslCertificate
import android.net.http.SslError
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
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
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.io.ByteArrayInputStream
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "MainActivity"
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
        // Bewusst wieder EXAKT wie in der nachweislich funktionierenden Erstversion -
        // ein zwischenzeitlicher Versuch, das durch aktives TCP-Probing zu ersetzen,
        // wurde zurueckgebaut: die Probe oeffnete alle 500ms eine rohe TCP-Verbindung
        // zum dumbpipe-Port und schloss sie sofort wieder ab - das entspricht exakt dem
        // bekannten Absturzmuster "Endpoint dropped without calling Endpoint::close"
        // (serverseitig mehrfach beobachtet) und konnte den echten, gerade im Aufbau
        // befindlichen Tunnel selbst gestoert haben.
        private const val TUNNEL_STARTUP_DELAY_MS = 2000L

        // Retry-Intervall und Obergrenze fuer den WebView-Ladeversuch (siehe
        // onReceivedError) - das war schon in der Erstversion der Mechanismus, der die
        // Verbindung tatsaechlich zustande brachte, wenn der Tunnel etwas laenger
        // brauchte. Jetzt nur mit einer klaren Obergrenze + Fehlermeldung statt
        // unbegrenzt stillschweigend zu retryen.
        private const val RETRY_INTERVAL_MS = 1500L
        private const val MAX_RETRIES = 25 // ca. 37s zusaetzlich zur Startverzoegerung
    }

    private lateinit var prefs: android.content.SharedPreferences
    private lateinit var ticketLayout: LinearLayout
    private lateinit var loadingLayout: LinearLayout
    private lateinit var webView: WebView
    private lateinit var ticketInput: EditText

    private var tunnelRunningForThisSession = false
    private val mainHandler = Handler(Looper.getMainLooper())
    private var pendingLoad: Runnable? = null

    // Zaehlt Ladeversuche fuer den AKTUELLEN Verbindungsversuch, zusammen mit
    // retryGeneration gegen veraltete Retry-Callbacks abgesichert (z.B. nach
    // Ticket-Wechsel sollen alte Retries nicht mehr weiterlaufen).
    private var retryCount = 0
    private var retryGeneration = 0
    @Volatile
    private var connectionGeneration = 0

    // Hintergrund-Thread NUR fuer das (potenziell bis zu 5s blockierende) Beenden
    // eines BEREITS LAUFENDEN Tunnel-Prozesses (siehe TunnelManager.stop() - wartet
    // auf tatsaechliche Prozess-Beendigung, damit ein direkt danach gestarteter neuer
    // Prozess nicht denselben lokalen Port noch belegt vorfindet). WICHTIG: der
    // normale Erststart (kein vorheriger Prozess) läuft bewusst NICHT über diesen
    // Executor, sondern synchron im UI-Thread wie in der Erstversion - dort ist
    // stop() ohnehin ein No-Op, ein Umweg über einen weiteren Thread brachte dort
    // nur zusaetzliche Verzoegerung/Fehlerquellen ohne Nutzen.
    private lateinit var tunnelExecutor: ExecutorService

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tunnelExecutor = Executors.newSingleThreadExecutor()

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

        val backCallback = object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.visibility == View.VISIBLE && webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        }
        onBackPressedDispatcher.addCallback(this, backCallback)
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
        connectionGeneration++
        pendingLoad?.let { mainHandler.removeCallbacks(it) }
        pendingLoad = null
        if (tunnelRunningForThisSession) {
            tunnelRunningForThisSession = false
            tunnelExecutor.submit { TunnelManager.stop() }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        tunnelExecutor.shutdown()
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
                Log.w(TAG, "WebView onReceivedError: $errorCode $description (Versuch ${retryCount + 1})")
                val myRetryGeneration = retryGeneration
                if (myRetryGeneration != connectionGeneration) return // ueberholter Versuch
                if (retryCount < MAX_RETRIES) {
                    retryCount++
                    view.postDelayed({
                        if (myRetryGeneration == connectionGeneration) view.loadUrl(BACKEND_URL)
                    }, RETRY_INTERVAL_MS)
                } else {
                    val logExcerpt = TunnelManager.recentLog.takeLast(8).joinToString("\n")
                    Log.e(
                        TAG,
                        "Tunnel nach $MAX_RETRIES Versuchen nicht erreichbar. Letzte dumbpipe-Ausgabe:\n$logExcerpt"
                    )
                    Toast.makeText(this@MainActivity, R.string.tunnel_timeout_error, Toast.LENGTH_LONG).show()
                    showTicketEntry()
                }
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

        connectionGeneration++

        if (!TunnelManager.isRunning()) {
            // KEIN vorheriger Prozess vorhanden (Normalfall: Erststart, oder die
            // App war schon vollstaendig gestoppt) - exakt wie in der
            // nachweislich funktionierenden Erstversion: synchron im UI-Thread,
            // kein Executor-Umweg. TunnelManager.start() ruft intern zwar stop()
            // auf, das ist hier aber ein reines No-Op (kein Prozess vorhanden),
            // also nicht blockierend.
            startTunnelAndScheduleLoad(ticket)
        } else {
            // Es laeuft noch ein Prozess (z.B. schneller Ticket-Wechsel oder ein
            // knapper onStop/onStart-Zyklus) - der muss ERST zuverlaessig beendet
            // werden (TunnelManager.stop() wartet ggf. bis zu 5s auf echte
            // Terminierung, siehe dortige Begruendung) - das NICHT im UI-Thread.
            tunnelExecutor.submit {
                TunnelManager.stop()
                runOnUiThread { startTunnelAndScheduleLoad(ticket) }
            }
        }
    }

    private fun startTunnelAndScheduleLoad(ticket: String) {
        if (!TunnelManager.start(this, ticket)) {
            Toast.makeText(this, R.string.tunnel_start_error, Toast.LENGTH_LONG).show()
            showTicketEntry()
            return
        }
        tunnelRunningForThisSession = true
        retryCount = 0
        retryGeneration = connectionGeneration

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
            connectionGeneration++
            pendingLoad?.let { mainHandler.removeCallbacks(it) }
            pendingLoad = null
            if (tunnelRunningForThisSession) {
                tunnelRunningForThisSession = false
                tunnelExecutor.submit { TunnelManager.stop() }
            }
            ticketInput.setText("")
            showTicketEntry()
            return true
        }
        return super.onOptionsItemSelected(item)
    }
}
