package app.romanvoice.ime;

import android.util.Base64;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.concurrent.atomic.AtomicBoolean;

import javax.net.ssl.SSLSocketFactory;

final class RomanVoiceStreamClient implements Closeable {
    private static final int CONNECT_TIMEOUT_MS = 10000;
    private static final long PING_INTERVAL_MS = 5000;
    private static final long PONG_TIMEOUT_MS = 12000;
    private static final long THREAD_JOIN_TIMEOUT_MS = 750;
    private static final int MAX_HTTP_HEADER_BYTES = 16384;
    private static final long MAX_INBOUND_FRAME_BYTES = 4L * 1024L * 1024L;
    private static final String WEBSOCKET_ACCEPT_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

    interface Listener {
        void onReady();

        void onStarted();

        void onPartial(String text);

        void onFinal(String text);

        void onError(String message);
    }

    private final URI uri;
    private final String token;
    private final String clientLabel;
    private final Listener listener;
    private final SecureRandom secureRandom = new SecureRandom();

    private Socket socket;
    private InputStream input;
    private OutputStream output;
    private Thread readerThread;
    private Thread keepAliveThread;
    private volatile boolean closed;
    private volatile boolean intentionalClose;
    private volatile long outstandingPingAtMs;
    private final AtomicBoolean disconnectNotified = new AtomicBoolean();

    RomanVoiceStreamClient(
            String streamUrl,
            String token,
            String clientLabel,
            Listener listener
    ) {
        this.uri = URI.create(streamUrl);
        this.token = token == null ? "" : token;
        this.clientLabel = clientLabel == null ? "android" : clientLabel.trim();
        this.listener = listener;
    }

    void connect() throws IOException {
        String scheme = uri.getScheme();
        if (!"ws".equalsIgnoreCase(scheme) && !"wss".equalsIgnoreCase(scheme)) {
            throw new IOException("Streaming URL must start with ws:// or wss://");
        }

        int port = uri.getPort();
        if (port < 0) {
            port = "wss".equalsIgnoreCase(scheme) ? 443 : 80;
        }

        socket = "wss".equalsIgnoreCase(scheme)
                ? SSLSocketFactory.getDefault().createSocket()
                : new Socket();
        socket.connect(new InetSocketAddress(uri.getHost(), port), CONNECT_TIMEOUT_MS);
        socket.setSoTimeout(CONNECT_TIMEOUT_MS);
        if ("wss".equalsIgnoreCase(scheme) && socket instanceof javax.net.ssl.SSLSocket) {
            ((javax.net.ssl.SSLSocket) socket).startHandshake();
        }
        input = socket.getInputStream();
        output = socket.getOutputStream();

        String key = randomWebSocketKey();
        String path = uri.getRawPath();
        if (path == null || path.isEmpty()) {
            path = "/";
        }
        if (uri.getRawQuery() != null && !uri.getRawQuery().isEmpty()) {
            path += "?" + uri.getRawQuery();
        }

        StringBuilder request = new StringBuilder();
        request.append("GET ").append(path).append(" HTTP/1.1\r\n");
        request.append("Host: ").append(uri.getHost()).append(":").append(port).append("\r\n");
        request.append("Upgrade: websocket\r\n");
        request.append("Connection: Upgrade\r\n");
        request.append("Sec-WebSocket-Key: ").append(key).append("\r\n");
        request.append("Sec-WebSocket-Version: 13\r\n");
        request.append("X-RomanVoice-Client: ").append(safeClientLabel()).append("\r\n");
        if (!token.isEmpty()) {
            request.append("Authorization: Bearer ").append(token).append("\r\n");
        }
        request.append("\r\n");
        output.write(request.toString().getBytes(StandardCharsets.US_ASCII));
        output.flush();

        String response = readHttpHeaders();
        if (!response.startsWith("HTTP/1.1 101") && !response.startsWith("HTTP/1.0 101")) {
            String firstLine = response.split("\r\n", 2)[0];
            throw new IOException("RomanVoice refused stream: " + firstLine);
        }
        String expectedAccept = expectedWebSocketAccept(key);
        String actualAccept = responseHeaderValue(response, "Sec-WebSocket-Accept");
        if (!expectedAccept.equals(actualAccept)) {
            throw new IOException("RomanVoice returned an invalid WebSocket handshake");
        }
        socket.setSoTimeout(0);

        readerThread = new Thread(this::readLoop, "RomanVoiceStreamReader");
        readerThread.start();
        keepAliveThread = new Thread(this::keepAliveLoop, "RomanVoiceStreamKeepAlive");
        keepAliveThread.start();
    }

