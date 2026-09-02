package de.blackforest.bottunnel

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.http.SslCertificate
import android.net.http.SslError
import android.os.Bundle
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
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import android.widget.VideoView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
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

        // NEU (ersetzt die fruehere Kombination aus fixer Start-Verzoegerung +
        // haeufigen WebView-Reload-Retries alle 1,5s): Diagnose hat gezeigt, dass
        // ein einzelner GEDULDIGER, sauberer HTTPS-Request (volles Verbinden bis
        // zum Response, kein abruptes Abbrechen) nach ausreichend Wartezeit
        // zuverlaessig funktioniert - waehrend haeufige, schnelle Neuversuche
        // (WebView-Reload alle 1,5s, ebenso wie der fruehere aktive Port-Probe) den
        // eigentlich erfolgreichen Verbindungsaufbau selbst gestoert haben
        // (dasselbe Absturzmuster "Endpoint dropped without calling
        // Endpoint::close", das serverseitig mehrfach beobachtet wurde). Deshalb:
        // wenige, aber lange geduldige Versuche VOR dem WebView-Laden statt vieler
        // kurzer danach.
        // 5 statt 3 Versuche: zusammen mit READINESS_RETRY_DELAY_MS deckt das rund
        // 12s Anlaufzeit ab. Im WLAN war der Tunnel nach ~3,2s bereit; ueber
        // Mobilfunk (weiterer Relay, langsameres net_report) darf es deutlich
        // laenger dauern, ohne dass die App vorschnell aufgibt. Haengt ein Versuch
        // dagegen, greift weiterhin READINESS_TIMEOUT_MS.
        private const val READINESS_ATTEMPTS = 5
        private const val READINESS_TIMEOUT_MS = 60_000

        // Pause ZWISCHEN den Versuchen. Ohne sie war die "Geduld" oben wirkungslos,
        // sobald ein Versuch nicht haengt, sondern SOFORT fehlschlaegt: dumbpipe
        // lauscht auf 127.0.0.1:8500 bereits ~20ms nach dem Start, sein Iroh-Pfad
        // steht aber erst nach ~3s (net_report + Relay-Verbindung). Ein Dial in
        // diesem Fenster scheitert unmittelbar, die Verbindung wird sofort
        // geschlossen - und alle drei Versuche waren binnen Millisekunden
        // aufgebraucht, lange bevor der Tunnel ueberhaupt einsatzbereit war. Die App
        // zeigte dann die Diagnose, obwohl der Tunnel Sekunden spaeter einwandfrei
        // funktionierte (nachgewiesen: der Port-Check der Diagnose selbst baute
        // anschliessend eine direkte LAN-Verbindung zum Pi auf).
        private const val READINESS_RETRY_DELAY_MS = 3_000L

        // Leichtes Sicherheitsnetz FALLS die WebView nach erfolgreicher
        // Bereitschaftspruefung trotzdem einmal einen Ladefehler meldet (z.B.
        // kurzer Netzwerk-Hakler waehrend des Browsens) - bewusst nur noch wenige,
        // grosszuegig auseinanderliegende Versuche, nicht mehr der primaere
        // Verbindungsaufbau-Mechanismus.
        private const val RETRY_INTERVAL_MS = 4000L
        private const val MAX_RETRIES = 5
    }

    private lateinit var prefs: android.content.SharedPreferences
    private lateinit var ticketLayout: LinearLayout
    private lateinit var loadingLayout: LinearLayout
    private lateinit var loadingVideo: VideoView
    private lateinit var webView: WebView
    private lateinit var ticketInput: EditText

    private var tunnelRunningForThisSession = false

    // Zaehlt Ladeversuche fuer das (leichte) WebView-Sicherheitsnetz NACH
    // erfolgreicher Bereitschaftspruefung, zusammen mit retryGeneration gegen
    // veraltete Retry-Callbacks abgesichert (z.B. nach Ticket-Wechsel sollen alte
    // Retries nicht mehr weiterlaufen).
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

    // EIGENER, separater Hintergrund-Thread nur fuer die (bis zu mehrere Minuten
    // dauernde) Bereitschaftspruefung. Bewusst NICHT derselbe wie tunnelExecutor:
    // sonst wuerde ein TunnelManager.stop()-Aufruf aus onStop() (z.B. User
    // verlaesst die App waehrend die Bereitschaftspruefung noch laeuft) hinter der
    // noch laufenden, langen Pruefung in der Warteschlange stecken bleiben und der
    // Tunnel-Prozess wuerde viel zu spaet tatsaechlich beendet.
    private lateinit var readinessExecutor: ExecutorService

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tunnelExecutor = Executors.newSingleThreadExecutor()
        readinessExecutor = Executors.newSingleThreadExecutor()

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
        loadingVideo = findViewById(R.id.loadingVideo)
        webView = findViewById(R.id.webView)
        ticketInput = findViewById(R.id.ticketInput)

        // Logo-Animation des Ladebildschirms. Stumm und in Dauerschleife: ein
        // Ladebildschirm, der ungefragt Ton abspielt, waere aufdringlich, und der
        // Clip ist mit 8s kuerzer als ein langsamer Tunnelaufbau dauern kann.
        loadingVideo.setVideoURI(
            android.net.Uri.parse("android.resource://$packageName/${R.raw.loading}")
        )
        loadingVideo.setOnPreparedListener { player ->
            player.isLooping = true
            player.setVolume(0f, 0f)
        }

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
        // Erhoehen genuegt, um eine noch laufende Bereitschaftspruefung
        // (readinessExecutor) und ein noch laufendes WebView-Sicherheitsnetz-Retry
        // beim naechsten Generation-Check von selbst abbrechen zu lassen - der
        // eigentliche Tunnel-Stop unten laeuft unabhaengig davon sofort.
        connectionGeneration++
        if (tunnelRunningForThisSession) {
            tunnelRunningForThisSession = false
            tunnelExecutor.submit { TunnelManager.stop() }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        tunnelExecutor.shutdown()
        readinessExecutor.shutdownNow() // shutdownNow: eine laufende Bereitschaftspruefung soll nicht die App-Zerstoerung verzoegern
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
                    showTicketEntry()
                    Toast.makeText(this@MainActivity, R.string.tunnel_timeout_error, Toast.LENGTH_LONG).show()
                    // Diagnose-Dialog zusaetzlich zum Toast automatisch anzeigen - der
                    // User soll die Rohdaten direkt kopieren/teilen koennen, ohne
                    // ADB/PC zu brauchen (ist ueber das Menue auch jederzeit manuell
                    // erreichbar, siehe onOptionsItemSelected). WICHTIG:
                    // includeLivePortCheck=false - der Tunnel-PROZESS laeuft nach dem
                    // Aufgeben der WebView ggf. noch im Hintergrund weiter und
                    // versucht evtl. noch, die (NAT-Hole-Punching/Relay-)Verbindung
                    // fertig aufzubauen. Ein Live-Port-Check hier wuerde exakt den
                    // bekannten Server-Absturz "Endpoint dropped without calling
                    // Endpoint::close" ausloesen und einen eventuell kurz vor dem
                    // Erfolg stehenden Verbindungsaufbau selbst zerstoeren.
                    showDiagnosticsDialog(includeLivePortCheck = false)
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
        setLoadingVisible(false)
        webView.visibility = View.GONE
    }

    /**
     * Blendet den Ladebildschirm ein/aus und steuert dabei die Logo-Animation
     * mit. Die Wiedergabe wird pausiert, sobald der Ladebildschirm verschwindet -
     * sonst liefe der Decoder waehrend der gesamten WebView-Nutzung unsichtbar
     * weiter und wuerde nur Akku kosten.
     */
    private fun setLoadingVisible(visible: Boolean) {
        loadingLayout.visibility = if (visible) View.VISIBLE else View.GONE
        if (visible) {
            if (!loadingVideo.isPlaying) loadingVideo.start()
        } else {
            if (loadingVideo.isPlaying) loadingVideo.pause()
        }
    }

    private fun connectAndShowWebView() {
        val ticket = prefs.getString(KEY_TICKET, null)
        if (ticket.isNullOrEmpty()) {
            showTicketEntry()
            return
        }
        ticketLayout.visibility = View.GONE
        setLoadingVisible(true)
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
        val myGeneration = connectionGeneration

        // Statt sofort/nach fixer kurzer Verzoegerung zu laden und dann bei Fehlern
        // haeufig neu zu versuchen (fruehere Version - siehe Konstanten-Kommentar
        // oben, warum das den eigentlich erfolgreichen Verbindungsaufbau selbst
        // stoeren konnte): jetzt GEDULDIG im Hintergrund warten, bis eine echte
        // HTTPS-Antwort kommt, und die WebView erst DANACH ueberhaupt laden.
        readinessExecutor.submit {
            val ready = waitForBackendReady(myGeneration)
            runOnUiThread {
                if (myGeneration != connectionGeneration || isFinishing || isDestroyed) return@runOnUiThread
                if (ready) {
                    setLoadingVisible(false)
                    webView.visibility = View.VISIBLE
                    webView.loadUrl(BACKEND_URL)
                } else {
                    val logExcerpt = TunnelManager.recentLog.takeLast(8).joinToString("\n")
                    Log.e(TAG, "Tunnel nach $READINESS_ATTEMPTS Versuchen nicht bereit. Letzte dumbpipe-Ausgabe:\n$logExcerpt")
                    showTicketEntry()
                    Toast.makeText(this, R.string.tunnel_timeout_error, Toast.LENGTH_LONG).show()
                    // includeLivePortCheck=false: siehe Begruendung in onReceivedError weiter unten -
                    // ein Live-Port-Check hier koennte einen noch laufenden Tunnel-Prozess stoeren.
                    showDiagnosticsDialog(includeLivePortCheck = false)
                }
            }
        }
    }

    /**
     * Wartet GEDULDIG (mehrere Minuten Gesamtbudget), bis das Backend ueber den
     * Tunnel tatsaechlich antwortet - wenige lange Versuche statt vieler kurzer,
     * siehe Begruendung bei READINESS_ATTEMPTS/READINESS_TIMEOUT_MS oben. Bricht
     * fruehzeitig ab, falls der Verbindungsversuch inzwischen ueberholt wurde
     * (Ticket gewechselt, App verlassen).
     */
    private fun waitForBackendReady(myGeneration: Int): Boolean {
        repeat(READINESS_ATTEMPTS) { attemptIndex ->
            if (myGeneration != connectionGeneration) return false
            Log.i(
                TAG,
                "Bereitschafts-Check Versuch ${attemptIndex + 1}/$READINESS_ATTEMPTS " +
                    "(bis zu ${READINESS_TIMEOUT_MS / 1000}s Geduld)"
            )
            if (isBackendReadyOnce(READINESS_TIMEOUT_MS)) return true

            // Nur warten, wenn noch ein Versuch folgt. Entscheidend fuer den Fall
            // "schlaegt sofort fehl" - siehe READINESS_RETRY_DELAY_MS: ohne diese
            // Pause waren alle Versuche verbraucht, bevor der Tunnel bereit war.
            if (attemptIndex < READINESS_ATTEMPTS - 1) {
                Log.i(TAG, "Warte ${READINESS_RETRY_DELAY_MS}ms vor dem naechsten Versuch")
                try {
                    Thread.sleep(READINESS_RETRY_DELAY_MS)
                } catch (e: InterruptedException) {
                    Thread.currentThread().interrupt()
                    return false
                }
            }
        }
        return false
    }

    /**
     * EIN sauberer, vollstaendiger HTTPS-Request (Verbindungsaufbau bis zur
     * Antwort, kein abruptes Abbrechen) - genau die Methode, die sich in der
     * Diagnose als zuverlaessig erwiesen hat, im Gegensatz zu rohen
     * Connect-und-sofort-Trennen-Proben.
     *
     * Das Zertifikat wird hier bewusst NICHT geprueft (permissiver TrustManager) -
     * das ist an dieser Stelle nur ein reiner Erreichbarkeits-/Bereitschafts-Check,
     * es werden keine echten Daten ausgetauscht oder vertraut. Die eigentliche
     * Sicherheitsentscheidung (Certificate Pinning) passiert weiterhin
     * ausschliesslich beim tatsaechlichen WebView-Laden in onReceivedSslError.
     */
    private fun isBackendReadyOnce(timeoutMs: Int): Boolean {
        var connection: javax.net.ssl.HttpsURLConnection? = null
        return try {
            connection = (java.net.URL(BACKEND_URL).openConnection() as javax.net.ssl.HttpsURLConnection).apply {
                sslSocketFactory = permissiveSslContext.socketFactory
                hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
                connectTimeout = timeoutMs
                readTimeout = timeoutMs
                requestMethod = "HEAD"
            }
            connection.connect()
            connection.responseCode // erzwingt den tatsaechlichen Request/Response-Zyklus
            true
        } catch (e: Exception) {
            Log.d(TAG, "Bereitschafts-Check fehlgeschlagen: ${e.javaClass.simpleName}: ${e.message}")
            false
        } finally {
            connection?.disconnect()
        }
    }

    private val permissiveSslContext: javax.net.ssl.SSLContext by lazy {
        val trustAllCerts = arrayOf<javax.net.ssl.TrustManager>(object : javax.net.ssl.X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        })
        javax.net.ssl.SSLContext.getInstance("TLS").apply {
            init(null, trustAllCerts, java.security.SecureRandom())
        }
    }

    /**
     * Niedrigschwellige On-Device-Diagnose ohne ADB/PC: zeigt Prozess-Status
     * (PID, laeuft/abgestuerzt, Exit-Code), Binary-Pfad/Ausfuehrbarkeit, die
     * letzten dumbpipe-Log-Zeilen und einen einmaligen Port-Erreichbarkeits-Check
     * als selektierbaren Text an, mit Kopieren- und Teilen-Knopf.
     */
    private fun showDiagnosticsDialog(includeLivePortCheck: Boolean = true) {
        // TunnelManager.diagnosticsText() macht ggf. einen einmaligen (synchronen)
        // Socket-Verbindungsversuch - kurz, aber sicherheitshalber nicht im
        // UI-Thread blockierend ausfuehren.
        tunnelExecutor.submit {
            val text = TunnelManager.diagnosticsText(this, includeLivePortCheck)
            runOnUiThread {
                // Activity kann inzwischen beendet/zerstoert worden sein (z.B. User
                // hat waehrend der ~37s Retry-Wartezeit die App verlassen oder das
                // Geraet gedreht) - ein AlertDialog auf einer toten Activity wirft
                // eine ungefangene WindowManager.BadTokenException und crasht die App.
                if (isFinishing || isDestroyed) return@runOnUiThread
                val textView = TextView(this).apply {
                    setText(text)
                    setTextIsSelectable(true)
                    textSize = 11f
                    typeface = android.graphics.Typeface.MONOSPACE
                    setPadding(32, 24, 32, 24)
                }
                val scrollView = ScrollView(this).apply { addView(textView) }
                AlertDialog.Builder(this)
                    .setTitle(R.string.diagnostics_title)
                    .setView(scrollView)
                    .setPositiveButton(R.string.diagnostics_copy) { _, _ ->
                        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        clipboard.setPrimaryClip(ClipData.newPlainText("Diagnose", text))
                        Toast.makeText(this, R.string.diagnostics_copied, Toast.LENGTH_SHORT).show()
                    }
                    .setNeutralButton(R.string.diagnostics_share) { _, _ ->
                        val shareIntent = Intent(Intent.ACTION_SEND).apply {
                            type = "text/plain"
                            putExtra(Intent.EXTRA_TEXT, text)
                        }
                        startActivity(Intent.createChooser(shareIntent, getString(R.string.diagnostics_share)))
                    }
                    .setNegativeButton(R.string.diagnostics_close, null)
                    .show()
            }
        }
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.main_menu, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == R.id.action_change_ticket) {
            connectionGeneration++ // laesst eine ggf. noch laufende Bereitschaftspruefung/Retry von selbst abbrechen
            if (tunnelRunningForThisSession) {
                tunnelRunningForThisSession = false
                tunnelExecutor.submit { TunnelManager.stop() }
            }
            ticketInput.setText("")
            showTicketEntry()
            return true
        }
        if (item.itemId == R.id.action_show_diagnostics) {
            showDiagnosticsDialog()
            return true
        }
        return super.onOptionsItemSelected(item)
    }
}
