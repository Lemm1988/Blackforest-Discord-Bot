package de.blackforest.bottunnel

import android.content.Context
import android.util.Log
import java.io.File
import java.net.InetSocketAddress
import java.net.Socket
import java.text.SimpleDateFormat
import java.util.Collections
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Startet/stoppt den dumbpipe-Tunnel-Prozess. Das Binary liegt als
 * jniLibs/arm64-v8a/libdumbpipe.so im APK (Standard-Android-Trick, um einem
 * beliebigen nativen Binary ueber den offiziellen Paketierungs-Mechanismus
 * Exec-Rechte im nativeLibraryDir zu verschaffen), ist aber ein normales
 * dumbpipe-Kommandozeilen-Binary, kein echtes JNI-.so.
 */
object TunnelManager {
    private const val TAG = "TunnelManager"
    private const val LOCAL_HOST = "127.0.0.1"
    private const val LOCAL_PORT = 8500
    private const val BACKEND_ADDR = "$LOCAL_HOST:$LOCAL_PORT"
    private const val MAX_LOG_LINES = 200

    private var process: Process? = null
    private var logThread: Thread? = null

    // Ring-artiger Log-Puffer der dumbpipe-Ausgabe (stdout+stderr zusammengefuehrt)
    // - vorher wurde dieser Stream nirgends gelesen, es gab also KEINERLEI
    // Diagnose-Moeglichkeit, was der Tunnel-Prozess tatsaechlich tut/meldet, wenn
    // die Verbindung nicht zustande kommt (z.B. NAT-Hole-Punching/Relay-Probleme).
    val recentLog: MutableList<String> = Collections.synchronizedList(mutableListOf())

    // Diagnose-Zustand, fuer die On-Device-Diagnose-Ansicht (MainActivity), damit
    // der User ohne ADB/PC konkrete Fehlerdaten kopieren/teilen kann.
    @Volatile private var lastBinaryPath: String? = null
    @Volatile private var lastBinaryExists: Boolean? = null
    @Volatile private var lastBinaryExecutable: Boolean? = null
    @Volatile private var lastStartException: String? = null
    @Volatile private var lastStartedAt: Long = 0L
    @Volatile private var lastTicketLength: Int = 0
    @Volatile private var lastTicketPreview: String = ""

    fun start(context: Context, ticket: String): Boolean {
        stop()
        recentLog.clear()
        lastStartException = null
        lastTicketLength = ticket.length
        lastTicketPreview = if (ticket.length > 24) {
            ticket.take(14) + "…" + ticket.takeLast(8)
        } else {
            ticket
        }
        return try {
            val binary = File(context.applicationInfo.nativeLibraryDir, "libdumbpipe.so")
            lastBinaryPath = binary.absolutePath
            lastBinaryExists = binary.exists()
            lastBinaryExecutable = binary.canExecute()
            if (!binary.exists()) {
                lastStartException = "Binary nicht gefunden unter ${binary.absolutePath}"
                Log.e(TAG, lastStartException!!)
                return false
            }
            if (!binary.canExecute()) {
                // Kein sofortiger Abbruch - manche Geraete melden canExecute()==false,
                // obwohl der Start trotzdem funktioniert. Wird aber in der Diagnose
                // sichtbar gemacht, falls DAS die Ursache ist (z.B. SELinux-Policy).
                Log.w(TAG, "Binary hat laut canExecute() KEINE Ausfuehrungsrechte - versuche trotzdem zu starten")
            }
            val pb = ProcessBuilder(
                binary.absolutePath, "connect-tcp", "--addr", BACKEND_ADDR, ticket
            )
            pb.redirectErrorStream(true)
            val p = pb.start()
            process = p
            lastStartedAt = System.currentTimeMillis()

            logThread = Thread {
                try {
                    p.inputStream.bufferedReader().forEachLine { line ->
                        Log.i(TAG, "dumbpipe: $line")
                        synchronized(recentLog) {
                            recentLog.add(line)
                            if (recentLog.size > MAX_LOG_LINES) recentLog.removeAt(0)
                        }
                    }
                } catch (e: Exception) {
                    // Erwartbar, wenn der Prozess beendet wird waehrend hier noch gelesen wird.
                    Log.d(TAG, "Log-Reader beendet: ${e.message}")
                }
            }.apply {
                isDaemon = true
                start()
            }

            Log.i(TAG, "Tunnel-Prozess gestartet, PID ${try { p.pid() } catch (e: Exception) { "?" }}")
            true
        } catch (e: Exception) {
            lastStartException = "${e.javaClass.simpleName}: ${e.message}"
            Log.e(TAG, "Tunnel-Start fehlgeschlagen", e)
            false
        }
    }

    fun stop() {
        process?.let {
            try {
                it.destroy()
                // WICHTIG: destroy() sendet nur das Terminierungssignal, kehrt aber
                // sofort zurueck, ohne dass der Prozess (und damit der von ihm
                // gehaltene lokale Port BACKEND_ADDR) tatsaechlich schon frei ist.
                // Ohne dieses Warten kann start() (z.B. beim "Ticket aendern")
                // sofort danach einen neuen Prozess auf demselben Port versuchen,
                // waehrend der alte ihn noch haelt - der neue Connect-Versuch
                // scheitert dann still an "Address already in use", die WebView
                // zeigt ERR_CONNECTION_CLOSED, obwohl das neue Ticket serverseitig
                // einwandfrei funktioniert (genau dieses Muster wurde beim
                // Bot-Connect-Dashboard-Port 5050 schon einmal beobachtet).
                if (!it.waitFor(3, TimeUnit.SECONDS)) {
                    Log.w(TAG, "Tunnel-Prozess reagierte nicht auf destroy(), erzwinge Beendigung.")
                    it.destroyForcibly()
                    it.waitFor(2, TimeUnit.SECONDS)
                }
                Unit
            } catch (e: Exception) {
                Log.e(TAG, "Fehler beim Beenden des Tunnel-Prozesses", e)
            }
        }
        process = null
        logThread = null
    }

