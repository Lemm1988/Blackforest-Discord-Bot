# BlackforestBot Android Build

Baut das dumbpipe-Binary (Iroh-Tunnel-Tool) fuer Android (aarch64/arm64-v8a) per GitHub Actions,
da lokale Cross-Compile-Versuche (QEMU-Emulation) an einem Emulations-Bug in der ring-Crate
gescheitert sind. Ergebnis-Binary wird als Workflow-Artefakt bereitgestellt, danach in eine
schlanke Android-Kotlin-App gebundlet (separater Schritt, nicht Teil dieses Repos).
