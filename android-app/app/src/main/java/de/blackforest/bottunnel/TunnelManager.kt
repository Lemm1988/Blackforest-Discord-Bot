package de.blackforest.bottunnel

import android.content.Context
import android.util.Log
import java.io.File

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
            } catch (e: Exception) {
                Log.e(TAG, "Fehler beim Beenden des Tunnel-Prozesses", e)
            }
        }
        process = null
    }

    fun isRunning(): Boolean = process?.isAlive == true
}