    // ACHTUNG, bewusst NICHT vorhanden: eine DAUERHAFTE/wiederholte aktive
    // TCP-Connect-Probe gegen LOCAL_PORT waehrend des normalen Verbindungsaufbaus.
    // Ein frueherer Versuch, den Tunnel-Aufbau darueber zu ueberwachen (verbinden,
    // sofort wieder schliessen, alle 500ms), wurde zurueckgebaut: dumbpipe
    // behandelt jede eingehende lokale Verbindung als echten Weiterleitungs-Stream
    // ueber die P2P-Verbindung - ein abrupt geschlossener Socket entspricht exakt
    // dem bekannten Absturzmuster "Endpoint dropped without calling
    // Endpoint::close. Aborting ungracefully" (serverseitig mehrfach beobachtet)
    // und konnte dadurch den echten, gerade im Aufbau befindlichen Tunnel selbst
    // stoeren. Der Bereitschafts-Check laeuft deshalb wieder ueber die WebView
    // selbst (echter, legitimer Verbindungsversuch), siehe MainActivity.
    //
    // Der EINMALIGE Check unten (nur bei explizitem Diagnose-Abruf durch den User,
    // kein wiederholtes Polling) ist bewusst als bekannte, seltene Ausnahme davon
    // gestattet - ein einzelner Verbindungsversuch beim Klick auf "Diagnose
    // anzeigen" hat nicht dasselbe Wiederholungs-/Stoer-Risiko.
    fun isRunning(): Boolean = process?.isAlive == true

    private fun checkPortOnceForDiagnostics(): Boolean {
        return try {
            Socket().use { socket ->
                socket.connect(InetSocketAddress(LOCAL_HOST, LOCAL_PORT), 1000)
                true
            }
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Kopier-/teilbarer Diagnose-Text fuer den User ohne ADB/PC - zeigt alles, was
     * fuer die Fehlersuche bei einem Verbindungsproblem relevant ist.
     */
    fun diagnosticsText(context: Context): String {
        val timeFmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.GERMANY)
        val sb = StringBuilder()
        sb.appendLine("=== BlackforestBot Zugang - Diagnose ===")
        sb.appendLine("Zeitpunkt: ${timeFmt.format(Date())}")
        val versionName = try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName
        } catch (e: Exception) {
            "?"
        }
        sb.appendLine("App-Version: $versionName")
        sb.appendLine(
            "Geraet: ${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}, " +
                "Android ${android.os.Build.VERSION.RELEASE} (SDK ${android.os.Build.VERSION.SDK_INT})"
        )
        sb.appendLine()
        sb.appendLine("--- Ticket ---")
        sb.appendLine("Laenge: $lastTicketLength Zeichen")
        sb.appendLine("Ausschnitt: $lastTicketPreview")
        sb.appendLine()
        sb.appendLine("--- Binary ---")
        sb.appendLine("Pfad: $lastBinaryPath")
        sb.appendLine("Existiert: $lastBinaryExists")
        sb.appendLine("canExecute(): $lastBinaryExecutable")
        sb.appendLine()
        sb.appendLine("--- Prozess ---")
        val p = process
        if (p == null) {
            sb.appendLine("Kein Prozess-Handle vorhanden (nie gestartet oder bereits beendet+null gesetzt).")
        } else {
            val pid = try {
                p.pid().toString()
            } catch (e: Exception) {
                "unbekannt (${e.message})"
            }
            sb.appendLine("PID: $pid")
            sb.appendLine(
                "Gestartet um: " +
                    if (lastStartedAt > 0) timeFmt.format(Date(lastStartedAt)) else "?"
            )
            sb.appendLine("Laeuft noch: ${p.isAlive}")
            if (!p.isAlive) {
                val exitCode = try {
                    p.exitValue().toString()
                } catch (e: Exception) {
                    "unbekannt"
                }
                sb.appendLine("Exit-Code: $exitCode")
            }
        }
        if (lastStartException != null) {
            sb.appendLine("Fehler beim Start: $lastStartException")
        }
        sb.appendLine()
        sb.appendLine("--- Lokaler Port ($LOCAL_HOST:$LOCAL_PORT) ---")
        sb.appendLine("Einmaliger Check gerade eben: ${if (checkPortOnceForDiagnostics()) "ERREICHBAR" else "NICHT erreichbar"}")
        sb.appendLine()
        sb.appendLine("--- Letzte dumbpipe-Ausgabe (stdout+stderr, neueste zuletzt) ---")
        synchronized(recentLog) {
            if (recentLog.isEmpty()) {
                sb.appendLine("(keine Ausgabe empfangen)")
            } else {
                recentLog.takeLast(40).forEach { sb.appendLine(it) }
            }
        }
        return sb.toString()
    }
}
