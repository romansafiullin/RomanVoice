package app.romanvoice.ime;

final class RomanVoiceTextRange {
    static final int RELOCATION_MIN_CHARS = 6;

    private RomanVoiceTextRange() {
    }

    static int[] findLiveDictationRange(
            String currentText,
            int insertionStart,
            int insertionEnd,
            String lastDictationText
    ) {
        String current = currentText == null ? "" : currentText;
        String last = lastDictationText == null ? "" : lastDictationText;
        if (last.isEmpty()) {
            return null;
        }

        int start = clamp(insertionStart, 0, current.length());
        int end = clamp(insertionEnd, start, current.length());
        if (end - start == last.length() && current.substring(start, end).equals(last)) {
            return new int[]{start, end};
        }

        if (last.length() < RELOCATION_MIN_CHARS) {
            return null;
        }

        int firstMatch = current.indexOf(last);
        if (firstMatch >= 0) {
            int secondMatch = current.indexOf(last, firstMatch + last.length());
            if (secondMatch < 0) {
                return new int[]{firstMatch, firstMatch + last.length()};
            }
        }

        if (end > start && end <= current.length() && end - start == last.length()) {
            return new int[]{start, end};
        }
        return null;
    }

    private static int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(value, max));
    }
}