    void sendStart(int sampleRate, String polish) throws IOException {
        try {
            JSONObject payload = new JSONObject();
            payload.put("type", "start");
            payload.put("sample_rate", sampleRate);
            payload.put("channel_count", 1);
            payload.put("sample_format", "pcm_s16le");
            payload.put("polish", polish == null || polish.isEmpty() ? "settings" : polish);
            sendText(payload.toString());
        } catch (JSONException exception) {
            throw new IOException("Failed to build start message", exception);
        }
    }

    void sendAudio(byte[] audioBytes, int length) throws IOException {
        if (length <= 0) {
            return;
        }
        byte[] payload = new byte[length];
        System.arraycopy(audioBytes, 0, payload, 0, length);
        sendFrame(0x2, payload);
    }

    void sendStop() throws IOException {
        try {
            JSONObject payload = new JSONObject();
            payload.put("type", "stop");
            sendText(payload.toString());
        } catch (JSONException exception) {
            throw new IOException("Failed to build stop message", exception);
        }
    }

    @Override
    public void close() {
        intentionalClose = true;
        try {
            if (!closed) {
                sendFrame(0x8, new byte[]{0x03, (byte) 0xE8});
            }
        } catch (Exception ignored) {
        }
        closed = true;
        closeSocket();
        interruptAndJoin(keepAliveThread);
        interruptAndJoin(readerThread);
    }

    private void closeSocket() {
        try {
            if (socket != null) {
                socket.close();
            }
        } catch (IOException ignored) {
        }
    }

    private void sendText(String text) throws IOException {
        sendFrame(0x1, text.getBytes(StandardCharsets.UTF_8));
    }

    private synchronized void sendFrame(int opcode, byte[] payload) throws IOException {
        if (closed && opcode != 0x8) {
            throw new IOException("RomanVoice stream is closed");
        }

        int length = payload.length;
        ByteArrayOutputStream frame = new ByteArrayOutputStream();
        frame.write(0x80 | opcode);
        if (length < 126) {
            frame.write(0x80 | length);
        } else if (length <= 0xFFFF) {
            frame.write(0x80 | 126);
            frame.write(ByteBuffer.allocate(2).putShort((short) length).array());
        } else {
            frame.write(0x80 | 127);
            frame.write(ByteBuffer.allocate(8).putLong(length).array());
        }

        byte[] mask = new byte[4];
        secureRandom.nextBytes(mask);
        frame.write(mask);
        for (int index = 0; index < length; index++) {
            frame.write(payload[index] ^ mask[index % 4]);
        }
        output.write(frame.toByteArray());
        output.flush();
    }

    private void readLoop() {
        try {
            while (!closed) {
                Frame frame = readFrame();
                if (frame.opcode == 0x8) {
                    notifyUnexpectedDisconnect("RomanVoice stream closed unexpectedly");
                    return;
                }
                if (frame.opcode == 0x9) {
                    sendFrame(0xA, frame.payload);
                    continue;
                }
                if (frame.opcode == 0xA) {
                    outstandingPingAtMs = 0;
                    continue;
                }
                if (frame.opcode == 0x1) {
                    handleText(new String(frame.payload, StandardCharsets.UTF_8));
                }
            }
        } catch (Exception exception) {
            notifyUnexpectedDisconnect(exception.getMessage());
        } finally {
            closed = true;
            closeSocket();
        }
    }

    private void keepAliveLoop() {
        try {
            while (!closed) {
                Thread.sleep(PING_INTERVAL_MS);
                if (closed) {
                    return;
                }
                long now = System.currentTimeMillis();
                long sentAt = outstandingPingAtMs;
                if (sentAt > 0 && now - sentAt > PONG_TIMEOUT_MS) {
                    notifyUnexpectedDisconnect("RomanVoice stream ping timed out");
                    return;
                }
                outstandingPingAtMs = now;
                sendFrame(0x9, new byte[]{});
            }
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        } catch (Exception exception) {
            notifyUnexpectedDisconnect(exception.getMessage());
        } finally {
            if (!intentionalClose && disconnectNotified.get()) {
                closed = true;
                closeSocket();
            }
        }
    }

    private void notifyUnexpectedDisconnect(String message) {
        if (intentionalClose || !disconnectNotified.compareAndSet(false, true)) {
            return;
        }
        closed = true;
        closeSocket();
        String detail = message == null || message.trim().isEmpty()
                ? "RomanVoice stream closed unexpectedly"
                : message;
        listener.onError(detail);
    }

