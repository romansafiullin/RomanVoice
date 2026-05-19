package app.romanvoice.ime;

import android.util.Base64;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;

import javax.net.ssl.SSLSocketFactory;

final class RomanVoiceStreamClient implements Closeable {
    interface Listener {
        void onReady();

        void onStarted();

        void onPartial(String text);

        void onFinal(String text);

        void onError(String message);
    }

    private final URI uri;
    private final String token;
    private final Listener listener;
    private final SecureRandom secureRandom = new SecureRandom();

    private Socket socket;
    private InputStream input;
    private OutputStream output;
    private Thread readerThread;
    private volatile boolean closed;

    RomanVoiceStreamClient(String streamUrl, String token, Listener listener) {
        this.uri = URI.create(streamUrl);
        this.token = token == null ? "" : token;
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

        if ("wss".equalsIgnoreCase(scheme)) {
            socket = SSLSocketFactory.getDefault().createSocket(uri.getHost(), port);
        } else {
            socket = new Socket(uri.getHost(), port);
        }
        socket.setSoTimeout(15000);
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

        readerThread = new Thread(this::readLoop, "RomanVoiceStreamReader");
        readerThread.start();
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
        if (closed) {
            return;
        }
        closed = true;
        try {
            sendFrame(0x8, new byte[]{0x03, (byte) 0xE8});
        } catch (Exception ignored) {
        }
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
            return;
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
                    closed = true;
                    return;
                }
                if (frame.opcode == 0x9) {
                    sendFrame(0xA, frame.payload);
                    continue;
                }
                if (frame.opcode == 0x1) {
                    handleText(new String(frame.payload, StandardCharsets.UTF_8));
                }
            }
        } catch (Exception exception) {
            if (!closed) {
                listener.onError(exception.getMessage());
            }
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
        long length = second & 0x7F;
        if (length == 126) {
            length = ByteBuffer.wrap(readExact(2)).getShort() & 0xFFFF;
        } else if (length == 127) {
            length = ByteBuffer.wrap(readExact(8)).getLong();
        }

        boolean masked = (second & 0x80) != 0;
        byte[] mask = masked ? readExact(4) : new byte[0];
        byte[] payload = readExact((int) length);
        if (masked) {
            for (int index = 0; index < payload.length; index++) {
                payload[index] = (byte) (payload[index] ^ mask[index % 4]);
            }
        }
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

    private static final class Frame {
        final int opcode;
        final byte[] payload;

        Frame(int opcode, byte[] payload) {
            this.opcode = opcode;
            this.payload = payload;
        }
    }
}
