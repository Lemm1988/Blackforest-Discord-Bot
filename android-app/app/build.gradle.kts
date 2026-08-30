plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "de.blackforest.bottunnel"
    compileSdk = 34

    defaultConfig {
        applicationId = "de.blackforest.bottunnel"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    // Wichtig: das gebundelte "libdumbpipe.so" ist in Wahrheit ein normales
    // Kommandozeilen-Binary, kein echtes JNI-.so. Ohne "legacy packaging" wuerde
    // Android es ab bestimmten AGP-Voreinstellungen komprimiert in der APK lassen
    // und nicht als eigenstaendige, ausfuehrbare Datei ins nativeLibraryDir
    // extrahieren - dann waere es zur Laufzeit nicht als Prozess startbar.
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.1")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
}
