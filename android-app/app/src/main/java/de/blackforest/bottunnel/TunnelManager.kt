package de.blackforest.bottunnel

import android.content.Context
import android.util.Log
import java.io.File
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
    private const val BACKEND_ADDR = "127.0.0.1:8500"

    private var process: Process? = null

    fun start(context: Context, ticket: String): Boolean {
        stop()
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
            process = pb.start()
            Log.i(TAG, "Tunnel-Prozess gestartet (PID unbekannt, Process-Handle vorhanden)")
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
    }

    fun isRunning(): Boolean = process?.isAlive == true
}
