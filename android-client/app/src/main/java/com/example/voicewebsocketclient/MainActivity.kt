package com.example.voicewebsocketclient

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.example.voicewebsocketclient.databinding.ActivityMainBinding
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID

class MainActivity : AppCompatActivity() {

    private companion object {
        const val FIXED_SERVER_URL = "wss://fancy-entrench-stoke.ngrok-free.dev/ws/voice"
    }

    private lateinit var binding: ActivityMainBinding

    private val okHttpClient = OkHttpClient()
    private var webSocket: WebSocket? = null
    private var audioRecord: AudioRecord? = null
    private var recordingThread: Thread? = null
    private var isRecording = false
    private var responseBuffer = ByteArrayOutputStream()
    private var responseFormat: String? = null
    private var startedAtMs: Long = 0L
    private var receivedResponseType: String? = null
    private var currentRequestId: String = ""
    private var mediaPlayer: MediaPlayer? = null

    private val sampleRate = 16000
    private val audioFormat = AudioFormat.ENCODING_PCM_16BIT
    private val channelMask = AudioFormat.CHANNEL_IN_MONO

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        currentRequestId = "android-${Build.MANUFACTURER}-${Build.MODEL}-${UUID.randomUUID().toString().take(8)}"
        binding.serverUrl.setText(FIXED_SERVER_URL)
        binding.serverUrl.isEnabled = false
        binding.serverUrl.isFocusable = false

