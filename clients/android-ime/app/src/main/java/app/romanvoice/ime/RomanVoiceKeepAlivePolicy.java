package app.romanvoice.ime;

final class RomanVoiceKeepAlivePolicy {
    enum Action {
        SEND,
        WAIT,
        TIMEOUT
    }

    private RomanVoiceKeepAlivePolicy() {
    }

    static Action nextAction(long nowMs, long outstandingPingAtMs, long timeoutMs) {
        if (outstandingPingAtMs <= 0) {
            return Action.SEND;
        }
        if (nowMs - outstandingPingAtMs > timeoutMs) {
            return Action.TIMEOUT;
        }
        return Action.WAIT;
    }
}
