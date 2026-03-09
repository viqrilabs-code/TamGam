package com.tamgam.app

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.CountDownTimer
import android.util.Log
import android.view.View
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseException
import com.google.firebase.FirebaseNetworkException
import com.google.firebase.FirebaseTooManyRequestsException
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.FirebaseAuthException
import com.google.firebase.auth.FirebaseAuthInvalidCredentialsException
import com.google.firebase.auth.PhoneAuthCredential
import com.google.firebase.auth.PhoneAuthOptions
import com.google.firebase.auth.PhoneAuthProvider
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var loadingContainer: View
    private lateinit var errorContainer: View
    private lateinit var errorBodyText: TextView
    private lateinit var retryButton: MaterialButton

    private lateinit var phoneAuthContainer: View
    private lateinit var phoneNumberInput: TextInputEditText
    private lateinit var otpInput: TextInputEditText
    private lateinit var sendOtpButton: MaterialButton
    private lateinit var resendOtpButton: MaterialButton
    private lateinit var verifyOtpButton: MaterialButton
    private lateinit var authProgress: ProgressBar
    private lateinit var authStatusText: TextView

    private var firebaseAuth: FirebaseAuth? = null
    private var isFirebaseConfigured: Boolean = false
    private var isAuthBusy: Boolean = false
    private var verificationId: String? = null
    private var resendToken: PhoneAuthProvider.ForceResendingToken? = null
    private var resendCooldownSeconds: Int = 0
    private var resendCountdownTimer: CountDownTimer? = null
    private var lastRequestedPhone: String? = null
    private var pendingSession: WebSessionPayload? = null

    private val webBaseUri: Uri by lazy { Uri.parse(BuildConfig.WEB_APP_BASE_URL) }
    private val webBaseOrigin: String by lazy {
        "${webBaseUri.scheme ?: "http"}://${webBaseUri.host ?: ""}" +
            (if (webBaseUri.port > 0) ":${webBaseUri.port}" else "")
    }
    private val landingUrl: String by lazy {
        "${BuildConfig.WEB_APP_BASE_URL.removeSuffix("/")}/index.html?platform=android"
    }
    private val firebasePhoneLoginUrl: String by lazy {
        "${BuildConfig.WEB_APP_BASE_URL.removeSuffix("/")}/api/v1/auth/firebase-phone"
    }

    private val phoneAuthCallbacks = object : PhoneAuthProvider.OnVerificationStateChangedCallbacks() {
        override fun onVerificationCompleted(credential: PhoneAuthCredential) {
            setAuthStatus(getString(R.string.auth_status_auto_verification))
            otpInput.setText(credential.smsCode.orEmpty())
            signInWithPhoneCredential(credential)
        }

        override fun onVerificationFailed(exception: FirebaseException) {
            Log.e("PhoneAuth", "Phone verification failed", exception)
            setAuthBusy(false)
            setAuthStatus(friendlyPhoneAuthError(exception))
        }

        override fun onCodeSent(
            newVerificationId: String,
            token: PhoneAuthProvider.ForceResendingToken,
        ) {
            verificationId = newVerificationId
            resendToken = token
            setAuthBusy(false)
            val maskedPhone = maskPhone(lastRequestedPhone)
            if (maskedPhone.isBlank()) {
                setAuthStatus(getString(R.string.auth_status_otp_sent))
            } else {
                setAuthStatus(getString(R.string.auth_status_otp_sent_to, maskedPhone))
            }
            startResendCooldown()
        }

        override fun onCodeAutoRetrievalTimeOut(newVerificationId: String) {
            super.onCodeAutoRetrievalTimeOut(newVerificationId)
            verificationId = newVerificationId
            if (resendCooldownSeconds <= 0 && resendToken != null) {
                setAuthStatus(getString(R.string.auth_status_resend_ready))
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        loadingContainer = findViewById(R.id.loadingContainer)
        errorContainer = findViewById(R.id.errorContainer)
        errorBodyText = findViewById(R.id.errorBodyText)
        retryButton = findViewById(R.id.retryButton)

        phoneAuthContainer = findViewById(R.id.phoneAuthContainer)
        phoneNumberInput = findViewById(R.id.phoneNumberInput)
        otpInput = findViewById(R.id.otpInput)
        sendOtpButton = findViewById(R.id.sendOtpButton)
        resendOtpButton = findViewById(R.id.resendOtpButton)
        verifyOtpButton = findViewById(R.id.verifyOtpButton)
        authProgress = findViewById(R.id.authProgress)
        authStatusText = findViewById(R.id.authStatusText)

        isFirebaseConfigured = ensureFirebaseInitialized()
        if (!isFirebaseConfigured) {
            setAuthStatus(getString(R.string.auth_firebase_not_configured))
        } else {
            firebaseAuth = FirebaseAuth.getInstance()
            setAuthStatus(getString(R.string.auth_status_idle))
        }
        setAuthBusy(false)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadsImagesAutomatically = true
            useWideViewPort = false
            loadWithOverviewMode = false
            textZoom = 100
            cacheMode = WebSettings.LOAD_NO_CACHE
            builtInZoomControls = false
            displayZoomControls = false
        }
        webView.clearCache(true)
        webView.clearHistory()

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url
                val scheme = uri.scheme?.lowercase()
                return if (scheme == "http" || scheme == "https") {
                    val sameHost = uri.host?.equals(webBaseUri.host, ignoreCase = true) == true
                    val isMainFrame = request.isForMainFrame
                    val path = uri.path.orEmpty()
                    val lowerPath = path.lowercase()
                    val isLoginPath = lowerPath == "/login.html" || lowerPath.endsWith("/login.html")
                    if (sameHost && isMainFrame && isLoginPath) {
                        // Use the web login flow (email + verification code) directly.
                        false
                    } else {
                        val isHtml = lowerPath.endsWith(".html")
                        val isRoot = path.isBlank() || path == "/"
                        val hasPlatform = uri.getQueryParameter("platform") != null
                        if (sameHost && isMainFrame && (isHtml || isRoot) && !hasPlatform) {
                            val target = if (isRoot) {
                                "$webBaseOrigin/index.html?platform=android"
                            } else {
                                uri.buildUpon()
                                    .appendQueryParameter("platform", "android")
                                    .build()
                                    .toString()
                            }
                            view.loadUrl(target)
                            true
                        } else {
                            false
                        }
                    }
                } else {
                    openExternal(uri)
                    true
                }
            }

            override fun onPageFinished(view: WebView, url: String) {
                super.onPageFinished(view, url)
                val session = pendingSession
                if (session != null && isSameHostUrl(url)) {
                    pendingSession = null
                    injectSessionAndRedirect(session)
                    return
                }
                if (phoneAuthContainer.visibility != View.VISIBLE) {
                    showContent()
                }
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError,
            ) {
                super.onReceivedError(view, request, error)
                if (request.isForMainFrame) {
                    showError(error.description?.toString())
                }
            }

            override fun onReceivedHttpError(
                view: WebView,
                request: WebResourceRequest,
                errorResponse: WebResourceResponse,
            ) {
                super.onReceivedHttpError(view, request, errorResponse)
                if (request.isForMainFrame && errorResponse.statusCode >= 400) {
                    showError("HTTP ${errorResponse.statusCode}")
                }
            }
        }

        webView.overScrollMode = View.OVER_SCROLL_NEVER

        retryButton.setOnClickListener {
            loadLandingPage()
        }
        sendOtpButton.setOnClickListener {
            startPhoneVerification(forceResend = false)
        }
        resendOtpButton.setOnClickListener {
            startPhoneVerification(forceResend = true)
        }
        verifyOtpButton.setOnClickListener {
            verifyOtpCode()
        }

        loadLandingPage()
    }

    private fun ensureFirebaseInitialized(): Boolean {
        return try {
            FirebaseApp.getApps(this).isNotEmpty() || FirebaseApp.initializeApp(this) != null
        } catch (_: Exception) {
            false
        }
    }

    private fun loadLandingPage() {
        phoneAuthContainer.visibility = View.GONE
        errorContainer.visibility = View.GONE
        loadingContainer.visibility = View.VISIBLE
        webView.visibility = View.VISIBLE
        webView.loadUrl(landingUrl)
    }

    private fun showContent() {
        loadingContainer.visibility = View.GONE
        errorContainer.visibility = View.GONE
        webView.visibility = View.VISIBLE
    }

    private fun showError(detail: String? = null) {
        phoneAuthContainer.visibility = View.GONE
        loadingContainer.visibility = View.GONE
        webView.visibility = View.GONE
        errorContainer.visibility = View.VISIBLE

        val baseMessage = getString(R.string.landing_page_error_body, BuildConfig.WEB_APP_BASE_URL)
        errorBodyText.text = if (detail.isNullOrBlank()) {
            baseMessage
        } else {
            "$baseMessage\n\nDetails: $detail"
        }
    }

    private fun showPhoneVerification() {
        loadingContainer.visibility = View.GONE
        errorContainer.visibility = View.GONE
        webView.visibility = View.GONE
        phoneAuthContainer.visibility = View.VISIBLE
        if (!isFirebaseConfigured) {
            setAuthStatus(getString(R.string.auth_firebase_not_configured))
            sendOtpButton.isEnabled = false
            resendOtpButton.isEnabled = false
            verifyOtpButton.isEnabled = false
        } else {
            setAuthBusy(false)
            if (verificationId.isNullOrBlank()) {
                setAuthStatus(getString(R.string.auth_status_idle))
            }
        }
    }

    private fun setAuthBusy(isBusy: Boolean) {
        isAuthBusy = isBusy
        authProgress.visibility = if (isBusy) View.VISIBLE else View.GONE
        sendOtpButton.isEnabled = !isBusy && isFirebaseConfigured
        verifyOtpButton.isEnabled = !isBusy && isFirebaseConfigured && !verificationId.isNullOrBlank()
        phoneNumberInput.isEnabled = !isBusy
        otpInput.isEnabled = !isBusy
        updateResendButtonState()
    }

    private fun setAuthStatus(message: String) {
        authStatusText.text = message
    }

    private fun updateResendButtonState() {
        if (!this::resendOtpButton.isInitialized) return
        resendOtpButton.text =
            if (resendCooldownSeconds > 0) {
                getString(R.string.resend_otp_in_seconds, resendCooldownSeconds)
            } else {
                getString(R.string.resend_otp)
            }
        resendOtpButton.isEnabled =
            !isAuthBusy && isFirebaseConfigured && resendToken != null && resendCooldownSeconds <= 0
    }

    private fun startResendCooldown(seconds: Int = 30) {
        stopResendCooldown()
        resendCooldownSeconds = seconds
        updateResendButtonState()
        resendCountdownTimer =
            object : CountDownTimer(seconds * 1000L, 1000L) {
                override fun onTick(millisUntilFinished: Long) {
                    resendCooldownSeconds = (millisUntilFinished / 1000L).toInt().coerceAtLeast(1)
                    updateResendButtonState()
                }

                override fun onFinish() {
                    resendCooldownSeconds = 0
                    updateResendButtonState()
                    if (!isAuthBusy && resendToken != null) {
                        setAuthStatus(getString(R.string.auth_status_resend_ready))
                    }
                }
            }.start()
    }

    private fun stopResendCooldown() {
        resendCountdownTimer?.cancel()
        resendCountdownTimer = null
        resendCooldownSeconds = 0
        updateResendButtonState()
    }

    private fun startPhoneVerification(forceResend: Boolean) {
        if (!isFirebaseConfigured) {
            setAuthStatus(getString(R.string.auth_firebase_not_configured))
            return
        }
        val auth = firebaseAuth ?: run {
            setAuthStatus(getString(R.string.auth_firebase_not_configured))
            return
        }
        val phone = normalizePhoneNumber(phoneNumberInput.text?.toString().orEmpty())
        if (phone == null) {
            setAuthStatus(getString(R.string.auth_invalid_phone))
            return
        }
        if (forceResend && resendToken == null) {
            setAuthStatus(getString(R.string.auth_resend_unavailable))
            return
        }
        lastRequestedPhone = phone
        if (!forceResend) {
            verificationId = null
            otpInput.setText("")
        }
        setAuthBusy(true)
        setAuthStatus(getString(R.string.auth_status_sending_otp))

        val optionsBuilder = PhoneAuthOptions.newBuilder(auth)
            .setPhoneNumber(phone)
            .setTimeout(60L, TimeUnit.SECONDS)
            .setActivity(this)
            .setCallbacks(phoneAuthCallbacks)
        if (forceResend) {
            optionsBuilder.setForceResendingToken(requireNotNull(resendToken))
        }
        PhoneAuthProvider.verifyPhoneNumber(optionsBuilder.build())
    }

    private fun verifyOtpCode() {
        val code = otpInput.text?.toString()?.trim().orEmpty()
        val currentVerificationId = verificationId
        if (currentVerificationId.isNullOrBlank() || code.length < 6) {
            setAuthStatus(getString(R.string.auth_invalid_otp))
            return
        }
        val credential = PhoneAuthProvider.getCredential(currentVerificationId, code)
        signInWithPhoneCredential(credential)
    }

    private fun signInWithPhoneCredential(credential: PhoneAuthCredential) {
        val auth = firebaseAuth ?: run {
            setAuthStatus(getString(R.string.auth_firebase_not_configured))
            return
        }
        setAuthBusy(true)
        setAuthStatus(getString(R.string.auth_status_signing_in))

        auth.signInWithCredential(credential).addOnCompleteListener(this) { signInTask ->
            if (!signInTask.isSuccessful) {
                setAuthBusy(false)
                setAuthStatus(signInTask.exception?.localizedMessage ?: getString(R.string.auth_otp_verify_failed))
                return@addOnCompleteListener
            }

            val firebaseUser = signInTask.result?.user
            if (firebaseUser == null) {
                setAuthBusy(false)
                setAuthStatus(getString(R.string.auth_otp_verify_failed))
                return@addOnCompleteListener
            }

            firebaseUser.getIdToken(true).addOnCompleteListener(this) { tokenTask ->
                val idToken = tokenTask.result?.token
                if (!tokenTask.isSuccessful || idToken.isNullOrBlank()) {
                    setAuthBusy(false)
                    setAuthStatus(tokenTask.exception?.localizedMessage ?: getString(R.string.auth_otp_verify_failed))
                    return@addOnCompleteListener
                }
                exchangeFirebaseToken(idToken)
            }
        }
    }

    private fun exchangeFirebaseToken(idToken: String) {
        setAuthBusy(true)
        thread {
            val result = requestBackendSession(idToken)
            runOnUiThread {
                if (result.session != null) {
                    setAuthBusy(false)
                    setAuthStatus(getString(R.string.auth_status_login_success))
                    completeNativeLogin(result.session)
                } else {
                    setAuthBusy(false)
                    setAuthStatus(result.error ?: getString(R.string.auth_token_exchange_failed))
                }
            }
        }
    }

    private fun requestBackendSession(idToken: String): BackendSessionResult {
        val payload = JSONObject().put("id_token", idToken).toString()
        var connection: HttpURLConnection? = null
        return try {
            connection = (URL(firebasePhoneLoginUrl).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 15000
                readTimeout = 20000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("Accept", "application/json")
            }
            OutputStreamWriter(connection.outputStream, StandardCharsets.UTF_8).use { writer ->
                writer.write(payload)
                writer.flush()
            }
            val statusCode = connection.responseCode
            val stream = if (statusCode in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.use {
                BufferedReader(InputStreamReader(it, StandardCharsets.UTF_8)).readText()
            }.orEmpty()
            if (statusCode in 200..299) {
                val json = JSONObject(body)
                val accessToken = json.optString("access_token")
                val refreshToken = json.optString("refresh_token")
                if (accessToken.isBlank() || refreshToken.isBlank()) {
                    return BackendSessionResult(error = getString(R.string.auth_token_exchange_failed))
                }
                BackendSessionResult(
                    session = WebSessionPayload(
                        accessToken = accessToken,
                        refreshToken = refreshToken,
                        userId = json.optString("user_id"),
                        role = json.optString("role"),
                        fullName = json.optString("full_name"),
                        isSubscribed = json.optBoolean("is_subscribed", false),
                        isVerifiedTeacher = json.optBoolean("is_verified_teacher", false),
                    ),
                )
            } else {
                BackendSessionResult(error = parseBackendError(body, statusCode))
            }
        } catch (exception: Exception) {
            BackendSessionResult(error = exception.localizedMessage ?: getString(R.string.auth_token_exchange_failed))
        } finally {
            connection?.disconnect()
        }
    }

    private fun parseBackendError(body: String, statusCode: Int): String {
        return try {
            val json = JSONObject(body)
            val detail = json.opt("detail")
            when (detail) {
                is String -> detail
                is JSONObject -> detail.optString("message", getString(R.string.auth_token_exchange_failed))
                else -> json.optString(
                    "message",
                    getString(R.string.auth_backend_http_error, statusCode),
                )
            }
        } catch (_: Exception) {
            getString(R.string.auth_backend_http_error, statusCode)
        }
    }

    private fun completeNativeLogin(session: WebSessionPayload) {
        verificationId = null
        resendToken = null
        lastRequestedPhone = null
        stopResendCooldown()
        otpInput.setText("")
        pendingSession = session
        loadLandingPage()
    }

    private fun injectSessionAndRedirect(session: WebSessionPayload) {
        val userJson = JSONObject()
            .put("user_id", session.userId)
            .put("role", session.role)
            .put("full_name", session.fullName)
            .put("is_subscribed", session.isSubscribed)
            .put("is_verified_teacher", session.isVerifiedTeacher)
            .toString()
        val targetPath = when (session.role.lowercase()) {
            "teacher" -> if (session.isSubscribed) "/teacher-dashboard.html" else "/plans.html?onboarding=1"
            "admin" -> "/admin.html"
            else -> "/dashboard.html"
        }
        val script = """
            (function() {
              try {
                localStorage.setItem('tg_access', ${JSONObject.quote(session.accessToken)});
                localStorage.setItem('tg_refresh', ${JSONObject.quote(session.refreshToken)});
                localStorage.setItem('tg_user', ${JSONObject.quote(userJson)});
              } catch (_e) {}
              window.location.replace(${JSONObject.quote(targetPath)});
            })();
        """.trimIndent()
        webView.evaluateJavascript(script, null)
    }

    private fun isSameHostUrl(rawUrl: String?): Boolean {
        if (rawUrl.isNullOrBlank()) return false
        val uri = Uri.parse(rawUrl)
        return uri.host?.equals(webBaseUri.host, ignoreCase = true) == true
    }

    private fun normalizePhoneNumber(rawPhone: String): String? {
        val trimmed = rawPhone.trim()
        if (trimmed.isBlank()) return null
        val digits = trimmed.filter { it.isDigit() }
        if (digits.length < 8 || digits.length > 15) return null
        return "+$digits"
    }

    private fun maskPhone(phone: String?): String {
        if (phone.isNullOrBlank()) return ""
        val digits = phone.filter { it.isDigit() }
        if (digits.length < 6) return phone
        val visiblePrefix = digits.take(2)
        val visibleSuffix = digits.takeLast(2)
        val hidden = "*".repeat((digits.length - 4).coerceAtLeast(2))
        return "+$visiblePrefix$hidden$visibleSuffix"
    }

    private fun friendlyPhoneAuthError(exception: FirebaseException): String {
        return when (exception) {
            is FirebaseTooManyRequestsException -> withAuthDebug(
                baseMessage = getString(R.string.auth_error_too_many_requests),
                fallbackCode = "FIREBASE_TOO_MANY_REQUESTS",
                exception = exception,
            )
            is FirebaseNetworkException -> withAuthDebug(
                baseMessage = getString(R.string.auth_error_network),
                fallbackCode = "FIREBASE_NETWORK",
                exception = exception,
            )
            is FirebaseAuthInvalidCredentialsException -> withAuthDebug(
                baseMessage = getString(R.string.auth_error_invalid_phone),
                errorCode = exception.errorCode,
                exception = exception,
            )
            is FirebaseAuthException -> {
                when (exception.errorCode) {
                    "ERROR_QUOTA_EXCEEDED" -> withAuthDebug(
                        baseMessage = getString(R.string.auth_error_quota_exceeded),
                        errorCode = exception.errorCode,
                        exception = exception,
                    )
                    "ERROR_APP_NOT_AUTHORIZED", "ERROR_INVALID_APP_CREDENTIAL" -> {
                        withAuthDebug(
                            baseMessage = getString(R.string.auth_error_app_not_authorized),
                            errorCode = exception.errorCode,
                            exception = exception,
                        )
                    }
                    "ERROR_INVALID_PHONE_NUMBER" -> withAuthDebug(
                        baseMessage = getString(R.string.auth_error_invalid_phone),
                        errorCode = exception.errorCode,
                        exception = exception,
                    )
                    else -> withAuthDebug(
                        baseMessage = getString(R.string.auth_error_generic_with_code, exception.errorCode),
                        errorCode = exception.errorCode,
                        exception = exception,
                    )
                }
            }
            else -> withAuthDebug(
                baseMessage = exception.localizedMessage ?: getString(R.string.auth_otp_send_failed),
                fallbackCode = "FIREBASE_GENERIC",
                exception = exception,
            )
        }
    }

    private fun withAuthDebug(
        baseMessage: String,
        exception: FirebaseException,
        errorCode: String? = null,
        fallbackCode: String? = null,
    ): String {
        val code = (errorCode ?: fallbackCode ?: exception.javaClass.simpleName).ifBlank { "UNKNOWN" }
        val detail = exception.localizedMessage.orEmpty().replace("\n", " ").trim()
        val clippedDetail = if (detail.length > 80) detail.take(80) + "..." else detail
        return if (clippedDetail.isBlank()) {
            "$baseMessage [code=$code]"
        } else {
            "$baseMessage [code=$code, detail=$clippedDetail]"
        }
    }

    private fun openExternal(uri: Uri) {
        val intent = Intent(Intent.ACTION_VIEW, uri)
        try {
            startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            // Ignore if no app can handle the URI.
        }
    }

    override fun onBackPressed() {
        if (phoneAuthContainer.visibility == View.VISIBLE) {
            super.onBackPressed()
            return
        }
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        stopResendCooldown()
        webView.stopLoading()
        webView.webViewClient = WebViewClient()
        webView.destroy()
        super.onDestroy()
    }

    private data class WebSessionPayload(
        val accessToken: String,
        val refreshToken: String,
        val userId: String,
        val role: String,
        val fullName: String,
        val isSubscribed: Boolean,
        val isVerifiedTeacher: Boolean,
    )

    private data class BackendSessionResult(
        val session: WebSessionPayload? = null,
        val error: String? = null,
    )
}