        binding.connectButton.setOnClickListener { connect() }
        binding.recordButton.setOnClickListener { toggleRecording() }
        binding.stopButton.setOnClickListener { stopRecording() }
        binding.recordButton.text = "Start Audio"
        binding.stopButton.text = "Stop Recording"

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.RECORD_AUDIO),
                1001
            )
        }

        connect()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1001) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                setStatus("Microphone permission granted")
            } else {
                setStatus("Microphone permission needed for recording")
            }
        }
    }

    private fun connect() {
        val url = binding.serverUrl.text.toString().trim()
        if (url.isEmpty()) {
            setStatus("Please enter a valid WebSocket URL")
            return
        }

        val normalizedUrl = if (url.startsWith("ws://") || url.startsWith("wss://")) {
            url
        } else {
            "ws://$url"
        }

        val request = Request.Builder().url(normalizedUrl).build()
        webSocket = okHttpClient.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: okhttp3.Response) {
                runOnUiThread {
                    setStatus("Connected to server")
                    webSocket.send("{\"type\":\"start\",\"imei\":\"$currentRequestId\"}")
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runOnUiThread {
                    handleTextMessage(text)
                }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                runOnUiThread {
                    handleBinaryMessage(bytes)
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                runOnUiThread { setStatus("Socket closing: $reason") }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: okhttp3.Response?) {
                runOnUiThread {
                    setStatus("Connection failed: ${t.message}")
                }
            }
        })
    }

    private fun handleTextMessage(message: String) {
        if (message.trimStart().startsWith("{")) {
            val payload = JSONObject(message)
            val type = payload.optString("type")
            receivedResponseType = type

            when (type) {
                "ready" -> setStatus("Server ready")
                "error" -> {
                    val msg = payload.optString("message", "Unknown error")
                    setStatus("Server error: $msg")
                }
                "text_response" -> {
                    val text = payload.optString("text", "")
                    binding.responseText.text = text
                    setStatus("Text response received")
                    updateLatency()
                }
                "response_start" -> {
                    responseFormat = payload.optString("format", "mp3")
                    responseBuffer.reset()
                    val size = payload.optLong("size", 0L)
                    setStatus("Receiving ${responseFormat ?: "audio"} response (${size} bytes)")
                }
                "response_end" -> {
                    setStatus("Response complete. ${responseBuffer.size()} bytes captured")
                    playReceivedAudio()
                }
                else -> setStatus("Received: $message")
            }
        } else {
            setStatus("Received non-JSON text: $message")
        }
    }

    private fun handleBinaryMessage(bytes: ByteString) {
        responseBuffer.write(bytes.toByteArray())
        setStatus("Received binary audio chunk (${responseBuffer.size()} bytes)")
        Log.d("VoiceClient", "Binary chunk received, total=${responseBuffer.size()} bytes")
    }

    private fun toggleRecording() {
        if (isRecording) {
            stopRecording()
        } else {
            startRecording()
        }
    }

    private fun startRecording() {
        if (webSocket == null) {
            connect()
        }

        if (webSocket == null) {
            setStatus("Connecting to the server...")
            return
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            setStatus("Microphone permission required")
            return
        }

        if (isRecording) {
            return
        }

        startedAtMs = System.currentTimeMillis()
        responseBuffer.reset()
        responseFormat = null
        receivedResponseType = null
        binding.responseText.text = "Waiting for response..."

        val minBufferSize = AudioRecord.getMinBufferSize(sampleRate, channelMask, audioFormat)
        if (minBufferSize <= 0) {
            setStatus("Audio device not available")
            return
        }

        val bufferSize = minBufferSize * 4
        val pcmBuffer = ByteArrayOutputStream()
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            channelMask,
            audioFormat,
            bufferSize
        )

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            setStatus("Microphone initialization failed")
            return
        }

        audioRecord = recorder
        isRecording = true
        binding.recordButton.text = "Recording..."
        recorder.startRecording()

        recordingThread = Thread {
            val tempBuffer = ByteArray(bufferSize)
            while (isRecording) {
                val bytesRead = recorder.read(tempBuffer, 0, tempBuffer.size)
                if (bytesRead > 0) {
                    pcmBuffer.write(tempBuffer, 0, bytesRead)
                }
            }

            val wavBytes = pcmToWav(pcmBuffer.toByteArray(), sampleRate, 1, 16)
            runOnUiThread {
                setStatus("Sending audio to server...")
                webSocket?.send(ByteString.of(*wavBytes))
                webSocket?.send("{\"type\":\"audio_end\"}")
            }
        }

        recordingThread?.start()
        setStatus("Recording started")
    }

    private fun stopRecording() {
        if (!isRecording) {
            return
        }

        isRecording = false
        binding.recordButton.text = "Start Audio"
        recordingThread?.join(1500)
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
        recordingThread = null
        setStatus("Recording stopped and sent to server")
    }

    private fun updateLatency() {
        val diff = System.currentTimeMillis() - startedAtMs
        binding.latencyText.text = "Latency: $diff ms | response: ${receivedResponseType ?: "unknown"}"
    }

    private fun playReceivedAudio() {
        val bytes = responseBuffer.toByteArray()
        if (bytes.isEmpty()) {
            setStatus("No response audio received")
            return
        }

        val extension = if ((responseFormat ?: "mp3").lowercase() == "wav") "wav" else "mp3"
        val targetFile = File(cacheDir, "response.$extension")
        targetFile.writeBytes(bytes)

        if (mediaPlayer != null) {
            mediaPlayer?.stop()
            mediaPlayer?.release()
        }

        mediaPlayer = MediaPlayer().apply {
            setDataSource(targetFile.absolutePath)
            prepare()
            start()
            setOnCompletionListener {
                setStatus("Playback complete")
                updateLatency()
            }
        }

        binding.responseText.text = "Audio response ready"
        setStatus("Playing response audio")
        updateLatency()
    }

    private fun setStatus(message: String) {
        binding.statusText.text = message
    }

    private fun pcmToWav(pcm: ByteArray, sampleRate: Int, channels: Int, bitsPerSample: Int): ByteArray {
        val byteRate = sampleRate * channels * bitsPerSample / 8
        val totalAudioLen = pcm.size.toLong()
        val totalDataLen = totalAudioLen + 36
        val header = ByteArray(44)

        header[0] = 'R'.code.toByte()
        header[1] = 'I'.code.toByte()
        header[2] = 'F'.code.toByte()
        header[3] = 'F'.code.toByte()

        writeInt(header, 4, totalDataLen.toInt())
        header[8] = 'W'.code.toByte()
        header[9] = 'A'.code.toByte()
        header[10] = 'V'.code.toByte()
        header[11] = 'E'.code.toByte()
        header[12] = 'f'.code.toByte()
        header[13] = 'm'.code.toByte()
        header[14] = 't'.code.toByte()
        header[15] = ' '.code.toByte()
        writeInt(header, 16, 16)
        writeShort(header, 20, 1)
        writeShort(header, 22, channels.toShort())
        writeInt(header, 24, sampleRate)
        writeInt(header, 28, byteRate)
        writeShort(header, 32, (channels * bitsPerSample / 8).toShort())
        writeShort(header, 34, bitsPerSample.toShort())
        header[36] = 'd'.code.toByte()
        header[37] = 'a'.code.toByte()
        header[38] = 't'.code.toByte()
        header[39] = 'a'.code.toByte()
        writeInt(header, 40, totalAudioLen.toInt())

        val out = ByteArrayOutputStream()
        out.write(header)
        out.write(pcm)
        return out.toByteArray()
    }

    private fun writeInt(array: ByteArray, offset: Int, value: Int) {
        array[offset] = (value and 0xFF).toByte()
        array[offset + 1] = ((value shr 8) and 0xFF).toByte()
        array[offset + 2] = ((value shr 16) and 0xFF).toByte()
        array[offset + 3] = ((value shr 24) and 0xFF).toByte()
    }

    private fun writeShort(array: ByteArray, offset: Int, value: Short) {
        array[offset] = (value.toInt() and 0xFF).toByte()
        array[offset + 1] = ((value.toInt() shr 8) and 0xFF).toByte()
    }

    override fun onDestroy() {
        super.onDestroy()
        isRecording = false
        mediaPlayer?.release()
        audioRecord?.release()
    }
}
