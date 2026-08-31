package de.blackforest.bottunnel

import android.content.Context
import android.util.Log
import java.io.File
import java.net.InetSocketAddress
import java.net.Socket
import java.util.Collections
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

    fun start(context: Context, ticket: String): Boolean {
        stop()
        recentLog.clear()
        return try {
            val binary = File(context.applicationInfo.nativeLibraryDir, "libdumbpipe.so")
            if (!binary.exists()) {
                Log.e(TAG, "dumbpipe-Binary nicht gefunden unter ${binary.absolutePath}")
                return false
            }
            val pb = ProcessBuilder(
                binary.absolutePath, "connect-tcp", "--addr", BACKEND_ADDR, ticket
            )
            pb.redirectErrorStream(true)
            val p = pb.start()
            process = p

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

            Log.i(TAG, "Tunnel-Prozess gestartet")
            true
        } catch (e: Exception) {
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

    fun isRunning(): Boolean = process?.isAlive == true

    /**
     * Echter, funktionaler Bereitschafts-Check statt einer festen Wartezeit: baut
     * eine rohe TCP-Verbindung zum lokalen Port auf. dumbpipe bindet diesen Port
     * erst, sobald die eigentliche Iroh-Verbindung zum Server steht (empirisch
     * bestaetigt: Port-offen und erste funktionierende HTTP-Antwort liegen beim
     * Server-seitigen Test nur Millisekunden auseinander) - "Port erreichbar" ist
     * deshalb ein verlaesslicher Indikator fuer "Tunnel ist einsatzbereit", nicht
     * nur "Prozess laeuft".
     */
    fun isLocalPortReachable(): Boolean {
        return try {
            Socket().use { socket ->
                socket.connect(InetSocketAddress(LOCAL_HOST, LOCAL_PORT), 800)
                true
            }
        } catch (e: Exception) {
            false
        }
    }
}