    private void interruptAndJoin(Thread thread) {
        if (thread == null || thread == Thread.currentThread()) {
            return;
        }
        thread.interrupt();
        try {
            thread.join(THREAD_JOIN_TIMEOUT_MS);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    private void handleText(String text) throws JSONException {
        JSONObject payload = new JSONObject(text);
        String type = payload.optString("type", "");
        if ("ready".equals(type)) {
            listener.onReady();
        } else if ("started".equals(type)) {
            listener.onStarted();
        } else if ("partial".equals(type)) {
            listener.onPartial(payload.optString("text", ""));
        } else if ("final".equals(type)) {
            listener.onFinal(payload.optString("text", ""));
        } else if ("error".equals(type)) {
            listener.onError(payload.optString("error", "RomanVoice stream error"));
        }
    }

    private Frame readFrame() throws IOException {
        int first = readByte();
        int second = readByte();
        int opcode = first & 0x0F;
        if ((first & 0x70) != 0) {
            throw new IOException("RomanVoice sent unsupported WebSocket extensions");
        }
        if ((first & 0x80) == 0) {
            throw new IOException("RomanVoice sent a fragmented WebSocket frame");
        }
        long length = second & 0x7F;
        if (length == 126) {
            length = ByteBuffer.wrap(readExact(2)).getShort() & 0xFFFF;
        } else if (length == 127) {
            length = ByteBuffer.wrap(readExact(8)).getLong();
        }

        if (length < 0 || length > MAX_INBOUND_FRAME_BYTES) {
            throw new IOException("RomanVoice WebSocket frame is too large");
        }
        if ((opcode & 0x08) != 0 && length > 125) {
            throw new IOException("RomanVoice sent an oversized WebSocket control frame");
        }

        boolean masked = (second & 0x80) != 0;
        if (masked) {
            throw new IOException("RomanVoice server sent a masked WebSocket frame");
        }
        byte[] payload = readExact((int) length);
        return new Frame(opcode, payload);
    }

    private String readHttpHeaders() throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        int matched = 0;
        byte[] marker = new byte[]{'\r', '\n', '\r', '\n'};
        while (matched < marker.length) {
            int next = input.read();
            if (next < 0) {
                throw new IOException("RomanVoice closed during handshake");
            }
            if (buffer.size() >= MAX_HTTP_HEADER_BYTES) {
                throw new IOException("RomanVoice WebSocket handshake headers are too large");
            }
            buffer.write(next);
            matched = next == marker[matched] ? matched + 1 : 0;
        }
        return buffer.toString(StandardCharsets.UTF_8.name());
    }

    private byte[] readExact(int length) throws IOException {
        byte[] buffer = new byte[length];
        int offset = 0;
        while (offset < length) {
            int read = input.read(buffer, offset, length - offset);
            if (read < 0) {
                throw new IOException("RomanVoice stream closed");
            }
            offset += read;
        }
        return buffer;
    }

    private int readByte() throws IOException {
        int value = input.read();
        if (value < 0) {
            throw new IOException("RomanVoice stream closed");
        }
        return value;
    }

    private String randomWebSocketKey() {
        byte[] bytes = new byte[16];
        secureRandom.nextBytes(bytes);
        return Base64.encodeToString(bytes, Base64.NO_WRAP);
    }

    private String expectedWebSocketAccept(String key) throws IOException {
        try {
            MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
            byte[] digest = sha1.digest(
                    (key + WEBSOCKET_ACCEPT_GUID).getBytes(StandardCharsets.US_ASCII)
            );
            return Base64.encodeToString(digest, Base64.NO_WRAP);
        } catch (NoSuchAlgorithmException exception) {
            throw new IOException("SHA-1 is unavailable for WebSocket validation", exception);
        }
    }

    private String responseHeaderValue(String response, String headerName) {
        String[] lines = response.split("\r\n");
        for (int index = 1; index < lines.length; index++) {
            int separator = lines[index].indexOf(':');
            if (separator > 0
                    && headerName.equalsIgnoreCase(lines[index].substring(0, separator).trim())) {
                return lines[index].substring(separator + 1).trim();
            }
        }
        return "";
    }

    private String safeClientLabel() {
        String safe = clientLabel.replace("\r", "").replace("\n", "");
        return safe.isEmpty() ? "android" : safe;
    }

    private static final class Frame {
        final int opcode;
        final byte[] payload;

        Frame(int opcode, byte[] payload) {
            this.opcode = opcode;
            this.payload = payload;
        }
    }
}
