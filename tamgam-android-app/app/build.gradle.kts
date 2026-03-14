plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.gms.google-services")
}

fun Project.stringProperty(name: String): String? =
    (findProperty(name) as? String)
        ?.trim()
        ?.takeIf { it.isNotEmpty() }

fun Project.intProperty(name: String, defaultValue: Int): Int {
    val parsed = stringProperty(name)?.toIntOrNull()
    return parsed ?: defaultValue
}

val debugWebBaseUrl =
    (project.stringProperty("TAMGAM_WEB_BASE_URL_DEBUG")
        ?: project.stringProperty("TAMGAM_WEB_BASE_URL")
        ?: "http://10.0.2.2:8000")
        .removeSuffix("/")

val releaseWebBaseUrl =
    (project.stringProperty("TAMGAM_WEB_BASE_URL_RELEASE")
        ?: "https://tamgam.in")
        .removeSuffix("/")

val appVersionCode = project.intProperty("TAMGAM_ANDROID_VERSION_CODE", 2)
val appVersionName = project.stringProperty("TAMGAM_ANDROID_VERSION_NAME") ?: "1.0.1"

val releaseStoreFile = project.stringProperty("TAMGAM_RELEASE_STORE_FILE").orEmpty()
val releaseStorePassword = project.stringProperty("TAMGAM_RELEASE_STORE_PASSWORD").orEmpty()
val releaseKeyAlias = project.stringProperty("TAMGAM_RELEASE_KEY_ALIAS").orEmpty()
val releaseKeyPassword = project.stringProperty("TAMGAM_RELEASE_KEY_PASSWORD").orEmpty()

val releaseSigningParts =
    listOf(releaseStoreFile, releaseStorePassword, releaseKeyAlias, releaseKeyPassword)
val hasAnyReleaseSigningPart = releaseSigningParts.any { it.isNotBlank() }
val hasAllReleaseSigningParts = releaseSigningParts.all { it.isNotBlank() }
val releaseTaskRequested = gradle.startParameter.taskNames.any { taskName ->
    taskName.contains("release", ignoreCase = true)
}

if (hasAnyReleaseSigningPart && !hasAllReleaseSigningParts) {
    throw GradleException(
        "Incomplete release signing config. Set TAMGAM_RELEASE_STORE_FILE, " +
            "TAMGAM_RELEASE_STORE_PASSWORD, TAMGAM_RELEASE_KEY_ALIAS, and TAMGAM_RELEASE_KEY_PASSWORD."
    )
}

if (releaseTaskRequested && !hasAllReleaseSigningParts) {
    throw GradleException(
        "Release task requested but signing config is missing. " +
            "Set TAMGAM_RELEASE_STORE_FILE, TAMGAM_RELEASE_STORE_PASSWORD, " +
            "TAMGAM_RELEASE_KEY_ALIAS, and TAMGAM_RELEASE_KEY_PASSWORD."
    )
}

android {
    namespace = "com.tamgam.app"
    compileSdk = 34

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "com.tamgam.app"
        minSdk = 24
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersionName

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    if (hasAllReleaseSigningParts) {
        val keystore = rootProject.file(releaseStoreFile)
        if (!keystore.exists()) {
            throw GradleException("Release keystore not found at: ${keystore.absolutePath}")
        }
        signingConfigs {
            create("release") {
                storeFile = keystore
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = true
                enableV2Signing = true
            }
        }
    }

    buildTypes {
        debug {
            buildConfigField("String", "WEB_APP_BASE_URL", "\"$debugWebBaseUrl\"")
        }

        release {
            buildConfigField("String", "WEB_APP_BASE_URL", "\"$releaseWebBaseUrl\"")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            if (hasAllReleaseSigningParts) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation(platform("com.google.firebase:firebase-bom:33.12.0"))
    implementation("com.google.firebase:firebase-auth")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
